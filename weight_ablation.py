#!/usr/bin/env python3
"""Headless weight-effectiveness ablation harness.

For each of Pac-Man's 51 learned weights (LEARNED_WEIGHT_DEFAULTS in
Ghost_Pacman.py), plays PAIRS_PER_WEIGHT paired games comparing the
current brain's value for that weight against its "ablated" value (0.0,
or the weight's own lower bound for the handful of weights whose bounds
don't allow 0) - both members of each pair share the same RNG seed
(common random numbers), so only the one weight being tested can move
that pair's result.

This is read-only diagnostics: it never touches pacman_brain.json. Each
game is played with a disposable stand-in for PacmanBrain (FixedBrain
below) that holds a fixed weight dict and never saves to disk, so no
game played here feeds back into real training or perturbs the
coordinate-ascent trial currently in progress.

Answers "is this weight currently pulling its weight" directly, instead
of only inferring it from whether the coordinate-ascent trial mechanism
happened to nudge it away from its default - see project memory on why
that inference can be misleading (a weight can win its own trials while
the brain's overall win rate still regresses from unrelated drift).

Run with:
    python weight_ablation.py [pairs_per_weight] [output_json_path]

Writes incrementally to output_json_path (default weight_ablation_results.json
in this directory) after every weight, so a long run can be checked or
killed partway through without losing completed weights.
"""
import json
import os
import random
import sys
import time

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import Ghost_Pacman as gp

# Diagnostic games must not land in pacman_history.jsonl or the replay list.
gp.record_game_history = lambda *args, **kwargs: None
gp.save_replay = lambda *args, **kwargs: None

PAIRS_PER_WEIGHT = int(sys.argv[1]) if len(sys.argv) > 1 else 100
OUTPUT_PATH = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "weight_ablation_results.json"
)

STEP = 10  # see train_pacman.py's comment on why 10ms is the right tick size


class FixedBrain:
    """Plays exactly one fixed weight dict for one game, with a
    pre-chosen seed, and never writes anything to disk."""

    def __init__(self, weights, seed):
        self.weights = weights
        self._seed = seed
        self.games_played = 0
        self.average_reward = 0.0
        self.best_reward = 0.0

    def current_weights(self):
        return self.weights

    def seed_for_next_game(self):
        return self._seed

    def learn(self, reward):
        self.games_played += 1


def play_one(g, weights, seed):
    g.brain = FixedBrain(weights, seed)
    g.ghost_autoplay_weights = None
    g.ghost_autoplay_mistake_chance = 0.0
    random.seed(seed)
    g.reset_game()
    g.start_time = 0
    now = 0
    while not g.game_over:
        now += STEP
        g.update(now)
    meta = g.finished_game_meta
    return meta["won"], meta["reward"]


def load_baseline_weights():
    weights = dict(gp.LEARNED_WEIGHT_DEFAULTS)
    try:
        with open(gp.BRAIN_FILE) as f:
            data = json.load(f)
        for key in weights:
            if key in data.get("weights", {}):
                weights[key] = float(data["weights"][key])
    except (OSError, ValueError):
        pass
    return weights


def main():
    baseline_weights = load_baseline_weights()
    keys = list(baseline_weights.keys())
    print(f"Ablating {len(keys)} weights, {PAIRS_PER_WEIGHT} paired games each "
          f"({PAIRS_PER_WEIGHT * 2} games per weight, ~{len(keys) * PAIRS_PER_WEIGHT * 2} games total).")
    print(f"Baseline snapshot: pacman_brain.json as of {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Writing results to {OUTPUT_PATH}\n", flush=True)

    g = gp.Game()
    g.player_ghost_passive = True
    results = []
    overall_start = time.time()

    for idx, key in enumerate(keys, 1):
        base_val = baseline_weights[key]
        low, _high = gp.LEARNED_WEIGHT_BOUNDS[key]
        ablated_val = max(low, 0.0)

        if base_val == ablated_val:
            results.append({"weight": key, "baseline_value": base_val, "ablated_value": ablated_val, "skipped": True})
            print(f"[{idx}/{len(keys)}] {key}: already at its ablated value ({base_val:.3g}), skipping", flush=True)
            with open(OUTPUT_PATH, "w") as f:
                json.dump(results, f, indent=2)
            continue

        ablated_weights = dict(baseline_weights)
        ablated_weights[key] = ablated_val

        base_wins = 0
        abl_wins = 0
        base_rewards = []
        abl_rewards = []
        t0 = time.time()
        for _ in range(PAIRS_PER_WEIGHT):
            seed = random.randint(0, 2**31 - 1)
            won, reward = play_one(g, baseline_weights, seed)
            base_wins += int(won)
            base_rewards.append(reward)
            won2, reward2 = play_one(g, ablated_weights, seed)
            abl_wins += int(won2)
            abl_rewards.append(reward2)
        elapsed = time.time() - t0

        base_wr = base_wins / PAIRS_PER_WEIGHT
        abl_wr = abl_wins / PAIRS_PER_WEIGHT
        base_avg = sum(base_rewards) / len(base_rewards)
        abl_avg = sum(abl_rewards) / len(abl_rewards)
        row = {
            "weight": key,
            "baseline_value": base_val,
            "ablated_value": ablated_val,
            "pairs": PAIRS_PER_WEIGHT,
            "baseline_win_rate": base_wr,
            "ablated_win_rate": abl_wr,
            "win_rate_delta": base_wr - abl_wr,
            "baseline_avg_reward": base_avg,
            "ablated_avg_reward": abl_avg,
            "reward_delta": base_avg - abl_avg,
        }
        results.append(row)
        print(
            f"[{idx}/{len(keys)}] {key} (val={base_val:.3g} vs {ablated_val:.3g}): "
            f"win_rate {base_wr:.0%} vs {abl_wr:.0%} (delta {row['win_rate_delta']:+.0%}), "
            f"reward {base_avg:+.3f} vs {abl_avg:+.3f} (delta {row['reward_delta']:+.3f}) "
            f"[{elapsed:.0f}s]",
            flush=True,
        )
        with open(OUTPUT_PATH, "w") as f:
            json.dump(results, f, indent=2)

    print(f"\nDone in {time.time() - overall_start:.0f}s. Full results: {OUTPUT_PATH}")
    scored = [r for r in results if not r.get("skipped")]
    scored.sort(key=lambda r: r["win_rate_delta"], reverse=True)
    print("\nRanked by win-rate contribution (baseline minus ablated - positive means the weight is currently helping):")
    for r in scored:
        print(f"  {r['win_rate_delta']:+.1%}  {r['weight']} (value={r['baseline_value']:.3g})")
    skipped = [r["weight"] for r in results if r.get("skipped")]
    if skipped:
        print(f"\nSkipped (already at ablated value): {', '.join(skipped)}")


if __name__ == "__main__":
    main()
