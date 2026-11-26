#!/usr/bin/env python3
"""Checkpoint/restore helper for the two learned-weight files this project
trains - pacman_brain.json (PacmanBrain's coordinate-ascent weights) and
player_ghost_weights.json (the player ghost's fitted imitation model).

Run directly:
    python checkpoints.py save <label>
    python checkpoints.py list
    python checkpoints.py restore <checkpoint_dir>

Also imported by train_pacman.py to auto-snapshot both files right before
a training run starts, and to record that run's measured win rate against
the snapshot once it finishes - so a regression can always be traced back
to a known-good prior state instead of being permanently overwritten with
no way back (two training runs in a row regressed pacman_brain.json with
no backup to restore from before this existed).
"""
import json
import os
import shutil
import time

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
CHECKPOINT_DIR = os.path.join(PROJECT_DIR, "checkpoints")
LOG_FILE = os.path.join(CHECKPOINT_DIR, "checkpoint_log.jsonl")

TRACKED_FILES = {
    "pacman_brain": os.path.join(PROJECT_DIR, "pacman_brain.json"),
    "player_ghost_weights": os.path.join(PROJECT_DIR, "player_ghost_weights.json"),
}


def save_checkpoint(label, extra=None):
    """Snapshot both tracked files into a new timestamped checkpoint
    directory, log it, and return the directory path. Skips any tracked
    file that doesn't currently exist."""
    os.makedirs(CHECKPOINT_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest_dir = os.path.join(CHECKPOINT_DIR, f"{stamp}_{label}")
    os.makedirs(dest_dir, exist_ok=True)
    saved = []
    for name, path in TRACKED_FILES.items():
        if os.path.exists(path):
            shutil.copy2(path, os.path.join(dest_dir, os.path.basename(path)))
            saved.append(name)
    entry = {"timestamp": stamp, "label": label, "dir": dest_dir, "files": saved}
    if extra:
        entry.update(extra)
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")
    return dest_dir


def record_outcome(dest_dir, **metrics):
    """Append a follow-up log line noting what happened after training
    started from the given checkpoint (e.g. games played, win rate), so
    each checkpoint's log entry can be matched against its result."""
    entry = {"timestamp": time.strftime("%Y%m%d-%H%M%S"), "outcome_for": dest_dir}
    entry.update(metrics)
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")


def list_checkpoints():
    if not os.path.exists(LOG_FILE):
        return []
    with open(LOG_FILE) as f:
        return [json.loads(line) for line in f if line.strip()]


def restore_checkpoint(dest_dir):
    """Copy a checkpoint's files back over the live tracked files."""
    restored = []
    for name, path in TRACKED_FILES.items():
        src = os.path.join(dest_dir, os.path.basename(path))
        if os.path.exists(src):
            shutil.copy2(src, path)
            restored.append(name)
    return restored


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(1)
    cmd = sys.argv[1]
    if cmd == "save":
        label = sys.argv[2] if len(sys.argv) > 2 else "manual"
        d = save_checkpoint(label)
        print(f"Saved checkpoint: {d}")
    elif cmd == "list":
        for entry in list_checkpoints():
            print(entry)
    elif cmd == "restore":
        if len(sys.argv) < 3:
            print("usage: python checkpoints.py restore <checkpoint_dir>")
            raise SystemExit(1)
        restored = restore_checkpoint(sys.argv[2])
        print(f"Restored {restored} from {sys.argv[2]}")
    else:
        print(__doc__)
        raise SystemExit(1)
