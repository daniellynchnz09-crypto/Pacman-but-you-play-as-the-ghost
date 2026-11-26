#!/usr/bin/env python3
"""Fit a small weighted model that imitates how YOU actually play the
player-controlled ghost, from the moves recorded during real play (see
PLAYER_MOVES_FILE / Game._record_player_ghost_move in Ghost_Pacman.py).

Run this after playing a batch of real sessions:

    python fit_player_ghost.py

It reads player_ghost_moves.jsonl, fits PLAYER_GHOST_WEIGHTS_FILE via
multinomial-logistic (softmax) gradient ascent - choosing weights that make
your own recorded choices as likely as possible under the model - and
reports how well the fit predicts choices it never trained on. A headless
training script can then do:

    g = gp.Game()
    with open(gp.PLAYER_GHOST_WEIGHTS_FILE) as f:
        g.ghost_autoplay_weights = json.load(f)

and the player's ghost will be driven the way its player actually plays,
instead of sitting passive after leaving the house.

Roughly 10-20 short real play sessions' worth of moves (a few thousand
decisions with more than one legal option) is a reasonable amount before
this is likely to generalize past memorizing a handful of specific
situations - see MIN_RECOMMENDED_EXAMPLES below. Re-run this any time
after playing more; it always refits from scratch on everything recorded
so far.
"""
import json
import math
import os
import random
import sys

import Ghost_Pacman as gp

EPOCHS = 400
LEARNING_RATE = 2e-6  # squared-distance features are large (maze-sized), so this stays small
MIN_RECOMMENDED_EXAMPLES = 2000


def load_moves():
    moves = []
    if not os.path.isfile(gp.PLAYER_MOVES_FILE):
        return moves
    with open(gp.PLAYER_MOVES_FILE) as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                moves.append(json.loads(line))
            except ValueError:
                continue
    return moves


def build_examples(moves):
    """Each recorded move becomes one (feature_rows, chosen_index)
    example: a feature row per direction that was actually legal at that
    moment, and which index was the one actually chosen. Moves with only
    one legal option are skipped - there was no real choice to learn
    from."""
    examples = []
    for move in moves:
        options = [tuple(d) for d in move["options"]]
        if len(options) < 2:
            continue
        chosen = tuple(move["chosen_dir"])
        if chosen not in options:
            continue
        pacman_position = tuple(move["pacman"])
        pacman_distances = gp.bfs_distances(pacman_position)
        # power_pellets/power_remaining_ms are absent on anything recorded
        # before player_ghost_features grew its exit-coverage/power-pellet-
        # guard/frightened-timer inputs - those features just see "no
        # pellets left"/"0ms remaining" on older moves rather than a
        # fabricated guess.
        power_pellets = [tuple(p) for p in move.get("power_pellets", [])]
        frightened = move.get("frightened", False)
        power_remaining_ms = move.get("power_remaining_ms", 0)
        # Which ordinary pellets were still uneaten (recorder version 2 only;
        # older moves leave the one input that uses it at 0).
        pellets = None
        if "pellets" in move:
            mask = int(move["pellets"], 16)
            pellets = {tile for i, tile in enumerate(gp.pellet_tile_order()) if mask >> i & 1}
        rows = [
            gp.player_ghost_features(
                tuple(move["ghost"]), tuple(move["prev_dir"]),
                pacman_position, tuple(move["pacman_dir"]),
                [tuple(p) for p in move["ai_ghosts"]],
                option, pacman_distances,
                power_pellets=power_pellets, frightened=frightened, power_remaining_ms=power_remaining_ms,
                pellets=pellets,
            )
            for option in options
        ]
        examples.append((rows, options.index(chosen)))
    return examples


def softmax(scores):
    peak = max(scores)
    exps = [math.exp(s - peak) for s in scores]
    total = sum(exps)
    return [e / total for e in exps]


def score_row(weights, row, feature_names):
    return sum(weights[name] * row[name] for name in feature_names)


def fit(examples, feature_names, epochs=EPOCHS, lr=LEARNING_RATE):
    weights = {name: 0.0 for name in feature_names}
    for _epoch in range(epochs):
        random.shuffle(examples)
        grad = {name: 0.0 for name in feature_names}
        for rows, chosen_idx in examples:
            probs = softmax([score_row(weights, row, feature_names) for row in rows])
            for name in feature_names:
                observed = rows[chosen_idx][name]
                expected = sum(p * row[name] for p, row in zip(probs, rows))
                grad[name] += observed - expected
        n = max(1, len(examples))
        for name in feature_names:
            weights[name] += lr * grad[name] / n
    return weights


def accuracy(examples, weights, feature_names):
    if not examples:
        return None
    correct = 0
    for rows, chosen_idx in examples:
        scores = [score_row(weights, row, feature_names) for row in rows]
        if scores.index(max(scores)) == chosen_idx:
            correct += 1
    return correct / len(examples)


def main():
    moves = load_moves()
    examples = build_examples(moves)
    print(f"{len(moves)} recorded moves total, {len(examples)} usable (had a real choice to make)")

    if not examples:
        print("Nothing to fit yet - play some real games first (the moves log fills up "
              "automatically whenever you control the ghost after it leaves the house).")
        sys.exit(0)

    if len(examples) < MIN_RECOMMENDED_EXAMPLES:
        print(f"Note: fewer than {MIN_RECOMMENDED_EXAMPLES} usable moves so far - roughly "
              f"10-20 short play sessions is a reasonable target before this generalizes "
              f"well rather than just memorizing a few specific situations. Fitting anyway "
              f"with what's here; re-run this any time after playing more.")

    random.shuffle(examples)
    split = max(1, int(len(examples) * 0.8))
    train, test = examples[:split], examples[split:]

    weights = fit(train, gp.PLAYER_GHOST_FEATURE_NAMES)
    train_acc = accuracy(train, weights, gp.PLAYER_GHOST_FEATURE_NAMES)
    test_acc = accuracy(test, weights, gp.PLAYER_GHOST_FEATURE_NAMES)

    print("\nfitted weights:")
    for name, value in weights.items():
        print(f"  {name}: {value:+.6f}")
    print(f"\ntrain accuracy: {train_acc * 100:.1f}% (on {len(train)} moves)")
    if test_acc is not None:
        print(f"held-out accuracy: {test_acc * 100:.1f}% (on {len(test)} moves never used for fitting)")
    else:
        print("held-out accuracy: n/a (not enough examples for a held-out split)")

    with open(gp.PLAYER_GHOST_WEIGHTS_FILE, "w") as handle:
        json.dump(weights, handle, indent=2)
    print(f"\nsaved to {gp.PLAYER_GHOST_WEIGHTS_FILE}")


if __name__ == "__main__":
    main()
