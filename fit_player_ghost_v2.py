#!/usr/bin/env python3
"""Fit the player-ghost imitation model properly, and say honestly how well
it predicts the recorded human moves.

    python fit_player_ghost_v2.py                  # report only, writes nothing
    python fit_player_ghost_v2.py --save goose2    # also writes player_ghost_versions/goose2.json
    python fit_player_ghost_v2.py --era all        # which recorded moves to use: new | old | all
    python fit_player_ghost_v2.py --emphasis 1     # how much extra a junction decision or a turn counts (default 3)
    python fit_player_ghost_v2.py --half a         # fit on every other game only (a or b), for honest tests

Same model as fit_player_ghost.py (one weight per input of
Ghost_Pacman.player_ghost_features, highest score wins), so the saved file
drives the ghost through the existing code unchanged. What differs is the
fitting:

- every input is put on the same scale first, and the fit runs until it has
  converged (fit_player_ghost.py stops far short of that: its step size is
  tiny because the raw distance inputs are huge);
- accuracy is measured on whole games held out of the fit (every 5th game),
  not on moves shuffled out of the same games;
- the result is compared with two rules that need no fitting at all - "keep
  going straight" and "take the shortest way to Pac-Man" - and with the
  current Goose, overall, at junctions (3+ ways open) and on the moves where
  the player actually turned. A model that cannot beat "keep going straight"
  has not learned how the player steers.

"Emphasis": four moves in five are "carry straight on along a corridor", so
a fit that counts every move the same learns mostly that, and the ghost it
produces drifts instead of hunting - Pac-Man cleared the maze against it
far more often, and survived about twice as long, as against the person it
was fitted to. Counting each junction decision and each turn 3 times gives
a ghost that catches Pac-Man about as fast as the real player does, ends
games the same ways, and takes the shortest way / doubles back about as
often (measured 2026-11-18). So judge a fit by playing it, not only by the
percentages printed here.

"Seal": the fit alone still under-uses the one thing that makes the real
player deadly against a Pac-Man that predicts them - standing where he has
least room instead of following him (parking on the row above the bottom
row and mirroring him until a classic ghost arrives). Move by move that
looks ordinary, so the fit gives the room_alone input almost no weight, and
the ghost it produces caught Pac-Man at 66 s where the player took 29 s.
--seal K subtracts K from the saved room_alone weight (per ten tiles of
room). K = 3 was calibrated on 2026-11-20 against the 30 games played
versus the predicting Pac-Man: caught at a median 41 s with 44% eaten, no
clears, 47% of kills in the bottom rows (player: 29 s, 43%, none, 57%). It
is applied to the saved file only; the accuracy figures printed are for the
plain fit.

"Era": moves recorded since the maze-clear-only rules (recorder version 2)
are "new"; everything before is "old" (different rules, a much weaker
Pac-Man). The default is the new era once it holds enough decisions, since
that is the play Pac-Man now has to beat.
"""
import argparse
import json
import os
import sys

import numpy as np

import fit_player_ghost as fpg
import Ghost_Pacman as gp

VERSIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "player_ghost_versions")
NAMES = list(gp.PLAYER_GHOST_FEATURE_NAMES)
MIN_NEW_ERA_DECISIONS = 1500  # below this, the new era alone is too little to fit on
HOLD_OUT_EVERY = 5  # every 5th game is kept out of the fit and used to measure it
MAX_OPTIONS = 4


def is_new_era(move):
    return move.get("v", 1) >= 2


def game_ids(moves):
    """Which game each recorded move belongs to. New-era moves say so; older
    ones are split wherever the game clock jumps backwards."""
    ids = []
    current, last_t, last_tag = 0, -1, None
    for move in moves:
        tag = move.get("game")
        if (tag is not None and tag != last_tag) or (tag is None and move["t"] < last_t) or (tag is None) != (last_tag is None):
            current += 1
        ids.append(current)
        last_t, last_tag = move["t"], tag
    return ids


def build(moves):
    """One row of inputs per way open to the ghost, for every recorded move
    where there was a real choice. Returns a dict of arrays."""
    ids = game_ids(moves)
    X, mask, chosen, game, n_options, turned, straight_guess = [], [], [], [], [], [], []
    for move, gid in zip(moves, ids):
        examples = fpg.build_examples([move])
        if not examples:
            continue
        rows, chosen_index = examples[0]
        options = [tuple(d) for d in move["options"]]
        previous = tuple(move["prev_dir"])
        x = np.zeros((MAX_OPTIONS, len(NAMES)))
        m = np.zeros(MAX_OPTIONS, dtype=bool)
        for i, row in enumerate(rows):
            x[i] = [row[name] for name in NAMES]
            m[i] = True
        # What "keep going straight" would have done here: carry on if that
        # way is open, else the first way that is not a U-turn.
        if previous in options:
            guess = options.index(previous)
        else:
            reverse = (-previous[0], -previous[1])
            guess = next((i for i, d in enumerate(options) if d != reverse), 0)
        X.append(x)
        mask.append(m)
        chosen.append(chosen_index)
        game.append(gid)
        n_options.append(len(rows))
        turned.append(tuple(move["chosen_dir"]) != previous)
        straight_guess.append(guess)
    return {
        "X": np.array(X), "mask": np.array(mask), "chosen": np.array(chosen), "game": np.array(game),
        "n_options": np.array(n_options), "turned": np.array(turned), "straight": np.array(straight_guess),
    }


def usable_inputs(data):
    """Inputs that differ between the ways open in at least one decision. One
    that is the same for every way (frightened_remaining_fraction is) cannot
    change which way scores highest, so it is left at zero."""
    X, mask = data["X"], data["mask"]
    keep = []
    for j in range(X.shape[2]):
        column = np.where(mask, X[:, :, j], np.nan)
        spread = np.nanmax(column, axis=1) - np.nanmin(column, axis=1)
        if np.any(spread > 0):
            keep.append(j)
    return keep


def fit(data, rows, columns, scale, iterations=3000, step=0.05, shrink=1e-4, emphasis=1.0):
    """Weights (on the scaled inputs) that make the recorded choices as
    likely as possible: softmax regression, Adam, run to convergence.
    A junction decision (3+ ways open) or a turn counts `emphasis` times."""
    xs = data["X"][rows][:, :, columns] / scale[columns]
    mask, chosen = data["mask"][rows], data["chosen"][rows]
    onehot = np.zeros((len(chosen), MAX_OPTIONS))
    onehot[np.arange(len(chosen)), chosen] = 1
    count = np.where((data["n_options"][rows] >= 3) | data["turned"][rows], float(emphasis), 1.0)
    count = count / count.sum()
    w = np.zeros(len(columns))
    m1 = np.zeros(len(columns))
    m2 = np.zeros(len(columns))
    for t in range(1, iterations + 1):
        scores = np.where(mask, xs @ w, -1e9)
        scores = scores - scores.max(axis=1, keepdims=True)
        p = np.exp(scores) * mask
        p = p / p.sum(axis=1, keepdims=True)
        gradient = (((onehot - p) * count[:, None])[:, :, None] * xs).sum(axis=(0, 1)) - shrink * w
        m1 = 0.9 * m1 + 0.1 * gradient
        m2 = 0.999 * m2 + 0.001 * gradient * gradient
        w = w + step * (m1 / (1 - 0.9 ** t)) / (np.sqrt(m2 / (1 - 0.999 ** t)) + 1e-8)
    return w


def to_weight_dict(w, columns, scale):
    """Back to one weight per raw input, as the game uses them."""
    weights = {name: 0.0 for name in NAMES}
    for value, j in zip(w, columns):
        weights[NAMES[j]] = float(value / scale[j])
    return weights


def predictions(data, rows, weights):
    """The way each decision would go under `weights` - first of the highest
    scores, as choose_player_ghost_direction breaks ties."""
    vector = np.array([weights.get(name, 0.0) for name in NAMES])
    scores = np.where(data["mask"][rows], data["X"][rows] @ vector, -np.inf)
    return scores.argmax(axis=1)


def report_line(label, right, data, rows):
    junction = data["n_options"][rows] >= 3
    turned = data["turned"][rows]

    def pct(selection):
        return "%5.1f%%" % (100.0 * right[selection].mean()) if selection.any() else "   n/a"

    print("  %-40s all %s   junctions %s   turns %s   turns at junctions %s" % (
        label, pct(np.ones(len(right), dtype=bool)), pct(junction), pct(turned), pct(junction & turned)))


def main():
    parser = argparse.ArgumentParser(description="Fit and measure the player-ghost imitation model.")
    parser.add_argument("--era", choices=("new", "old", "all"), default=None)
    parser.add_argument("--save", metavar="NAME", default=None,
                        help="write the fit (on every game of the era) to player_ghost_versions/NAME.json")
    parser.add_argument("--emphasis", type=float, default=3.0,
                        help="how many times a junction decision or a turn counts in the fit (default 3)")
    parser.add_argument("--seal", type=float, default=3.0, metavar="K",
                        help="subtract K from the saved room_alone weight (default 3; see the note at the top)")
    parser.add_argument("--since", type=int, default=None, metavar="GAME",
                        help="use only games numbered GAME or later (the 'game' field of the new-era records)")
    parser.add_argument("--half", choices=("a", "b"), default=None,
                        help="use only every other game (a: 1st, 3rd, ...; b: 2nd, 4th, ...)")
    args = parser.parse_args()

    moves = fpg.load_moves()
    new = [m for m in moves if is_new_era(m)]
    old = [m for m in moves if not is_new_era(m)]
    print("%d recorded moves: %d old era, %d new era (maze-clear rules)" % (len(moves), len(old), len(new)))

    era = args.era
    if era is None:
        new_decisions = sum(1 for m in new if len(m["options"]) >= 2)
        era = "new" if new_decisions >= MIN_NEW_ERA_DECISIONS else "all"
        if era == "all" and new:
            print("only %d new-era decisions so far (want %d): using every era for now" % (
                new_decisions, MIN_NEW_ERA_DECISIONS))
    chosen_moves = {"new": new, "old": old, "all": moves}[era]
    if args.since is not None:
        chosen_moves = [m for m in chosen_moves if m.get("game", -1) >= args.since]
    data = build(chosen_moves)
    total = len(data["chosen"])
    if total == 0:
        print("nothing to fit in era '%s' yet" % era)
        sys.exit(0)
    games = np.unique(data["game"])
    if args.half:
        games = games[(0 if args.half == "a" else 1)::2]
        keep = np.isin(data["game"], games)
        data = {key: value[keep] for key, value in data.items()}
        total = len(data["chosen"])
    print("era '%s'%s: %d decisions with a real choice, in %d games" % (
        era, " (half %s)" % args.half if args.half else "", total, len(games)))

    held_games = games[HOLD_OUT_EVERY - 1::HOLD_OUT_EVERY]
    held = np.where(np.isin(data["game"], held_games))[0]
    fitted = np.where(~np.isin(data["game"], held_games))[0]
    if len(held) == 0 or len(fitted) == 0:
        print("too few games to hold any out - play more before trusting a fit")
        sys.exit(0)

    scale = data["X"][data["mask"]].std(axis=0)
    scale[scale == 0] = 1.0
    columns = usable_inputs(data)
    dropped = [NAMES[j] for j in range(len(NAMES)) if j not in columns]
    if dropped:
        print("inputs that never differ between the ways open (left at zero): %s" % ", ".join(dropped))

    w = fit(data, fitted, columns, scale, emphasis=args.emphasis)
    weights = to_weight_dict(w, columns, scale)

    print("\nhow often each predicts the player's actual move, on %d held-out games (%d decisions, %d at junctions, %d turns):" % (
        len(held_games), len(held), int((data["n_options"][held] >= 3).sum()), int(data["turned"][held].sum())))
    truth = data["chosen"][held]
    report_line("keep going straight", data["straight"][held] == truth, data, held)
    chase = {name: 0.0 for name in NAMES}
    chase["dist_pacman"] = -1.0
    report_line("shortest way to Pac-Man (Claude+)", predictions(data, held, chase) == truth, data, held)
    goose_path = os.path.join(VERSIONS_DIR, "goose.json")
    if os.path.isfile(goose_path):
        with open(goose_path) as handle:
            report_line("current Goose", predictions(data, held, json.load(handle)) == truth, data, held)
    report_line("this fit", predictions(data, held, weights) == truth, data, held)
    report_line("  (this fit, on the games it was fitted to)", predictions(data, fitted, weights) == data["chosen"][fitted], data, fitted)

    print("\nwhat the fit leans on (weight per typical spread of each input, largest first):")
    for value, j in sorted(zip(w, columns), key=lambda pair: -abs(pair[0])):
        print("  %-32s %+.3f" % (NAMES[j], value))

    if args.save:
        everything = np.arange(total)
        final = to_weight_dict(fit(data, everything, columns, scale, emphasis=args.emphasis), columns, scale)
        if args.seal and "room_alone" in final:
            final["room_alone"] -= args.seal
        os.makedirs(VERSIONS_DIR, exist_ok=True)
        path = os.path.join(VERSIONS_DIR, args.save + ".json")
        with open(path, "w") as handle:
            json.dump(final, handle, indent=2)
        print("\nsaved a fit on all %d games to %s" % (len(games), path))
        print("(the accuracy above is for a fit that never saw the held-out games; this one has seen them all)")
        print("now rebuild the table of its moves that Pac-Man plans against:")
        print("    python build_player_ghost_hops.py %s" % args.save)
    else:
        print("\nnothing saved (use --save NAME to write player_ghost_versions/NAME.json)")


if __name__ == "__main__":
    main()
