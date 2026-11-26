"""Build the table of a player-ghost model's moves that Pac-Man's route
planner plans against (see PURSUER_ROUTE_HOPS_FILE in Ghost_Pacman.py).

    python build_player_ghost_hops.py <version> [games]

Plays `games` (default 2000) headless games with player_ghost_versions/
<version>.json driving the player's ghost - Pac-Man not told its moves, as
in a real game - and writes player_ghost_versions/<version>_hops.json:
for each pair (ghost tile, Pac-Man tile) the step the model usually took,
separately for normal play and for when it was frightened. A pair is kept
when it was seen at least twice and one step was taken at least 60% of the
time.

Run it again after every refit of the model (fit_player_ghost_v2.py), for
full_power and for the two halves used in testing.
"""
import collections
import json
import multiprocessing as mp
import os
import random
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
_game = _weights = _pe = None
_moves = []


def _init(version):
    global _game, _weights, _pe
    os.environ["PACMAN_PLAYER_GHOST_VERSION"] = version
    os.environ["PACMAN_PLAYER_GHOST_HIDDEN"] = "1"
    os.environ["PACMAN_ASSUMED_PLAYER"] = version
    os.environ["PACMAN_ROUTE_HOPS"] = "none"  # no table while one is being made
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    try:
        import ctypes
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
    except (AttributeError, OSError):
        pass
    sys.path.insert(0, ROOT)
    stdout, sys.stdout = sys.stdout, open(os.devnull, "w")
    import Ghost_Pacman as gp
    import parallel_eval as pe
    sys.stdout = stdout
    gp.record_game_history = lambda *a, **k: None
    gp.save_replay = lambda *a, **k: None
    gp._next_run_id = lambda: 0
    original = gp.Game.move_ghost

    def move_ghost(self, now):
        before, out = self.ghost_position, self.ghost_has_left_house
        pacman, frightened = self.pacman_position, now < self.power_until
        result = original(self, now)
        if out and self.ghost_position != before and self.ghost_position not in gp.HOUSE_TILES:
            _moves.append((before, pacman, self.ghost_position, frightened))
        return result

    gp.Game.move_ghost = move_ghost
    _game = gp.Game()
    pe.configure_game(_game)
    _weights = pe.load_weights(os.path.join(ROOT, "pacman_brain.json"))
    _pe = pe


def _play(seed):
    game = _game
    game.brain = _pe._FixedBrain(_weights)
    random.seed(seed)
    del _moves[:]
    game.reset_game()
    game.start_time = 0
    now = 0
    while not game.game_over:
        now += 10
        game.update(now)
    return list(_moves)


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    version = sys.argv[1]
    games = int(sys.argv[2]) if len(sys.argv) > 2 else 2000
    if not os.path.isfile(os.path.join(ROOT, "player_ghost_versions", version + ".json")):
        raise SystemExit("no such version: " + version)
    rng = random.Random(20261120)
    seeds = [rng.randint(0, 2 ** 31 - 1) for _ in range(games)]
    counts = (collections.defaultdict(collections.Counter), collections.defaultdict(collections.Counter))
    started = time.time()
    total = 0
    with mp.Pool(max(1, min(12, (os.cpu_count() or 2) - 2)), initializer=_init, initargs=(version,)) as pool:
        for moves in pool.imap_unordered(_play, seeds, chunksize=4):
            for ghost, pacman, step, frightened in moves:
                counts[1 if frightened else 0][(ghost, pacman)][step] += 1
            total += len(moves)
    tables = []
    for counter in counts:
        table = {}
        for (ghost, pacman), steps in counter.items():
            step, times = steps.most_common(1)[0]
            seen = sum(steps.values())
            if seen >= 2 and times >= 0.6 * seen:
                table["%d,%d,%d,%d" % (ghost + pacman)] = list(step)
        tables.append(table)
    path = os.path.join(ROOT, "player_ghost_versions", version + "_hops.json")
    scratch = path + ".tmp"
    with open(scratch, "w") as f:
        json.dump({"version": version, "games": games, "moves": total, "normal": tables[0], "frightened": tables[1]}, f)
    os.replace(scratch, path)
    print("%s: %d games, %d moves -> %d normal pairs, %d frightened pairs (%.0f s)" % (
        path, games, total, len(tables[0]), len(tables[1]), time.time() - started))


if __name__ == "__main__":
    main()
