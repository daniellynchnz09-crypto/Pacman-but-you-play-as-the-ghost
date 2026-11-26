#!/usr/bin/env python3
"""Builds the named player-ghost AI version snapshots in
player_ghost_versions/ - see PLAYER_GHOST_VERSION in train_pacman.py for
how a training run picks one by name. Each version is a complete weight
dict for player_ghost_features/choose_player_ghost_direction:

- claude_plus ("Claude+"): a bare, almost featureless direct chaser -
  chases Pac-Man's real maze distance and nothing else, no ambush, no
  exit-coverage, no power-pellet guarding. Meant to feel like "just
  another ghost" (comparable to Blinky's own directness), so training
  can get Pac-Man used to a 5th pursuer existing at all before it has to
  deal with anything smarter than that.
- blinky_plus ("Blinky+"): claude_plus plus a small amount of
  exit-awareness - "a teeny bit smarter", a mild step up in challenge
  that still shouldn't wreck Pac-Man's progress.
- bad_goose ("Bad Goose"): refit from only the earliest slice of the
  moves recorded so far in player_ghost_moves.jsonl (see
  BAD_GOOSE_MOVE_CUTOFF below) - an approximation of how underfit and
  noisy the very first real imitation fit was, back when there was
  barely any data. The actual historical first fit was never saved under
  its own name (fit_player_ghost.py always overwrites the same file), so
  this reconstructs it from the same early slice of the log instead of
  claiming to be a byte-for-byte original.
- goose ("Goose"): a snapshot of the current player_ghost_weights.json
  as it stands right now.

"full_power" ("Full power") is deliberately NOT generated here - it is
written by fit_player_ghost_v2.py (--save full_power) from the games
recorded against the maze-clear-rules Pac-Man. Re-run this script any time to refresh goose/bad_goose
from the latest player_ghost_weights.json/player_ghost_moves.jsonl;
claude_plus/blinky_plus are fixed by hand and don't change run to run.

Run with:
    python build_player_ghost_versions.py
"""
import json
import os

import fit_player_ghost as fpg
import Ghost_Pacman as gp

VERSIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "player_ghost_versions")
BAD_GOOSE_MOVE_CUTOFF = 1200  # ~first 11% of the moves recorded so far - see module docstring


def build_claude_plus():
    weights = {name: 0.0 for name in gp.PLAYER_GHOST_FEATURE_NAMES}
    weights["dist_pacman"] = -1.0
    return weights


def build_blinky_plus():
    weights = build_claude_plus()
    weights["dist_pacman_exit"] = -0.3
    return weights


def build_bad_goose():
    moves = fpg.load_moves()[:BAD_GOOSE_MOVE_CUTOFF]
    examples = fpg.build_examples(moves)
    print(f"bad_goose: fitting on the first {len(moves)} recorded moves ({len(examples)} usable)")
    weights = fpg.fit(examples, gp.PLAYER_GHOST_FEATURE_NAMES)
    acc = fpg.accuracy(examples, weights, gp.PLAYER_GHOST_FEATURE_NAMES)
    if acc is not None:
        print(f"bad_goose: train accuracy {acc * 100:.1f}% on {len(examples)} examples "
              f"(no held-out split - this is deliberately a weak, early snapshot, not a polished fit)")
    return weights


def build_goose():
    with open(gp.PLAYER_GHOST_WEIGHTS_FILE) as f:
        return json.load(f)


def main():
    os.makedirs(VERSIONS_DIR, exist_ok=True)
    versions = {
        "claude_plus": build_claude_plus(),
        "blinky_plus": build_blinky_plus(),
        "bad_goose": build_bad_goose(),
        "goose": build_goose(),
    }
    for name, weights in versions.items():
        path = os.path.join(VERSIONS_DIR, f"{name}.json")
        with open(path, "w") as f:
            json.dump(weights, f, indent=2)
        print(f"saved {path}")


if __name__ == "__main__":
    main()
