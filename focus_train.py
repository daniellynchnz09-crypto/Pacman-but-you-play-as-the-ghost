#!/usr/bin/env python3
"""Headless training run that hill-climbs ONE weight exclusively, instead of
cycling through all of them coordinate-wise - see PacmanBrain's normal
1-in-N coordinate cycle in Ghost_Pacman.py. Useful right after fixing a bug
that changes what a weight actually does (see endgame_rush_weight, fixed
2026-11-13 - it used to add an identical bonus to every candidate move and
so was behaviorally inert) and wanting it to settle on a sensible value
fast, without waiting for its turn to come up across every other weight.

Leaves every other weight exactly where it is: a challenger trial here only
ever nudges FOCUS_WEIGHT. Safe to interleave with normal train_pacman.py
runs - it reuses the same pacman_brain.json, and the coordinate index it
leaves behind (always 0, since there's only one slot in the cycle) is still
a valid starting point for the next run's full coordinate cycle.

Run with:
    python focus_train.py <weight_name> [num_games] [max_minutes]
"""
import os
import random
import sys
import time

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import Ghost_Pacman as gp

FOCUS_WEIGHT = sys.argv[1] if len(sys.argv) > 1 else None
MAX_MINUTES = float(sys.argv[3]) if len(sys.argv) > 3 else None
NUM_GAMES = int(sys.argv[2]) if len(sys.argv) > 2 else (100_000 if MAX_MINUTES else 5000)
PROGRESS_EVERY = 100


def main():
    if FOCUS_WEIGHT not in gp.LEARNED_WEIGHT_DEFAULTS:
        print(f"Usage: python focus_train.py <weight_name> [num_games] [max_minutes]")
        print(f"Unknown weight '{FOCUS_WEIGHT}' - choices: {list(gp.LEARNED_WEIGHT_DEFAULTS)}")
        sys.exit(1)

    g = gp.Game()
    g.brain._coordinate_order = [FOCUS_WEIGHT]
    g.brain._coordinate_index = 0
    print(
        f"Focused run: every challenger trial nudges only '{FOCUS_WEIGHT}' "
        f"(currently {g.brain.weights[FOCUS_WEIGHT]:.4f}, bounds {gp.LEARNED_WEIGHT_BOUNDS[FOCUS_WEIGHT]}, "
        f"step {gp.LEARNED_WEIGHT_EXPLORATION[FOCUS_WEIGHT]})."
    )
    if MAX_MINUTES:
        print(f"Time-boxed run: stopping after {MAX_MINUTES:.1f} minute(s) (or {NUM_GAMES} games, whichever first).")

    step = 10
    wins = 0
    window_wins = 0
    start = time.time()
    game_i = 0
    for game_i in range(1, NUM_GAMES + 1):
        random.seed(g.brain.seed_for_next_game())
        g.reset_game()
        g.start_time = 0
        now = 0
        while not g.game_over:
            now += step
            g.update(now)

        if g.outcome == "maze_cleared":
            wins += 1
            window_wins += 1

        elapsed = time.time() - start
        if game_i % PROGRESS_EVERY == 0:
            print(
                f"[{game_i}/{NUM_GAMES}] brain_games={g.brain.games_played} "
                f"phase={g.brain._phase} {FOCUS_WEIGHT}={g.brain.weights[FOCUS_WEIGHT]:.4f} "
                f"avg_reward={g.brain.average_reward:+.3f} best_reward={g.brain.best_reward:+.3f} "
                f"win_rate(last {PROGRESS_EVERY})={window_wins / PROGRESS_EVERY:.0%} "
                f"overall_win_rate={wins / game_i:.0%} elapsed={elapsed:.1f}s",
                flush=True,
            )
            window_wins = 0

        if MAX_MINUTES and elapsed >= MAX_MINUTES * 60:
            print(f"\nTime budget reached after {game_i} games ({elapsed:.1f}s).")
            break

    played = game_i
    print(
        f"\nDone: {played} games, overall win rate {wins / played:.1%}, "
        f"final {FOCUS_WEIGHT}={g.brain.weights[FOCUS_WEIGHT]:.4f}"
    )


if __name__ == "__main__":
    main()
