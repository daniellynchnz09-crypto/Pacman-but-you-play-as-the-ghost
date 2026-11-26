#!/usr/bin/env python3
"""Headless Pac-Man-brain training run, with the player-controlled ghost
driven by the fitted imitation model (player_ghost_weights.json) instead
of sitting passive the whole game - see fit_player_ghost.py and
Game.ghost_autoplay_weights.

Run with:

    python train_pacman.py [num_games]
    python train_pacman.py [num_games] [max_minutes]

Give max_minutes to stop early once that much wall-clock time has passed,
whichever comes first - num_games then just acts as a safety cap (default
a high one when max_minutes is given, so the time budget is what actually
governs how many games run).

Pac-Man's brain (pacman_brain.json), training history
(pacman_history.jsonl), and any winning/near-winning replays all persist
exactly as they do during normal play - this just plays a lot of games
back-to-back without a real window or real-time waiting.
"""
import json
import os
import random
import sys
import time

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import Ghost_Pacman as gp
import checkpoints

MAX_MINUTES = float(sys.argv[2]) if len(sys.argv) > 2 else None
NUM_GAMES = int(sys.argv[1]) if len(sys.argv) > 1 else (100_000 if MAX_MINUTES else 5000)
PROGRESS_EVERY = 100

# Off again: 3 successive runs at the full difficulty (classic ghosts +
# player ghost, ~35,000 games total) showed no real traction - 0.6%,
# 0.8%, 0.3% win rate, flat/noisy, not trending up. Easing off the player
# ghost once more to let Pac-Man consolidate its newer abilities
# (ambush/bait, ghosts-eaten reward, real player-ghost lookahead
# prediction - none of which existed during its last ~19% run here)
# against just the classic ghosts before taking another run at the full
# combination.
#
# Named difficulty stages for the player ghost's autoplay model (see
# build_player_ghost_versions.py for how each was built) - None means it
# sits passive. In rough order of difficulty:
#   "claude_plus" - bare direct chaser, no sophistication at all
#   "blinky_plus" - claude_plus plus a little exit-awareness
#   "bad_goose"   - the underfit, noisy early imitation fit
#   "goose"       - the current real fitted model
#   "full_power"  - fitted 2026-11-18 by fit_player_ghost_v2.py to the
#                   games played against the maze-clear-rules Pac-Man,
#                   with the "how a person hunts" inputs: the closest
#                   stand-in for the real player (it catches Pac-Man about
#                   as fast and in the same ways). For a test that means
#                   what a real game would, play it hidden - Pac-Man not
#                   told its moves: PACMAN_PLAYER_GHOST_HIDDEN=1 with
#                   parallel_eval.py / train_parallel.py.
PLAYER_GHOST_VERSION = None

# Curriculum knob (see Game.ghost_autoplay_mistake_chance): what fraction
# of the player ghost's autoplayed moves are a random blunder instead of
# its real model's choice, only meaningful when PLAYER_GHOST_VERSION is
# set. 0.0 is full strength for whichever version is selected - this
# composes with PLAYER_GHOST_VERSION (e.g. "goose" at 50% mistake chance
# is its own, finer-grained difficulty step) rather than replacing it.
PLAYER_GHOST_MISTAKE_CHANCE = 0.0

PLAYER_GHOST_VERSIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "player_ghost_versions")


def main():
    autoplay_weights = None
    if PLAYER_GHOST_VERSION:
        path = os.path.join(PLAYER_GHOST_VERSIONS_DIR, f"{PLAYER_GHOST_VERSION}.json")
        try:
            with open(path) as f:
                autoplay_weights = json.load(f)
            print(f"Driving the player's ghost with version '{PLAYER_GHOST_VERSION}' ({path})")
            if PLAYER_GHOST_MISTAKE_CHANCE > 0:
                print(f"Player ghost mistake chance: {PLAYER_GHOST_MISTAKE_CHANCE:.0%} (reduced-strength curriculum stage)")
        except (OSError, ValueError) as exc:
            print(f"Could not load player ghost version '{PLAYER_GHOST_VERSION}' ({exc}) - ghost will be passive.")
    else:
        print("No player ghost version selected - player's ghost will sit passive.")

    checkpoint_dir = checkpoints.save_checkpoint("pre-training")
    print(f"Checkpointed pacman_brain.json/player_ghost_weights.json to {checkpoint_dir}")

    g = gp.Game()
    g.ghost_autoplay_weights = autoplay_weights
    g.player_ghost_passive = autoplay_weights is None
    g.ghost_autoplay_mistake_chance = PLAYER_GHOST_MISTAKE_CHANCE
    if MAX_MINUTES:
        print(f"Time-boxed run: stopping after {MAX_MINUTES:.1f} minute(s) (or {NUM_GAMES} games, whichever first).")

    # 10ms = the GCD of every move/perception delay the game checks
    # (GHOST_MOVE_DELAY 180, PACMAN_MOVE_DELAY 190, AI_GHOST_MOVE_DELAY 260,
    # AI_GHOST_FRIGHTENED_MOVE_DELAY 390, GHOST_FRIGHTENED_MOVE_DELAY 270,
    # AI_GHOST_PERCEPTION_INTERVAL 500). Interactive play advances `now`
    # via pygame.time.get_ticks() at 60fps (~16.7ms resolution), so those
    # delays land on their exact threshold. Stepping by PACMAN_MOVE_DELAY
    # (190) instead doesn't evenly divide 260/390, so the AI ghosts'
    # "now - last_move < delay" check overshoots to the next multiple of
    # 190 - they end up moving roughly every 380ms instead of 260ms, ~46%
    # slower than real play (Pac-Man and the 180ms player ghost are close
    # enough to 190 that this barely shows). 10ms divides all of them
    # exactly, so every timer fires on its real threshold.
    step = 10
    wins = 0
    window_wins = 0
    start = time.time()
    game_i = 0
    for game_i in range(1, NUM_GAMES + 1):
        random.seed(g.brain.seed_for_next_game())
        g.reset_game()
        g.start_time = 0
        tick = 0
        while not g.game_over:
            tick += 1
            now = gp.sim_tick_time(tick)  # real 60fps frame times
            g.update(now)

        if g.outcome == "maze_cleared":
            wins += 1
            window_wins += 1

        elapsed = time.time() - start
        if game_i % PROGRESS_EVERY == 0:
            print(
                f"[{game_i}/{NUM_GAMES}] brain_games={g.brain.games_played} "
                f"phase={g.brain._phase} avg_reward={g.brain.average_reward:+.3f} "
                f"best_reward={g.brain.best_reward:+.3f} "
                f"win_rate(last {PROGRESS_EVERY})={window_wins / PROGRESS_EVERY:.0%} "
                f"overall_win_rate={wins / game_i:.0%} elapsed={elapsed:.1f}s",
                flush=True,
            )
            window_wins = 0

        if MAX_MINUTES and elapsed >= MAX_MINUTES * 60:
            print(f"\nTime budget reached after {game_i} games ({elapsed:.1f}s).")
            break

    played = game_i
    win_rate = wins / played
    print(f"\nDone: {played} games, overall win rate {win_rate:.1%}")
    print("Final weights:")
    print(json.dumps(g.brain.weights, indent=2))
    checkpoints.record_outcome(checkpoint_dir, games=played, win_rate=round(win_rate, 4))


if __name__ == "__main__":
    main()
