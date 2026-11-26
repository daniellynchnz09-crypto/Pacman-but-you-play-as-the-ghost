#!/usr/bin/env python3
"""Parallel fixed-weight evaluation of Pac-Man brains.

Plays headless games across several CPU cores, each with an explicit
weight dict and RNG seed, and returns the results. Nothing here learns
or writes to disk: games use a disposable stand-in for PacmanBrain, and
history/replay recording is switched off inside the workers.

Compare two or more weight sets on the same seeds (common random
numbers) with `Evaluator.evaluate`, then judge the difference with
`paired_z`.

Run directly to compare the live brain against a checkpoint:
    python parallel_eval.py <checkpoint_dir> [pairs]
"""
import json
import math
import multiprocessing as mp
import os
import random
import sys

STEP = 10  # ms per tick - see train_pacman.py for why
DEFAULT_WORKERS = max(1, min(12, (os.cpu_count() or 2) - 2))

_game = None
_gp = None


class _FixedBrain:
    def __init__(self, weights):
        self.weights = weights
        self.games_played = 0
        self.average_reward = 0.0
        self.best_reward = 0.0

    def current_weights(self):
        return self.weights

    def seed_for_next_game(self):
        return 0

    def learn(self, reward):
        self.games_played += 1


def _init_worker():
    global _game, _gp
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    sys.stdout = open(os.devnull, "w")  # silence pygame's import banner
    import Ghost_Pacman as gp

    sys.stdout = sys.__stdout__
    gp.record_game_history = lambda *args, **kwargs: None
    gp.save_replay = lambda *args, **kwargs: None
    gp._next_run_id = lambda: 0  # evaluation workers are not training runs
    _gp = gp
    _game = gp.Game()
    configure_game(_game)


def configure_game(game):
    """Set a Game up the way evaluation games are played: player ghost
    passive, or driven by the version named in PACMAN_PLAYER_GHOST_VERSION.
    With PACMAN_PLAYER_GHOST_HIDDEN=1 Pac-Man is not told that version's
    moves (he plans against PACMAN_ASSUMED_PLAYER, as in a real game)."""
    version = os.environ.get("PACMAN_PLAYER_GHOST_VERSION", "")
    if version:
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "player_ghost_versions", version + ".json")) as f:
            game.ghost_autoplay_weights = json.load(f)
        game.player_ghost_passive = False
        game.ghost_autoplay_hidden = os.environ.get("PACMAN_PLAYER_GHOST_HIDDEN", "0") == "1"
    else:
        game.ghost_autoplay_weights = None
        game.ghost_autoplay_hidden = False
        game.player_ghost_passive = True
    game.ghost_autoplay_mistake_chance = float(os.environ.get("PACMAN_PLAYER_GHOST_MISTAKE_CHANCE", "0"))


def sim_tick_time(tick):
    return sys.modules["Ghost_Pacman"].sim_tick_time(tick)


def play_in_process(game, weights, seed):
    """Play one game in this process (same result a worker would get for
    the same weights and seed). Whatever recording the caller has left
    switched on in Ghost_Pacman - e.g. save_replay - happens as normal."""
    game.brain = _FixedBrain(weights)
    random.seed(seed)
    game.reset_game()
    game.start_time = 0
    tick = 0
    while not game.game_over:
        tick += 1
        now = sim_tick_time(tick)  # real 60fps frame times (see PACMAN_SIM_FRAMES)
        game.update(now)
    return game.finished_game_meta


def _play(task):
    index, weights, seed = task
    g = _game
    g.brain = _FixedBrain(weights)
    random.seed(seed)
    g.reset_game()
    g.start_time = 0
    tick = 0
    while not g.game_over:
        tick += 1
        now = sim_tick_time(tick)  # real 60fps frame times (see PACMAN_SIM_FRAMES)
        g.update(now)
    meta = g.finished_game_meta
    return index, seed, bool(meta["won"]), float(meta["reward"]), meta["outcome"], meta["elapsed_ms"], float(meta["pellet_fraction"]), g.ghosts_eaten, g.near_miss_count


class Evaluator:
    def __init__(self, workers=DEFAULT_WORKERS):
        self.workers = workers
        self.pool = mp.Pool(workers, initializer=_init_worker)

    def close(self):
        self.pool.close()
        self.pool.join()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.pool.terminate()
        self.pool.join()

    def evaluate(self, variants, seeds):
        """Play every weight dict in `variants` on every seed. Returns one
        list per variant, ordered like `seeds`, of result dicts with keys
        won, reward, outcome, elapsed_ms, pellet_fraction."""
        tasks = [(i, w, s) for s in seeds for i, w in enumerate(variants)]
        position = {s: n for n, s in enumerate(seeds)}
        out = [[None] * len(seeds) for _ in variants]
        chunk = max(1, len(tasks) // (self.workers * 8))
        for index, seed, won, reward, outcome, elapsed, pellets, eaten, near in self.pool.imap_unordered(_play, tasks, chunksize=chunk):
            out[index][position[seed]] = {
                "won": won, "reward": reward, "outcome": outcome,
                "elapsed_ms": elapsed, "pellet_fraction": pellets,
                "ghosts_eaten": eaten, "near_misses": near,
            }
        return out


def make_seeds(count, rng=random):
    seeds = set()
    while len(seeds) < count:
        seeds.add(rng.randint(0, 2**31 - 1))
    return list(seeds)


def win_rate(results):
    return sum(r["won"] for r in results) / len(results)


def mean_reward(results):
    return sum(r["reward"] for r in results) / len(results)


def paired_z(a, b):
    """Sign-test z score for 'a wins more than b' on shared seeds:
    (a-only wins - b-only wins) / sqrt(discordant pairs)."""
    a_only = sum(1 for x, y in zip(a, b) if x["won"] and not y["won"])
    b_only = sum(1 for x, y in zip(a, b) if y["won"] and not x["won"])
    n = a_only + b_only
    return ((a_only - b_only) / math.sqrt(n) if n else 0.0), a_only, b_only


def load_weights(path):
    import Ghost_Pacman as gp

    weights = dict(gp.LEARNED_WEIGHT_DEFAULTS)
    with open(path) as f:
        saved = json.load(f).get("weights", {})
    for key in weights:
        if key in saved:
            weights[key] = float(saved[key])
    return weights


if __name__ == "__main__":
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    here = os.path.dirname(os.path.abspath(__file__))
    other_dir = sys.argv[1]
    pairs = int(sys.argv[2]) if len(sys.argv) > 2 else 500
    live = load_weights(os.path.join(here, "pacman_brain.json"))
    other = load_weights(os.path.join(other_dir, "pacman_brain.json"))
    with Evaluator() as ev:
        a, b = ev.evaluate([live, other], make_seeds(pairs))
    z, a_only, b_only = paired_z(a, b)
    print(f"live brain:  {win_rate(a):.1%} (avg reward {mean_reward(a):+.3f})")
    print(f"checkpoint:  {win_rate(b):.1%} (avg reward {mean_reward(b):+.3f})")
    print(f"live-only wins {a_only}, checkpoint-only wins {b_only}, z={z:+.2f} (positive favours live)")
