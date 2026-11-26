#!/usr/bin/env python3
"""Parallel Pac-Man trainer with a strict acceptance gate.

    python train_parallel.py <minutes> [es|coordinate]

Replaces the 24-game champion/challenger judgment in PacmanBrain with:

- Proposals from one of two searches:
    es          - population search over all weights at once (antithetic
                  evolution strategy with rank-shaped fitness), so weights
                  that only work in combination can be found.
    coordinate  - one weight nudged at a time, screened on a small batch.
- A gate: a proposal only replaces the champion in pacman_brain.json if
  it beats the champion over GATE_PAIRS games on shared seeds with
  z >= GATE_Z. A 24-game trial cannot tell a 2-point change from luck;
  this can.
- A best-brain record (pacman_brain_best.json) and a checkpoint on every
  accepted change, so a regression can always be undone.

Frozen weights (frozen_weights.json, a JSON list of names) are held at
their current value and left out of the search.

Which opponent: PACMAN_PLAYER_GHOST_VERSION (see parallel_eval.py), and
PACMAN_PLAYER_GHOST_HIDDEN=1 to play it the way a real game goes - Pac-Man
not told its moves, planning against PACMAN_ASSUMED_PLAYER instead.

PACMAN_TRAIN_SCORE=progress judges by progress instead of wins: a game
scores the share of the maze eaten, plus 0.5 for clearing it. Against the
stand-in for the real player Pac-Man clears only a few games in a hundred,
so a win/loss gate would need tens of thousands of games to tell two
brains apart, while how far he gets differs in every game. The gate is then
a paired test on that score (mean difference / its standard error >=
GATE_Z) and the search climbs the same score.

Sizes can be set without editing this file: PACMAN_GATE_PAIRS,
PACMAN_GATE_Z, PACMAN_ES_PAIRS, PACMAN_ES_SEEDS, PACMAN_ES_GATE_EVERY.
"""
import json
import os
import random
import sys
import time

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import checkpoints
import parallel_eval as pe

HERE = os.path.dirname(os.path.abspath(__file__))
BRAIN_FILE = os.path.join(HERE, "pacman_brain.json")
BEST_FILE = os.path.join(HERE, "pacman_brain_best.json")
FROZEN_FILE = os.path.join(HERE, "frozen_weights.json")

GATE_PAIRS = int(os.environ.get("PACMAN_GATE_PAIRS", "2000"))
GATE_Z = float(os.environ.get("PACMAN_GATE_Z", "1.5"))

ES_PAIRS = int(os.environ.get("PACMAN_ES_PAIRS", "16"))          # antithetic pairs per generation (population = 2x)
ES_SEEDS = int(os.environ.get("PACMAN_ES_SEEDS", "96"))          # games per population member per generation
ES_LEARNING_RATE = 1.5
ES_GATE_EVERY = int(os.environ.get("PACMAN_ES_GATE_EVERY", "3"))  # generations between gate attempts
TRAIN_SCORE = os.environ.get("PACMAN_TRAIN_SCORE", "wins")       # "wins" or "progress" - see the note at the top

SCREEN_PAIRS = 300     # coordinate mode: cheap first filter
REPLAYS_PER_GATE = 5   # champion's best wins at each gate, re-played so they reach the replay browser


def progress(result):
    """One game's progress score: share of the maze eaten, +0.5 for a clear."""
    return result["pellet_fraction"] + (0.5 if result["won"] else 0.0)


def mean_progress(results):
    return sum(progress(r) for r in results) / len(results)


def paired_progress_z(a, b):
    """z score for 'a gets further than b' on shared seeds."""
    diffs = [progress(x) - progress(y) for x, y in zip(a, b)]
    n = len(diffs)
    mean = sum(diffs) / n
    variance = sum((d - mean) ** 2 for d in diffs) / max(1, n - 1)
    return mean / ((variance / n) ** 0.5) if variance > 0 else 0.0


def clamp(value, low, high):
    return min(high, max(low, value))


def search_sigma(gp, key, value):
    """Per-weight search scale: same shape as PacmanBrain._exploration_step,
    so a weight that has settled small is searched in small steps."""
    base = gp.LEARNED_WEIGHT_EXPLORATION[key]
    return min(base, max(base * gp.WEIGHT_STEP_FLOOR_FRACTION, abs(value) * gp.WEIGHT_STEP_RELATIVE_FRACTION))


def save_brain(weights, games_added):
    try:
        with open(BRAIN_FILE) as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    data["weights"] = weights
    data["games_played"] = int(data.get("games_played", 0)) + games_added
    with open(BRAIN_FILE, "w") as f:
        json.dump(data, f, indent=2)
    return data["games_played"]


def record_history(gp, results, first_game_number):
    for offset, r in enumerate(results):
        gp.record_game_history(
            first_game_number + offset, r["outcome"], won=r["won"], reward=r["reward"],
            elapsed_ms=r["elapsed_ms"], pellet_fraction=r["pellet_fraction"],
            ghosts_eaten=r["ghosts_eaten"], near_misses=r["near_misses"],
        )


def capture_replays(gp, game, weights, seeds, results):
    """Workers don't save replays (a dozen processes writing the top-10
    list at once would corrupt it). Games are deterministic for a given
    weight set and seed, so the champion's best wins are simply played
    again here, in the main process, with replay saving on."""
    wins = [i for i, r in enumerate(results) if r["won"]]
    wins.sort(key=lambda i: (results[i]["outcome"] != "maze_cleared", -results[i]["pellet_fraction"], results[i]["elapsed_ms"]))
    real_record = gp.record_game_history
    gp.record_game_history = lambda *args, **kwargs: None  # already recorded by the gate
    try:
        for i in wins[:REPLAYS_PER_GATE]:
            pe.play_in_process(game, weights, seeds[i])
    finally:
        gp.record_game_history = real_record


def main():
    import Ghost_Pacman as gp

    minutes = float(sys.argv[1]) if len(sys.argv) > 1 else 30
    mode = sys.argv[2] if len(sys.argv) > 2 else "es"
    deadline = time.time() + minutes * 60

    champion = pe.load_weights(BRAIN_FILE)
    frozen = set()
    if os.path.exists(FROZEN_FILE):
        with open(FROZEN_FILE) as f:
            frozen = set(json.load(f))
    keys = [k for k in champion if k not in frozen]
    bounds = gp.LEARNED_WEIGHT_BOUNDS
    print(f"Mode {mode}, {minutes:.0f} min, {pe.DEFAULT_WORKERS} workers, searching {len(keys)} weights ({len(frozen)} frozen).")
    print(f"Checkpoint: {checkpoints.save_checkpoint('pre-parallel-training')}", flush=True)

    replay_game = gp.Game()
    pe.configure_game(replay_game)
    total_games = 0
    accepted = 0
    champion_rates = []

    with pe.Evaluator() as ev:

        def gate(candidate, label):
            """Champion vs candidate on fresh shared seeds. Returns True
            (and installs the candidate) only on a clear win."""
            nonlocal champion, total_games, accepted
            seeds = pe.make_seeds(GATE_PAIRS)
            champ_r, cand_r = ev.evaluate([champion, candidate], seeds)
            total_games += 2 * GATE_PAIRS
            z, cand_only, champ_only = pe.paired_z(cand_r, champ_r)
            champ_rate, cand_rate = pe.win_rate(champ_r), pe.win_rate(cand_r)
            if TRAIN_SCORE == "progress":
                z = paired_progress_z(cand_r, champ_r)
                print(f"  gate [{label}]: progress champion {mean_progress(champ_r):.3f} vs candidate "
                      f"{mean_progress(cand_r):.3f}", flush=True)
            champion_rates.append(champ_rate)
            games_played = save_brain(champion, GATE_PAIRS)
            record_history(gp, champ_r, games_played - GATE_PAIRS + 1)
            capture_replays(gp, replay_game, champion, seeds, champ_r)
            verdict = "ACCEPTED" if z >= GATE_Z else "rejected"
            print(f"  gate [{label}]: champion {champ_rate:.1%} vs candidate {cand_rate:.1%} "
                  f"(z={z:+.2f}) -> {verdict}", flush=True)
            if z < GATE_Z:
                return False
            champion = dict(candidate)
            accepted += 1
            save_brain(champion, 0)
            best_rate = -1.0
            if os.path.exists(BEST_FILE):
                with open(BEST_FILE) as f:
                    best_rate = json.load(f).get("win_rate", -1.0)
            if cand_rate > best_rate or TRAIN_SCORE == "progress":
                # (In progress mode every accepted change is the new best:
                # win rates this low are too noisy to rank brains by.)
                with open(BEST_FILE, "w") as f:
                    json.dump({
                        "win_rate": cand_rate, "progress": mean_progress(cand_r), "games": GATE_PAIRS,
                        "opponent": os.environ.get("PACMAN_PLAYER_GHOST_VERSION", ""), "weights": champion,
                    }, f, indent=2)
            folder = checkpoints.save_checkpoint("gate-accepted")
            checkpoints.record_outcome(folder, games=GATE_PAIRS, win_rate=round(cand_rate, 4))
            return True

        if mode == "coordinate":
            index = 0
            while time.time() < deadline:
                key = keys[index % len(keys)]
                index += 1
                low, high = bounds[key]
                step = search_sigma(gp, key, champion[key])
                candidate = dict(champion)
                candidate[key] = clamp(champion[key] + random.uniform(-step, step), low, high)
                if candidate[key] == champion[key]:
                    continue
                champ_r, cand_r = ev.evaluate([champion, candidate], pe.make_seeds(SCREEN_PAIRS))
                total_games += 2 * SCREEN_PAIRS
                z, _, _ = pe.paired_z(cand_r, champ_r)
                print(f"screen {key}: {champion[key]:.4g} -> {candidate[key]:.4g}  z={z:+.2f}", flush=True)
                if z >= 1.0 and time.time() < deadline:
                    gate(candidate, key)
        else:
            mean = dict(champion)
            generation = 0
            while time.time() < deadline:
                generation += 1
                sigma = {k: search_sigma(gp, k, mean[k]) * 0.5 for k in keys}
                noises, population = [], []
                for _ in range(ES_PAIRS):
                    eps = {k: random.gauss(0.0, 1.0) for k in keys}
                    noises.append(eps)
                    for sign in (1.0, -1.0):
                        member = dict(mean)
                        for k in keys:
                            member[k] = clamp(mean[k] + sign * sigma[k] * eps[k], *bounds[k])
                        population.append(member)
                results = ev.evaluate(population, pe.make_seeds(ES_SEEDS))
                total_games += len(population) * ES_SEEDS
                fitness = [mean_progress(r) if TRAIN_SCORE == "progress" else pe.mean_reward(r) for r in results]
                order = sorted(range(len(fitness)), key=lambda i: fitness[i])
                rank = {i: n / (len(order) - 1) - 0.5 for n, i in enumerate(order)}
                for k in keys:
                    grad = sum((rank[2 * p] - rank[2 * p + 1]) * noises[p][k] for p in range(ES_PAIRS)) / ES_PAIRS
                    mean[k] = clamp(mean[k] + ES_LEARNING_RATE * sigma[k] * grad, *bounds[k])
                pop_rate = sum(pe.win_rate(r) for r in results) / len(results)
                print(f"gen {generation}: population win rate {pop_rate:.1%}, "
                      f"{'progress' if TRAIN_SCORE == 'progress' else 'reward'} mean "
                      f"{sum(fitness) / len(fitness):+.3f} best {max(fitness):+.3f}", flush=True)
                if generation % ES_GATE_EVERY == 0 and time.time() < deadline:
                    if not gate(mean, f"gen {generation}"):
                        # Keep searching from the unproven mean, but pull it
                        # halfway back so it cannot drift far unvalidated.
                        for k in keys:
                            mean[k] = (mean[k] + champion[k]) / 2.0

    rate = sum(champion_rates) / len(champion_rates) if champion_rates else float("nan")
    print(f"\nDone: {total_games} games, {accepted} change(s) accepted, "
          f"champion win rate at gates {rate:.1%} (latest {champion_rates[-1] if champion_rates else float('nan'):.1%})")


if __name__ == "__main__":
    main()
