#!/usr/bin/env python3
"""Ghost Team vs Pac-Man: control one ghost with four AI ghost allies.

Controls:
  Arrow keys / WASD : move the ghost
  Enter             : start
  R                 : restart
  Esc / Q           : quit

Requires: pygame (python -m pip install pygame)
"""

import array
import json
import math
import os
import random
import sys
import time
from collections import deque

import pygame


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
CELL_SIZE = 26
RAW_MAZE = ['############################', '#............##............#', '#.####.#####.##.#####.####.#', '#@#  #.#   #.##.#   #.#  #@#', '#.####.#####.##.#####.####.#', '#..........................#', '#.####.##.########.##.####.#', '#.####.##.########.##.####.#', '#......##....##....##......#', '######.##### ## #####.######', '     #.##### ## #####.#     ', '     #.##          ##.#     ', '     #.## ###--### ##.#     ', '######.## #------# ##.######', '      .   #------#   .      ', '######.## #------# ##.######', '     #.## ######## ##.#     ', '     #.##          ##.#     ', '     #.## ######## ##.#     ', '######.## ######## ##.######', '#............##............#', '#.####.#####.##.#####.####.#', '#.####.#####.##.#####.####.#', '#@..##.......  .......##..@#', '###.##.##.########.##.##.###', '###.##.##.########.##.##.###', '#......##....##....##......#', '#.##########.##.##########.#', '#.##########.##.##########.#', '#..........................#', '############################']

GRID_W = max(len(row) for row in RAW_MAZE)
GRID_H = len(RAW_MAZE)
MAZE = tuple(row.ljust(GRID_W, "#") for row in RAW_MAZE)

# The walled ghost-house cage plus its gate. Pac-Man can never set foot in
# any of these tiles, and a ghost may not walk back into them once it has
# left for the first time. This closes an exploit where Pac-Man could camp
# in the house and repeatedly eat ghosts the instant they respawned there.
HOUSE_TILES = frozenset(
    (x, y)
    for y in range(12, 16)
    for x in range(11, 17)
    if MAZE[y][x] != "#"
)

HUD_HEIGHT = 54
SCREEN_WIDTH = GRID_W * CELL_SIZE
SCREEN_HEIGHT = GRID_H * CELL_SIZE + HUD_HEIGHT
FPS = 60

GHOST_MOVE_DELAY = 180
GHOST_FRIGHTENED_MOVE_DELAY = 270
PACMAN_MOVE_DELAY = 190
# The game only moves anything on a frame (FPS = 60, one every ~16.7ms),
# so "a move every 190ms" really waits for the first frame at or after
# 190ms: 12 frames = 200ms. Likewise the player ghost's 180ms is 11
# frames = 183ms (the player's logged moves are 181-184ms apart), and
# the classic ghosts' 260ms is 266.7ms. Pac-Man used to plan with the
# design numbers, so it believed it was relatively faster against the
# player ghost than it is (190/180 = 5.6% slower, really 200/183 = 9.1%)
# - about 10ms too optimistic per tile, every tile, always in its own
# favour - and took races to a junction that it then lost by a few ms.
# The game's own move timers keep the design numbers (the *_GATE names,
# so the game plays exactly as before); every *_DELAY below is what a
# move really takes, which is what all of Pac-Man's planning uses.
# PACMAN_FRAME_TIMING=0 puts back the old planning numbers.
# PACMAN_SIM_FRAMES (default on) makes the headless simulations tick
# frame by frame like the real game instead of every 10ms - see
# sim_tick_time - so that what they measure matches real play.
PACMAN_FRAME_TIMING = os.environ.get("PACMAN_FRAME_TIMING", "1") != "0"
PACMAN_SIM_FRAMES = os.environ.get("PACMAN_SIM_FRAMES", "1") != "0"
FRAME_MS = 1000.0 / FPS


# Measured (400 paired games against the stand-in, 2026-11-23): planning
# with Pac-Man's own true 200ms everywhere made him far worse (-13 pts) -
# his weights and longer-range estimates were all tuned with the old
# numbers - while the player ghost's true speed helped (+3.5/+4.3). So by
# default only the player ghost's times are rounded here, and everyone's
# true times are used inside the exact look-ahead (PACMAN_NEAR_REAL_MS,
# default the whole of it: +17/+20 pts over the old planning in the two
# directions, against +12/+15 for its first 3 moves only). The damage was
# in the long-range estimates (the evasion table, route planner), not in
# the move-by-move simulation of the next few seconds.
_FRAME_TIMING_ONLY = set(filter(None, os.environ.get("PACMAN_FRAME_TIMING_ONLY", "ghost,ghost_fright").split(",")))


def frame_rounded_delay(ms, part=None):
    """How long a move gated at `ms` really takes at FPS frames a second.
    PACMAN_FRAME_TIMING_ONLY (diagnostic: a comma list of pacman, ghost,
    ghost_fright, ai, ai_fright) rounds only those."""
    if not PACMAN_FRAME_TIMING or (_FRAME_TIMING_ONLY and part not in _FRAME_TIMING_ONLY):
        return ms
    return math.ceil(ms / FRAME_MS - 1e-6) * FRAME_MS


def sim_tick_time(tick):
    """Clock reading (ms) at the `tick`-th update of a headless game: the
    frame times of the real 60fps loop, or the old 10ms steps."""
    if PACMAN_SIM_FRAMES:
        return tick * 1000 // FPS
    return tick * 10


GHOST_MOVE_GATE = GHOST_MOVE_DELAY
GHOST_FRIGHTENED_MOVE_GATE = GHOST_FRIGHTENED_MOVE_DELAY
PACMAN_MOVE_GATE = PACMAN_MOVE_DELAY
GHOST_MOVE_DELAY = frame_rounded_delay(GHOST_MOVE_GATE, "ghost")
GHOST_FRIGHTENED_MOVE_DELAY = frame_rounded_delay(GHOST_FRIGHTENED_MOVE_GATE, "ghost_fright")
PACMAN_MOVE_DELAY = frame_rounded_delay(PACMAN_MOVE_GATE, "pacman")


def _true_move_ms(ms):
    return math.ceil(ms / FRAME_MS - 1e-6) * FRAME_MS


# What every move really takes, whatever the planning numbers above say.
# For the first PACMAN_NEAR_REAL_MS of the exact look-ahead (default: all
# of it - where a race to a junction is won or lost by a few ms) these are
# used instead of the planning numbers.
GHOST_MOVE_TRUE = _true_move_ms(GHOST_MOVE_GATE)
GHOST_FRIGHTENED_MOVE_TRUE = _true_move_ms(GHOST_FRIGHTENED_MOVE_GATE)
PACMAN_MOVE_TRUE = _true_move_ms(PACMAN_MOVE_GATE)
PACMAN_NEAR_REAL_MS = float(os.environ.get("PACMAN_NEAR_REAL_MS", "inf"))
# The evasion table (how long Pac-Man stays ahead of a perfect chaser) built
# with the true move times instead of the planning numbers.
PACMAN_EVASION_TRUE = os.environ.get("PACMAN_EVASION_TRUE", "0") != "0"
POWER_DURATION = 5_000
# Pac-Man wins only by eating every pellet; the ghost team wins by
# catching him. There is no score and no clock to run down. STALEMATE_LIMIT
# is only a backstop so that a game in which neither ever happens still
# ends - it counts as a ghost win, since Pac-Man did not clear the maze.
STALEMATE_LIMIT = int(os.environ.get("PACMAN_STALEMATE_LIMIT_MS", "300000"))
GHOST_HEAD_START = 3_000

PACMAN_DEADEND_RADIUS = 6
GHOST_CLUSTER_RADIUS = 10  # mean maze distance between ghosts below which they count as "clustered"

# How many of Pac-Man's most recent real moves loop_escape_weight looks
# back over (see Game.pacman_recent_positions/choose_pacman_step) to spot
# oscillating back and forth within a goose-guarded pocket instead of
# actually escaping it. At PACMAN_MOVE_DELAY (190ms/move) this is a
# little under 4 seconds of history - long enough to catch a real stall,
# short enough that ordinary backtracking during normal pellet-hunting
# (which revisits a tile here and there anyway) doesn't get mistaken for
# one.
PACMAN_RECENT_POSITIONS_WINDOW = 20

# Wider than PACMAN_DEADEND_RADIUS on purpose - trap_crowd_penalty (see
# choose_pacman_step) is meant to fire *before* any single ghost is
# actually close, as a preventive "don't even walk in here" signal
# scaled by how many ghosts are converging on a tight pocket at once,
# not a reactive one like deadend_penalty/semi_deadend_penalty (which
# only look at the single nearest ghost, and only once it's already
# within PACMAN_DEADEND_RADIUS).
POCKET_CROWD_RADIUS = 12

# Manhattan distance beyond which an AI ghost counts as "unsupported"
# for weak_link_weight (see choose_pacman_step) - too far from its
# nearest ally to have it immediately back up a pincer right now.
WEAK_LINK_GAP_THRESHOLD = 10

# inky_decoy_weight (see choose_pacman_step) only matters while Blinky
# is actually close enough that his position is the thing driving
# Inky's reflected target tile right now - INKY_DECOY_BLINKY_RADIUS is
# that cutoff, and INKY_DECOY_DISTANCE_CAP keeps the resulting bonus
# from blowing up if the reflection happens to land somewhere absurdly
# far away.
INKY_DECOY_BLINKY_RADIUS = 10
INKY_DECOY_DISTANCE_CAP = 15

# How close the player ghost has to be to a power pellet to count as
# actively guarding it, for pellet_contest_weight (see choose_pacman_step).
PELLET_GUARD_RADIUS = 7

# Manhattan distance counted as a dangerous close call - only a statistic
# now (Game.near_miss_count, written to the history file); it no longer
# feeds the reward, which is purely about clearing the maze (see
# Game.finish_game).
NEAR_MISS_RADIUS = 2

# These four evasion/strategy weights are what Pac-Man's brain (see
# PacmanBrain below) learns across games. The values here are only the
# starting point for a brand-new brain file.
# danger_weight used to be a single number shared by every ghost. Split per
# AI personality (Blinky's direct chase is a different threat than Clyde's
# wandering) so Pac-Man can actually learn to fear/ignore them differently,
# instead of retraining one number that's already converged and plateaued
# regardless of how much room it's given to explore. The four AI weights
# start from the old shared weight's converged value (1.77) rather than a
# naive default, so this is a refinement of what was already learned, not
# a reset. The player-controlled ghost gets its own weight too - danger_
# weight_player - with a bounds floor above every AI weight's ceiling, so
# it's always classed as the single most dangerous ghost on the board,
# however training nudges the rest.
LEARNED_WEIGHT_DEFAULTS = {
    "safe_radius": 9.0,
    "danger_weight_player": 10.0,
    "danger_weight_blinky": 1.77,
    "danger_weight_pinky": 1.77,
    "danger_weight_inky": 1.77,
    "danger_weight_clyde": 1.77,
    "deadend_penalty": 450.0,
    "cluster_bonus": 15.0,
    # 24 more weights (32 total), added to test whether the plateau against
    # the imitation-learned player ghost is a capacity problem - these 8
    # original weights may simply not be expressive enough to represent a
    # winning counter-strategy, however long training runs against them.
    # Each one is a genuinely new signal choose_pacman_step didn't have
    # access to before, not a cosmetic split of an existing one. Grouped by
    # what they're meant to help with:
    #
    # Longer-range, linear "I see you coming" awareness per ghost, below
    # the quadratic near-field danger_weight_* terms kick in.
    "danger_far_weight_player": 0.2,
    "danger_far_weight_blinky": 0.15,
    "danger_far_weight_pinky": 0.15,
    "danger_far_weight_inky": 0.15,
    "danger_far_weight_clyde": 0.15,
    # Reacts to a ghost's distance actively shrinking tick over tick, not
    # just its raw current distance - the "it's closing in" signal that
    # pure distance alone can't express.
    "closing_threat_weight": 2.0,
    # Avoids each AI ghost's own predicted personality target tile (Pinky's
    # ambush point, Inky's reflected pincer point, etc.), not just their
    # current positions - a step toward actually out-thinking them.
    "predicted_target_avoidance_weight": 0.3,
    # Penalizes moves that increase how surrounded Pac-Man is specifically
    # (as opposed to cluster_bonus, which only cares if ghosts are bunched
    # up together anywhere) - the direct counter to a coordinated
    # corner-trap/goose-guard pincer.
    "pincer_penalty_weight": 5.0,
    # General mobility: more open neighboring tiles at the candidate step
    # is safer, as a graded signal alongside the binary deadend check.
    "mobility_bonus_weight": 1.0,
    "semi_deadend_penalty": 80.0,  # softer version of deadend_penalty for 2-exit (not just 1-exit) tiles
    "junction_proximity_weight": 0.5,  # pulls toward tiles with 3+ exits - maximum future escape options
    "tunnel_escape_bonus": 10.0,  # using the tunnel row to break a chase
    "reversal_penalty": 3.0,  # discourages doubling back without real cause
    "pellet_density_bonus": 0.5,  # prefers routes through pellet-dense areas
    "second_target_distance_weight": 0.3,  # considers the 2nd-closest target too, not just the nearest
    "power_pellet_proactive_weight": 0.2,  # pulled toward power pellets even without an immediate threat
    "endgame_rush_weight": 2.0,  # extra urgency once only a handful of pellets remain
    # The frightened-ghosts branch used to be an unlearned, hardcoded
    # formula - these three finally make it part of the same learning loop.
    "frightened_hunt_weight": 0.5,
    "frightened_pellet_weight": 1.0,
    "frightened_safety_weight": 0.1,
    "frightened_bait_pellet_weight": 0.8,  # extra pull toward a pellet specifically because a ghost is loitering near it
    "clyde_bait_weight": 1.0,  # rewards staying just outside Clyde's scatter-trigger radius to draw him off
    "target_distance_weight": 1.0,  # the main "go toward pellets" pull, now tunable instead of a fixed -1
    "occupied_tile_penalty_weight": 10_000.0,  # "never step on a ghost", now tunable instead of a fixed -10000
    "total_threat_count_weight": 3.0,  # penalizes raw number of ghosts in range, not just the nearest one
    # Recursive multi-tile lookahead (see _pacman_lookahead): every other
    # weight above only looks at the *immediate* candidate tile's distance
    # to danger/pellets. These score each candidate by simulating several
    # tiles further ahead - how long that branch keeps surviving, and how
    # many dots it picks up along the way, feeding into the same weighted
    # score as every single-step term above.
    "lookahead_survival_weight": 20.0,
    "lookahead_pellet_weight": 1.5,
    # How many ghosts are near a power pellet a candidate path would reach
    # (see _ambush_gain) - pulls Pac-Man out of his way to grab a power
    # pellet specifically when it sets up a multi-ghost ambush, scaling
    # with ghost count rather than just whether one happens to be nearby.
    "lookahead_ambush_weight": 3.0,
    # Countermeasures against being goose-guarded (a ghost holding a
    # choke point/corner while its allies close in) - added ahead of
    # bringing the player-controlled ghost (the one that actually plays
    # this way - see choose_player_ghost_direction) back into training.
    # Both are genuinely candidate-step-dependent (see PACMAN_RECENT_
    # POSITIONS_WINDOW / the mobility+danger condition below), not flat
    # bonuses - that's the exact bug class (see endgame_rush_weight's
    # history) that would make either of these inert no matter what
    # value training fits for it.
    #
    # Penalizes stepping back onto a tile visited recently (see
    # Game.pacman_recent_positions) - without this, a cornered Pac-Man
    # has no explicit pressure against oscillating between the same 2-3
    # "locally safest" tiles turn after turn while the net quietly
    # closes, since nothing else in this scoring function tracks history
    # at all.
    "loop_escape_weight": 2.0,
    # A stronger, situational pull toward the nearest power pellet
    # specifically when actually cornered (tight on exits AND a ghost
    # already close) - distinct from power_pellet_proactive_weight's
    # constant, always-on pull, which doesn't scale with how trapped
    # Pac-Man currently is. This is the "use the power pellet as a bail-
    # out" skill, not the "time it for a multi-kill" skill.
    "corner_bailout_weight": 0.5,
    # Preventive counterpart to deadend_penalty/semi_deadend_penalty:
    # those only react once a single ghost is already close to a tight
    # tile; this discourages walking into one at all once multiple
    # ghosts are converging on it from further out (see
    # POCKET_CROWD_RADIUS) - avoiding the trap in the first place, not
    # just penalizing it once already deep inside one.
    "trap_crowd_penalty": 10.0,
    # A bonus for continuing the current direction of travel, scaled by
    # how many ghosts are currently in near-field range (threats_in_range)
    # - the user's own first-hand read of playing the goose-guarding
    # ghost: there's often a real gap Pac-Man could slip through if it
    # just commits to a direction and sticks with it, but the existing
    # per-tick scoring has nothing that actively favors "keep going the
    # way I was already going" over "flip to whichever option looks
    # marginally better this exact instant" - reversal_penalty only
    # catches the literal 180-degree case, not general indecisive
    # wavering between two close-scoring options at a junction.
    "commitment_weight": 1.5,
    # Further "smarter against both ghost types" skills, brainstormed
    # and then all implemented together at the user's request. Each is
    # a genuinely new, step-dependent signal - none of these duplicate
    # what an existing weight already covers (see each one's own note
    # below and at its scoring-loop use for why).
    #
    # Rewards whichever candidate step has the best lead to the next
    # junction/escape point over the nearest ghost's straight-line
    # distance to that same point - lets commitment_weight actually
    # commit to the *right* direction instead of just whichever one
    # Pac-Man already happened to be facing.
    "exit_race_weight": 0.5,
    # Reduces one AI ghost's effective danger weight for this decision
    # only - whichever one is currently farthest from its nearest ally
    # (see WEAK_LINK_GAP_THRESHOLD) and so can't immediately get backed
    # up into a pincer. Exploits the same thing the user described from
    # playing the goose-guarding ghost themselves: Clyde/Inky are the
    # ones you can't always trust to back you up.
    "weak_link_weight": 0.3,
    # A stronger, situational pull toward the nearest power pellet
    # specifically as a scatter window is about to end (see
    # time_until_chase_resumes) - timed so the frightened effect covers
    # the chase window that's about to resume, instead of being wasted
    # mid-scatter.
    "scatter_timing_weight": 0.4,
    # While frightened, rewards keeping a *second* kill chainable (the
    # two nearest ghosts' combined distance), not just beelining for
    # whichever single ghost is closest - frightened_hunt_weight only
    # ever looks at the one nearest ghost.
    "frightened_sequence_weight": 0.3,
    # Rewards distance from Inky's predicted (reflected-through-Blinky)
    # target specifically while Blinky is close enough that his position
    # is actually the thing driving that reflection right now - smooth
    # and Inky-specific, unlike predicted_target_avoidance_weight's
    # thresholded, all-three-ghosts-lumped-together avoidance.
    "inky_decoy_weight": 0.2,
    # Rewards a direction change timed for right after the AI ghosts'
    # last perception refresh (see AI_GHOST_PERCEPTION_INTERVAL) rather
    # than right before their next one - the ghosts won't notice the
    # turn for longer, maximizing the "disappeared" window instead of
    # revealing the new direction right as they're about to re-sight.
    "perception_window_weight": 0.3,
    # The one-tick-ahead version of pincer_penalty_weight: simulates
    # every AI ghost's next move (the same generic model the recursive
    # lookahead uses) and penalizes a pincer that's about to assemble,
    # not just one that's already formed.
    "future_pincer_weight": 2.0,
    # Softens (or cancels, at a high enough value) the pull toward the
    # nearest power pellet specifically when the player ghost is
    # actually positioned close enough to be guarding it (see
    # PELLET_GUARD_RADIUS), instead of detouring into a pellet that's
    # actively contested.
    "pellet_contest_weight": 0.2,
    # Discounts a ghost's effective danger weight for this decision only
    # while it's still caged/walking out of the house (either the very
    # first release or just respawning after eat_ghost) - it physically
    # can't chase yet, so treating its raw maze-distance like a real
    # threat (which can happen if Pac-Man is near the house exit) is
    # needlessly cautious. eat_ghost doesn't actually reset a fresh
    # release-time countdown (checked before adding this), so this keys
    # off ai_ghost_has_left_house/ghost_has_left_house directly rather
    # than a timer.
    "respawn_discount_weight": 2.0,
    # A pair of time-of-game weights, added after loss-pattern analysis
    # of 20,000 real games found the endgame danger isn't really about
    # pellet count (endgame_rush_weight's own signal) - it's about the
    # game clock crossing PERMANENT_CHASE_START, after which no more
    # scatter relief is ever coming for the rest of the match. Neither
    # of these duplicates the scattering flag, which is only "is a
    # scatter window active this exact instant" - these are about the
    # longer-horizon question of whether any more relief is coming at
    # all before the match ends.
    #
    # Raises every ghost's effective danger weight once the schedule
    # has permanently run out - deliberately uniform across all 5
    # ghosts (added to per_ghost_danger_weight before the per-step loop,
    # the same pattern closing_threat_weight already uses), not step-
    # constant the way endgame_rush_weight used to be before its own
    # fix: it's multiplied by each step's own ghost distance inside
    # danger_score, so it still shifts which candidate wins, not just
    # the overall score level.
    "permanent_chase_caution_weight": 1.0,
    # The flip side: extra pellet-seeking urgency specifically *before*
    # the schedule runs out, front-loading pellet collection during the
    # part of the match that's structurally safer, instead of treating
    # every moment of the game as equally worth the same risk.
    "early_rush_weight": 0.4,
    # Against a player ghost that outruns Pac-Man, power pellets are the
    # only way to reset the chase (eating the ghost sends it home), so they
    # must be saved, reached in time, and followed up on:
    #  - power_pellet_save_weight: penalty for stepping onto one while the
    #    player ghost is still more than PURSUER_SAVE_GAP tiles away.
    #  - pursuer_refuge_weight: pull toward the nearest power pellet Pac-Man
    #    can reach before the player ghost, once the gap gets short.
    #  - pursuer_hunt_weight: while powered up, pull toward the player
    #    ghost itself if it can be reached before the power runs out.
    # None of these do anything while the player ghost is passive.
    "power_pellet_save_weight": 150.0,
    "pursuer_refuge_weight": 5.0,
    "pursuer_hunt_weight": 6.0,
    # Pull away from the player ghost at any range up to PURSUER_KITE_RANGE
    # (maze distance) - against a faster chaser the gap is Pac-Man's only
    # clock, and walking back toward it for pellets burns it.
    "pursuer_kite_weight": 4.0,
    # Pull toward the move that keeps Pac-Man ahead of a perfect chaser for
    # longest (see _evasion_table), capped at PURSUER_EVASION_CAP moves.
    "pursuer_evasion_weight": 2.0,
    # How strongly Pac-Man follows the pellet route worked out by
    # _route_values (PURSUER_PLAN "route"), per pellet of difference between
    # the best route starting with one move and the best starting with another.
    "pursuer_route_weight": 10.0,
}
LEARNED_WEIGHT_BOUNDS = {
    "safe_radius": (3.0, 20.0),
    # Raised from 15.0: against the imitation-learned player ghost (which
    # goose-guards Pac-Man into a corner far more effectively than the
    # classic AI ghosts chase alone), a 5000-game run pushed this weight
    # to 12.7/15 - 85% of its old range - in one run, with no sign of
    # slowing down. More headroom lets training keep pushing Pac-Man's
    # fear of this specific threat further before hitting a ceiling that
    # was tuned for an easier opponent.
    "danger_weight_player": (8.0, 24.0),
    "danger_weight_blinky": (0.3, 7.0),
    "danger_weight_pinky": (0.3, 7.0),
    "danger_weight_inky": (0.3, 7.0),
    "danger_weight_clyde": (0.3, 7.0),
    "deadend_penalty": (50.0, 1500.0),
    "cluster_bonus": (0.0, 70.0),
    "danger_far_weight_player": (0.0, 1.5),
    "danger_far_weight_blinky": (0.0, 1.0),
    "danger_far_weight_pinky": (0.0, 1.0),
    "danger_far_weight_inky": (0.0, 1.0),
    "danger_far_weight_clyde": (0.0, 1.0),
    "closing_threat_weight": (0.0, 10.0),
    "predicted_target_avoidance_weight": (0.0, 2.0),
    "pincer_penalty_weight": (0.0, 30.0),
    "mobility_bonus_weight": (0.0, 8.0),
    "semi_deadend_penalty": (0.0, 400.0),
    "junction_proximity_weight": (0.0, 3.0),
    "tunnel_escape_bonus": (0.0, 50.0),
    "reversal_penalty": (0.0, 20.0),
    "pellet_density_bonus": (0.0, 3.0),
    "second_target_distance_weight": (0.0, 1.0),
    "power_pellet_proactive_weight": (0.0, 1.5),
    "endgame_rush_weight": (0.0, 10.0),
    "frightened_hunt_weight": (0.0, 2.0),
    "frightened_pellet_weight": (0.2, 3.0),
    "frightened_safety_weight": (0.0, 1.0),
    "frightened_bait_pellet_weight": (0.0, 3.0),
    "clyde_bait_weight": (0.0, 8.0),
    "target_distance_weight": (0.3, 3.0),
    # Lower bound dropped from 2000.0: three separate runs in a row drove
    # this weight straight to that floor and sat there, the same signature
    # danger_weight_player showed before its own ceiling got raised (see
    # above) - a weight pinned against a bound across multiple independent
    # runs means the bound is cutting off real headroom, not that the
    # bound happens to be optimal.
    "occupied_tile_penalty_weight": (0.0, 20_000.0),
    "total_threat_count_weight": (0.0, 15.0),
    "lookahead_survival_weight": (0.0, 150.0),
    "lookahead_pellet_weight": (0.0, 10.0),
    "lookahead_ambush_weight": (0.0, 20.0),
    "loop_escape_weight": (0.0, 15.0),
    "corner_bailout_weight": (0.0, 3.0),
    "trap_crowd_penalty": (0.0, 60.0),
    "commitment_weight": (0.0, 15.0),
    "exit_race_weight": (0.0, 4.0),
    "weak_link_weight": (0.0, 3.0),
    "scatter_timing_weight": (0.0, 3.0),
    "frightened_sequence_weight": (0.0, 2.0),
    "inky_decoy_weight": (0.0, 2.0),
    "perception_window_weight": (0.0, 3.0),
    "future_pincer_weight": (0.0, 15.0),
    "pellet_contest_weight": (0.0, 1.5),
    "respawn_discount_weight": (0.0, 10.0),
    "permanent_chase_caution_weight": (0.0, 8.0),
    "early_rush_weight": (0.0, 3.0),
    "power_pellet_save_weight": (0.0, 1000.0),
    "pursuer_refuge_weight": (0.0, 60.0),
    "pursuer_hunt_weight": (0.0, 30.0),
    "pursuer_kite_weight": (0.0, 30.0),
    "pursuer_evasion_weight": (0.0, 30.0),
    "pursuer_route_weight": (0.0, 100.0),
}
LEARNED_WEIGHT_EXPLORATION = {
    "safe_radius": 1.2,
    "danger_weight_player": 1.6,  # bigger step to match the widened range above
    "danger_weight_blinky": 0.45,
    "danger_weight_pinky": 0.45,
    "danger_weight_inky": 0.45,
    "danger_weight_clyde": 0.45,
    "deadend_penalty": 100.0,
    "cluster_bonus": 8.0,
    "danger_far_weight_player": 0.1,
    "danger_far_weight_blinky": 0.08,
    "danger_far_weight_pinky": 0.08,
    "danger_far_weight_inky": 0.08,
    "danger_far_weight_clyde": 0.08,
    "closing_threat_weight": 1.0,
    "predicted_target_avoidance_weight": 0.15,
    "pincer_penalty_weight": 3.0,
    "mobility_bonus_weight": 0.5,
    "semi_deadend_penalty": 30.0,
    "junction_proximity_weight": 0.2,
    "tunnel_escape_bonus": 5.0,
    "reversal_penalty": 2.0,
    "pellet_density_bonus": 0.3,
    "second_target_distance_weight": 0.15,
    "power_pellet_proactive_weight": 0.1,
    "endgame_rush_weight": 1.0,
    "frightened_hunt_weight": 0.2,
    "frightened_pellet_weight": 0.3,
    "frightened_safety_weight": 0.1,
    "frightened_bait_pellet_weight": 0.3,
    "clyde_bait_weight": 0.5,
    "target_distance_weight": 0.3,
    "occupied_tile_penalty_weight": 2000.0,
    "total_threat_count_weight": 1.5,
    "lookahead_survival_weight": 10.0,
    "lookahead_pellet_weight": 1.0,
    "lookahead_ambush_weight": 2.0,
    "loop_escape_weight": 1.5,
    "corner_bailout_weight": 0.3,
    "trap_crowd_penalty": 6.0,
    "commitment_weight": 1.5,
    "exit_race_weight": 0.4,
    "weak_link_weight": 0.3,
    "scatter_timing_weight": 0.3,
    "frightened_sequence_weight": 0.2,
    "inky_decoy_weight": 0.2,
    "perception_window_weight": 0.3,
    "future_pincer_weight": 1.5,
    "pellet_contest_weight": 0.15,
    "respawn_discount_weight": 1.0,
    "permanent_chase_caution_weight": 0.5,
    "early_rush_weight": 0.2,
    "power_pellet_save_weight": 30.0,
    "pursuer_refuge_weight": 6.0,
    "pursuer_hunt_weight": 3.0,
    "pursuer_kite_weight": 3.0,
    "pursuer_evasion_weight": 1.0,
    "pursuer_route_weight": 2.0,
}
PURSUER_KITE_RANGE = 30  # tiles; beyond this the gap is big enough to roam freely
# "farm": while powered up, kill the player ghost as many times as possible
# (each kill costs the ghost team 50 points). "far": make the last kill far
# from the house for a bigger head start. In this maze a head start is gone
# within seconds, so "farm" is the default.
# Start heading for a power pellet once the player ghost is this close
# (it is only eaten once the gap is down to PURSUER_SAVE_GAP).
# Two settings depend on what kind of chaser the player ghost is (see
# pursuer_profile): one that only follows Pac-Man, or one that also cuts him
# off. An environment variable, when set, overrides both kinds.
PURSUER_REFUGE_GAP_FOLLOWER, PURSUER_REFUGE_GAP_CUTTER = 8, 14
PURSUER_LEAF_CAP_FOLLOWER, PURSUER_LEAF_CAP_CUTTER = 150, 300
PURSUER_REFUGE_GAP_OVERRIDE = os.environ.get("PACMAN_PURSUER_REFUGE_GAP")
PURSUER_LEAF_CAP_OVERRIDE = os.environ.get("PACMAN_PURSUER_LEAF_CAP")
PURSUER_EXPIRY_CAUTION_MS = int(os.environ.get("PACMAN_PURSUER_EXPIRY_CAUTION_MS", "1000"))
PURSUER_TACTIC = os.environ.get("PACMAN_PURSUER_TACTIC", "evade")
# How Pac-Man balances eating against the player ghost chasing him.
#   "evade"   - the plan from when running the clock down was a win: keep as
#               far ahead of the chaser as possible, eat what is on the way.
#   "harvest" - for clearing the maze: while a power pellet can still be
#               reached ahead of the chaser (or the chaser is a long way
#               behind), the chaser is ignored and Pac-Man simply eats; only
#               the shortfall below that safety line is penalised.
PURSUER_PLAN = os.environ.get("PACMAN_PURSUER_PLAN", "route")
#   "route"   - plan a pellet route many moves ahead against the chaser
#               (see _route_values) and follow it, leaving the classic
#               ghosts to the usual danger terms and lookahead.
PURSUER_ROUTE_DEPTH = int(os.environ.get("PACMAN_ROUTE_DEPTH", "40"))  # Pac-Man moves planned ahead
PURSUER_ROUTE_WIDTH = int(os.environ.get("PACMAN_ROUTE_WIDTH", "24"))  # routes kept alive at each move
PURSUER_ROUTE_LEAD = float(os.environ.get("PACMAN_ROUTE_LEAD", "0.5"))  # what one tile of lead over the chaser is worth, in pellets
PURSUER_ROUTE_CAP = int(os.environ.get("PACMAN_ROUTE_CAP", "200"))  # stay-ahead moves beyond which more lead is worth nothing
PURSUER_ROUTE_PP_GAP = int(os.environ.get("PACMAN_ROUTE_PP_GAP", "4"))  # chaser this close: a power pellet is well spent
PURSUER_ROUTE_PP_COST = float(os.environ.get("PACMAN_ROUTE_PP_COST", "10"))  # pellets' worth lost by eating one with the chaser far away
PURSUER_ROUTE_CLIP = float(os.environ.get("PACMAN_ROUTE_CLIP", "15"))  # most a move can be marked down for being off-route
PURSUER_ROUTE_AVOID = int(os.environ.get("PACMAN_ROUTE_AVOID", "5"))  # first moves of a route that must keep off tiles beside a classic ghost
# Pellets close to the ghost-house door are the ones that get left for last
# and then cannot be fetched (every ghost comes out there). A pellet within
# PURSUER_ROUTE_DOOR_RANGE steps of the door counts for up to
# PURSUER_ROUTE_DOOR_BONUS extra in a planned route, so they are taken
# whenever a route can do it safely - above all in the opening seconds.
PURSUER_ROUTE_DOOR_BONUS = float(os.environ.get("PACMAN_ROUTE_DOOR_BONUS", "0"))
PURSUER_ROUTE_DOOR_RANGE = int(os.environ.get("PACMAN_ROUTE_DOOR_RANGE", "9"))
# What eating the chaser is worth to a planned route, in pellets: it is sent
# home and has to sit out its cooldown, which resets Pac-Man's lead. Without
# this a route that merely runs from a frightened chaser looks as good as one
# that turns round and eats it, because the lead a route ends with is only
# counted up to PURSUER_ROUTE_CAP.
PURSUER_ROUTE_KILL = float(os.environ.get("PACMAN_ROUTE_KILL", "0"))
PURSUER_ROUTE_REFUGE = os.environ.get("PACMAN_ROUTE_REFUGE", "0") == "1"  # also keep the old "run for a power pellet when the chaser is close" pull
PURSUER_ROUTE_LEAF = os.environ.get("PACMAN_ROUTE_LEAF", "1") == "1"  # also keep the lookahead's own end-of-search lead term
# Should the route planner also move the four classic ghosts along with each
# route, and drop the routes on which one of them catches Pac-Man? "0" no
# (they are only kept at arm's length for the first PURSUER_ROUTE_AVOID
# moves), "habit" yes, each by its own usual targeting, "direct" yes, each
# heading straight for Pac-Man. Only for the first PURSUER_ROUTE_AI_DEPTH
# moves of a route: their random moves make anything further a guess.
PURSUER_ROUTE_AI = os.environ.get("PACMAN_ROUTE_AI", "habit")  # on since 2026-11-19: +2 pts of maze and about twice the clears against the hidden stand-in for the player (768 fresh games, twice)
PURSUER_ROUTE_AI_DEPTH = int(os.environ.get("PACMAN_ROUTE_AI_DEPTH", "10"))
# The route planner's ghost follows Pac-Man by the shortest path. A person
# does something more dangerous: they stand where he has least room - on the
# row above the bottom row, say, shadowing him so that they win the race to
# every way out - and wait for a classic ghost to finish it. Against a
# follower a route into such a place looks fine (the follower comes in after
# him and he leaves by the other end); against a person it is a cage.
# So the routes are also planned against a ghost that plays that way
# (_seal_step), and a route is then worth its pellets plus
# PURSUER_ROUTE_ROOM pellets for every tile of room he is left with at the
# end of it (up to PURSUER_ROUTE_ROOM_CAP tiles) - room meaning the tiles he
# could still reach before the player ghost. "0": off. "min": each first
# move gets the worse of its two marks (follower / sealer). "mean": their
# average. "only": the sealer's alone. Only for a ghost whose model is
# unknown or not a simple one - which includes every real game.
# Measured 2026-11-20 against the stand-in for the player (800 games, Pac-Man
# not told its moves): "only" with a room value of 1, 2 or 4 all gave the
# same - caught after 55-60 s instead of 46-49 s, games over in under 22 s
# 5-8% instead of 13%. "min" and "mean", and room values under 0.5, gave
# less.
PURSUER_ROUTE_SEAL = os.environ.get("PACMAN_ROUTE_SEAL", "only")
PURSUER_ROUTE_ROOM = float(os.environ.get("PACMAN_ROUTE_ROOM", "1"))
PURSUER_ROUTE_ROOM_CAP = int(os.environ.get("PACMAN_ROUTE_ROOM_CAP", "100"))
# The power-pellet plan (2026-11-20). In the games the player won, Pac-Man
# died with three or four of his four power pellets uneaten two times in
# three, the nearest of them 17 tiles away and out of reach. The four land
# on different tiles every game, so the plan is made from where they are:
#   cover   - he is "covered" while there is a power pellet he would reach
#             before the player ghost could (by PURSUER_ROUTE_COVER_SLACK
#             ms; no ghost can head him off on the way if he wins that
#             race). Every planned move he is not covered and not powered
#             costs PURSUER_ROUTE_EXPOSE pellets, and a route that ends
#             covered is worth PURSUER_ROUTE_COVER more.
#   danger  - the places a ghost standing well away can shut him into (the
#             corners, the bottom rows: _danger_map). A pellet eaten there
#             while he is powered is worth PURSUER_ROUTE_POWER_BONUS extra,
#             which is what makes a power pellet beside such a place worth
#             spending on clearing it (it pays for PURSUER_ROUTE_PP_COST).
#   escape  - a power pellet costs nothing to eat when the player ghost has
#             him shut into fewer than PURSUER_ROUTE_PP_SEALED tiles.
# Only with the sealing-aware planner (PURSUER_ROUTE_SEAL).
PURSUER_ROUTE_EXPOSE = float(os.environ.get("PACMAN_ROUTE_EXPOSE", "0.25"))
PURSUER_ROUTE_COVER = float(os.environ.get("PACMAN_ROUTE_COVER", "0"))
PURSUER_ROUTE_COVER_SLACK = int(os.environ.get("PACMAN_ROUTE_COVER_SLACK", "200"))
PURSUER_ROUTE_POWER_BONUS = float(os.environ.get("PACMAN_ROUTE_POWER_BONUS", "0"))
PURSUER_ROUTE_DANGER_ROOM = int(os.environ.get("PACMAN_ROUTE_DANGER_ROOM", "45"))
PURSUER_ROUTE_DANGER_AWAY = int(os.environ.get("PACMAN_ROUTE_DANGER_AWAY", "6"))
PURSUER_ROUTE_PP_SEALED = int(os.environ.get("PACMAN_ROUTE_PP_SEALED", "0"))
# Who decides when a power pellet is eaten. Pac-Man's move scoring has a
# flat rule - power_pellet_save_weight, bigger than anything the route
# planner can say - against stepping onto one while the player ghost is more
# than PURSUER_SAVE_GAP tiles off, so a plan like "eat the pellet by the
# bottom row and clear the row with it" could never be carried out. With
# this on, the rule stands aside when the planner's best route (within
# PURSUER_PP_PLAN_MARGIN pellets of it), which has already paid
# PURSUER_ROUTE_PP_COST for the pellet, still begins by eating it. The
# classic ghosts never make a pellet cheaper: against them one is eaten only
# when every other way gets him caught (the look-ahead sees to that).
PURSUER_PP_PLAN = os.environ.get("PACMAN_PP_PLAN", "1") == "1"
# A "strike" - walk to a power pellet, eat it, clear the danger area beside
# it while the power lasts - only shows its worth some twenty moves in: the
# walk there eats little and the pellet itself costs PURSUER_ROUTE_PP_COST.
# A beam that keeps the PURSUER_ROUTE_WIDTH best routes so far drops it
# long before that. So for every power pellet within
# PURSUER_ROUTE_SCOUT_RANGE moves the beam also keeps, at each move, the
# route that has got nearest to it and the PURSUER_ROUTE_SCOUT best routes
# that have eaten it and are still powered. 0: off.
PURSUER_ROUTE_SCOUT = int(os.environ.get("PACMAN_ROUTE_SCOUT", "0"))
# What the power is for, besides clearing a danger area: getting out. Traced
# games showed him eat a power pellet in the bottom rows, clear a pocket
# with it, and still be in the pocket when it ran out - the player ghost
# had simply kept its place over the exits, out of his reach, and waited.
#   PURSUER_ROUTE_FLEE       the planner's frightened sealer keeps sealing
#                            but backs away once he is within this many
#                            tiles (a person does; before, it stood still,
#                            so "walk into it and eat it" looked possible
#                            and "drive it off the exit" did not). 0: stands.
#   PURSUER_ROUTE_POWER_ROOM count his room while powered as if the power
#                            ended now, instead of "plenty": a powered route
#                            that stays shut in then scores as shut in.
#   PURSUER_ROUTE_DIVERSE    the beam keeps the best route to each tile
#                            before any second route to the same tile, so
#                            a route heading out of the pocket is not
#                            crowded out by many that stay to eat.
PURSUER_ROUTE_FLEE = int(os.environ.get("PACMAN_ROUTE_FLEE", "0"))
PURSUER_ROUTE_POWER_ROOM = os.environ.get("PACMAN_ROUTE_POWER_ROOM", "1") == "1"
PURSUER_ROUTE_DIVERSE = os.environ.get("PACMAN_ROUTE_DIVERSE", "1") == "1"
# Hesitation (2026-11-21). In the player's games about one Pac-Man move in
# eight was a U-turn, half of them with no ghost near, and he could go most
# of a minute without eating. Cause: after forty moves every route left in
# the beam usually began with the same first move, and the other first
# moves, having no route left, got the worst mark there is
# (-PURSUER_ROUTE_CLIP) however little worse they really were. Times
# pursuer_route_weight that is a swing of 150 points on a difference that
# may be a fraction of a pellet - and one tile on, the difference falls the
# other way and he turns round.
#   PURSUER_ROUTE_FAIR     the beam keeps an equal share of its places for
#                          the routes of each first move, so each gets the
#                          mark of its own best route.
#   PURSUER_ROUTE_INERTIA  a planned U-turn is marked down this many
#                          pellets: turning round has to be worth it.
PURSUER_ROUTE_FAIR = os.environ.get("PACMAN_ROUTE_FAIR", "0") == "1"
#   PURSUER_ROUTE_GRADED   the same aim without spending beam places on it
#                          (FAIR halved the search for the better move and
#                          cost 7 points of maze): a first move whose last
#                          route drops out of the beam keeps the mark it had
#                          at that moment - how far its best route then was
#                          behind the best of all.
PURSUER_ROUTE_GRADED = os.environ.get("PACMAN_ROUTE_GRADED", "1") == "1"
# Traced move by move, four U-turns in five made with nothing near him were
# the planner changing its mind: it plans a route one way, he takes a step,
# it plans again from the new tile and likes the other way better (the
# ghost it imagines has shifted a tile too), and so on - two tiles forward,
# two back. The rest were the planner insisting on a first step that his
# look-ahead says gets him caught: he steps aside, is told to go back, and
# bounces.
#   PURSUER_ROUTE_COMMIT   having chosen a route he keeps to it for this
#                          many moves: its next step is marked up by
#                          PURSUER_ROUTE_COMMIT_BONUS pellets, and only when
#                          the moves are used up, or every route that way is
#                          caught, or he was made to step off it, is a new
#                          route chosen. Needs the honest marks of GRADED,
#                          which it turns on for itself. 0: off.
#   PURSUER_ROUTE_VETO     when the planned first step survives less than
#                          half as long in the look-ahead as the best step
#                          does (and that one survives at least this many
#                          moves), plan again without it. 0: off.
PURSUER_ROUTE_COMMIT = int(os.environ.get("PACMAN_ROUTE_COMMIT", "0"))
# And the root of it: half the routes planned with safe pellets right
# beside him contained a deliberate stall (a step aside and back, then on).
# A route was marked by his room at its very last move, and room jumps
# about from one move to the next as the imagined ghost shifts, so wasting
# two moves to land the end of the route on a good moment "paid".
# PURSUER_ROUTE_ROOM_SMOOTH: mark a route by the least room over its last
# this-many moves (2 or 3), which a stall cannot improve. 0 or 1: the last
# move alone.
PURSUER_ROUTE_ROOM_SMOOTH = int(os.environ.get("PACMAN_ROUTE_ROOM_SMOOTH", "0"))
# What the player asked for (2026-11-21): "keep the stand-offs, remove the
# genuine dithering". A U-turn costs reversal_penalty as always, times
# PACMAN_FREE_REVERSAL_FACTOR when he is free - the player ghost at least
# PACMAN_FREE_GAP moves off (or still in the house), no classic ghost within
# PACMAN_FREE_CLASSIC, and at least PACMAN_FREE_ROOM tiles he would reach
# before the player ghost. Hovering while the player guards the last
# pellets keeps its old price. Powered: not counted as free (the hunt has
# its own rules).
# Measured on 384 games vs the stand-in: free U-turns with a safe pellet
# near 11 -> ~5 per 100 moves at x10, stand-off U-turns unchanged; maze
# eaten x6 -1.8 pts, x10 -3.4, x20 -6.4. x6 chosen as the compromise.
FREE_REVERSAL_FACTOR = float(os.environ.get("PACMAN_FREE_REVERSAL_FACTOR", "6"))
# Powered (2026-11-22, user: "just ate a power pellet ... going back and
# forth with only one ghost around and lots of pellets, as if it wasn't sure
# whether to get the ghost or the pellets"). The powered branch of
# choose_pacman_step had no U-turn price at all, so near-equal pulls (the
# route, the nearest pellet, the frightened ghost) flipped him every move.
# A U-turn while powered costs reversal_penalty times this. 0: none.
POWER_REVERSAL_FACTOR = float(os.environ.get("PACMAN_POWER_REVERSAL_FACTOR", "6"))
# Traced: under power the route planner itself flips (plans forward from one
# tile, back from the next), and a first step whose routes merely fell out of
# the beam gets the worst mark (-CLIP), so no U-turn price can hold him.
# PACMAN_ROUTE_GRADED_POWER=1 gives honest marks (see PURSUER_ROUTE_GRADED)
# while powered only, so the U-turn price above decides the close calls.
PURSUER_ROUTE_GRADED_POWER = os.environ.get("PACMAN_ROUTE_GRADED_POWER", "1") == "1"
# The last PURSUER_EXPIRY_CAUTION_MS of power are played as normal moves; with
# this on, the powered U-turn price applies there too.
POWER_REVERSAL_CAUTION = os.environ.get("PACMAN_POWER_REVERSAL_CAUTION", "1") == "1"
# Raids (2026-11-21). Half the pellets are in sealable areas (_danger_map),
# and when he dies about 57 of those are left against 26 safe ones: he
# grazes the open maze first and must go into guarded pockets at the end. A
# person does the reverse - raids the pockets while the guard is elsewhere
# and keeps the open-maze pellets for when it is close.
#   PURSUER_ROUTE_RAID      extra pellets' worth for eating a sealable-area
#                           pellet while the player ghost is at least
#                           PURSUER_ROUTE_RAID_GAP moves from it (or in the
#                           house). 0: off.
#   PURSUER_ROUTE_AREA      area order, looking past the end of the route:
#                           at each decision, the pocket (connected piece of
#                           the sealable areas) with pellets that he reaches
#                           furthest ahead of the player ghost is the next
#                           one to raid, and a route is worth this many
#                           pellets less per move its end lies from that
#                           pocket's pellets. Only when he reaches it at
#                           least PURSUER_ROUTE_AREA_MARGIN moves ahead of
#                           the ghost. 0: off.
PURSUER_ROUTE_RAID = float(os.environ.get("PACMAN_ROUTE_RAID", "0"))
PURSUER_ROUTE_RAID_GAP = int(os.environ.get("PACMAN_ROUTE_RAID_GAP", "10"))
# Measured 2026-11-21: raids cost 3-5 pts of maze (he is caught sooner;
# left off). Area order 0.3: +0.4 / +2.0 pts on 800 fresh games, clears
# 5 vs 1 (11 vs 2 over every test) - on by default.
PURSUER_ROUTE_AREA = float(os.environ.get("PACMAN_ROUTE_AREA", "0.3"))
PURSUER_ROUTE_AREA_MARGIN = int(os.environ.get("PACMAN_ROUTE_AREA_MARGIN", "4"))
# Endgame (2026-11-21). In a third of his losses he is down to 40 pellets or
# fewer - mostly in two sealable pockets - and in 117 of those 136 he has
# already spent all four power pellets, so nothing stops a faster ghost
# guarding the pockets.
#   PURSUER_ROUTE_PP_RESERVE       keep this many power pellets back for the
#                                  endgame: eating one that leaves fewer than
#                                  this costs PURSUER_ROUTE_PP_RESERVE_COST
#                                  pellets more, unless the player ghost is
#                                  close enough to eat (an escape) or the
#                                  endgame has begun. 0: off.
#   PURSUER_ROUTE_END              the endgame: this many pellets or fewer left.
#   PURSUER_ROUTE_END_ROOM         in the endgame, room is worth this times as
#                                  much (below 1: he commits to the pockets).
PURSUER_ROUTE_PP_RESERVE = int(os.environ.get("PACMAN_ROUTE_PP_RESERVE", "0"))
PURSUER_ROUTE_PP_RESERVE_COST = float(os.environ.get("PACMAN_ROUTE_PP_RESERVE_COST", "30"))
PURSUER_ROUTE_END = int(os.environ.get("PACMAN_ROUTE_END", "60"))
PURSUER_ROUTE_END_ROOM = float(os.environ.get("PACMAN_ROUTE_END_ROOM", "0.25"))
FREE_GAP = int(os.environ.get("PACMAN_FREE_GAP", "0"))  # 2026-11-22: was 8; U-turns while chased in open space count too (user: "move one direction and then move back ... cost it space")
FREE_CLASSIC = int(os.environ.get("PACMAN_FREE_CLASSIC", "5"))
FREE_ROOM = int(os.environ.get("PACMAN_FREE_ROOM", "90"))
PURSUER_ROUTE_COMMIT_BONUS = float(os.environ.get("PACMAN_ROUTE_COMMIT_BONUS", "6"))
PURSUER_ROUTE_VETO = int(os.environ.get("PACMAN_ROUTE_VETO", "0"))
PURSUER_ROUTE_INERTIA = float(os.environ.get("PACMAN_ROUTE_INERTIA", "0"))
# Which room counts. "all": every tile he would reach before the player
# ghost. "safe": only tiles of open maze (outside the sealable areas of
# _danger_map). Forty tiles of bottom row with the ghost over the exits is
# not room, it is the trap; one corridor that still leads out is worth more
# than that. With "safe" and PURSUER_ROUTE_POWER_ROOM, a powered Pac-Man is
# counted as free as long as he can walk to open maze with
# PURSUER_ROUTE_POWER_EXIT_MS of power to spare - the ghost cannot bar the
# way of a Pac-Man who would eat it - and as shut in otherwise.
PURSUER_ROUTE_ROOM_KIND = os.environ.get("PACMAN_ROUTE_ROOM_KIND", "all")
# The route planner cannot afford to ask the model of the player for every
# move of every route it tries (thousands a decision). Instead: a table,
# made once from games the model played, of where it stepped for each pair
# (its tile, Pac-Man's tile) - one for normal play, one for when it is
# frightened. The planner's ghost follows the table and falls back on the
# sealer (_seal_step) for pairs the table has not seen. A file name inside
# player_ghost_versions/ (or a full path). Not set: the table that goes
# with the model he assumes, <PACMAN_ASSUMED_PLAYER>_hops.json, made by
# build_player_ghost_hops.py (run it again after every refit). "none" or a
# missing file: no table.
#
# Measured 2026-11-20 on 800 fresh games against the stand-in for the
# player (Pac-Man not told its moves; table and assumed model from the
# other half of the player's games), what is now on by default -
# PURSUER_ROUTE_DIVERSE, this table, PURSUER_ROUTE_POWER_ROOM,
# PURSUER_PP_PLAN with PURSUER_ROUTE_PP_COST 10, PURSUER_ROUTE_EXPOSE 0.25 -
# against the sealing-aware planner alone: maze eaten 56% -> 70%, caught
# after 54 s -> 80 s, clears still under 1%. Tried and left off, no gain or
# a loss: PURSUER_ROUTE_POWER_BONUS, PURSUER_ROUTE_SCOUT,
# PURSUER_ROUTE_ROOM_KIND "safe", PURSUER_ROUTE_FLEE, PURSUER_ROUTE_COVER,
# PURSUER_ROUTE_PP_SEALED, a wider or deeper beam, a lower room cap.
PURSUER_ROUTE_HOPS_FILE = os.environ.get("PACMAN_ROUTE_HOPS", "")
PURSUER_ROUTE_POWER_EXIT_MS = int(os.environ.get("PACMAN_ROUTE_POWER_EXIT_MS", "600"))
PURSUER_ROUTE_SCOUT_RANGE = int(os.environ.get("PACMAN_ROUTE_SCOUT_RANGE", "14"))
PURSUER_PP_PLAN_MARGIN = float(os.environ.get("PACMAN_PP_PLAN_MARGIN", "0"))
PURSUER_COMFORT = int(os.environ.get("PACMAN_PURSUER_COMFORT", "100"))  # stay-ahead moves that count as safe without cover
PURSUER_COMFORT_BARE = int(os.environ.get("PACMAN_PURSUER_COMFORT_BARE", "20"))  # same, once no power pellet is left: running only delays the end, so eat
PURSUER_HARVEST_FACTOR = float(os.environ.get("PACMAN_PURSUER_HARVEST_FACTOR", "1"))  # penalty per move of shortfall, x pursuer_evasion_weight
# An eaten ghost sits in the house this long before it starts walking out
# again (a "last move" time in the future holds it still until then), so a
# powered-up Pac-Man cannot eat the same ghost over and over at the door.
GHOST_RESPAWN_COOLDOWN_MS = int(os.environ.get("PACMAN_GHOST_RESPAWN_COOLDOWN_MS", "3000"))
PURSUER_EVASION_CAP = int(os.environ.get("PACMAN_PURSUER_EVASION_CAP", "400"))  # above this much stay-ahead time, evasion is ignored
PURSUER_HOUSE_EVASION = os.environ.get("PACMAN_PURSUER_HOUSE_EVASION", "0") == "1"
PURSUER_LEAF_FACTOR = float(os.environ.get("PACMAN_PURSUER_LEAF_FACTOR", "1"))  # leaf evasion weight, as a multiple of pursuer_evasion_weight
PURSUER_AI_HUNT_FACTOR = float(os.environ.get("PACMAN_PURSUER_AI_HUNT_FACTOR", "4"))  # pull per tile towards a frightened classic ghost, in tiles of head start
PURSUER_AI_HUNT_RANGE = int(os.environ.get("PACMAN_PURSUER_AI_HUNT_RANGE", "30"))
PURSUER_AI_CATCH_MS = 300  # rough time to close one tile on a slow, randomly moving frightened ghost
PURSUER_REACH_MARGIN = int(os.environ.get("PACMAN_PURSUER_REACH_MARGIN", "2"))
PURSUER_COVER_FACTOR = float(os.environ.get("PACMAN_PURSUER_COVER_FACTOR", "0"))  # bonus for keeping a power pellet reachable, x pursuer_evasion_weight
# How the player ghost is simulated inside Pac-Man's lookahead: "auto" = its
# real model, computed the cheap way when the model allows it; "model" =
# always the full decision function; "bfs" = force a plain shortest-path
# chaser when the cheap way is not available.
PURSUER_SIM = os.environ.get("PACMAN_PURSUER_SIM", "auto")
# The plain stand-in: what Pac-Man assumes a player ghost does when he has no
# model of the player to go by (PACMAN_ASSUMED_PLAYER), and beyond that
# model's horizon. "line": it steps to whichever neighbouring tile is nearest
# to him as the crow flies (so it stops against walls). "path": it walks the
# shortest route to him round the walls. Neither is how a person plays: on
# recorded games "path" was the worse guess at where the real player's ghost
# would be a few moves later (worse even than "it stays where it is"), and
# Pac-Man did worse assuming it; a person heads him off far more than they
# follow him. The fitted model is what captures that - these are only the
# fallback.
PURSUER_STANDIN = os.environ.get("PACMAN_PURSUER_STANDIN", "line")
# Second opinions. Whatever Pac-Man's main picture of the player ghost is (a
# fitted model of the person, or the stand-in above), a person does not
# always do what it says - and a route that is only safe if they do is how
# he gets cut off. So his look-ahead is run again, for survival only, with
# the ghost doing each of the things listed here instead ("path" / "line",
# as for PURSUER_STANDIN), and each run's survival is added to a move's
# score at PURSUER_DOUBT_FACTOR x lookahead_survival_weight. "" switches
# this off.
PURSUER_DOUBT = tuple(k for k in os.environ.get("PACMAN_PURSUER_DOUBT", "").split(",") if k)
PURSUER_DOUBT_FACTOR = float(os.environ.get("PACMAN_PURSUER_DOUBT_FACTOR", "0.5"))
PURSUER_DOUBT_DEPTH = int(os.environ.get("PACMAN_PURSUER_DOUBT_DEPTH", "16"))
# Worst case. In the games recorded against a person, most of Pac-Man's
# deaths were ones his look-ahead called safe until the last move or two: he
# walked up a corridor towards the player's ghost because his picture of the
# player said it would turn off, with a classic ghost closing the way back.
# So, for the next PURSUER_ADVERSARY_DEPTH moves only, each way open is also
# scored by how many moves he is SURE to survive on it whatever the player
# ghost does (see _adversary_values), at PURSUER_ADVERSARY_FACTOR x
# lookahead_survival_weight a move. Depth 0 switches it off. Not used
# against a ghost whose model he has been told: there is nothing to doubt.
PURSUER_ADVERSARY_DEPTH = int(os.environ.get("PACMAN_PURSUER_ADVERSARY_DEPTH", "0"))
PURSUER_ADVERSARY_FACTOR = float(os.environ.get("PACMAN_PURSUER_ADVERSARY_FACTOR", "3"))
PURSUER_ADVERSARY_BUDGET = int(os.environ.get("PACMAN_PURSUER_ADVERSARY_BUDGET", "2000"))
# Room to move. A person playing the ghost rarely runs Pac-Man down: they
# let a classic ghost push him along and close the other way out
# themselves. Seen from his side that is his room shrinking - the tiles he
# could still get to before any ghost does (each at its real speed) - long
# before any single move looks dangerous. Each way open is scored
# PURSUER_ROOM_WEIGHT per tile of room it leaves him, up to
# PURSUER_ROOM_CAP tiles: with plenty of room this says nothing, and as the
# room closes in it pulls him out towards open maze while a way out is
# still there. Weight 0 switches it off. See _room_setup.
PURSUER_ROOM_WEIGHT = float(os.environ.get("PACMAN_PURSUER_ROOM_WEIGHT", "0"))
PURSUER_ROOM_CAP = int(os.environ.get("PACMAN_PURSUER_ROOM_CAP", "40"))
# Whose reach counts against his room: "all" ghosts, or the "player" ghost
# only (the one that deliberately pens him in; the classic ghosts wander).
PURSUER_ROOM_MODE = os.environ.get("PACMAN_PURSUER_ROOM_MODE", "all")
PURSUER_SIM_FAST = PURSUER_SIM == "bfs"
PURSUER_PESSIMISM_FACTOR = float(os.environ.get("PACMAN_PURSUER_PESSIMISM_FACTOR", "0.3"))  # weight of the pessimistic search, x lookahead_survival_weight
PURSUER_PESSIMISM_DEPTH = int(os.environ.get("PACMAN_PURSUER_PESSIMISM_DEPTH", "16"))
PURSUER_POLICY_TABLE = os.environ.get("PACMAN_PURSUER_POLICY_TABLE", "0")  # "0" plain chaser, "1" the ghost's own model, "min" the more cautious of the two;  # evasion table from the ghost model itself when possible
PURSUER_EXACT_SPEED = os.environ.get("PACMAN_PURSUER_EXACT_SPEED", "1") == "1"  # "0" restores the old one-move-per-ply player ghost in the search
PURSUER_EVASION_CLIP = int(os.environ.get("PACMAN_PURSUER_EVASION_CLIP", "36"))  # most stay-ahead moves one choice can be penalised for
PURSUER_EVASION_HORIZON = 400  # Pac-Man moves the evasion table looks ahead (about 76s)
PURSUER_FARM_CLOSING = os.environ.get("PACMAN_PURSUER_FARM_CLOSING", "1") == "1"
PURSUER_DOOR_BIAS = float(os.environ.get("PACMAN_PURSUER_DOOR_BIAS", "0"))
PURSUER_CAMP_FACTOR = float(os.environ.get("PACMAN_PURSUER_CAMP_FACTOR", "0"))
PURSUER_DEPART_MS = int(os.environ.get("PACMAN_PURSUER_DEPART_MS", "0"))
PURSUER_FARM_MARGIN_MS = int(os.environ.get("PACMAN_PURSUER_FARM_MARGIN_MS", "500"))
PURSUER_HUNT_MARGIN_MS = 400  # power time kept in hand when judging whether a kill is still reachable
PURSUER_LATE_WINDOW_MS = 2000  # last part of a power window, when Pac-Man turns to make the final kill
# ms per tile of gap closed when Pac-Man and a frightened player ghost walk toward each other
PURSUER_CLOSING_MS_PER_TILE = 1.0 / (1.0 / PACMAN_MOVE_DELAY + 1.0 / GHOST_FRIGHTENED_MOVE_DELAY)
PURSUER_SAVE_GAP = int(os.environ.get("PACMAN_PURSUER_SAVE_GAP", "4"))  # tiles; closer than this, eating a power pellet is no longer "wasting" it
# See PacmanBrain._exploration_step: a challenger trial's actual step is
# the smaller of LEARNED_WEIGHT_EXPLORATION[key] and half the weight's own
# current value, floored at this fraction of the original fixed step so a
# weight sitting at/near 0 can still explore upward at a modest pace.
WEIGHT_STEP_RELATIVE_FRACTION = 0.5
WEIGHT_STEP_FLOOR_FRACTION = 0.05
BRAIN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pacman_brain.json")

# Every completed game appends one line here: win/loss and exactly how the
# game ended (caught, cleared the maze, timer ran out, score hit zero).
# JSON-lines so a long history can be appended to cheaply without rewriting
# the whole file each game.
HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pacman_history.jsonl")

# Every real (human-driven) move the player-controlled ghost makes after
# leaving the house gets logged here - see _record_player_ghost_move,
# called from move_ghost. fit_player_ghost.py fits PLAYER_GHOST_WEIGHTS_FILE
# from this log by imitation learning, so a training run can later drive
# the player's ghost the way its actual player plays (see
# choose_player_ghost_direction / Game.ghost_autoplay_weights) instead of
# leaving it passive. Never written to during headless training itself,
# since nothing there ever sets a real pending_direction to begin with.
PLAYER_MOVES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "player_ghost_moves.jsonl")
# Every decision Pac-Man makes in a real (human-driven) game is logged here,
# one line a move: each way open to him as [x, y, score, guaranteed moves
# counted by the worst-case check, route value, look-ahead survival] (just
# [x, y, score] while a power pellet lasts), the way he took, where everyone
# was, how many moves that way really guaranteed him ("sure", uncapped) and
# how long he took to decide. One closing line a game says how it ended and
# who caught him. Written only so his mistakes can be read afterwards -
# nothing reads it during play, and headless games never write it.
# PACMAN_DECISION_LOG=0 turns it off.
PACMAN_DECISIONS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pacman_decisions.jsonl")
PACMAN_DECISION_LOG = os.environ.get("PACMAN_DECISION_LOG", "1") != "0"
# The list choose_pacman_step fills with one entry per way open while a real
# game is asking (see Game.move_pacman); None the rest of the time.
PACMAN_DECISION_TRACE = None
PLAYER_GHOST_WEIGHTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "player_ghost_weights.json")
PLAYER_GHOST_VERSIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "player_ghost_versions")
# What Pac-Man assumes a player ghost he has NOT been told about will do -
# which is every real game, where a person is at the keyboard. The name of
# a model in player_ghost_versions/ fitted to that person's recorded play
# (see fit_player_ghost_v2.py): his look-ahead then moves the ghost the way
# the model says the person would. "" or "none": no model, he falls back on
# the plain stand-in chaser (see PURSUER_STANDIN).
PACMAN_ASSUMED_PLAYER = os.environ.get("PACMAN_ASSUMED_PLAYER", "full_power")
# How many of Pac-Man's moves ahead his look-ahead moves the ghost by that
# model before falling back on the plain stand-in. The model's guess at
# where a person's ghost will be is good for a few moves and little better
# than the stand-in's a dozen moves out, and it is by far the most
# expensive thing in a decision, so there is no point paying for it all
# the way down. 0: no limit. Only applies to a ghost he has not been told
# about. Measured 2026-11-18 against a hidden model of the player: 8 or 12
# moves did as well as no limit, at a third of the time per decision.
PACMAN_ASSUMED_PLAYER_HORIZON = int(os.environ.get("PACMAN_ASSUMED_PLAYER_HORIZON", "8"))
# Past that horizon (2026-11-21): "line" - the straight-line stand-in, as
# before; "hops" - the assumed model's own moves table (<version>_hops.json,
# the one the route planner uses), the stand-in only where the table has no
# entry. Running the model for the whole lookahead was worth +10 points of
# maze against the hidden stand-in but tripled the cost of a decision.
PACMAN_ASSUMED_PLAYER_PAST = os.environ.get("PACMAN_ASSUMED_PLAYER_PAST", "hops")
# The worst case, exactly (2026-11-24). Everything above plans against a
# picture of the player: the fitted model, its moves table, a sealer. When
# the real player does something else - above all, cuts straight across to
# the junction Pac-Man is walking towards - the plan is simply wrong, and in
# the player's recorded games he threw his whole lead away that way within
# two seconds of the ghost leaving the house, game after game (re-asked at
# those positions, this code made the same moves).
# _worst_case_table() is the one-on-one chase solved exactly: for every
# (Pac-Man tile, ghost tile), how many more moves he can be SURE of whatever
# the player ghost does (at the real speeds: at most 192, about 11 for each
# tile of lead). Each way open is then also marked down by what it gives up
# of that, compared with the best way open:
#   PACMAN_WORST_WEIGHT  points per guaranteed move given up, counting
#                        guaranteed moves only up to
#   PACMAN_WORST_SAFE    (above that he has lead to spend on pellets; below
#                        it every tile given away costs).
#   PACMAN_WORST_COVER   what a way counts as guaranteeing when he would
#                        win the race to a power pellet from there (by
#                        PACMAN_WORST_COVER_SLACK ms): the ghost cannot stop
#                        him eating it. -1: the same as PACMAN_WORST_SAFE.
#   PACMAN_WORST_CLIP    the most a way can be marked down - kept below
#                        what the look-ahead says about a way that gets him
#                        killed within a dozen moves, so that this never
#                        walks him into a classic ghost to save a tile of
#                        lead on the player's.
# Weight 0 switches it off. Measured 2026-11-24 (60fps world, fresh seeds,
# weight 10 / safe 66 against none):
#   against a stand-in that pounces - plays the exact chase whenever he is
#   within 33 guaranteed moves and the fitted model otherwise, which is
#   nearer the player's own record of quick catches than the fitted model
#   alone - 200 games: maze eaten 47% -> 62% (+15.3 points, z 8.7), caught
#   after a median 32 s -> 60 s, the quickest quarter of games 12 s -> 35 s;
#   against the fitted model alone, 300 and 200 games (the two halves):
#   -0.9 and -0.4 points (z under 1) - its own catches fall by a quarter,
#   the classic ghosts' rise by as much.
# 16 / 44 measured the same. Heavier settings cost against the fitted model
# (16 / 110: -7 points; he keeps clear of the player ghost - its catches
# fall from 67 to 9 in 200 games - but eats less and the classic ghosts get
# him instead). Clears stay under 1% either way.
PACMAN_WORST_WEIGHT = float(os.environ.get("PACMAN_WORST_WEIGHT", "10"))
PACMAN_WORST_SAFE = int(os.environ.get("PACMAN_WORST_SAFE", "66"))
PACMAN_WORST_COVER = int(os.environ.get("PACMAN_WORST_COVER", "-1"))
PACMAN_WORST_COVER_SLACK = int(os.environ.get("PACMAN_WORST_COVER_SLACK", "200"))
PACMAN_WORST_CLIP = float(os.environ.get("PACMAN_WORST_CLIP", "400"))
# Two holes in the check, both found in one death in the user's games
# (2026-11-24, game 381105: running up a corridor with the ghost three
# tiles behind, he turned round and walked back into it).
# PACMAN_WORST_COVER_REPLIES: the "wins the race to a power pellet" test took
# the ghost's move clock as it stood, so a way could count as safe by a
# sliver one move and be lost the next, when the ghost got two moves to his
# one. 2: the race must be won with the clock at its worst.
# PACMAN_WORST_DEAD: a way onto a tile the ghost can simply be on before he
# moves again was only marked down like any other (at most PACMAN_WORST_CLIP),
# and his plans could outweigh that. It now costs this much more, unless
# every way is like that. 0: off.
# PACMAN_FLIP_WEIGHT: stepping back and forth between the same two tiles. In
# the user's 30 games of 2026-11-24 he did it (three or more reversals in a
# row) 30 times, and 9 of the 30 games ended within two seconds of one: each
# step looked good from the tile before it and bad once he was on it, so he
# spent the last seconds a pincer left him going nowhere. One reversal is
# free (a dodge); each further one in a row costs this much times the number
# already made - but only a step the worst-case check itself rates below
# another way open, so he is pushed out of the trap, never further in. 0: off.
# PACMAN_ROUTE_PELLET: what a pellet is worth to the route planner against
# a tile of room (1 = the same). Penned into the top of the maze by a ghost
# holding the middle (game 381097) he circled for 53 of 78 seconds with
# pellets a few tiles off, because circling kept as much room as eating.
#
# Measured 2026-11-24, 200 games each on the same seeds, against the stand-in
# refitted to the user that day / against the "pouncer" (the stand-in playing
# the exact chase whenever he is within 33 guaranteed moves); points are
# share of maze eaten plus half a point per clear:
#   cover clock at its worst (2)   -1.6 / +1.8   (both inside the noise) - ON
#   flip weight 300                -0.5 /  0.0   (stand-ins hardly ever park
#       or hold a line, so they cannot show what it is for)            - ON
#   dead step 2000                 -3.4 / -0.1   player-ghost catches fall
#       from 59 to 8 in 200 but the classic ghosts take him a moment later;
#       400: -1.2 / +0.5                                               - OFF
#   pellet worth 2 tiles of room   -2.4 / (with the rest) +1.7         - OFF
# The first two are on because they put right what he did in real games:
# replayed, the cover clock keeps him running in game 381105 and the flip
# weight breaks the back-and-forth of game 381104 after two flips, not 13.
# Final check, 400 fresh games from this file: the two together +1.1 / +0.3
# (noise); with the picture of the user refitted the same day (see
# PACMAN_ASSUMED_PLAYER) 63.9% -> 74.6% of the maze and caught at a median
# 72 -> 90 s against the stand-in, 51.1% -> 58.0% and 43 -> 51 s against the
# pouncer. Clears stay under 1%.
PACMAN_FLIP_WEIGHT = float(os.environ.get("PACMAN_FLIP_WEIGHT", "300"))
PURSUER_ROUTE_PELLET = float(os.environ.get("PACMAN_ROUTE_PELLET", "1"))
PACMAN_WORST_COVER_REPLIES = int(os.environ.get("PACMAN_WORST_COVER_REPLIES", "2"))
PACMAN_WORST_DEAD = float(os.environ.get("PACMAN_WORST_DEAD", "0"))
# An idle player ghost (2026-11-25). His planner and look-ahead move the
# ghost they imagine from the very next instant, wherever it stands and
# whatever it has been doing. Against a ghost that simply never left the
# house he therefore stalled - 13.5% clears, most games a five-minute
# stalemate (which the ghosts win), against about 86% when he knows there is
# no player ghost - and against one parked just outside the door, 2%.
# Game.move_pacman now times how long the ghost has stood on one tile
# although free to move (released, not in its respawn wait, not frightened).
# Once that is long enough he takes it that the ghost will go on standing
# there for PACMAN_HOLD_FACTOR times as long again, at most
# PACMAN_HOLD_CAP_MS: the planner's and the look-ahead's ghost stay put
# until then.
#   in the house:  counts after PACMAN_HOLD_MIN_MS, and the worst-case check
#                  counts the moves it is taken to stand as in hand.
#   outside:       counts only after PACMAN_HOLD_MIN_OUT_MS, and the
#                  worst-case check is left as it is
#                  (PACMAN_HOLD_CREDIT_OUT=1 relaxes it there too). In the
#                  player's 30 games of 2026-11-24 the ghost stood still for
#                  three or more of his moves only 13 times, never longer
#                  than 2.4 s, and away from the house mostly on guard over
#                  the bottom rows - not idleness. Replayed with a 0.6 s
#                  threshold the rule changed 8 of those 45 decisions, every
#                  one a step nearer the ghost, one into a real ambush; at
#                  3 s it changes none.
# Measured on fresh games (52 each, classic ghosts playing): ghost never
# leaves the house 7.7% -> 84.6% clears; ghost parked outside the door 3.8%
# -> 57.7%. Against opponents that keep moving it never comes into play
# (400 games each against the stand-in and the pouncer: every game the same
# with it on or off). A ghost that shuffles between two tiles instead of
# standing still is not caught by it. PACMAN_HOLD_FACTOR=0: off.
PACMAN_HOLD_FACTOR = float(os.environ.get("PACMAN_HOLD_FACTOR", "2"))
PACMAN_HOLD_MIN_MS = int(os.environ.get("PACMAN_HOLD_MIN_MS", "600"))
PACMAN_HOLD_MIN_OUT_MS = int(os.environ.get("PACMAN_HOLD_MIN_OUT_MS", "3000"))
PACMAN_HOLD_CAP_MS = int(os.environ.get("PACMAN_HOLD_CAP_MS", "8000"))
PACMAN_HOLD_CREDIT_OUT = os.environ.get("PACMAN_HOLD_CREDIT_OUT", "0") == "1"
# 2026-11-25, later: a ghost that steps back and forth between two tiles next
# to each other got round the rule (it never "stood still"). With
# PACMAN_HOLD_SHUFFLE that counts as standing still too, by the same clock
# as for a ghost outside the house (PACMAN_HOLD_MIN_OUT_MS, worst-case check
# untouched), and stepping onto either of the two tiles costs
# PACMAN_HOLD_SHUFFLE_TILE while the rule is in force. Fresh games, 52 each,
# against a ghost shuffling beside the door all game: 7.7% -> 40.4% clears;
# 200 each against the stand-in and the pouncer: identical game for game. In
# the player's 43 logged games it would have been in force at 2 decisions
# of 14,016. "0": off.
PACMAN_HOLD_SHUFFLE = os.environ.get("PACMAN_HOLD_SHUFFLE", "1") == "1"
PACMAN_HOLD_SHUFFLE_TILE = float(os.environ.get("PACMAN_HOLD_SHUFFLE_TILE", "5000"))


def load_assumed_player_weights(name=None):
    """The weights of the model named by PACMAN_ASSUMED_PLAYER (or `name`),
    or None when there is none or it cannot be read."""
    name = PACMAN_ASSUMED_PLAYER if name is None else name
    if not name or name.lower() == "none":
        return None
    try:
        with open(os.path.join(PLAYER_GHOST_VERSIONS_DIR, name + ".json")) as handle:
            weights = json.load(handle)
    except (OSError, ValueError):
        return None
    return weights if isinstance(weights, dict) else None

# Wins, and "near wins" (caught but almost cleared the maze or almost
# outlasted the clock), get their full move-by-move replay saved here so
# pacman_replay_browser.py can list and play them back later. At most
# MAX_LEADERBOARD *unpinned* replays are kept (see save_replay) - a clip
# the user pins in the browser sits outside that cap entirely, so there
# can be any number of pinned clips and they're never bumped out.
REPLAY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "replays")
MAX_LEADERBOARD = 10  # cap on UNPINNED replays only; pinned ones are unlimited
NEAR_WIN_PELLET_FRACTION = 0.9  # caught with at least this much of the maze eaten counts as a near win

# Every run (whichever script constructs a Game()) gets the next number
# from this counter, so each saved replay can record which run produced
# it (see Game.__init__/save_replay) for display in the replay browser.
RUN_COUNTER_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "run_counter.txt")


def _next_run_id():
    try:
        with open(RUN_COUNTER_FILE, "r") as handle:
            current = int(handle.read().strip())
    except (OSError, ValueError):
        current = 0
    next_id = current + 1
    try:
        with open(RUN_COUNTER_FILE, "w") as handle:
            handle.write(str(next_id))
    except OSError:
        pass
    return next_id


def outcome_bucket(meta):
    """Lower is better. A saved replay is always a win or a near win, so
    this only needs to rank those: an actual win beats a near win, and
    among wins, clearing every pellet beats the old rules' clock wins
    (still present in replays saved before the score was removed)."""
    if meta["won"]:
        return 0 if meta["outcome"] == "maze_cleared" else 1
    return 2


def replay_rank_key(meta):
    """Composite ranking key, best run first: win > near win, cleared the
    maze > ran the clock out, more pellets eaten > fewer, and - as the
    final tiebreaker - a shorter time > a longer one. Shared by save_replay
    (to decide what belongs in the MAX_LEADERBOARD-sized leaderboard) and
    pacman_replay_browser.py (to display the RANK column), so both always
    agree on what "best" means."""
    return (outcome_bucket(meta), -meta["pellet_fraction"], meta["elapsed_ms"])

BLACK = (8, 8, 22)
WHITE = (245, 245, 255)
WALL = (30, 75, 210)
WALL_EDGE = (90, 145, 255)
GHOST_BLUE = (40, 190, 255)
YELLOW = (255, 225, 35)
PELLET = (255, 225, 190)
POWER_PELLET = (255, 135, 200)
FRIGHTENED_BLUE = (35, 65, 255)
HUD = (30, 30, 50)
PLAYER_GHOST_COLOR = (255, 200, 0)  # player's ghost: yellow, distinct from the original four

# Each way a game can end gets its own look on the game-over screen: a
# title, a one-line subtitle, an accent color, and a tinted overlay. "icon"
# names which glyph is drawn above the title (see Game.draw_outcome_icon).
GAME_OVER_STYLES = {
    "ghost_win": {
        "title": "YOUR GHOST TEAM CAUGHT PAC-MAN!",
        "subtitle": "Victory for the ghosts.",
        "accent": (255, 90, 90),
        "tint": (70, 15, 15, 170),
        "icon": "ghost",
    },
    "maze_cleared": {
        "title": "PAC-MAN CLEARED THE MAZE!",
        "subtitle": "Every pellet eaten - Pac-Man's ultimate win.",
        "accent": YELLOW,
        "tint": (70, 55, 10, 170),
        "icon": "pacman",
    },
    "stalemate": {
        "title": "STALEMATE - GHOSTS WIN",
        "subtitle": "Pac-Man could not clear the maze.",
        "accent": GHOST_BLUE,
        "tint": (10, 35, 70, 170),
        "icon": "clock",
    },
    # The two below can no longer happen (the score and clock they belonged
    # to are gone) - kept only so replays saved under the old rules still
    # open in the replay browser.
    "survived": {
        "title": "PAC-MAN SURVIVED!",
        "subtitle": "The ghost team ran out the clock.",
        "accent": GHOST_BLUE,
        "tint": (10, 35, 70, 170),
        "icon": "clock",
    },
    "timeout": {
        "title": "TIME'S UP!",
        "subtitle": "The ghost team's score hit zero.",
        "accent": FRIGHTENED_BLUE,
        "tint": (30, 15, 70, 170),
        "icon": "clock",
    },
}

CARDINALS = ((0, -1), (0, 1), (-1, 0), (1, 0))

# Row 14 is the classic warp tunnel: walking off one edge wraps to the
# other side of the maze, the same as the original arcade game.
TUNNEL_ROWS = frozenset({14})

# All five ghosts start caged in the center ghost house and are released
# one at a time, player first, just like the original game.
GHOST_START = (13, 14)  # Player-controlled ghost
AI_GHOST_STARTS = ((11, 14), (16, 14), (12, 13), (15, 13))  # Four AI allies
AI_RELEASE_INTERVAL = 2_000  # ms between each successive AI ghost's release

PACMAN_START = (10, 8)
AI_GHOST_MOVE_GATE = 260
AI_GHOST_FRIGHTENED_MOVE_GATE = 390
AI_GHOST_MOVE_DELAY = frame_rounded_delay(AI_GHOST_MOVE_GATE, "ai")  # see PACMAN_FRAME_TIMING
AI_GHOST_FRIGHTENED_MOVE_DELAY = frame_rounded_delay(AI_GHOST_FRIGHTENED_MOVE_GATE, "ai_fright")
AI_GHOST_MOVE_TRUE = _true_move_ms(AI_GHOST_MOVE_GATE)
AI_GHOST_FRIGHTENED_MOVE_TRUE = _true_move_ms(AI_GHOST_FRIGHTENED_MOVE_GATE)

# Original-game ghost identities and colors. The player's ghost is yellow
# and manually controlled; the four AI allies are the original cast, each
# with its own targeting personality (see ghost_target_tile):
#   Blinky (red)    - chases Pac-Man's exact tile, directly.
#   Pinky (pink)    - ambushes 4 tiles ahead of Pac-Man's facing direction.
#   Inky (cyan)     - targets the point obtained by reflecting Blinky's
#                     position through a spot 2 tiles ahead of Pac-Man.
#   Clyde (orange)  - chases directly like Blinky, but shies off toward his
#                     own corner whenever he gets within CLYDE_SCATTER_RADIUS.
AI_GHOST_PERSONALITIES = ("blinky", "pinky", "inky", "clyde")
AI_GHOST_COLORS = ((255, 0, 0), (255, 184, 255), (0, 255, 255), (255, 184, 82))
PINKY_AMBUSH_DISTANCE = 4
INKY_PIVOT_DISTANCE = 2
CLYDE_SCATTER_RADIUS = 8
CLYDE_SCATTER_CORNER = (1, GRID_H - 2)  # Clyde's classic bottom-left home corner

# The other three AI ghosts' classic home corners, for the global
# scatter/chase cycle below - Clyde already has his own (just above).
BLINKY_SCATTER_CORNER = (GRID_W - 2, 1)  # top-right
PINKY_SCATTER_CORNER = (1, 1)  # top-left
INKY_SCATTER_CORNER = (GRID_W - 2, GRID_H - 2)  # bottom-right
AI_GHOST_SCATTER_CORNERS = (BLINKY_SCATTER_CORNER, PINKY_SCATTER_CORNER, INKY_SCATTER_CORNER, CLYDE_SCATTER_CORNER)

# Matches the original arcade's level-1 scatter/chase cadence: until now
# this game had every AI ghost but Clyde locked into permanent chase the
# whole match, which is a meaningfully harder fight than the real game -
# the original's repeating scatter windows (every ghost retreats to its
# own corner and ignores Pac-Man entirely) are a big part of how players
# rack up the high win rates this project is chasing. Clyde's own
# proximity-based retreat (CLYDE_SCATTER_RADIUS, above) still applies on
# top of this during his chase windows - in the original he's the one
# ghost with both behaviors stacked.
SCATTER_CHASE_SCHEDULE = (
    (7_000, "scatter"),
    (20_000, "chase"),
    (7_000, "scatter"),
    (20_000, "chase"),
    (5_000, "scatter"),
    (20_000, "chase"),
)  # permanent chase once this runs out, same as the original

# ms into the game when SCATTER_CHASE_SCHEDULE runs out for good - after
# this, no more scheduled relief is ever coming for the rest of the
# match. Loss-pattern analysis of 20,000 real games found losses with
# >=70% of the maze already cleared have a *median* death time right at
# this boundary (79,360ms vs this constant's 79,000ms), and over half of
# them happen after it - the endgame isn't dangerous because of pellet
# count, it's dangerous because the scatter windows have run out right
# around when only stragglers are left. Feeds permanent_chase_caution_
# weight/early_rush_weight (see choose_pacman_step).
PERMANENT_CHASE_START = sum(duration for duration, _ in SCATTER_CHASE_SCHEDULE)


def scatter_or_chase(elapsed):
    """Which global phase the AI ghosts are in, this many ms into the
    game (see SCATTER_CHASE_SCHEDULE) - shared by all four AI ghosts,
    like the original arcade."""
    t = elapsed
    for duration, mode in SCATTER_CHASE_SCHEDULE:
        if t < duration:
            return mode
        t -= duration
    return "chase"


def time_until_chase_resumes(elapsed):
    """ms remaining in the current scatter window before the AI ghosts
    resume chasing - 0 if they're already chasing (or no scatter window
    is left in the schedule at all). Feeds scatter_timing_weight (see
    choose_pacman_step): eating a power pellet right as a scatter window
    is about to end covers the most dangerous chase window with the
    most of the frightened effect still intact, instead of wasting it
    mid-scatter when little is actually hunting."""
    t = elapsed
    for duration, mode in SCATTER_CHASE_SCHEDULE:
        if t < duration:
            return (duration - t) if mode == "scatter" else 0
        t -= duration
    return 0

# Clyde keeps Clyde's AI (see above) but is reskinned to a small pixel-art
# crab styled after Claude Code's mascot, "Claw'd". AI_GHOST_SKINS is purely
# cosmetic - it never affects movement/targeting, only which draw_* method
# renders that ghost.
AI_GHOST_SKINS = ("ghost", "ghost", "ghost", "clawd")
CLAWD_BODY_COLOR = (218, 119, 88)  # clay/orange, close to Claw'd's #DA7758
CLAWD_WALK_FRAME_MS = 180  # how fast its legs alternate while scuttling

AI_GHOST_MISTAKE_CHANCE = 0.3  # chance an AI ghost blunders instead of pathing optimally
AI_GHOST_PERCEPTION_INTERVAL = 500  # ms between AI ghosts "noticing" Pac-Man's position


def is_open(x, y):
    """Every non-wall map character is traversable floor."""
    return 0 <= x < GRID_W and 0 <= y < GRID_H and MAZE[y][x] != "#"


def direction_between(old, new):
    """Facing direction from a move, correct even across a tunnel wrap
    (where the raw coordinate delta would otherwise span the whole grid)."""
    dx = new[0] - old[0]
    if dx > 1:
        dx = -1
    elif dx < -1:
        dx = 1
    return (dx, new[1] - old[1])


def step_position(position, direction):
    """Apply one step of movement, wrapping left-right on the tunnel row
    instead of walking off the edge of the grid."""
    x, y = position
    dx, dy = direction
    nx, ny = x + dx, y + dy
    if dy == 0 and y in TUNNEL_ROWS and (nx < 0 or nx >= GRID_W):
        nx %= GRID_W
    return nx, ny


def animated_position(prev, current, move_time, delay, now):
    """Pixel-space center (plus the 0..1 progress fraction) for a character
    gliding smoothly from its previous tile to its current one, instead of
    snapping between them. Falls back to no interpolation for a tunnel wrap
    or a respawn teleport, where the two tiles aren't actually adjacent on
    screen."""
    px, py = prev
    cx, cy = current
    if abs(cx - px) > 1 or abs(cy - py) > 1:
        px, py = cx, cy
    t = 1.0 if delay <= 0 else max(0.0, min(1.0, (now - move_time) / delay))
    fx = px + (cx - px) * t
    fy = py + (cy - py) * t
    return (fx * CELL_SIZE + CELL_SIZE / 2, fy * CELL_SIZE + CELL_SIZE / 2), t


def neighbours(position):
    candidates = (step_position(position, direction) for direction in CARDINALS)
    return [tile for tile in candidates if is_open(*tile)]


def pacman_neighbours(position):
    """Like neighbours(), but the ghost house is off-limits to Pac-Man."""
    return [tile for tile in neighbours(position) if tile not in HOUSE_TILES]


def _flood_fill_distances(start, neighbour_fn):
    """One BFS flood from `start`, tile-step distances to everything
    reachable via `neighbour_fn`. Only used to build the precomputed
    all-pairs tables below - bfs_distances/pacman_bfs_distances look those
    up instead of reflooding on every call."""
    distances = {start: 0}
    queue = deque([start])
    while queue:
        current = queue.popleft()
        for nxt in neighbour_fn(current):
            if nxt not in distances:
                distances[nxt] = distances[current] + 1
                queue.append(nxt)
    return distances


_ALL_OPEN_TILES = frozenset(
    (x, y) for y, row in enumerate(MAZE) for x, char in enumerate(row) if char != "#"
)
_PACMAN_OPEN_TILES = frozenset(t for t in _ALL_OPEN_TILES if t not in HOUSE_TILES)

# Every tile-to-tile distance in the maze, computed once at import time
# instead of reflooding a fresh BFS on every single ghost/Pac-Man move
# decision - the maze layout never changes, so this table never goes
# stale. Profiling a 100-game batch found this re-flooding was by far the
# single largest cost in the whole AI (ghost_cluster_score alone, which
# calls bfs_distances once per ghost every tick, was ~48% of total CPU
# time); bfs_distances/pacman_bfs_distances now just index into these.
# Two separate tables because Pac-Man's own pathfinding graph excludes the
# ghost house entirely (see pacman_neighbours) while the ghosts' doesn't.
GHOST_DISTANCE_TABLE = {start: _flood_fill_distances(start, neighbours) for start in _ALL_OPEN_TILES}
PACMAN_DISTANCE_TABLE = {start: _flood_fill_distances(start, pacman_neighbours) for start in _PACMAN_OPEN_TILES}


def bfs_distances(start):
    # Falls back to a live flood only if asked about a tile that somehow
    # isn't in the precomputed table (never expected in practice, since
    # every real ghost/Pac-Man position is always an open maze tile).
    return GHOST_DISTANCE_TABLE.get(start) or _flood_fill_distances(start, neighbours)


def pacman_bfs_distances(start):
    """Maze distances for Pac-Man's own pathfinding: never routes through,
    or treats as reachable, any tile inside the ghost house."""
    return PACMAN_DISTANCE_TABLE.get(start) or _flood_fill_distances(start, pacman_neighbours)


JUNCTION_MIN_EXITS = 3
# Every open tile with 3+ exits - a real intersection, not just a corridor
# bend - precomputed once since the maze never changes. Used by
# junction_proximity_weight to reward candidate moves that keep Pac-Man
# close to maximum future escape options.
JUNCTION_TILES = frozenset(
    (x, y)
    for y, row in enumerate(MAZE)
    for x, char in enumerate(row)
    if char != "#" and (x, y) not in HOUSE_TILES and len(pacman_neighbours((x, y))) >= JUNCTION_MIN_EXITS
)


def ghost_cluster_score(ghost_positions):
    """1.0 when every ghost is right on top of the others, fading to 0.0 by
    GHOST_CLUSTER_RADIUS maze-tiles apart on average. A power pellet eaten
    while the ghosts are clustered scatters/slows all of them at once
    instead of just whichever one happened to be nearest Pac-Man, so this
    is the signal used to time power pellets well."""
    if len(ghost_positions) < 2:
        return 0.0
    distance_maps = [bfs_distances(g) for g in ghost_positions]
    pairwise = [
        distance_maps[i].get(ghost_positions[j], 10_000)
        for i in range(len(ghost_positions))
        for j in range(i + 1, len(ghost_positions))
    ]
    mean_distance = sum(pairwise) / len(pairwise)
    return max(0.0, (GHOST_CLUSTER_RADIUS - mean_distance) / GHOST_CLUSTER_RADIUS)


def _clamp_to_grid(tile):
    x, y = tile
    return (max(0, min(GRID_W - 1, x)), max(0, min(GRID_H - 1, y)))


def ghost_target_tile(personality, pacman_position, pacman_direction, ghost_position=None, blinky_position=None):
    """Where an AI ghost aims, per its original-game personality. Chasing a
    target tile (rather than always Pac-Man's exact position) is what gives
    each ghost its own character."""
    if personality == "pinky":
        dx, dy = pacman_direction
        tx = pacman_position[0] + dx * PINKY_AMBUSH_DISTANCE
        ty = pacman_position[1] + dy * PINKY_AMBUSH_DISTANCE
        return _clamp_to_grid((tx, ty))

    if personality == "inky":
        # Classic Inky: take the spot 2 tiles ahead of Pac-Man, then
        # reflect Blinky's position through it - an erratic, pincer-like
        # target that depends on where Blinky currently is.
        dx, dy = pacman_direction
        pivot = (pacman_position[0] + dx * INKY_PIVOT_DISTANCE, pacman_position[1] + dy * INKY_PIVOT_DISTANCE)
        bx, by = blinky_position if blinky_position is not None else pacman_position
        return _clamp_to_grid((2 * pivot[0] - bx, 2 * pivot[1] - by))

    if personality == "clyde":
        # Chases directly like Blinky, but loses its nerve and retreats to
        # its own corner once it actually gets close.
        if ghost_position is not None:
            distance = abs(ghost_position[0] - pacman_position[0]) + abs(ghost_position[1] - pacman_position[1])
            if distance <= CLYDE_SCATTER_RADIUS:
                return CLYDE_SCATTER_CORNER
        return pacman_position

    return pacman_position  # blinky: chase the exact tile


# A single constant blend of weights can't express a strategy that
# *changes* with the situation - e.g. chase normally, but switch to
# guarding an exit once Pac-Man is actually cornered. So the 3
# positioning features are each duplicated as a "_cornered" variant that
# is zero unless Pac-Man is genuinely cornered right now - letting the
# fit learn a separate adjustment that only kicks in then, instead of
# that behavior being averaged away by the far more common situations
# where he isn't.
#
# "Cornered" can't just mean "standing in a tile with few maze exits" -
# Pac-Man's own AI actively avoids literal dead ends near a ghost (see
# deadend_penalty), so it almost never visits one; that definition had
# zero real examples to learn from. What actually matters is dynamic:
# are his CURRENT escape routes being covered by a ghost right now? So
# this checks, for Pac-Man's best (least-covered) exit, how close the
# nearest ghost still is to it - if even his safest option is covered,
# he's cornered, regardless of the maze's static exit count there.
CORNER_GHOST_DISTANCE = 16  # squared tiles; a ghost within this of Pac-Man's best exit counts as "covering" it


def _pacman_is_cornered(pacman_position, all_ghost_positions):
    exits = pacman_neighbours(pacman_position)
    if not exits:
        return True
    best_exit_safety = max(
        min((ex - gx) ** 2 + (ey - gy) ** 2 for gx, gy in all_ghost_positions)
        for ex, ey in exits
    )
    return best_exit_safety <= CORNER_GHOST_DISTANCE


_PELLET_TILE_ORDER = []


def pellet_tile_order():
    """Every tile that starts a game holding a pellet (ordinary or power),
    in a fixed order - bit i of a recorded move's "pellets" mask says
    whether tile i still has its ordinary pellet (see
    Game._record_player_ghost_move)."""
    if not _PELLET_TILE_ORDER:
        _PELLET_TILE_ORDER.extend(
            (x, y) for y, row in enumerate(MAZE) for x, char in enumerate(row) if char == "."
        )
    return _PELLET_TILE_ORDER


PLAYER_GHOST_FEATURE_NAMES = (
    "dist_pacman", "dist_lead", "dist_blinky", "dist_pinky", "dist_inky", "dist_clyde",
    "dist_pacman_exit", "reversal",
    "dist_pacman_cornered", "dist_lead_cornered", "dist_pacman_exit_cornered",
    "exit_coverage_margin", "uncovered_exit_count", "power_pellet_guard_distance",
    "frightened_remaining_fraction", "dist_pacman_urgency",
    "straight", "nearest_to_pacman",
    # How a person hunts - see player_ghost_hunt_terms.
    "evasion", "junction_ahead_dist", "junction_ahead_first", "junction_behind_dist", "junction_behind_first",
    "lead3_dist", "lead6_dist", "lead10_dist",
    "nearest_without_reversing", "nearest_at_junction", "straight_at_junction",
    "flee", "flee_near", "straight_frightened",
    "evasion_best", "junction_ahead_nearest", "reversal_closing", "reversal_away", "pellet_mean",
    # Sealing him in (added 2026-11-19, from the games played against the
    # Pac-Man that predicts the player) - see player_ghost_hunt_terms.
    "room_alone", "room_all", "room_alone_best", "room_all_best",
)
_PLAYER_GHOST_ROOM_NAMES = ("room_alone", "room_all", "room_alone_best", "room_all_best")
PLAYER_GHOST_HUNT_NAMES = PLAYER_GHOST_FEATURE_NAMES[18:]
_PLAYER_GHOST_HUNT_INDEX = {name: i for i, name in enumerate(PLAYER_GHOST_HUNT_NAMES)}
_PLAYER_GHOST_NO_HUNT = (0.0,) * len(PLAYER_GHOST_HUNT_NAMES)
_NEXT_JUNCTIONS = {}
_LEAD_TILES = {}
_PELLET_MEANS = [None, -1, {}]  # the pellet set the cached means are for, its size then, {tile: mean}


def _pacman_next_junction(tile, direction):
    """The tile where Pac-Man next has a choice of ways if he carries on
    from `tile` heading `direction`, following a corridor round its bends
    (`tile` itself if he is standing at a choice, or is not moving)."""
    key = (tile, direction)
    found = _NEXT_JUNCTIONS.get(key)
    if found is not None:
        return found
    here = tile
    if direction != (0, 0):
        behind = step_position(here, (-direction[0], -direction[1]))
        for _ in range(40):
            onward = [t for t in pacman_neighbours(here) if t != behind]
            if len(onward) != 1:
                break
            behind, here = here, onward[0]
            if len(pacman_neighbours(here)) >= 3:
                break
    _NEXT_JUNCTIONS[key] = here
    return here


def _pacman_lead_tile(tile, direction, moves):
    """Where Pac-Man is `moves` tiles on if he keeps going: straight while
    he can, round a bend where there is only one way on, stopping where he
    would have to choose."""
    key = (tile, direction, moves)
    found = _LEAD_TILES.get(key)
    if found is not None:
        return found
    here, behind, heading = tile, None, direction
    for _ in range(moves):
        ways = pacman_neighbours(here)
        ahead = step_position(here, heading)
        if heading != (0, 0) and ahead in ways:
            behind, here = here, ahead
            continue
        onward = [t for t in ways if t != behind]
        if len(onward) != 1:
            break
        heading = (onward[0][0] - here[0], onward[0][1] - here[1])
        if abs(heading[0]) + abs(heading[1]) != 1:
            break  # through the tunnel: stop here
        behind, here = here, onward[0]
    _LEAD_TILES[key] = here
    return here


def _pellet_distance_mean(tile, pellets):
    """Average walking distance from `tile` to every pellet still uneaten -
    low means standing in among what Pac-Man still has to come for. Cached
    for as long as it is asked about the same set at the same size (the
    game only ever removes pellets from its set)."""
    if not pellets:
        return 0.0
    cache = _PELLET_MEANS
    if cache[0] is not pellets or cache[1] != len(pellets):
        cache[0], cache[1], cache[2] = pellets, len(pellets), {}
    mean = cache[2].get(tile)
    if mean is None:
        row = PACMAN_DISTANCE_TABLE.get(tile, {})
        mean = cache[2][tile] = sum(row.get(t, 999) for t in pellets) / len(pellets)
    return mean


_PACMAN_HEADINGS = {}
_PLAIN_EVASION = []


def _pacman_heading(pacman_position, pacman_direction):
    """(junction ahead, junction behind, tile straight ahead or None, where
    he will be in 3 / 6 / 10 moves) for Pac-Man on this tile facing this
    way - all fixed by the maze, so worked out once per (tile, direction)."""
    key = (pacman_position, pacman_direction)
    found = _PACMAN_HEADINGS.get(key)
    if found is None:
        if pacman_direction != (0, 0):
            behind = _pacman_next_junction(pacman_position, (-pacman_direction[0], -pacman_direction[1]))
            straight_on = step_position(pacman_position, pacman_direction)
            if straight_on not in PACMAN_DISTANCE_TABLE:
                straight_on = None
        else:
            behind, straight_on = pacman_position, None
        found = _PACMAN_HEADINGS[key] = (
            _pacman_next_junction(pacman_position, pacman_direction), behind, straight_on,
            _pacman_lead_tile(pacman_position, pacman_direction, 3),
            _pacman_lead_tile(pacman_position, pacman_direction, 6),
            _pacman_lead_tile(pacman_position, pacman_direction, 10),
        )
    return found


def _player_ghost_room_terms(candidates, pacman_position, ai_ghost_positions):
    """For each way open to the player ghost: (room_alone, room_all) - how
    many tiles Pac-Man could still reach before the player ghost alone, and
    before any ghost at all, if it went that way (tens of tiles; see
    _room_tables). None when the tables are unavailable."""
    tables = _room_tables()
    if tables is None:
        return None
    np, index, pacman_ms, player_ms, classic_ms = tables
    p = index.get(pacman_position)
    if p is None:
        return None
    pacman_reach = pacman_ms[p]
    classic = None
    for tile in ai_ghost_positions or ():
        i = index.get(tile)
        reach = classic_ms[i] if i is not None else classic_ms[index[HOUSE_EXIT_TARGET]] + 2 * AI_GHOST_MOVE_DELAY
        classic = reach if classic is None else np.minimum(classic, reach)
    free_of_classic = None if classic is None else pacman_reach < classic
    out = []
    for tile in candidates:
        i = index.get(tile)
        if i is None or tile == pacman_position:
            # (Stepping onto Pac-Man's own tile catches him: he has no room
            # left at all. Counted as "the ghost one move behind him" it
            # gave him every tile within ten moves, so the model's biggest
            # input called catching him the worst move it had - and Pac-Man,
            # trusting that, walked up next to the player's ghost.)
            out.append((0.0, 0.0))
            continue
        first = pacman_reach < player_ms[i] + GHOST_MOVE_DELAY
        alone = int(first.sum())
        both = alone if free_of_classic is None else int((first & free_of_classic).sum())
        out.append((alone / 10.0, both / 10.0))
    return out


def player_ghost_hunt_terms(
    position, direction, pacman_position, pacman_direction, options, frightened=False, pellets=None,
    ai_ghost_positions=None, room=True,
):
    """The inputs that describe how a person actually hunts with the ghost,
    one tuple per way open (`options`, directions), in the order of
    PLAYER_GHOST_HUNT_NAMES. All distances are walking distances.

    Fitting the recorded play with only the positional inputs gave a ghost
    that mostly drifted straight on; these are what a person can be seen to
    steer by. Unless frightened:
      evasion                   how long Pac-Man could stay ahead of a plain
                                chaser starting from this way (tens of moves)
      junction_ahead_dist/_first    distance to the junction Pac-Man is
                                heading for; whether the ghost would be
                                there before him
      junction_behind_dist/_first   the same for the junction behind him
      evasion_best, junction_ahead_nearest   yes/no: this way is the best
                                of those open for that input
      reversal_closing/_away    this way is a U-turn, with Pac-Man coming
                                towards the ghost / not
      room_alone, room_all      how much of the maze Pac-Man could still get
                                to first if the ghost went this way - ahead
                                of the player ghost alone, and ahead of
                                every ghost (tens of tiles). A person seals
                                him in: they stand where he has least room,
                                even if that means not following him.
                                `room=False` skips these (they read 0) for a
                                model that does not weight them.
      room_alone_best, room_all_best   yes/no: this way leaves him least
    Always:
      lead3/6/10_dist           distance to where Pac-Man will be in 3, 6
                                and 10 moves if he keeps going
      nearest_without_reversing this is the shortest way to Pac-Man that is
                                not a U-turn
      nearest_at_junction, straight_at_junction   "nearest way" and "carry
                                on" counted only where 3+ ways are open
      pellet_mean               average distance to the pellets left
    Only while frightened:
      flee, flee_near           distance from Pac-Man (the second capped at
                                12 tiles: getting clear matters, how far
                                does not)
      straight_frightened       carrying straight on"""
    table = PACMAN_DISTANCE_TABLE
    to_pacman = table.get(pacman_position)
    if to_pacman is None or not options:
        return [_PLAYER_GHOST_NO_HUNT for _ in options]
    far = 999
    candidates = [step_position(position, d) for d in options]
    rows = [table.get(c) or {} for c in candidates]
    d_pac = [to_pacman.get(c, far) for c in candidates]
    reverse = (-direction[0], -direction[1])
    near_all = min(d_pac)
    near_forward = min([d_pac[k] for k, d in enumerate(options) if d != reverse] or d_pac)
    no_forward = all(d == reverse for d in options)
    junction = 1.0 if len(options) >= 3 else 0.0
    ahead, behind, straight_on, lead3, lead6, lead10 = _pacman_heading(pacman_position, pacman_direction)
    terms = []
    if frightened:
        for k, d in enumerate(options):
            row = rows[k]
            gap = d_pac[k]
            straight = 1.0 if d == direction else 0.0
            terms.append((
                0.0, 0.0, 0.0, 0.0, 0.0,
                float(row.get(lead3, far)), float(row.get(lead6, far)), float(row.get(lead10, far)),
                1.0 if ((no_forward or d != reverse) and gap == near_forward) else 0.0,
                junction if gap == near_all else 0.0, straight * junction,
                float(gap), float(gap if gap < 12 else 12), straight,
                0.0, 0.0, 0.0, 0.0,
                _pellet_distance_mean(candidates[k], pellets),
                0.0, 0.0, 0.0, 0.0,
            ))
        return terms
    if not _PLAIN_EVASION:
        _PLAIN_EVASION.append(_evasion_table(None))
    evasion_row = _PLAIN_EVASION[0].get(pacman_position) or {}
    evasion = [float(min(evasion_row.get(c, 0), 120)) for c in candidates]
    ahead_d = [row.get(ahead, far) for row in rows]
    best_evasion = min(evasion)
    best_ahead = min(ahead_d)
    pacman_to_ahead = to_pacman.get(ahead, far) * PACMAN_MOVE_DELAY
    pacman_to_behind = to_pacman.get(behind, far) * PACMAN_MOVE_DELAY
    closing = 0.0
    if straight_on is not None and table[straight_on].get(position, far) < to_pacman.get(position, far):
        closing = 1.0
    rooms = _player_ghost_room_terms(candidates, pacman_position, ai_ghost_positions) if room else None
    if rooms is not None:
        least_alone = min(r[0] for r in rooms)
        least_all = min(r[1] for r in rooms)
    for k, d in enumerate(options):
        row = rows[k]
        gap = d_pac[k]
        behind_d = row.get(behind, far)
        u_turn = 1.0 if d == reverse else 0.0
        terms.append((
            evasion[k] / 10.0,
            float(ahead_d[k]), 1.0 if ahead_d[k] * GHOST_MOVE_DELAY <= pacman_to_ahead else 0.0,
            float(behind_d), 1.0 if behind_d * GHOST_MOVE_DELAY <= pacman_to_behind else 0.0,
            float(row.get(lead3, far)), float(row.get(lead6, far)), float(row.get(lead10, far)),
            1.0 if ((no_forward or d != reverse) and gap == near_forward) else 0.0,
            junction if gap == near_all else 0.0, (1.0 if d == direction else 0.0) * junction,
            0.0, 0.0, 0.0,
            1.0 if evasion[k] == best_evasion else 0.0, 1.0 if ahead_d[k] == best_ahead else 0.0,
            u_turn * closing, u_turn * (1.0 - closing),
            _pellet_distance_mean(candidates[k], pellets),
        ) + ((0.0, 0.0, 0.0, 0.0) if rooms is None else (
            rooms[k][0], rooms[k][1],
            1.0 if rooms[k][0] == least_alone else 0.0, 1.0 if rooms[k][1] == least_all else 0.0,
        )))
    return terms


def player_ghost_features(
    position, direction, pacman_position, pacman_direction, ai_ghost_positions, candidate_direction,
    pacman_distances=None, power_pellets=None, frightened=False, power_remaining_ms=0, pellets=None,
):
    """Feature vector for one candidate direction out of the player
    ghost's current position: real maze-path distance (not straight-line
    - a tile can look close across a wall it would actually take a long
    detour to reach) from the resulting tile to Pac-Man directly, plus
    straight-line squared distance to a tile a few steps ahead of Pac-Man
    (an ambush-style lead), to each individual AI ghost by name (see
    below), and to the nearest tile Pac-Man could actually escape
    through right now (guarding an exit rather than chasing directly) -
    plus whether this candidate reverses the ghost's current direction,
    a cornered-only copy of the three positioning features (see
    _pacman_is_cornered), exit-containment bookkeeping, power-pellet
    guarding, and frightened-timer awareness (see below). Shared by
    choose_player_ghost_direction (runtime scoring) and
    fit_player_ghost.py (offline fitting from recorded human play), so
    the two can never drift out of sync with each other.

    `pacman_distances` is bfs_distances(pacman_position), the same for
    every candidate direction in one decision - pass it in once rather
    than recomputing a full maze BFS for each of up to 4 candidates.
    `power_pellets`, `frightened` and `power_remaining_ms` are optional
    (default to "no pellets left"/"not frightened"/0) for a caller that
    doesn't track them - see choose_player_ghost_direction. `pellets` is
    the set of ordinary pellets still uneaten (None: not known, the one
    input that needs it reads 0)."""
    candidate = step_position(position, candidate_direction)
    lead_tile = (
        pacman_position[0] + pacman_direction[0] * 4,
        pacman_position[1] + pacman_direction[1] * 4,
    )
    pacman_exits = pacman_neighbours(pacman_position)
    reverse = (-direction[0], -direction[1]) if direction != (0, 0) else None
    if pacman_distances is None:
        pacman_distances = bfs_distances(pacman_position)
    if power_pellets is None:
        power_pellets = []

    dist_pacman = pacman_distances.get(candidate, len(pacman_distances) + 1)
    dist_lead = (candidate[0] - lead_tile[0]) ** 2 + (candidate[1] - lead_tile[1]) ** 2
    dist_pacman_exit = min(
        ((candidate[0] - ex) ** 2 + (candidate[1] - ey) ** 2 for ex, ey in pacman_exits),
        default=0,
    )
    cornered = _pacman_is_cornered(pacman_position, [position, *ai_ghost_positions])

    # Blinky and Pinky reliably hold their side of a box-in; Clyde and
    # Inky sometimes back out of one and let Pac-Man slip through - so
    # their proximity is tracked separately per identity instead of
    # blended into one "nearest ally" number, letting the model lean on
    # the trustworthy two and compensate when only Clyde/Inky are
    # covering a gap.
    blinky_pos, pinky_pos, inky_pos, clyde_pos = ai_ghost_positions
    dist_blinky = (candidate[0] - blinky_pos[0]) ** 2 + (candidate[1] - blinky_pos[1]) ** 2
    dist_pinky = (candidate[0] - pinky_pos[0]) ** 2 + (candidate[1] - pinky_pos[1]) ** 2
    dist_inky = (candidate[0] - inky_pos[0]) ** 2 + (candidate[1] - inky_pos[1]) ** 2
    dist_clyde = (candidate[0] - clyde_pos[0]) ** 2 + (candidate[1] - clyde_pos[1]) ** 2

    # Containment bookkeeping: among Pac-Man's current escape tiles, how
    # many would this candidate itself be close enough to threaten
    # (exit_coverage_margin), and separately how many are covered by
    # NEITHER this candidate NOR any ally (uncovered_exit_count) - the
    # "is it actually safe to commit to the final box-in move yet, or
    # are there still gaps" signal. Same CORNER_GHOST_DISTANCE threshold
    # _pacman_is_cornered already uses for "close enough to cover."
    def _covers(ghost_pos, exit_tile):
        return (ghost_pos[0] - exit_tile[0]) ** 2 + (ghost_pos[1] - exit_tile[1]) ** 2 <= CORNER_GHOST_DISTANCE

    exit_coverage_margin = sum(1 for e in pacman_exits if _covers(candidate, e))
    uncovered_exit_count = sum(
        1 for e in pacman_exits
        if not _covers(candidate, e) and not any(_covers(ally, e) for ally in ai_ghost_positions)
    )

    # Which power pellet Pac-Man looks like he's actually heading for
    # (nearest to his lead point, the same ambush-style point dist_lead
    # uses) - guarding it means positioning so he has to get through
    # this candidate tile to reach it. 10_000 (consistent with the
    # "nothing to target" sentinel pattern used elsewhere in this
    # codebase) once none are left, so an empty board doesn't look like
    # perfect guarding.
    if power_pellets:
        target_pellet = min(
            power_pellets,
            key=lambda p: (p[0] - lead_tile[0]) ** 2 + (p[1] - lead_tile[1]) ** 2,
        )
        power_pellet_guard_distance = (candidate[0] - target_pellet[0]) ** 2 + (candidate[1] - target_pellet[1]) ** 2
    else:
        power_pellet_guard_distance = 10_000

    # How much of Pac-Man's power-pellet window is left, and a
    # closing-distance term that only matters while frightened and grows
    # as the window nears its end - near 0 (no pull to close in yet)
    # right after Pac-Man powers up, rising toward dist_pacman's full
    # value as the timer runs out, so the model has the inputs to learn
    # "hang back early, press the approach right before it expires"
    # instead of treating every frightened tick identically.
    remaining_fraction = 0.0
    if frightened and POWER_DURATION > 0:
        remaining_fraction = max(0.0, min(1.0, power_remaining_ms / POWER_DURATION))
    dist_pacman_urgency = dist_pacman * (1.0 - remaining_fraction) if frightened else 0.0

    # How a person actually steers: mostly carrying on the way they are
    # already going, and at a choice of ways usually taking the one that
    # is the shortest walk to Pac-Man (a yes/no for "this is the nearest
    # of the ways open to me", which a plain distance cannot express).
    nearest = min(
        (
            pacman_distances.get(tile, len(pacman_distances) + 1)
            for tile in (step_position(position, d) for d in CARDINALS)
            if is_open(*tile) and tile not in HOUSE_TILES
        ),
        default=dist_pacman,
    )

    ways = [
        d for d in CARDINALS
        if is_open(*step_position(position, d)) and step_position(position, d) not in HOUSE_TILES
    ]
    if candidate_direction in ways:
        hunt = player_ghost_hunt_terms(
            position, direction, pacman_position, pacman_direction, ways, frightened, pellets, ai_ghost_positions,
        )[ways.index(candidate_direction)]
    else:
        hunt = _PLAYER_GHOST_NO_HUNT

    features = {
        "dist_pacman": dist_pacman,
        "dist_lead": dist_lead,
        "dist_blinky": dist_blinky,
        "dist_pinky": dist_pinky,
        "dist_inky": dist_inky,
        "dist_clyde": dist_clyde,
        "dist_pacman_exit": dist_pacman_exit,
        "reversal": 1.0 if candidate_direction == reverse else 0.0,
        "dist_pacman_cornered": dist_pacman if cornered else 0.0,
        "dist_lead_cornered": dist_lead if cornered else 0.0,
        "dist_pacman_exit_cornered": dist_pacman_exit if cornered else 0.0,
        "exit_coverage_margin": exit_coverage_margin,
        "uncovered_exit_count": uncovered_exit_count,
        "power_pellet_guard_distance": power_pellet_guard_distance,
        "frightened_remaining_fraction": remaining_fraction,
        "dist_pacman_urgency": dist_pacman_urgency,
        "straight": 1.0 if candidate_direction == direction and direction != (0, 0) else 0.0,
        "nearest_to_pacman": 1.0 if dist_pacman <= nearest else 0.0,
    }
    features.update(zip(PLAYER_GHOST_HUNT_NAMES, hunt))
    return features


def choose_player_ghost_direction(
    position, direction, pacman_position, pacman_direction, ai_ghost_positions, weights,
    power_pellets=None, frightened=False, power_remaining_ms=0, pellets=None,
):
    """Pick a direction for the player-controlled ghost using a small
    learned scoring model (see fit_player_ghost.py) fit by imitation from
    recorded human play - the same role move_ai_ghosts' fixed
    personalities play for the AI ghosts, but learned instead of
    hand-picked. Only used when Game.ghost_autoplay_weights is set
    (headless training opting in); real play always uses actual keyboard
    input instead, never this."""
    options = [
        d for d in CARDINALS
        if is_open(*step_position(position, d)) and step_position(position, d) not in HOUSE_TILES
    ]
    if not options:
        return direction

    pacman_distances = bfs_distances(pacman_position)
    best_dir, best_score = options[0], float("-inf")
    for d in options:
        features = player_ghost_features(
            position, direction, pacman_position, pacman_direction, ai_ghost_positions, d, pacman_distances,
            power_pellets=power_pellets, frightened=frightened, power_remaining_ms=power_remaining_ms,
            pellets=pellets,
        )
        score = sum(weights.get(name, 0.0) * value for name, value in features.items())
        if score > best_score:
            best_score, best_dir = score, d
    return best_dir


_PLAYER_GHOST_PLANS = {}


_PLAYER_GHOST_WAYS = {}


def fast_player_ghost_direction(
    position, direction, pacman_position, pacman_direction, ai_ghost_positions, weights,
    power_pellets=None, frightened=False, power_remaining_ms=0, pellets=None,
):
    """Same answer as choose_player_ghost_direction, for use inside
    Pac-Man's lookahead where it is called hundreds of times per decision:
    only the inputs this model actually weights are computed, the parts
    that are the same for every candidate direction are computed once, and
    no per-candidate dict is built. Terms are added in the same order as
    player_ghost_features lists them, so scores and tie-breaks match (a
    scratch check compares the two on thousands of random positions
    whenever either is changed)."""
    ident = id(weights)
    plan = _PLAYER_GHOST_PLANS.get(ident)
    if plan is None or plan[0] is not weights:
        base = tuple(weights.get(name, 0.0) or 0.0 for name in PLAYER_GHOST_FEATURE_NAMES[:18])
        hunt_weights = tuple(
            (i, weights[name]) for i, name in enumerate(PLAYER_GHOST_HUNT_NAMES) if weights.get(name, 0.0) != 0.0
        )
        plan = _PLAYER_GHOST_PLANS[ident] = (
            weights, base, hunt_weights, any(weights.get(name, 0.0) != 0.0 for name in _PLAYER_GHOST_ROOM_NAMES),
        )
    _, base, hunt_weights, wants_room = plan
    (w_pacman, w_lead, w_blinky, w_pinky, w_inky, w_clyde, w_exit, w_reversal, w_pacman_c, w_lead_c, w_exit_c,
     w_coverage, w_uncovered, w_guard, w_fraction, w_urgency, w_straight, w_nearest) = base

    ways = _PLAYER_GHOST_WAYS.get(position)
    if ways is None:
        ways = _PLAYER_GHOST_WAYS[position] = tuple(
            (d, step_position(position, d)) for d in CARDINALS
            if is_open(*step_position(position, d)) and step_position(position, d) not in HOUSE_TILES
        )
    if not ways:
        return direction

    pacman_distances = bfs_distances(pacman_position)
    unreachable = len(pacman_distances) + 1
    lead_x = pacman_position[0] + pacman_direction[0] * 4
    lead_y = pacman_position[1] + pacman_direction[1] * 4
    pacman_exits = _outside_neighbours(pacman_position)
    reverse = (-direction[0], -direction[1]) if direction != (0, 0) else None
    cornered = False
    if w_pacman_c or w_lead_c or w_exit_c:
        cornered = _pacman_is_cornered(pacman_position, [position, *ai_ghost_positions])
    guard_x = guard_y = None
    if w_guard and power_pellets:
        guard_x, guard_y = min(power_pellets, key=lambda t: (t[0] - lead_x) ** 2 + (t[1] - lead_y) ** 2)
    remaining_fraction = 0.0
    if frightened and POWER_DURATION > 0:
        remaining_fraction = max(0.0, min(1.0, power_remaining_ms / POWER_DURATION))
    limit = CORNER_GHOST_DISTANCE
    gaps = [pacman_distances.get(tile, unreachable) for _d, tile in ways]
    nearest = min(gaps)
    moving = direction != (0, 0)
    hunt = None
    if hunt_weights:
        hunt = player_ghost_hunt_terms(
            position, direction, pacman_position, pacman_direction, [d for d, _tile in ways], frightened, pellets,
            ai_ghost_positions, wants_room,
        )
    open_exits = None
    if w_uncovered:
        # Pac-Man's exits that no classic ghost is close enough to cover -
        # the same for every way the player ghost could go.
        open_exits = [
            (ex, ey) for ex, ey in pacman_exits
            if not any((ax - ex) ** 2 + (ay - ey) ** 2 <= limit for ax, ay in ai_ghost_positions)
        ]

    best_dir, best_score = ways[0][0], float("-inf")
    for k, (d, (cx, cy)) in enumerate(ways):
        dist_pacman = gaps[k]
        dist_lead = (cx - lead_x) ** 2 + (cy - lead_y) ** 2
        dist_exit = 0
        if pacman_exits:
            dist_exit = min([(cx - ex) ** 2 + (cy - ey) ** 2 for ex, ey in pacman_exits])
        score = 0
        if w_pacman:
            score += w_pacman * dist_pacman
        if w_lead:
            score += w_lead * dist_lead
        if w_blinky:
            score += w_blinky * ((cx - ai_ghost_positions[0][0]) ** 2 + (cy - ai_ghost_positions[0][1]) ** 2)
        if w_pinky:
            score += w_pinky * ((cx - ai_ghost_positions[1][0]) ** 2 + (cy - ai_ghost_positions[1][1]) ** 2)
        if w_inky:
            score += w_inky * ((cx - ai_ghost_positions[2][0]) ** 2 + (cy - ai_ghost_positions[2][1]) ** 2)
        if w_clyde:
            score += w_clyde * ((cx - ai_ghost_positions[3][0]) ** 2 + (cy - ai_ghost_positions[3][1]) ** 2)
        if w_exit:
            score += w_exit * dist_exit
        if w_reversal:
            score += w_reversal * (1.0 if d == reverse else 0.0)
        if w_pacman_c:
            score += w_pacman_c * (dist_pacman if cornered else 0.0)
        if w_lead_c:
            score += w_lead_c * (dist_lead if cornered else 0.0)
        if w_exit_c:
            score += w_exit_c * (dist_exit if cornered else 0.0)
        if w_coverage:
            score += w_coverage * sum([1 for ex, ey in pacman_exits if (cx - ex) ** 2 + (cy - ey) ** 2 <= limit])
        if w_uncovered:
            score += w_uncovered * sum([1 for ex, ey in open_exits if (cx - ex) ** 2 + (cy - ey) ** 2 > limit])
        if w_guard:
            score += w_guard * (10_000 if guard_x is None else (cx - guard_x) ** 2 + (cy - guard_y) ** 2)
        if w_fraction:
            score += w_fraction * remaining_fraction
        if w_urgency:
            score += w_urgency * (dist_pacman * (1.0 - remaining_fraction) if frightened else 0.0)
        if w_straight:
            score += w_straight * (1.0 if (moving and d == direction) else 0.0)
        if w_nearest:
            score += w_nearest * (1.0 if dist_pacman <= nearest else 0.0)
        if hunt is not None:
            values = hunt[k]
            for index, weight in hunt_weights:
                score += weight * values[index]
        if score > best_score:
            best_score, best_dir = score, d
    return best_dir


_SIMPLE_PLAYER_GHOST_FEATURES = ("dist_pacman", "dist_pacman_exit")
_PLAYER_GHOST_POLICY_KEYS = {}
_PLAYER_GHOST_OPTIONS = {}


def player_ghost_policy_key(weights):
    """(dist_pacman weight, dist_pacman_exit weight) when those are the only
    features this player-ghost model uses - its move then depends on nothing
    but where it and Pac-Man stand, so it can be computed directly (see
    simple_player_ghost_step) and tabulated (see _evasion_table). None for a
    model that uses anything else, or for no model at all."""
    if weights is None:
        return None
    ident = id(weights)
    cached = _PLAYER_GHOST_POLICY_KEYS.get(ident)
    if cached is not None and cached[0] is weights:
        return cached[1]
    simple = all(value == 0 or name in _SIMPLE_PLAYER_GHOST_FEATURES for name, value in weights.items())
    key = (float(weights.get("dist_pacman", 0.0)), float(weights.get("dist_pacman_exit", 0.0))) if simple else None
    _PLAYER_GHOST_POLICY_KEYS[ident] = (weights, key)
    return key


def pursuer_profile(weights):
    """(refuge_gap, leaf_cap) for this player ghost. A model that does
    nothing but follow Pac-Man by the shortest path can be let close and
    eaten at each power pellet; anything that also cuts him off - a model
    with other inputs, or a human - is given a wider berth: Pac-Man heads
    for a power pellet sooner and values a longer lead."""
    key = player_ghost_policy_key(weights)
    follower = key is not None and key[1] == 0
    gap = PURSUER_REFUGE_GAP_FOLLOWER if follower else PURSUER_REFUGE_GAP_CUTTER
    cap = PURSUER_LEAF_CAP_FOLLOWER if follower else PURSUER_LEAF_CAP_CUTTER
    if PURSUER_REFUGE_GAP_OVERRIDE is not None:
        gap = int(PURSUER_REFUGE_GAP_OVERRIDE)
    if PURSUER_LEAF_CAP_OVERRIDE is not None:
        cap = int(PURSUER_LEAF_CAP_OVERRIDE)
    return gap, cap


def simple_player_ghost_step(tile, pacman_tile, key):
    """The tile choose_player_ghost_direction would move to, for a model
    with only the two simple features (same scores, same tie-breaking),
    without building the full feature set."""
    options = _PLAYER_GHOST_OPTIONS.get(tile)
    if options is None:
        options = _PLAYER_GHOST_OPTIONS[tile] = tuple(
            step_position(tile, d) for d in CARDINALS
            if is_open(*step_position(tile, d)) and step_position(tile, d) not in HOUSE_TILES
        )
    if not options:
        return tile
    chase_weight, exit_weight = key
    to_pacman = bfs_distances(pacman_tile)
    unreachable = len(to_pacman) + 1
    exits = pacman_neighbours(pacman_tile) if exit_weight else ()
    best_tile, best_score = options[0], float("-inf")
    for candidate in options:
        score = chase_weight * to_pacman.get(candidate, unreachable)
        if exit_weight:
            cx, cy = candidate
            score += exit_weight * min(((cx - ex) ** 2 + (cy - ey) ** 2 for ex, ey in exits), default=0)
        if score > best_score:
            best_score, best_tile = score, candidate
    return best_tile


HOUSE_EXIT_TARGET = (13, 11)  # open floor just outside the gate
HOUSE_EXIT_DISTANCES = bfs_distances(HOUSE_EXIT_TARGET)  # computed once; the maze never changes


def house_exit_step(position):
    """Deterministic shortest step toward the house exit. A ghost still
    inside the cramped house interior uses this instead of its normal
    chase logic, mistake-chance, or (for the player) manual input - all of
    which could otherwise wander or get stuck in there instead of reliably
    finding the one way out."""
    options = neighbours(position)
    reachable = [tile for tile in options if tile in HOUSE_EXIT_DISTANCES]
    if not reachable:
        return position
    return min(reachable, key=lambda tile: HOUSE_EXIT_DISTANCES[tile])


def record_game_history(game_number, outcome, won, reward, elapsed_ms, pellet_fraction, ghosts_eaten, near_misses):
    """Append one line to HISTORY_FILE for a completed game: win/loss and
    exactly how it ended. JSON-lines, so this is a cheap append rather than
    a rewrite, even across thousands of games."""
    record = {
        "game": game_number,
        "outcome": outcome,
        "outcome_title": GAME_OVER_STYLES[outcome]["title"],
        "won": won,
        "reward": round(reward, 4),
        "elapsed_ms": elapsed_ms,
        "pellet_fraction": round(pellet_fraction, 4),
        "ghosts_eaten": ghosts_eaten,
        "near_misses": near_misses,
        "timestamp": time.time(),
    }
    try:
        with open(HISTORY_FILE, "a") as handle:
            handle.write(json.dumps(record) + "\n")
    except OSError:
        pass


def _current_leaderboard():
    """(path, meta) for every replay currently on disk."""
    entries = []
    try:
        names = [n for n in os.listdir(REPLAY_DIR) if n.endswith(".json")]
    except OSError:
        return entries
    for name in names:
        path = os.path.join(REPLAY_DIR, name)
        try:
            with open(path) as handle:
                data = json.load(handle)
            entries.append((path, data.get("meta", {})))
        except (OSError, ValueError):
            continue
    return entries


def _reconcile_leaderboard(candidate_meta=None):
    """Recompute the kept set across every replay on disk, plus an
    optional not-yet-written candidate (for save_replay to decide with,
    before actually writing it).

    A pinned replay competes in the exact same ranking as everything
    else - if it's good enough to land in the natural top MAX_LEADERBOARD
    by replay_rank_key, it just occupies one of those slots like any
    other, no bonus slot needed. Pinning only matters once better
    replays would have pushed it out: being pinned means it's kept
    anyway, beyond the MAX_LEADERBOARD cap, rather than evicted.

    "formerly_pinned" is a one-way sticky flag set the moment a replay is
    ever pinned (see pacman_replay_browser.toggle_pin_selected) and never
    cleared again - it protects the replay from this automatic rank-based
    eviction even after it's unpinned, so unpinning something never makes
    it vanish on its own; only an explicit delete in the browser removes
    it. A replay that was never pinned has no such protection, so once it
    falls out of the top MAX_LEADERBOARD it's deleted.

    Called from save_replay (with the new candidate) whenever a game
    finishes, and from pacman_replay_browser.py (with no candidate) right
    after a pin is toggled there.

    Returns whether the candidate (if given) made the cut.
    """
    pool = list(_current_leaderboard())
    if candidate_meta is not None:
        pool.append((None, candidate_meta))
    pool.sort(key=lambda pm: replay_rank_key(pm[1]))

    top_slots, overflow = pool[:MAX_LEADERBOARD], pool[MAX_LEADERBOARD:]
    for path, overflow_meta in overflow:
        protected = overflow_meta.get("pinned") or overflow_meta.get("formerly_pinned")
        if path is not None and not protected:
            try:
                os.remove(path)
            except OSError:
                pass

    return candidate_meta is not None and any(path is None for path, _m in top_slots)


def _build_replay_record(game, outcome, won, near_win, reward, elapsed_ms, pellet_fraction, pinned):
    """The on-disk shape of one replay: metadata plus the sparse
    move-event frames and initial pellet layout/eaten-pellet/ghost
    events - pacman_replay_browser.py reconstructs everything in between
    using animated_position(), the same function the live game uses to
    render smooth movement. Shared by save_replay (auto-saved wins/near
    wins, which start unpinned) and save_pinned_replay (a manual save
    from the game-over screen, which starts pinned so it's always kept
    regardless of outcome or rank)."""
    meta = {
        "outcome": outcome,
        "outcome_title": GAME_OVER_STYLES[outcome]["title"],
        "won": won,
        "near_win": near_win,
        "pellet_fraction": round(pellet_fraction, 4),
        "elapsed_ms": elapsed_ms,
        "reward": round(reward, 4),
        "timestamp": time.time(),
        "run": game.run_id,
        "pinned": pinned,
        "formerly_pinned": pinned,
        "name": "",
        "initial_pellets": [list(p) for p in game.replay_initial_pellets],
        "initial_power_pellets": [list(p) for p in game.replay_initial_power_pellets],
    }
    record = {
        "meta": meta,
        "frames": game.replay_frames,
        "pellet_events": game.replay_pellet_events,
        "power_pellet_events": game.replay_power_pellet_events,
        "ghost_eaten_events": game.replay_ghost_eaten_events,
    }
    return meta, record


def save_replay(game, outcome, won, near_win, reward, elapsed_ms, pellet_fraction):
    """Write one completed win/near-win's full move-by-move replay to
    REPLAY_DIR if it makes the cut (see _reconcile_leaderboard). "pinned"
    starts False and "name" starts blank; both are set later by the user
    from pacman_replay_browser.py (X to pin, N to name) and persisted
    back into this same file."""
    meta, record = _build_replay_record(game, outcome, won, near_win, reward, elapsed_ms, pellet_fraction, pinned=False)
    try:
        os.makedirs(REPLAY_DIR, exist_ok=True)
        if not _reconcile_leaderboard(meta):
            return  # didn't make the cut - discard it

        path = os.path.join(REPLAY_DIR, f"replay_{int(meta['timestamp'] * 1000)}_{outcome}.json")
        with open(path, "w") as handle:
            json.dump(record, handle)
    except OSError:
        pass


def save_pinned_replay(game, outcome, won, near_win, reward, elapsed_ms, pellet_fraction):
    """Write any completed game's replay to REPLAY_DIR already pinned -
    for a manual save the player chose to keep from the game-over screen
    (see Game.save_current_replay), regardless of whether it was
    otherwise a win, loss, or near win, and regardless of the
    MAX_LEADERBOARD cap, which pinned replays sit outside entirely."""
    _meta, record = _build_replay_record(game, outcome, won, near_win, reward, elapsed_ms, pellet_fraction, pinned=True)
    try:
        os.makedirs(REPLAY_DIR, exist_ok=True)
        path = os.path.join(REPLAY_DIR, f"replay_{int(record['meta']['timestamp'] * 1000)}_{outcome}.json")
        with open(path, "w") as handle:
            json.dump(record, handle)
    except OSError:
        pass


TRIAL_GAMES = 24  # games averaged together before judging a weight nudge


class PacmanBrain:
    """A tiny persistent learner for Pac-Man's evasion instincts.

    Pac-Man plays TRIAL_GAMES consecutive games per trial and alternates
    between two kinds of trial:

    - A "champion" trial plays the CURRENT weights with zero exploration,
      to get a fresh, honest average for them, which unconditionally
      replaces best_reward.
    - A "challenger" trial plays those same weights plus one small random
      nudge, and is judged against the best_reward that the champion
      trial *just* refreshed; if it wins, the nudge is kept in full and
      becomes the new champion (to be re-measured fresh next cycle).

    Re-measuring the champion every cycle, instead of trusting whatever
    score it happened to record whenever it first won, matters because
    TRIAL_GAMES games is still a small, noisy sample: a lucky run can hand
    a mediocre set of weights an inflated score that then becomes nearly
    impossible for any genuinely better challenger to beat by chance -
    stalling learning indefinitely on a fluke. A stale best_reward isn't
    just slow to improve, it's actively wrong once that happens, since
    every future comparison is being judged against a number that
    doesn't reflect what those weights actually average. Refreshing it
    every cycle keeps the bar honest at the cost of roughly double the
    games per accept/reject decision.

    TRIAL_GAMES was raised from 5 to 8 once the player ghost started
    being driven by the imitation model instead of sitting passive: with
    most games now ending in a loss regardless of the exact weights,
    reward swings between two similar trials are dominated by random
    per-game variance (ghost mistake rolls, starting conditions) rather
    than the weights themselves, at this harder difficulty. A bigger
    sample per judgment trades some trial throughput for a less noisy
    signal about which trial actually played better.

    TRIAL_GAMES was raised again from 8 to 24 after a run showed
    defensive weights (semi_deadend_penalty, junction_proximity_weight,
    occupied_tile_penalty_weight) drifting toward zero even with the
    win-vs-loss reward floor fixed: at a true win rate around 3-4%, an
    8-game trial averages under one win, so most trials still carry zero
    win/loss signal and the comparison is effectively judging "which
    weights lose slightly more gracefully" rather than "which weights
    win more often." 24 games pushes the expected win count per trial
    well above zero, at the cost of roughly 3x fewer accept/reject
    judgments per wall-clock minute.

    Briefly raised from 24 to 120 for the full difficulty (classic ghosts
    at correct speed plus the imitation-learned player ghost), which drove
    the true win rate down around 0.6-1% - P(a 24-game trial has zero
    wins) = 0.99^24 ~ 79% at that rate, so the same zero-signal problem
    was back, worse than before. That didn't pan out either: 3 successive
    30k+-game runs at 120 still showed no real trend (0.6%, 0.8%, 0.3%,
    flat/noisy). Reverted back to 24 alongside switching the player ghost
    back off in train_pacman.py - this value is sized for the ~15-19% win
    rate Pac-Man actually reaches against just the classic ghosts, where
    24 games per trial already keeps the expected win count comfortably
    above zero; it'll need raising again if/when the player ghost comes
    back once Pac-Man is winning reliably without it.

    This is still elitist, not a drifting EMA: a bad run of luck on a
    challenger trial can never move the weights (only a champion trial
    changes best_reward on its own, and it never touches the weights).
    The phase, best reward, and weights are all saved to BRAIN_FILE, so
    what Pac-Man learned carries over into every future run of the game.

    Each challenger trial nudges exactly ONE weight - cycling through all
    of them in a fixed order, one per challenger trial - instead of
    nudging every weight at once. With 32 weights, a full-vector random
    perturbation mixes good and bad nudges into the same trial, so the
    reward signal can't tell which of the 32 changes actually helped: a
    rejected trial teaches nothing about which dimension was the problem,
    and a genuinely useful nudge's own signal gets diluted by 31 others
    jittering at the same time. Coordinate-wise exploration makes every
    accept/reject verdict cleanly attributable to a single weight, at the
    cost of needing roughly 32x more trials to cover every weight once -
    a good trade now that the maze-pathfinding speedup (see
    GHOST_DISTANCE_TABLE/PACMAN_DISTANCE_TABLE) makes trials far cheaper.
    """

    def __init__(self):
        self.weights = dict(LEARNED_WEIGHT_DEFAULTS)
        self.average_reward = 0.0
        self.best_reward = -10.0
        self.games_played = 0
        self._phase = "champion"
        self._coordinate_order = list(self.weights)
        self._coordinate_index = 0
        self._trial_deltas = {}
        self._trial_rewards = []
        self._trial_seeds = []
        self._load()
        self._start_trial()

    def _load(self):
        try:
            with open(BRAIN_FILE, "r") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return
        for key in self.weights:
            if key in data.get("weights", {}):
                self.weights[key] = float(data["weights"][key])
        self.average_reward = float(data.get("average_reward", 0.0))
        self.best_reward = float(data.get("best_reward", -10.0))
        self.games_played = int(data.get("games_played", 0))
        self._phase = data.get("phase", "champion")
        if self._phase not in ("champion", "challenger"):
            self._phase = "champion"
        self._coordinate_index = int(data.get("coordinate_index", 0)) % len(self._coordinate_order)

    def _save(self):
        data = {
            "weights": self.weights,
            "average_reward": self.average_reward,
            "best_reward": self.best_reward,
            "games_played": self.games_played,
            "phase": self._phase,
            "coordinate_index": self._coordinate_index,
        }
        try:
            with open(BRAIN_FILE, "w") as handle:
                json.dump(data, handle, indent=2)
        except OSError:
            pass

    def _start_trial(self):
        """Choose this trial's experimental weights: zero nudge during a
        champion (re-measurement) trial, held fixed for the next
        TRIAL_GAMES games. A challenger trial nudges only the single
        weight currently up in the coordinate cycle - every other weight
        stays exactly at its current value.

        Also fixes the TRIAL_GAMES RNG seeds the trial's games will play
        with. A champion trial draws a fresh set; the challenger trial
        that immediately follows it (same coordinate) reuses that exact
        set unchanged, so both sides of the comparison see the same
        TRIAL_GAMES mazes/ghost-dice draws and only the weight difference
        can move the result. Without this, two equally-good trials differ
        by chance alone (whichever happened to draw more wins), which -
        given the reward spread between a win and a loss - swamps the
        signal from any single small nudge; see the module notes this
        change was made for. A missing or short seed list (e.g. right
        after resuming a challenger trial from a saved brain, which
        doesn't persist the paired champion's seeds) falls back to a
        fresh, merely-unpaired set rather than crashing."""
        self._trial_deltas = {key: 0.0 for key in self.weights}
        if self._phase == "challenger":
            key = self._coordinate_order[self._coordinate_index]
            step = self._exploration_step(key)
            self._trial_deltas[key] = random.uniform(-step, step)
        self._trial_rewards = []
        if self._phase == "champion" or len(self._trial_seeds) < TRIAL_GAMES:
            self._trial_seeds = [random.getrandbits(32) for _ in range(TRIAL_GAMES)]

    def _exploration_step(self, key):
        """How far a challenger trial may nudge `key` this time, annealed
        toward the weight's own current magnitude instead of staying a
        fixed fraction of its bounds range forever.

        LEARNED_WEIGHT_EXPLORATION[key] was sized as roughly 10% of each
        weight's *bounds range* - fine while a weight sits mid-range, but
        once coordinate ascent (correctly) settles a weight down near the
        bottom of a wide range, that fixed step can dwarf the weight's
        own value. occupied_tile_penalty_weight (bounds 0-20000, step
        2000) collapsed from 1678.9 to 173.7 in a single 45-minute run -
        one lucky 24-game challenger trial swung it by more than its own
        magnitude, and the elitist champion/challenger rule has no way to
        walk that back except an equally-lucky trial in the other
        direction. A dozen+ other weights showed the same risk (step at
        least as large as their current value) in the same audit.

        Scaling the step to half the weight's current magnitude fixes
        that - a trial can still move a settled weight meaningfully, just
        not by more than it's already worth - while a floor (5% of the
        original fixed step) keeps weights sitting at/near 0 able to
        explore back upward at a modest, non-destabilizing pace instead
        of being stuck with an effective step of zero. Capped at the
        original fixed step so this never explores *more* aggressively
        than before for a weight that's still genuinely mid-range."""
        base_step = LEARNED_WEIGHT_EXPLORATION[key]
        floor = base_step * WEIGHT_STEP_FLOOR_FRACTION
        relative = abs(self.weights[key]) * WEIGHT_STEP_RELATIVE_FRACTION
        return min(base_step, max(floor, relative))

    def seed_for_next_game(self):
        """The RNG seed the next game should be played with - call
        random.seed(...) with this right before starting the game, so
        the champion/challenger pairing in _start_trial actually lines
        up. Index is the current trial's game count so far (0-based,
        before learn() banks this game's result)."""
        return self._trial_seeds[len(self._trial_rewards)]

    def current_weights(self):
        """The weights Pac-Man should actually play this game with."""
        weights = {}
        for key, value in self.weights.items():
            low, high = LEARNED_WEIGHT_BOUNDS[key]
            weights[key] = min(high, max(low, value + self._trial_deltas.get(key, 0.0)))
        return weights

    def learn(self, reward):
        """Called once a finished game's reward is known. Banks the reward
        into the current trial; once TRIAL_GAMES have been banked, judges
        the trial per the champion/challenger rule above, persists, and
        starts the next trial (flipping phase, and on a resolved
        challenger, advancing to the next weight in the coordinate
        cycle - whether it won or lost, since either way that weight has
        now been tested and it's the next one's turn)."""
        self.games_played += 1
        self._trial_rewards.append(reward)
        if len(self._trial_rewards) < TRIAL_GAMES:
            self._save()  # keep games_played current even mid-trial
            return

        trial_reward = sum(self._trial_rewards) / len(self._trial_rewards)
        self.average_reward += (trial_reward - self.average_reward) * 0.2

        if self._phase == "champion":
            self.best_reward = trial_reward  # unconditional refresh, not a comparison
            self._phase = "challenger"
        else:
            if trial_reward > self.best_reward:
                for key, value in self.weights.items():
                    low, high = LEARNED_WEIGHT_BOUNDS[key]
                    delta = self._trial_deltas.get(key, 0.0)
                    self.weights[key] = min(high, max(low, value + delta))
                self.best_reward = trial_reward
            # else: challenger lost, weights are left exactly as they were.
            self._phase = "champion"
            self._coordinate_index = (self._coordinate_index + 1) % len(self._coordinate_order)

        self._save()
        self._start_trial()


FAR_DANGER_RADIUS = 18  # beyond safe_radius, where the linear danger_far_weight_* terms take over

# How many tiles ahead _pacman_lookahead recurses. Each extra level costs
# roughly (branching factor) times more work - kept modest like every
# other reference implementation of this idea, which all independently
# landed around 10-13 tiles before framerate/throughput suffered.
#
# Two pairs of these instead of one: calling the player ghost's real
# decision function for every node it appears in (see
# _advance_ghosts_toward) is far heavier than the generic greedy-distance
# model every other ghost gets, and pushed worst-case latency per decision
# from ~9ms to ~32-43ms at the old depth 9/budget 600 - trimming to 7/300
# brought that back to a stable ~16-20ms, measured by stress-testing every
# 3+-exit tile in the maze with the player ghost's weights active. But
# that trim only needs to apply when the player ghost is actually being
# predicted that way - with it off (DRIVE_PLAYER_GHOST = False in
# train_pacman.py, or real play's keyboard control), every ghost still
# uses the cheap model, so there's real headroom to search deeper instead
# of leaving it on the conservative setting unnecessarily.
# pacman_lookahead_branch_scores picks between these based on whether
# player_ghost_weights was actually given.
PACMAN_LOOKAHEAD_DEPTH_BASE = 11
PACMAN_LOOKAHEAD_DEPTH_WITH_PLAYER_GHOST = 7

# Hard cap on how many recursive calls one top-level decision may spend
# in total, shared across every candidate branch. Most of the maze is
# narrow corridor, where branching is low and this never gets close to
# binding - but the open lobby around the ghost house has real 3-4 way
# branching, and even a modest depth of that blows up node count fast.
# Once the budget runs out, any branch still being explored is simply cut
# off early rather than left unbounded - a worse, but still real,
# (survived, dots) estimate for whatever's left unexplored, instead of an
# unbounded worst case.
PACMAN_LOOKAHEAD_NODE_BUDGET_BASE = 900
PACMAN_LOOKAHEAD_NODE_BUDGET_WITH_PLAYER_GHOST = 300

# The real AI ghosts don't chase Pac-Man's true position - move_ai_ghosts
# only refreshes ai_perceived_pacman every AI_GHOST_PERCEPTION_INTERVAL
# (500ms), so they're always aiming at a position up to half a second
# stale. The original lookahead ignored this and had every ghost retarget
# onto Pac-Man's exact hypothetical tile on every single tile-ply - a
# worst-case assumption that made it blind to real gaps a stale-perception
# ghost would actually miss. Approximated here in tile-plies rather than
# ms, since the lookahead already treats one recursion level as one
# Pac-Man tile-move: the ghosts "re-sight" Pac-Man roughly once every this
# many plies, not every ply.
AI_GHOST_PERCEPTION_LOOKAHEAD_TICKS = max(1, round(AI_GHOST_PERCEPTION_INTERVAL / AI_GHOST_MOVE_DELAY))

# Straight-line (not maze-true - this is the cheap simulation layer, not
# the real pathfinding) radius within which a ghost counts as "near" a
# power pellet for the ambush bonus below.
LOOKAHEAD_AMBUSH_RADIUS = 6


def _ambush_gain(step, power_pellets, ghost_tuple):
    """How many ghosts are currently close enough to `step` to make eating
    the power pellet there worth detouring for - 0 if `step` isn't even a
    power pellet. Pac-Man's main draw is still pellets in general
    (lookahead_pellet_weight); this is the extra, separately-learned pull
    toward specifically ambushing nearby ghosts with one, scaling with how
    many are in range rather than just whether any are - eating one
    surrounded by three ghosts should visibly outweigh eating one with a
    single distant ghost nowhere near it."""
    if step not in power_pellets:
        return 0
    radius_sq = LOOKAHEAD_AMBUSH_RADIUS ** 2
    return sum(1 for g in ghost_tuple if (g[0] - step[0]) ** 2 + (g[1] - step[1]) ** 2 <= radius_sq)


def _advance_ghosts_toward(
    ghost_tuple, target, frightened, pacman_position=None, pacman_direction=None, player_ghost_weights=None,
    scattering=False,
):
    """One deterministic tile-step for every ghost in `ghost_tuple`, each
    greedily closing distance to `target` (or fleeing it, if frightened).
    This is deliberately a simplified stand-in for the real personality/
    mistake-chance rules in move_ghost/move_ai_ghosts - _pacman_lookahead
    below needs *some* fast, fixed prediction of how ghosts respond to a
    hypothetical future target, not the exact real chase logic. `target`
    is expected to already be the ghosts' *perceived* target (see
    AI_GHOST_PERCEPTION_LOOKAHEAD_TICKS above and _lookahead_perception_tick
    below), not necessarily Pac-Man's real hypothetical tile. Assuming
    every ghost always makes its single best move toward whatever target
    it's using (no mistakes) is still a worst-case-ish assumption given
    that target, which is the right bias for a safety-planning heuristic.
    A ghost still caged in the house is left in place - modeling release
    timing this far ahead isn't worth the complexity for a short lookahead
    window.

    The player ghost (index 0) is handled differently when
    `player_ghost_weights` is given (headless training driving it with
    the fitted imitation model - see Game.ghost_autoplay_weights): unlike
    the other four, it isn't a fixed personality with a formulaic target
    tile, it's itself a learned weighted-scoring function
    (choose_player_ghost_direction), so its real decision function is
    called directly instead of approximated with the generic
    greedy-distance model below - a strictly more accurate prediction for
    the single most dangerous opponent, not just another approximation of
    one. Its own facing direction is passed as (0,0) rather than threaded
    through the recursion, so its one direction-dependent feature
    (discouraging an immediate reversal) never fires in this search -
    everything else it bases its real decision on (distance to Pac-Man,
    the ambush point ahead of him, clustering with its allies, guarding
    his nearest exit) still comes from the real function, not an
    approximation of it. Falls back to the same generic model as every
    other ghost whenever no weights are given (real play's keyboard
    control, or a passive player ghost - see DRIVE_PLAYER_GHOST in
    train_pacman.py).

    `scattering` mirrors the real global scatter/chase cycle (see
    scatter_or_chase/move_ai_ghosts): when true, every AI ghost (index
    1-4, never the player ghost at index 0 - it has no scatter mode)
    heads for its own corner (AI_GHOST_SCATTER_CORNERS) instead of
    `target`, same as they really do during a scatter window."""
    new_positions = []
    for index, ghost in enumerate(ghost_tuple):
        if ghost in HOUSE_TILES:
            new_positions.append(ghost)
            continue
        options = neighbours(ghost)
        if not options:
            new_positions.append(ghost)
            continue
        if index == 0 and player_ghost_weights is not None:
            chosen_direction = choose_player_ghost_direction(
                ghost, (0, 0), pacman_position, pacman_direction, ghost_tuple[1:], player_ghost_weights,
            )
            new_positions.append(step_position(ghost, chosen_direction))
            continue
        if index == 0 and not frightened and pacman_position is not None and PURSUER_STANDIN == "path":
            new_positions.append(_standin_step(ghost, pacman_position))
            continue
        if frightened:
            # Frightened overrides scatter in the real game too (a scared
            # ghost just flees, it doesn't care about its home corner) -
            # still approximated as fleeing `target` (away from Pac-Man),
            # the same worst-case-ish stand-in for "random" used before
            # this change.
            best_tile = max(options, key=lambda t: (t[0] - target[0]) ** 2 + (t[1] - target[1]) ** 2)
        else:
            ghost_target = AI_GHOST_SCATTER_CORNERS[index - 1] if (scattering and index >= 1) else target
            best_tile = min(options, key=lambda t: (t[0] - ghost_target[0]) ** 2 + (t[1] - ghost_target[1]) ** 2)
        new_positions.append(best_tile)
    return tuple(new_positions)


def _lookahead_perception_tick(step, perceived_target, ticks_since_perception):
    """Whether the ghosts' perceived target refreshes to Pac-Man's real
    hypothetical tile (`step`) this ply, or keeps lagging behind on
    `perceived_target` - see AI_GHOST_PERCEPTION_LOOKAHEAD_TICKS. Returns
    (perceived_target_to_use_this_ply, new_ticks_since_perception)."""
    if ticks_since_perception + 1 >= AI_GHOST_PERCEPTION_LOOKAHEAD_TICKS:
        return step, 0
    return perceived_target, ticks_since_perception + 1


def _pacman_lookahead(
    position, prev_position, ghost_tuple, depth, pellets, power_pellets, frightened, memo, budget,
    perceived_target, ticks_since_perception, player_ghost_weights, lookahead_weights, scattering,
):
    """Best (tiles_survived, dots_collected, ambush_opportunities) reachable
    starting at `position` (having just arrived from `prev_position`) with
    ghosts already at `ghost_tuple`, searching `depth` further tiles
    ahead. A pure function of its key inputs - pellets/power_pellets are
    only ever checked for membership here, never mutated across branches,
    so the result is safe to memoize across different paths that reach
    the same state. `budget` is a shared one-element list, decremented
    once per node actually expanded, so one open, high-branching stretch
    of maze can't blow the whole decision's cost past
    PACMAN_LOOKAHEAD_NODE_BUDGET - once it hits zero, every further call
    reports "nothing further explored" instead of recursing, the same as
    hitting depth 0.

    `perceived_target`/`ticks_since_perception` track the ghosts' stale
    sighting of Pac-Man through the search (see
    AI_GHOST_PERCEPTION_LOOKAHEAD_TICKS) - ghosts in this model chase that,
    not whatever tile Pac-Man's hypothetical path is actually on.

    `frightened` is this node's *entering* status - once a branch steps
    onto a power pellet it flips to frightened for the rest of that
    branch's search (the real frightened window, 5 seconds, vastly
    outlasts this whole lookahead horizon, so there's no need to model it
    ever expiring again partway through). It's part of the memo key for
    exactly that reason: two branches can reach the same tile/ghost/depth
    state already-frightened or not, and those are genuinely different
    situations, not safe to conflate.

    Deeper tiers only consider non-reversing moves (like the ghosts'
    own movement rule), unless that's the only option - halves the
    branching factor in every corridor tile, where reversing mid-plan
    was never a real consideration anyway. The very first step (handled
    by pacman_lookahead_branch_scores below, not here) still considers
    every real option Pac-Man can currently take, reversal included."""
    if depth <= 0 or budget[0] <= 0:
        return (0, 0, 0)
    key = (position, prev_position, ghost_tuple, depth, perceived_target, ticks_since_perception, frightened)
    cached = memo.get(key)
    if cached is not None:
        return cached
    budget[0] -= 1

    all_options = pacman_neighbours(position)
    non_reversing = [t for t in all_options if t != prev_position]
    candidates = non_reversing if non_reversing else all_options

    survival_weight, pellet_weight, ambush_weight = lookahead_weights
    best = (0, 0, 0)
    best_value = float("-inf")
    for step in candidates:
        this_tick_target, next_ticks = _lookahead_perception_tick(step, perceived_target, ticks_since_perception)
        becomes_frightened = frightened or (step in power_pellets)
        step_direction = direction_between(position, step)
        new_ghosts = _advance_ghosts_toward(
            ghost_tuple, this_tick_target, becomes_frightened,
            pacman_position=step, pacman_direction=step_direction, player_ghost_weights=player_ghost_weights,
            scattering=scattering,
        )
        dot_gain = 1 if (step in pellets or step in power_pellets) else 0
        ambush_gain = _ambush_gain(step, power_pellets, ghost_tuple)
        if step in new_ghosts:
            candidate = (1, dot_gain, ambush_gain)  # reaches `step` but a ghost arrives there in the same instant
        else:
            sub_survived, sub_dots, sub_ambush = _pacman_lookahead(
                step, position, new_ghosts, depth - 1, pellets, power_pellets, becomes_frightened, memo, budget,
                this_tick_target, next_ticks, player_ghost_weights, lookahead_weights, scattering,
            )
            candidate = (1 + sub_survived, dot_gain + sub_dots, ambush_gain + sub_ambush)
        # Picking the branch with the highest WEIGHTED combination (the
        # same lookahead_survival_weight/lookahead_pellet_weight/
        # lookahead_ambush_weight the top-level score uses), not a pure
        # lexicographic (survived, dots, ambush) tuple max - a flat
        # survival-first tuple comparison always explores for one extra
        # tile of raw survival over any amount of pellets/ambush value,
        # regardless of what the learned weights actually say those are
        # worth (e.g. lookahead_ambush_weight has been fit *higher* than
        # lookahead_survival_weight before, which the old comparison could
        # never actually act on inside the recursion itself).
        survived, dots, ambush = candidate
        value = survival_weight * survived + pellet_weight * dots + ambush_weight * ambush
        if value > best_value:
            best_value = value
            best = candidate
    memo[key] = best
    return best


# The lookahead below simulates the ghosts with the game's real rules and
# real timing instead of the simplified "every ghost steps straight at
# Pac-Man once per Pac-Man tile" model above. Set PACMAN_EXACT_LOOKAHEAD=0
# in the environment to fall back to the old model (for A/B comparison).
EXACT_LOOKAHEAD = os.environ.get("PACMAN_EXACT_LOOKAHEAD", "1") != "0"
EXACT_LOOKAHEAD_DEPTH = int(os.environ.get("PACMAN_EXACT_LOOKAHEAD_DEPTH", "24"))
EXACT_LOOKAHEAD_NODE_BUDGET = int(os.environ.get("PACMAN_EXACT_LOOKAHEAD_BUDGET", "3000"))
# Used instead when the player ghost is driven by a model: every simulated
# move of it calls its real decision function, which costs far more.
EXACT_LOOKAHEAD_DEPTH_PLAYER_GHOST = int(os.environ.get("PACMAN_EXACT_LOOKAHEAD_DEPTH_PG", "32"))
EXACT_LOOKAHEAD_NODE_BUDGET_PLAYER_GHOST = int(os.environ.get("PACMAN_EXACT_LOOKAHEAD_BUDGET_PG", "4000"))


_OUTSIDE_NEIGHBOURS = {}


def _outside_neighbours(tile):
    """Cached pacman_neighbours(): open neighbours outside the ghost house.
    Callers must not mutate the returned list."""
    cached = _OUTSIDE_NEIGHBOURS.get(tile)
    if cached is None:
        cached = _OUTSIDE_NEIGHBOURS[tile] = pacman_neighbours(tile)
    return cached


_STANDIN_HOPS = {}


def _standin_step(tile, pacman_tile, kind=None):
    """Where a player ghost with no known model is assumed to step from
    `tile` with Pac-Man on `pacman_tile` (see PURSUER_STANDIN; `kind`
    overrides it). The "path" step is the same one the route planner uses
    for an unknown ghost."""
    if (kind or PURSUER_STANDIN) != "path":
        options = _outside_neighbours(tile)
        if not options:
            return tile
        x, y = pacman_tile
        return min(options, key=lambda t: (t[0] - x) ** 2 + (t[1] - y) ** 2)
    key = (tile, pacman_tile)
    step = _STANDIN_HOPS.get(key)
    if step is None:
        step = _STANDIN_HOPS[key] = simple_player_ghost_step(tile, pacman_tile, (-1.0, 0.0))
    return step


def _exact_move_time(last, normal_delay, frightened_delay, power_until, gate):
    """First tick at which a ghost that last moved at `last` moves again."""
    if last + frightened_delay < power_until:
        due = last + frightened_delay
    else:
        due = max(last + normal_delay, power_until)
    return max(due, gate)


_PLY_ORDER_ONE_MOVE = (0, 1, 2, 3, 4)
_PLY_ORDER_EXACT_SPEED = (0, 1, 2, 3, 4, 0)


def _exact_ply(position, step, gstate, perception, power_until, now, power_pellets, sim):
    """Pac-Man steps from `position` to `step` at time `now`; then every
    ghost due to move before Pac-Man's next move does so, by the same rules
    move_ghost/move_ai_ghosts use (own personality target, stale perceived
    sighting, scatter schedule, no reversing, house exit, real move delays).
    The 30% random "mistake" moves can't be predicted, so each ghost is
    assumed to make its intended move. Frightened ghosts move randomly in
    the real game and can't hurt Pac-Man, so they are held in place.

    `gstate` is one (tile, previous_tile, last_move_time, has_left_house)
    per ghost, player ghost first. Returns None if Pac-Man dies on arrival,
    else (caught_before_next_move, new_gstate, new_perception,
    new_power_until, ambush_gain)."""
    start = sim["start"]
    if step in power_pellets:
        ambush = _ambush_gain(step, power_pellets, tuple(g[0] for g in gstate))
        power_until = now + POWER_DURATION
    else:
        ambush = 0
    ghosts = list(gstate)
    release = sim["release"]
    for i, ghost in enumerate(ghosts):
        if ghost[0] != step:
            continue
        if i > 0 and now - start < release[i - 1]:
            continue  # still caged
        if now < power_until:
            home = GHOST_START if i == 0 else AI_GHOST_STARTS[i - 1]
            ghosts[i] = (home, home, now + GHOST_RESPAWN_COOLDOWN_MS, False)
        else:
            return None

    near = now < sim.get("near_until", -1)
    end = now + (PACMAN_MOVE_TRUE if near else PACMAN_MOVE_DELAY)
    earliest = now + (FRAME_MS if PACMAN_SIM_FRAMES else 10)
    perceived_tile, perceived_direction, perception_last = perception
    perception_time = perception_last + AI_GHOST_PERCEPTION_INTERVAL
    if perception_time < start + GHOST_HEAD_START:
        perception_time = start + GHOST_HEAD_START
    step_direction = direction_between(position, step)
    step_x, step_y = step
    caught = False
    scatter_cache = {}
    ai_direct = sim["ai_direct"]

    # The player ghost is slightly faster than Pac-Man (180ms a tile against
    # 190ms), so about once in every 18 of Pac-Man's moves it gets two moves
    # in. It is therefore visited a second time at the end of the pass: if
    # its next move is also due before Pac-Man's next one, it makes it.
    # Without that second visit (and with its move time rounded up, as it
    # used to be) the simulated ghost could never gain on Pac-Man, and the
    # search reported "safe" with the real ghost one tile behind.
    order = _PLY_ORDER_EXACT_SPEED if PURSUER_EXACT_SPEED else _PLY_ORDER_ONE_MOVE
    for i in order:
        tile, prev, last, left = ghosts[i]
        if i == 0:
            if sim["player_mode"] == "static":
                continue  # nobody is driving it: it never leaves the house
            if near:
                normal_delay, frightened_delay = GHOST_MOVE_TRUE, GHOST_FRIGHTENED_MOVE_TRUE
            else:
                normal_delay, frightened_delay = GHOST_MOVE_DELAY, GHOST_FRIGHTENED_MOVE_DELAY
            # (An idle player ghost is taken to stand still a while longer: PACMAN_HOLD_FACTOR.)
            gate = max(start + GHOST_HEAD_START, sim.get("player_hold", 0))
        else:
            if near:
                normal_delay, frightened_delay = AI_GHOST_MOVE_TRUE, AI_GHOST_FRIGHTENED_MOVE_TRUE
            else:
                normal_delay, frightened_delay = AI_GHOST_MOVE_DELAY, AI_GHOST_FRIGHTENED_MOVE_DELAY
            gate = start + release[i - 1]
        if last + frightened_delay < power_until:
            due = last + frightened_delay
        else:
            due = last + normal_delay
            if due < power_until:
                due = power_until
        if due < gate:
            due = gate
        if due > end:
            continue
        if i == 0 and PURSUER_EXACT_SPEED:
            if due < now:
                due = now  # it was held up; it moves as soon as it can
        elif due < earliest:
            due = earliest
        frightened = due < power_until

        if not left:
            next_tile = house_exit_step(tile)
        elif frightened:
            ghosts[i] = (tile, prev, due, left)
            continue
        elif i == 0:
            mode = sim["player_mode"]
            if mode == "model" and sim["policy_key"] is not None:
                # This model only looks at where it and Pac-Man stand, so its
                # move is computed directly - the same move the full decision
                # function would pick, at a fraction of the cost per node.
                next_tile = simple_player_ghost_step(tile, step, sim["policy_key"])
                if next_tile == tile:
                    continue
            elif mode == "model" and PURSUER_SIM_FAST:
                # Forced stand-in (PACMAN_PURSUER_SIM=bfs): a plain shortest-
                # path chaser, whatever the model really does.
                to_pacman = PACMAN_DISTANCE_TABLE.get(step)
                options = _outside_neighbours(tile)
                if not options or to_pacman is None:
                    continue
                next_tile = min(options, key=lambda t: to_pacman.get(t, 10_000))
            elif mode == "model" and now >= sim["model_until"]:
                next_tile = None
                if PACMAN_ASSUMED_PLAYER_PAST == "hops":
                    next_tile = _learned_hops()[0].get((tile, step))
                if next_tile is None:
                    next_tile = _standin_step(tile, step, sim.get("standin"))  # past the model's horizon
                if next_tile == tile:
                    continue
            elif mode == "model":
                direction = (choose_player_ghost_direction if PURSUER_SIM == "model" else fast_player_ghost_direction)(
                    tile, direction_between(prev, tile), step, step_direction, [g[0] for g in ghosts[1:]],
                    sim["player_weights"], power_pellets=power_pellets, pellets=sim.get("pellets"),
                )
                next_tile = step_position(tile, direction)
            else:
                next_tile = _standin_step(tile, step, sim.get("standin"))
                if next_tile == tile:
                    continue
        else:
            options = _outside_neighbours(tile)
            if not options:
                continue
            if len(options) == 1:
                next_tile = options[0]
            elif len(options) == 2 and prev in options:
                # Corridor: the only non-reversing move, whatever the target.
                next_tile = options[1] if options[0] == prev else options[0]
            else:
                scattering = scatter_cache.get(due)
                if scattering is None:
                    scattering = scatter_cache[due] = scatter_or_chase(due - start) == "scatter"
                if ai_direct:
                    # Pessimistic pass: every classic ghost makes straight for
                    # where Pac-Man really is, whatever its habits say.
                    target_x, target_y = step_x, step_y
                elif scattering:
                    target_x, target_y = AI_GHOST_SCATTER_CORNERS[i - 1]
                else:
                    if perception_time <= due:
                        seen_tile, seen_direction = step, step_direction
                    else:
                        seen_tile, seen_direction = perceived_tile, perceived_direction
                    target_x, target_y = ghost_target_tile(
                        AI_GHOST_PERSONALITIES[i - 1], seen_tile, seen_direction,
                        ghost_position=tile, blinky_position=ghosts[1][0],
                    )
                next_tile = None
                best_distance = None
                for t in options:
                    if t == prev:
                        continue
                    distance = (t[0] - target_x) ** 2 + (t[1] - target_y) ** 2
                    if best_distance is None or distance < best_distance:
                        best_distance, next_tile = distance, t

        if next_tile == step:
            if frightened:
                home = GHOST_START if i == 0 else AI_GHOST_STARTS[i - 1]
                ghosts[i] = (home, home, due, False)
                continue
            caught = True
        ghosts[i] = (next_tile, tile, due, left or next_tile not in HOUSE_TILES)

    if perception_time <= end:
        perception = (step, step_direction, perception_time)
    return caught, tuple(ghosts), perception, power_until, ambush


def _exact_branch(
    position, step, gstate, perception, power_until, now, depth, pellets, power_pellets, memo, budget,
    lookahead_weights, sim,
):
    """(survived, dots, ambush, leaf_evasion) for stepping from `position`
    to `step`. leaf_evasion is how long Pac-Man could go on staying ahead
    of the player ghost from where this branch ends (see _exact_leaf)."""
    outcome = _exact_ply(position, step, gstate, perception, power_until, now, power_pellets, sim)
    if outcome is None:
        return (0, 0, 0, 0)
    caught, new_gstate, new_perception, new_power_until, ambush_gain = outcome
    dot_gain = 1 if (step in pellets or step in power_pellets) else 0
    if caught:
        return (1, dot_gain, ambush_gain, 0)
    sub_survived, sub_dots, sub_ambush, sub_leaf = _exact_lookahead(
        step, position, new_gstate, new_perception, new_power_until,
        now + (PACMAN_MOVE_TRUE if now < sim.get("near_until", -1) else PACMAN_MOVE_DELAY), depth - 1,
        pellets, power_pellets, memo, budget, lookahead_weights, sim,
    )
    return (1 + sub_survived, dot_gain + sub_dots, ambush_gain + sub_ambush, sub_leaf)


def _exact_leaf(position, gstate, power_until, now, sim):
    """Where a searched branch stops, the search itself knows nothing about
    what comes next. Against an active player ghost, fill that in with the
    evasion table: the stay-ahead time from this end position, so a branch
    that survives the search but ends with the chaser on Pac-Man's heels
    scores below one that ends well clear."""
    if not sim["leaf_weight"]:
        return (0, 0, 0, 0)
    chaser = gstate[0]
    if not chaser[3] or now < power_until:
        return (0, 0, 0, sim["leaf_cap"])  # caged or frightened: no threat at this point
    moves = _evasion_table(sim["evasion_key"]).get(position, {}).get(chaser[0], sim["leaf_cap"])
    return (0, 0, 0, min(moves, sim["leaf_cap"]))


def _exact_lookahead(
    position, prev_position, gstate, perception, power_until, now, depth, pellets, power_pellets, memo, budget,
    lookahead_weights, sim,
):
    if depth <= 0 or budget[0] <= 0:
        return _exact_leaf(position, gstate, power_until, now, sim)
    key = (position, prev_position, gstate, perception, power_until, depth)
    cached = memo.get(key)
    if cached is not None:
        return cached
    budget[0] -= 1
    all_options = _outside_neighbours(position)
    non_reversing = [t for t in all_options if t != prev_position]
    survival_weight, pellet_weight, ambush_weight = lookahead_weights
    leaf_weight = sim["leaf_weight"]
    best = (0, 0, 0, 0)
    best_value = float("-inf")
    for step in (non_reversing if non_reversing else all_options):
        candidate = _exact_branch(
            position, step, gstate, perception, power_until, now, depth, pellets, power_pellets, memo, budget,
            lookahead_weights, sim,
        )
        value = (
            survival_weight * candidate[0] + pellet_weight * candidate[1] + ambush_weight * candidate[2]
            + leaf_weight * candidate[3]
        )
        if value > best_value:
            best_value = value
            best = candidate
    memo[key] = best
    return best


_ROOM_TABLES = False


def _room_tables():
    """(numpy, tile -> index, Pac-Man's / the player ghost's / a classic
    ghost's travel time in ms between every pair of tiles), built once.
    None without numpy."""
    global _ROOM_TABLES
    if _ROOM_TABLES is not False:
        return _ROOM_TABLES
    try:
        import numpy as np
    except ImportError:
        _ROOM_TABLES = None
        return None
    tiles = sorted(PACMAN_DISTANCE_TABLE)
    index = {tile: i for i, tile in enumerate(tiles)}
    steps = np.full((len(tiles), len(tiles)), 10_000, dtype=np.int64)
    for tile, row in PACMAN_DISTANCE_TABLE.items():
        i = index[tile]
        for other, distance in row.items():
            steps[i, index[other]] = distance
    _ROOM_TABLES = (np, index, steps * PACMAN_MOVE_DELAY, steps * GHOST_MOVE_DELAY, steps * AI_GHOST_MOVE_DELAY)
    return _ROOM_TABLES


def _room_setup(ghost_positions, left_house):
    """What _room needs for one decision: for every tile, the soonest any
    ghost could be there - the player ghost and the classic ghosts each at
    their own speed, by the shortest way, wherever they are actually
    heading (a classic ghost still in the house is counted from the door,
    two moves late). None when the tables are unavailable."""
    tables = _room_tables()
    if tables is None:
        return None
    np, index, pacman_ms, player_ms, classic_ms = tables
    soonest = None
    for slot, tile in enumerate(ghost_positions):
        if slot and PURSUER_ROOM_MODE == "player":
            break
        i = index.get(tile) if left_house[slot] else None
        if slot == 0:
            if i is None:
                continue
            reach = player_ms[i]
        elif i is None:
            reach = classic_ms[index[HOUSE_EXIT_TARGET]] + 2 * AI_GHOST_MOVE_DELAY
        else:
            reach = classic_ms[i]
        soonest = reach if soonest is None else np.minimum(soonest, reach)
    if soonest is None:
        return None
    return index, pacman_ms, soonest


def _room(setup, tile):
    """How many tiles Pac-Man, standing on `tile`, would reach before any
    ghost could."""
    index, pacman_ms, soonest = setup
    i = index.get(tile)
    if i is None:
        return 0
    return int((pacman_ms[i] < soonest).sum())


def _adversary_values(position, options, ghost_positions, power_pellets, perceived_pacman_position, sim_state):
    """{step: moves} - how many of his next moves Pac-Man is certain to
    survive if he starts with `step`, when the player ghost may do anything
    at all (every one of its possible moves is tried and the worst for him
    taken) and he then answers as well as he can, U-turns included. The
    classic ghosts move by their usual habits, exactly as in the main
    look-ahead.

    This deliberately knows nothing about what the player is LIKELY to do -
    the main look-ahead covers that. It answers one question: is this move
    only safe if the player's ghost behaves? A move that reaches a power
    pellet counts as safe from there on.

    Searched in stages - 4 moves deep, then 8, and so on up to
    PURSUER_ADVERSARY_DEPTH - within a budget of PURSUER_ADVERSARY_BUDGET
    positions, and the answer is the deepest stage that was finished: a
    half-searched stage would count whatever it had not reached as safe,
    which is exactly the wrong way round. Every move scores the same (so
    this has no say) while the player ghost is caged, frightened, or too
    far off to reach him within the depth."""
    depth = PURSUER_ADVERSARY_DEPTH
    now = sim_state["now"]
    pursuer = ghost_positions[0]
    if (
        not sim_state["left_house"][0] or now < sim_state["power_until"]
        or PACMAN_DISTANCE_TABLE[position].get(pursuer, 10_000) > 2 * depth + 2
    ):
        return {step: depth for step in options}
    # The player ghost's own slot is a stand-in that never moves: its moves
    # are tried one by one below instead.
    gstate = ((GHOST_START, GHOST_START, 0, False),) + tuple(zip(
        ghost_positions[1:], sim_state["ghost_prev"][1:], sim_state["ghost_last"][1:], sim_state["left_house"][1:],
    ))
    perception = (
        perceived_pacman_position if perceived_pacman_position is not None else position,
        sim_state["perceived_direction"], sim_state["perception_last"],
    )
    sim = {"start": sim_state["start"], "release": sim_state["release"], "player_mode": "static", "ai_direct": False}
    plies, budget = {}, [PURSUER_ADVERSARY_BUDGET]
    answer = None
    stage = 0
    while stage < depth:
        stage = min(stage + 4, depth)
        memo = {}
        values = {}
        for step in options:
            values[step] = _adversary_branch(
                position, step, pursuer, sim_state["ghost_last"][0], gstate, perception, sim_state["power_until"], now,
                stage, power_pellets, memo, plies, budget, sim,
            )
            if budget[0] <= 0:
                break
        if budget[0] <= 0:
            break  # unfinished: keep the last finished stage
        answer = values
        if max(values.values()) < stage:
            break  # no way lasts even this long: searching deeper cannot change these
    if answer is None:
        return {step: depth for step in options}
    return answer


def _adversary_branch(
    position, step, pursuer, pursuer_last, gstate, perception, power_until, now, depth, power_pellets, memo, plies,
    budget, sim,
):
    """Moves Pac-Man is sure to survive, stepping from `position` to `step`
    at `now` with the player ghost on `pursuer` (last moved at
    `pursuer_last`), at most `depth`."""
    if pursuer == step:
        return 0
    key = (position, step, gstate, perception, now)
    outcome = plies.get(key, False)
    if outcome is False:
        outcome = plies[key] = _exact_ply(position, step, gstate, perception, power_until, now, power_pellets, sim)
    if outcome is None or outcome[0]:
        return 0  # a classic ghost has him
    if step in power_pellets:
        return depth
    if depth <= 1:
        return 1
    end = now + PACMAN_MOVE_DELAY
    gate = sim["start"] + GHOST_HEAD_START
    # Everything the player ghost could do before Pac-Man moves again: one
    # move, now and then two (it is the faster of the pair), or none.
    replies = []
    due = max(pursuer_last + GHOST_MOVE_DELAY, gate)
    if due > end:
        replies.append((pursuer, pursuer_last))
    else:
        if due < now:
            due = now
        second = due + GHOST_MOVE_DELAY
        for first_tile in _outside_neighbours(pursuer):
            if first_tile == step:
                return 0
            if second > end:
                replies.append((first_tile, due))
            else:
                for second_tile in _outside_neighbours(first_tile):
                    if second_tile == step:
                        return 0
                    replies.append((second_tile, second))
        if not replies:
            replies.append((pursuer, pursuer_last))
    # The reply that brings it nearest is tried first: it is the likeliest
    # to cut the search short. One that leaves it too far away to reach him
    # in the moves that remain cannot matter and is not followed.
    to_step = PACMAN_DISTANCE_TABLE[step]
    reach = 2 * (depth - 1) + 2
    if len(replies) > 1:
        replies = sorted(set(replies), key=lambda reply: to_step.get(reply[0], 10_000))
    worst = depth
    for tile, last in replies:
        if to_step.get(tile, 10_000) > reach:
            break  # sorted: the rest are farther still
        value = 1 + _adversary_node(
            step, tile, last, outcome[1], outcome[2], power_until, end, depth - 1, power_pellets, memo, plies, budget, sim,
        )
        if value < worst:
            worst = value
            if worst <= 1:
                break
    return worst


def _adversary_node(
    position, pursuer, pursuer_last, gstate, perception, power_until, now, depth, power_pellets, memo, plies, budget, sim,
):
    """Pac-Man to move from `position`: the most moves he can be sure of."""
    if depth <= 0 or budget[0] <= 0:
        return depth
    key = (position, pursuer, pursuer_last, gstate, perception, depth)
    cached = memo.get(key)
    if cached is not None:
        return cached
    budget[0] -= 1
    # Ways leading away from the ghost first: if one of them is safe for the
    # whole depth there is no need to look at the others.
    from_ghost = PACMAN_DISTANCE_TABLE.get(pursuer, {})
    best = 0
    for step in sorted(_outside_neighbours(position), key=lambda t: -from_ghost.get(t, 0)):
        value = _adversary_branch(
            position, step, pursuer, pursuer_last, gstate, perception, power_until, now, depth, power_pellets, memo,
            plies, budget, sim,
        )
        if value > best:
            best = value
            if best >= depth:
                break
    memo[key] = best
    return best


def pacman_lookahead_branch_scores(
    position, options, ghost_positions, pellets, power_pellets, frightened, perceived_pacman_position=None,
    player_ghost_weights=None,
    lookahead_weights=(1.0, 0.0, 0.0),
    scattering=False,
    sim_state=None,
):
    """(survived, dots, ambush_opportunities) for each of Pac-Man's
    immediate candidate moves (`options`, already computed by the caller)
    from `position` - the best achievable continuation over the next
    PACMAN_LOOKAHEAD_DEPTH tiles for actually taking that step. One
    shared memo dict and node budget across every branch of this single
    decision, since different candidate paths through a maze often
    revisit the same tile/ghost-configuration, and the cost of exploring
    all of them together needs one combined cap.

    `perceived_pacman_position` is the AI ghosts' actual current (already
    stale) sighting of Pac-Man (Game.ai_perceived_pacman) - the ghosts in
    this search keep chasing that until it's time for their next re-sight,
    not Pac-Man's real position. Falls back to `position` itself (no lag
    modeled yet this decision) for a standalone caller that doesn't track
    it.

    `player_ghost_weights` is Game.ghost_autoplay_weights - when given,
    the player ghost's predicted moves in this search come from its own
    real decision function instead of the generic model the other four
    get (see _advance_ghosts_toward), at a real cost that also narrows
    the depth/budget this search runs at - see
    PACMAN_LOOKAHEAD_DEPTH_WITH_PLAYER_GHOST above.

    `lookahead_weights` is (lookahead_survival_weight, lookahead_pellet_weight,
    lookahead_ambush_weight) - the same three weights the top-level score
    in choose_pacman_step applies to this function's own output. Passed
    through to every recursive call so the search picks each branch's best
    continuation by that same weighted combination, not a pure
    (survived, dots, ambush) tuple comparison - see the comment where it's
    used in _pacman_lookahead for why that distinction matters. Defaults
    to pure survival-first (matching the search's old, weight-blind
    behavior) for the sake of any other caller that doesn't pass real
    weights.

    `scattering` is threaded straight through to _advance_ghosts_toward -
    during a real scatter window the AI ghosts it simulates should be
    heading for their own corners, not chasing, for the same reason
    choose_pacman_step's predicted_target_avoidance feature needs it."""
    if EXACT_LOOKAHEAD and sim_state is not None:
        sim = {
            "start": sim_state["start"],
            "release": sim_state["release"],
            "player_mode": sim_state["player_mode"],
            "player_weights": player_ghost_weights,
            "standin": sim_state.get("standin"),
            "model_until": sim_state["now"] + sim_state.get("model_horizon", 0) * PACMAN_MOVE_DELAY
            if sim_state.get("model_horizon") else float("inf"),
            "pellets": pellets,
            "near_until": sim_state["now"] + PACMAN_NEAR_REAL_MS if PACMAN_NEAR_REAL_MS > 0 else -1,
            "leaf_weight": sim_state.get("leaf_weight", 0.0),
            "ai_direct": sim_state.get("ai_direct", False),
            "evasion_key": _evasion_key(player_ghost_weights),
            "leaf_cap": pursuer_profile(player_ghost_weights)[1],
            "policy_key": None if PURSUER_SIM == "model" else player_ghost_policy_key(player_ghost_weights),
            "player_hold": sim_state.get("hold_until", 0),
        }
        gstate = tuple(zip(ghost_positions, sim_state["ghost_prev"], sim_state["ghost_last"], sim_state["left_house"]))
        perception = (
            perceived_pacman_position if perceived_pacman_position is not None else position,
            sim_state["perceived_direction"], sim_state["perception_last"],
        )
        if player_ghost_weights is not None:
            depth, node_budget = EXACT_LOOKAHEAD_DEPTH_PLAYER_GHOST, EXACT_LOOKAHEAD_NODE_BUDGET_PLAYER_GHOST
        else:
            depth, node_budget = EXACT_LOOKAHEAD_DEPTH, EXACT_LOOKAHEAD_NODE_BUDGET
        depth = min(depth, sim_state.get("max_depth", depth))
        memo = {}
        budget = [node_budget]
        return {
            step: _exact_branch(
                position, step, gstate, perception, sim_state["power_until"], sim_state["now"], depth,
                pellets, power_pellets, memo, budget, lookahead_weights, sim,
            )
            for step in options
        }

    ghost_tuple = tuple(ghost_positions)
    if player_ghost_weights is not None:
        depth = PACMAN_LOOKAHEAD_DEPTH_WITH_PLAYER_GHOST
        node_budget = PACMAN_LOOKAHEAD_NODE_BUDGET_WITH_PLAYER_GHOST
    else:
        depth = PACMAN_LOOKAHEAD_DEPTH_BASE
        node_budget = PACMAN_LOOKAHEAD_NODE_BUDGET_BASE
    memo = {}
    budget = [node_budget]
    scores = {}
    initial_perceived = perceived_pacman_position if perceived_pacman_position is not None else position
    for step in options:
        this_tick_target, next_ticks = _lookahead_perception_tick(step, initial_perceived, 0)
        becomes_frightened = frightened or (step in power_pellets)
        step_direction = direction_between(position, step)
        new_ghosts = _advance_ghosts_toward(
            ghost_tuple, this_tick_target, becomes_frightened,
            pacman_position=step, pacman_direction=step_direction, player_ghost_weights=player_ghost_weights,
            scattering=scattering,
        )
        dot_gain = 1 if (step in pellets or step in power_pellets) else 0
        ambush_gain = _ambush_gain(step, power_pellets, ghost_tuple)
        if step in new_ghosts:
            scores[step] = (1, dot_gain, ambush_gain)
        else:
            sub_survived, sub_dots, sub_ambush = _pacman_lookahead(
                step, position, new_ghosts, depth - 1, pellets, power_pellets, becomes_frightened,
                memo, budget, this_tick_target, next_ticks, player_ghost_weights, lookahead_weights, scattering,
            )
            scores[step] = (1 + sub_survived, dot_gain + sub_dots, ambush_gain + sub_ambush)
    return scores


_EVASION_TABLES = {}
_EVASION_GRAPH = False
_REACH_TABLES = {}
_REACH_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "evasion_cache")
_REACH_HORIZON = 72


def _evasion_graph():
    """Maze as index arrays for the chase tables below, built once:
    (numpy, tiles, tile->index, count, hop, neighbour_slots, ghost_axis),
    where hop[g, p] is the tile a ghost on g steps to when chasing Pac-Man
    on p. None without numpy."""
    global _EVASION_GRAPH
    if _EVASION_GRAPH is not False:
        return _EVASION_GRAPH
    try:
        import numpy as np
    except ImportError:
        _EVASION_GRAPH = None
        return None
    tiles = sorted(PACMAN_DISTANCE_TABLE)
    index = {tile: i for i, tile in enumerate(tiles)}
    count = len(tiles)
    hop = np.zeros((count, count), dtype=np.int32)
    for p_tile in tiles:
        to_pacman = PACMAN_DISTANCE_TABLE[p_tile]
        p = index[p_tile]
        for g_tile in tiles:
            options = [t for t in pacman_neighbours(g_tile) if t in to_pacman]
            hop[index[g_tile], p] = index[min(options, key=to_pacman.get)] if options else index[g_tile]
    neighbour_slots = np.full((4, count), -1, dtype=np.int32)
    for tile in tiles:
        for slot, other in enumerate(pacman_neighbours(tile)[:4]):
            neighbour_slots[slot, index[tile]] = index[other]
    ghost_axis = np.arange(count, dtype=np.int32)[None, :]
    _EVASION_GRAPH = (np, tiles, index, count, hop, neighbour_slots, ghost_axis)
    return _EVASION_GRAPH


def _reach_table(goal):
    """reach[pacman_index, ghost_index]: can Pac-Man get to `goal` (a power
    pellet) before a perfect chaser catches him, by ANY route, arriving with
    the chaser still at least PURSUER_REACH_MARGIN tiles from the pellet?
    Solved exactly, like _evasion_table, and pessimistically (the chaser's
    extra move comes on the very first step). Returns (reach, tile->index),
    or None without numpy. Cached in memory and in evasion_cache/, since
    power pellets land on different tiles every game."""
    cached = _REACH_TABLES.get(goal)
    if cached is not None:
        return cached
    graph = _evasion_graph()
    if graph is None or goal not in graph[2]:
        return None
    np, tiles, index, count, hop, neighbour_slots, ghost_axis = graph
    path = os.path.join(_REACH_CACHE_DIR, f"reach_m{PURSUER_REACH_MARGIN}_{goal[0]}_{goal[1]}.npy")
    reach = None
    try:
        reach = np.unpackbits(np.load(path))[:count * count].reshape(count, count).astype(bool)
    except (OSError, ValueError):
        pass
    if reach is None:
        to_goal = PACMAN_DISTANCE_TABLE[goal]
        arrived = np.zeros((count, count), dtype=bool)
        arrived[index[goal], :] = [to_goal.get(t, 0) >= PURSUER_REACH_MARGIN for t in tiles]
        reach = arrived.copy()
        for t in range(_REACH_HORIZON - 1, -1, -1):
            new = arrived.copy()
            for slot in range(4):
                target = neighbour_slots[slot]
                valid = (target >= 0)[:, None]
                step_to = np.where(target >= 0, target, 0)[:, None]
                ghost = np.broadcast_to(ghost_axis, (count, count))
                caught = ghost == step_to
                for _ in range(2 if t % 18 == 0 else 1):
                    ghost = hop[ghost, step_to]
                    caught = caught | (ghost == step_to)
                new |= valid & ~caught & reach[step_to, ghost]
            reach = new
        try:
            os.makedirs(_REACH_CACHE_DIR, exist_ok=True)
            scratch = f"{path}.{os.getpid()}.tmp.npy"
            np.save(scratch, np.packbits(reach))
            os.replace(scratch, path)
        except OSError:
            pass
    _REACH_TABLES[goal] = (reach, index)
    return _REACH_TABLES[goal]


def _covered(tile, pursuer, power_pellets):
    """Is some power pellet still reachable from `tile` before a chaser on
    `pursuer` catches Pac-Man? (False when the tables are unavailable.)"""
    for pellet in power_pellets:
        table = _reach_table(pellet)
        if table is not None:
            reach, index = table
            a, b = index.get(tile), index.get(pursuer)
            if a is not None and b is not None and reach[a, b]:
                return True
    return False


def _evasion_table(policy_key=None):
    """How many moves Pac-Man can stay ahead of a perfect chaser, for every
    (Pac-Man tile, ghost tile) pair: {pacman_tile: {ghost_tile: moves}}.
    `policy_key` (see player_ghost_policy_key) makes it that particular
    player-ghost model's table instead of the plain chaser's.

    The chaser always steps along the shortest path to Pac-Man at the player
    ghost's real speed (19 moves for every 18 of Pac-Man's). Solved exactly
    by working backwards from the horizon: the value of a position is one
    move plus the best value among the tiles Pac-Man can step to, after the
    ghost has made its reply. Other ghosts are not part of this - the
    lookahead handles those. Built once, on first use; needs numpy, and
    returns {} (feature off) without it."""
    cached = _EVASION_TABLES.get(policy_key)
    if cached is not None:
        return cached
    if policy_key is not None and policy_key[0] == "min":
        # The more cautious of the two estimates: this ghost's own model, and
        # a plain shortest-path chaser.
        own, plain = _evasion_table(policy_key[1]), _evasion_table(None)
        _EVASION_TABLES[policy_key] = {
            p_tile: {g_tile: min(moves, plain[p_tile][g_tile]) for g_tile, moves in row.items()}
            for p_tile, row in own.items()
        }
        return _EVASION_TABLES[policy_key]
    graph = _evasion_graph()
    if graph is None:
        _EVASION_TABLES[policy_key] = {}
        return _EVASION_TABLES[policy_key]
    np, tiles, index, count, hop, neighbour_slots, ghost_axis = graph
    if policy_key is not None:
        # This ghost's own moves instead of a plain shortest-path chase.
        hop = np.zeros((count, count), dtype=np.int32)
        for p_tile in tiles:
            p = index[p_tile]
            for g_tile in tiles:
                hop[index[g_tile], p] = index.get(simple_player_ghost_step(g_tile, p_tile, policy_key), index[g_tile])
    horizon = PURSUER_EVASION_HORIZON + 18
    ghost_moves, clock = [], 0
    mine, theirs = (PACMAN_MOVE_TRUE, GHOST_MOVE_TRUE) if PACMAN_EVASION_TRUE else (PACMAN_MOVE_DELAY, GHOST_MOVE_DELAY)
    for _ in range(horizon):
        clock += mine
        ghost_moves.append(int(clock // theirs))
        clock %= theirs
    value = np.zeros((count, count), dtype=np.int32)
    worst = None
    for t in range(horizon - 1, -1, -1):
        best = np.zeros((count, count), dtype=np.int32)
        for slot in range(4):
            target = neighbour_slots[slot]
            valid = (target >= 0)[:, None]
            step_to = np.where(target >= 0, target, 0)[:, None]
            ghost = np.broadcast_to(ghost_axis, (count, count))
            caught = ghost == step_to
            for _ in range(ghost_moves[t]):
                ghost = hop[ghost, step_to]
                caught = caught | (ghost == step_to)
            result = np.where(caught | ~valid, 0, 1 + value[step_to, ghost])
            best = np.maximum(best, result)
        value = best
        if t < 18:
            worst = value if worst is None else np.minimum(worst, value)
    rows = worst.tolist()
    _EVASION_TABLES[policy_key] = {
        p_tile: dict(zip(tiles, rows[index[p_tile]])) for p_tile in tiles
    }
    return _EVASION_TABLES[policy_key]


_WORST_TABLE = False
_WORST_HORIZON = 260
_WORST_GUARANTEES = {}
_WORST_REPLIES = {}
_WORST_DEAD_STEPS = set()  # filled by _worst_case_marks: see PACMAN_WORST_DEAD


def _worst_case_table():
    """(rows, tile -> index): rows[pacman index][ghost index] is how many
    more moves Pac-Man can be sure of surviving a lone player ghost that
    plays perfectly (may go any way, may stand still), with no power pellet
    and no classic ghost in it - the one-on-one chase solved exactly by
    working backwards from the horizon, each side choosing its best move
    (Pac-Man the largest, the ghost the smallest). The ghost's extra move -
    one for every eleven of his at the real frame times - is taken at
    whichever point in that cycle is worst for him. Built once (about two
    seconds) and kept in evasion_cache/; None without numpy."""
    global _WORST_TABLE
    if _WORST_TABLE is not False:
        return _WORST_TABLE
    graph = _evasion_graph()
    if graph is None:
        _WORST_TABLE = None
        return None
    np, tiles, index, count, _hop, neighbour_slots, _ghost_axis = graph
    pacman_frames = int(round(PACMAN_MOVE_TRUE / FRAME_MS))
    ghost_frames = int(round(GHOST_MOVE_TRUE / FRAME_MS))
    path = os.path.join(_REACH_CACHE_DIR, f"worst_{pacman_frames}_{ghost_frames}_{count}.npy")
    worst = None
    try:
        worst = np.load(path)
        if worst.shape != (count, count):
            worst = None
    except (OSError, ValueError):
        worst = None
    if worst is None:
        own = np.arange(count, dtype=np.int32)
        ghost_ways = np.vstack([neighbour_slots, own[None, :]])
        ghost_ways = np.where(ghost_ways >= 0, ghost_ways, own[None, :])  # a missing way: stand still
        replies, clock = [], 0
        for _ in range(_WORST_HORIZON):
            clock += pacman_frames
            replies.append(clock // ghost_frames)
            clock %= ghost_frames
        value = np.zeros((count, count), dtype=np.int16)
        kept = []
        for t in range(_WORST_HORIZON - 1, -1, -1):
            best = np.zeros((count, count), dtype=np.int16)
            for slot in range(4):
                target = neighbour_slots[slot]
                valid = target >= 0
                step_to = np.where(valid, target, 0)
                after = value[step_to, :] + 1
                after[own, step_to] = 0  # the ghost is standing on the tile he steps to
                for _ in range(replies[t]):
                    after = np.minimum.reduce([after[:, ghost_ways[way]] for way in range(5)])
                after[~valid, :] = 0
                best = np.maximum(best, after)
            value = best
            if t < ghost_frames:
                kept.append(value)
        worst = np.minimum.reduce(kept)
        try:
            os.makedirs(_REACH_CACHE_DIR, exist_ok=True)
            scratch = f"{path}.{os.getpid()}.tmp.npy"
            np.save(scratch, worst)
            os.replace(scratch, path)
        except OSError:
            pass
    _WORST_TABLE = (worst.tolist(), index)
    return _WORST_TABLE


def _guaranteed_moves(step, chaser, replies):
    """How many more moves Pac-Man can be sure of once he has stepped to
    `step`, if the player ghost on `chaser` then makes the worst `replies`
    moves for him (see _worst_case_table). 0: it can be on that tile first."""
    key = (step, chaser, replies)
    found = _WORST_GUARANTEES.get(key)
    if found is None:
        tables = _worst_case_table()
        found = 10_000
        if tables is not None:
            rows, index = tables
            s = index.get(step)
            if s is not None and chaser in index:
                reach = _WORST_REPLIES.get((chaser, replies))
                if reach is None:
                    reach = {chaser}
                    for _ in range(replies):
                        reach = reach.union(*[_outside_neighbours(tile) for tile in reach])
                    reach = _WORST_REPLIES[(chaser, replies)] = tuple(reach)
                row = rows[s]
                found = 0 if step in reach else min([row[index[tile]] for tile in reach])
        _WORST_GUARANTEES[key] = found
    return found


def _worst_case_marks(options, chaser, chaser_left, chaser_last, now, power_pellets):
    """{step: guaranteed moves, counted up to PACMAN_WORST_SAFE} for each
    way open to Pac-Man (see PACMAN_WORST_WEIGHT). The player ghost makes
    every move it has time for before his next one - two, about once in
    eleven - and a ghost still in the house is counted from the door. A way
    from which he wins the race to a power pellet counts as
    PACMAN_WORST_COVER."""
    safe = PACMAN_WORST_SAFE
    cover = safe if PACMAN_WORST_COVER < 0 else PACMAN_WORST_COVER
    if not chaser_left or chaser in HOUSE_TILES:
        chaser, replies = HOUSE_EXIT_TARGET, 0
    else:
        since = int(round((now - chaser_last) / FRAME_MS))
        ghost_frames = int(round(GHOST_MOVE_TRUE / FRAME_MS))
        pacman_frames = int(round(PACMAN_MOVE_TRUE / FRAME_MS))
        replies = max(1, min(2, (since + pacman_frames) // ghost_frames))
    marks = {}
    dead = _WORST_DEAD_STEPS
    dead.clear()
    # The race to a power pellet is judged with the ghost's move clock at its
    # worst for him (PACMAN_WORST_COVER_REPLIES), not as it happens to stand.
    race_replies = max(replies, PACMAN_WORST_COVER_REPLIES) if replies else 0
    for step in options:
        sure = _guaranteed_moves(step, chaser, replies)
        if sure == 0 and replies and step not in power_pellets:
            dead.add(step)  # the ghost can simply be on that tile before he moves again
        if sure > safe:
            sure = safe
        if sure < cover and power_pellets:
            if step in power_pellets:
                sure = cover
            else:
                mine = PACMAN_DISTANCE_TABLE.get(step, {})
                theirs = PACMAN_DISTANCE_TABLE.get(chaser, {})
                for pellet in power_pellets:
                    if (mine.get(pellet, 999) * PACMAN_MOVE_TRUE + PACMAN_WORST_COVER_SLACK
                            < (theirs.get(pellet, 0) - race_replies) * GHOST_MOVE_TRUE):
                        sure = cover
                        break
        marks[step] = sure
    if len(dead) == len(marks):
        dead.clear()  # no way out at all: nothing to choose between
    return marks


def warm_pacman_tables():
    """Build the big look-up tables Pac-Man plans with (about two seconds,
    once per run). Done when the game is created; left to his first decision
    it froze the first game for those two seconds just after it began."""
    _evasion_table(None)
    _room_tables()
    _learned_hops()
    if PACMAN_WORST_WEIGHT:
        _worst_case_table()
    if PURSUER_ROUTE_POWER_BONUS or PURSUER_ROUTE_ROOM_KIND == "safe":
        _safe_tables()


def _evasion_key(player_ghost_weights):
    """Which evasion table to use against this player-ghost model (see
    PURSUER_POLICY_TABLE)."""
    if PURSUER_POLICY_TABLE == "0":
        return None
    key = player_ghost_policy_key(player_ghost_weights)
    if key is None or PURSUER_POLICY_TABLE == "1":
        return key
    return ("min", key)


_ROUTE_HOPS = {}
_ROUTE_EXITS = {}
_ROUTE_BITS = {}
_ROUTE_NEIGHBOURS = {}
_ROUTE_PELLET_VALUE = {}


_ROOM_PAIRS = {}
_SEAL_STEPS = {}


def _room_pair(pacman_tile, ghost_tile):
    """How many tiles Pac-Man on `pacman_tile` could reach before the player
    ghost on `ghost_tile` (each at its own speed). 0 without numpy."""
    key = (pacman_tile, ghost_tile)
    room = _ROOM_PAIRS.get(key)
    if room is None:
        tables = _room_tables()
        room = 0
        if tables is not None:
            _np, index, pacman_ms, player_ms, _classic_ms = tables
            p, g = index.get(pacman_tile), index.get(ghost_tile)
            if p is not None and g is not None:
                room = int((pacman_ms[p] < player_ms[g]).sum())
        _ROOM_PAIRS[key] = room
    return room


_DANGER_MAP = None
_ROUTE_PP_TILES = {}


def _danger_map():
    """{tile: 1.0} for the tiles where Pac-Man can be shut in: a player
    ghost standing at least PURSUER_ROUTE_DANGER_AWAY tiles off can leave
    him fewer than PURSUER_ROUTE_DANGER_ROOM tiles he would reach first.
    In this maze: the top row and the four corners, the pockets at the
    sides, and everything from row 24 down. {} without numpy."""
    global _DANGER_MAP
    if _DANGER_MAP is None:
        _DANGER_MAP = {}
        tables = _room_tables()
        if tables is not None:
            np, index, pacman_ms, player_ms, _classic_ms = tables
            tiles = sorted(index, key=index.get)
            outside = [t for t in tiles if t not in HOUSE_TILES]
            for tile in outside:
                reach = pacman_ms[index[tile]][None, :]
                to_tile = PACMAN_DISTANCE_TABLE[tile]
                far = [index[g] for g in outside if to_tile.get(g, 0) >= PURSUER_ROUTE_DANGER_AWAY]
                if far and int((reach < player_ms[far]).sum(axis=1).min()) < PURSUER_ROUTE_DANGER_ROOM:
                    _DANGER_MAP[tile] = 1.0
    return _DANGER_MAP


_FLEE_STEPS = {}
_POCKETS = None


def _pockets():
    """The sealable areas split into connected pockets: a list of tile sets."""
    global _POCKETS
    if _POCKETS is None:
        danger = _danger_map()
        seen, pockets = set(), []
        for tile in danger:
            if tile in seen:
                continue
            pocket, todo = set(), [tile]
            seen.add(tile)
            while todo:
                here = todo.pop()
                pocket.add(here)
                for other in pacman_neighbours(here):
                    if other in danger and other not in seen:
                        seen.add(other)
                        todo.append(other)
            pockets.append(pocket)
        _POCKETS = pockets
    return _POCKETS


def _area_target(position, chaser, chaser_left, pellets):
    """{tile: moves to the nearest pellet of the pocket to raid next}, or
    None when no pocket with pellets can be reached safely ahead of the
    player ghost (see PURSUER_ROUTE_AREA)."""
    here = PACMAN_DISTANCE_TABLE.get(position, {})
    ghost = PACMAN_DISTANCE_TABLE.get(chaser if chaser_left else HOUSE_EXIT_TARGET, {})
    best, best_margin = None, PURSUER_ROUTE_AREA_MARGIN - 1
    for pocket in _pockets():
        left = [t for t in pocket if t in pellets]
        if not left:
            continue
        mine = min(here.get(t, 999) for t in left)
        theirs = min(ghost.get(t, 999) for t in left) + (0 if chaser_left else 6)
        margin = theirs - mine
        if margin > best_margin:
            best, best_margin = left, margin
    if best is None:
        return None
    goal = {}
    for t in best:
        for tile, d in PACMAN_DISTANCE_TABLE[t].items():
            if d < goal.get(tile, 999):
                goal[tile] = d
    return goal
_LEARNED_HOPS = None


def _learned_hops():
    """({(ghost tile, pacman tile): step} for normal play, the same for a
    frightened ghost) from PURSUER_ROUTE_HOPS_FILE; two empty tables if
    there is none."""
    global _LEARNED_HOPS
    if _LEARNED_HOPS is None:
        tables = ({}, {})
        path = PURSUER_ROUTE_HOPS_FILE
        if not path and PACMAN_ASSUMED_PLAYER and PACMAN_ASSUMED_PLAYER != "none":
            path = PACMAN_ASSUMED_PLAYER + "_hops.json"
        if path and path != "none":
            if not os.path.isabs(path):
                path = os.path.join(PLAYER_GHOST_VERSIONS_DIR, path)
            try:
                with open(path) as f:
                    data = json.load(f)
                for table, name in zip(tables, ("normal", "frightened")):
                    for key, step in data.get(name, {}).items():
                        gx, gy, px, py = (int(v) for v in key.split(","))
                        table[((gx, gy), (px, py))] = tuple(step)
            except (OSError, ValueError):
                tables = ({}, {})
        _LEARNED_HOPS = tables
    return _LEARNED_HOPS
_SAFE_ROOM_PAIRS = {}
_SAFE_TABLES = None


def _safe_tables():
    """(mask of open-maze tiles in _room_tables order, {tile: Pac-Man's
    moves to the nearest open-maze tile}); (None, {}) without numpy."""
    global _SAFE_TABLES
    if _SAFE_TABLES is None:
        tables = _room_tables()
        if tables is None:
            _SAFE_TABLES = (None, {})
        else:
            np, index = tables[0], tables[1]
            danger = _danger_map()
            tiles = sorted(index, key=index.get)
            open_tiles = [t for t in tiles if t not in danger and t not in HOUSE_TILES]
            mask = np.array([t not in danger and t not in HOUSE_TILES for t in tiles])
            way_out = {
                t: min(PACMAN_DISTANCE_TABLE[t].get(o, 10_000) for o in open_tiles) for t in tiles
            }
            _SAFE_TABLES = (mask, way_out)
    return _SAFE_TABLES


def _safe_room_pair(pacman_tile, ghost_tile):
    """How many tiles of open maze (outside the sealable areas) Pac-Man on
    `pacman_tile` could reach before the player ghost on `ghost_tile`."""
    key = (pacman_tile, ghost_tile)
    room = _SAFE_ROOM_PAIRS.get(key)
    if room is None:
        tables = _room_tables()
        room = 0
        mask = _safe_tables()[0]
        if tables is not None and mask is not None:
            _np, index, pacman_ms, player_ms, _classic_ms = tables
            p, g = index.get(pacman_tile), index.get(ghost_tile)
            if p is not None and g is not None:
                room = int(((pacman_ms[p] < player_ms[g]) & mask).sum())
        _SAFE_ROOM_PAIRS[key] = room
    return room


def _flee_step(ghost_tile, pacman_tile):
    """Where a frightened ghost backing away from Pac-Man steps: to the
    neighbouring tile farthest from him (staying put if none is farther)."""
    key = (ghost_tile, pacman_tile)
    step = _FLEE_STEPS.get(key)
    if step is None:
        from_pacman = PACMAN_DISTANCE_TABLE.get(pacman_tile, {})
        step = max(
            [ghost_tile] + list(_outside_neighbours(ghost_tile)), key=lambda t: from_pacman.get(t, -1))
        _FLEE_STEPS[key] = step
    return step


def _seal_step(ghost_tile, pacman_tile):
    """Where a ghost that seals Pac-Man in steps from `ghost_tile`: to the
    neighbouring tile that leaves him least room, the one nearer to him if
    two leave the same."""
    key = (ghost_tile, pacman_tile)
    step = _SEAL_STEPS.get(key)
    if step is None:
        options = _outside_neighbours(ghost_tile)
        if not options:
            step = ghost_tile
        else:
            to_pacman = PACMAN_DISTANCE_TABLE.get(pacman_tile, {})
            step = min(options, key=lambda t: (_room_pair(pacman_tile, t), to_pacman.get(t, 10_000)))
        _SEAL_STEPS[key] = step
    return step


def _route_values(
    position, options, pellets, power_pellets, chaser, chaser_last, chaser_left, power_until, now, gate,
    policy_key, evasion_key, blocked, classic=None, seal=False,
):
    """How good is each of Pac-Man's moves as the start of a pellet route?

    Searches PURSUER_ROUTE_DEPTH moves ahead, keeping the PURSUER_ROUTE_WIDTH
    best routes at each move (a beam search). Only Pac-Man and the player
    ghost are simulated - the ghost exactly, with its real speed, its real
    slow-down and respawn when a power pellet is eaten, and its own model's
    moves when that model is simple enough to compute (a plain shortest-path
    chaser otherwise). A route is worth the pellets it eats, plus what is
    left of Pac-Man's lead at the end of it (PURSUER_ROUTE_LEAD pellets per
    tile, from the evasion table), minus PURSUER_ROUTE_PP_COST for a power
    pellet eaten while the chaser is too far away to be caught with it.
    Routes on which the ghost catches Pac-Man are dropped.

    The classic ghosts are not simulated here; `blocked` (tiles on or beside
    one of them right now) is simply off limits for the first
    PURSUER_ROUTE_AVOID moves, and the lookahead and danger terms deal with
    them as before. Unless `classic` is given - (ghost states, the classic
    ghosts' sighting of Pac-Man, simulation settings), see _exact_ply - in
    which case they are moved along with each route for its first
    PURSUER_ROUTE_AI_DEPTH moves and a route they catch him on is dropped.

    Returns {step: value} for every step in `options`, 0 for the best one and
    negative for the others (no lower than -PURSUER_ROUTE_CLIP)."""
    if not _ROUTE_NEIGHBOURS:
        for tile in PACMAN_DISTANCE_TABLE:
            _ROUTE_NEIGHBOURS[tile] = tuple(pacman_neighbours(tile))
            _ROUTE_BITS[tile] = 1 << (tile[1] * GRID_W + tile[0])
            door = _door_distance(tile)
            _ROUTE_PELLET_VALUE[tile] = PURSUER_ROUTE_PELLET * (1.0 + PURSUER_ROUTE_DOOR_BONUS * max(
                0.0, (PURSUER_ROUTE_DOOR_RANGE - door) / PURSUER_ROUTE_DOOR_RANGE))
    bits = _ROUTE_BITS
    pellet_value = _ROUTE_PELLET_VALUE
    hop_key = "seal" if seal else (policy_key if policy_key is not None else (-1.0, 0.0))
    hops = _ROUTE_HOPS.setdefault(hop_key, {})
    room_value, room_cap = PURSUER_ROUTE_ROOM, PURSUER_ROUTE_ROOM_CAP
    expose, cover_value, power_bonus = PURSUER_ROUTE_EXPOSE, PURSUER_ROUTE_COVER, PURSUER_ROUTE_POWER_BONUS
    pp_sealed = PURSUER_ROUTE_PP_SEALED if seal else 0
    covering = seal and (expose or cover_value)
    danger = _danger_map() if (seal and power_bonus) else None
    cover_slack = PURSUER_ROUTE_COVER_SLACK
    flee_range = PURSUER_ROUTE_FLEE if seal else 0
    power_room = PURSUER_ROUTE_POWER_ROOM
    safe_kind = seal and PURSUER_ROUTE_ROOM_KIND == "safe"
    raid = PURSUER_ROUTE_RAID if seal else 0.0
    raid_gap = PURSUER_ROUTE_RAID_GAP
    raid_tiles = _danger_map() if raid else None
    area = PURSUER_ROUTE_AREA if seal else 0.0
    area_goal = _area_target(position, chaser, chaser_left, pellets) if area and now >= power_until else None
    learned, learned_frightened = _learned_hops() if seal else ({}, {})
    endgame = seal and len(pellets) <= PURSUER_ROUTE_END
    reserve = PURSUER_ROUTE_PP_RESERVE if (seal and not endgame) else 0
    reserve_cost = PURSUER_ROUTE_PP_RESERVE_COST
    if endgame:
        room_value = room_value * PURSUER_ROUTE_END_ROOM
    room_of = _safe_room_pair if safe_kind else _room_pair
    way_out = _safe_tables()[1] if safe_kind else None
    exit_spare = PURSUER_ROUTE_POWER_EXIT_MS
    holds_when_frightened = policy_key is None  # unknown model or a human: assume it does not walk into Pac-Man
    table = _evasion_table(evasion_key)
    cap = PURSUER_ROUTE_CAP
    lead_value = PURSUER_ROUTE_LEAD / 18.0
    rem0 = 0
    for tile in pellets:
        rem0 |= bits.get(tile, 0)
    pp0 = 0
    for tile in power_pellets:
        pp0 |= bits.get(tile, 0)
    result = {step: -PURSUER_ROUTE_CLIP for step in options}
    graded = (
        PURSUER_ROUTE_GRADED or PURSUER_ROUTE_COMMIT or (PURSUER_ROUTE_GRADED_POWER and now < power_until)
    ) and len(options) > 1
    behind = {}
    want_path = bool(seal and PURSUER_ROUTE_COMMIT)
    smooth = PURSUER_ROUTE_ROOM_SMOOTH if seal else 0
    rooms = None
    if seal:
        _ROUTE_BEST["path"] = None
        _ROUTE_BEST["dead"] = set()
    scouts = ()
    if seal and PURSUER_ROUTE_SCOUT and pp0 and now >= power_until:
        from_here = PACMAN_DISTANCE_TABLE.get(position, {})
        scouts = tuple(
            (bits[q], PACMAN_DISTANCE_TABLE[q]) for q in power_pellets
            if q in bits and from_here.get(q, 999) <= PURSUER_ROUTE_SCOUT_RANGE
        )
    if classic is not None:
        classic_ghosts, classic_seen, classic_sim = classic
    else:
        classic_ghosts = classic_seen = classic_sim = None
    # node: (value, tile, time, chaser, chaser_last, chaser_left, power_until, pellets, power pellets, gain, first step,
    #        classic ghosts, their sighting of Pac-Man)
    beam = [(
        0.0, position, now - PACMAN_MOVE_DELAY, chaser, chaser_last, chaser_left, power_until, rem0, pp0, 0.0, None,
        classic_ghosts, classic_seen, None, (PURSUER_ROUTE_ROOM_CAP, PURSUER_ROUTE_ROOM_CAP),
    )]
    for depth in range(PURSUER_ROUTE_DEPTH):
        candidates = {}
        if graded:
            alive_firsts = {node[10] for node in beam}
        avoid = blocked if depth < PURSUER_ROUTE_AVOID else None
        keep_classic = depth + 1 < PURSUER_ROUTE_AI_DEPTH
        for parent in beam:
            _value, tile, t_prev, ch0, last0, left0, pu0, rem, pp, gain0, first, ghosts0, seen0 = parent[:13]
            t = t_prev + PACMAN_MOVE_DELAY
            for step in (options if first is None else _ROUTE_NEIGHBOURS[tile]):
                if avoid and step in avoid:
                    continue
                ch, last, left, pu = ch0, last0, left0, pu0
                dead = False
                kills = 0
                # Every chaser move due before Pac-Man's, in the game's order.
                while True:
                    if last + GHOST_FRIGHTENED_MOVE_DELAY < pu:
                        due = last + GHOST_FRIGHTENED_MOVE_DELAY
                    else:
                        due = last + GHOST_MOVE_DELAY
                        if due < pu:
                            due = pu
                    if due < gate:
                        due = gate
                    if due > t:
                        break
                    if not left:
                        nxt = _ROUTE_EXITS.get(ch)
                        if nxt is None:
                            nxt = _ROUTE_EXITS[ch] = house_exit_step(ch)
                        if nxt not in HOUSE_TILES:
                            left = True
                    elif due < pu and holds_when_frightened:
                        nxt = learned_frightened.get((ch, tile)) if learned_frightened else None
                        if nxt is not None:
                            pass
                        elif not flee_range:
                            nxt = ch
                        elif PACMAN_DISTANCE_TABLE[tile].get(ch, 99) <= flee_range:
                            nxt = _flee_step(ch, tile)
                        else:
                            nxt = hops.get((ch, tile))
                            if nxt is None:
                                nxt = hops[(ch, tile)] = _seal_step(ch, tile)
                    else:
                        nxt = hops.get((ch, tile))
                        if nxt is None:
                            if seal:
                                nxt = learned.get((ch, tile)) or _seal_step(ch, tile)
                            else:
                                nxt = simple_player_ghost_step(ch, tile, hop_key)
                            hops[(ch, tile)] = nxt
                    ch = nxt
                    last = due
                    if ch == tile:
                        if due < pu:
                            ch, left, last = GHOST_START, False, due + GHOST_RESPAWN_COOLDOWN_MS
                            kills += 1
                        else:
                            dead = True
                            break
                if dead:
                    continue
                bit = bits[step]
                gain = gain0
                if rem & bit:
                    rem2 = rem & ~bit
                    gain += pellet_value[step]
                    if danger is not None and (t < pu or not left) and step in danger:
                        gain += power_bonus  # eaten while the player ghost cannot come for him
                    if raid and step in raid_tiles and (
                            not left or PACMAN_DISTANCE_TABLE[step].get(ch, 99) >= raid_gap):
                        gain += raid
                else:
                    rem2 = rem
                if pp & bit:
                    pp2 = pp & ~bit
                    gain += 1.0
                    if rem2:
                        # A power pellet is a reset: only well spent when the
                        # chaser is close enough to be eaten with it.
                        gap = PACMAN_DISTANCE_TABLE[step].get(ch, 99) if left else 99
                        if gap > PURSUER_ROUTE_PP_GAP and not (
                                pp_sealed and left and room_of(step, ch) < pp_sealed):
                            gain -= PURSUER_ROUTE_PP_COST
                            if reserve and bin(pp2).count("1") < reserve:
                                gain -= reserve_cost  # one kept back for the last pockets
                    pu = t + POWER_DURATION
                else:
                    pp2 = pp
                ghosts2 = seen2 = None
                if ghosts0 is not None:
                    outcome = _exact_ply(tile, step, ghosts0, seen0, pu0, t, (step,) if pp & bit else (), classic_sim)
                    if outcome is None or outcome[0]:
                        continue  # a classic ghost gets him on this move
                    if keep_classic:
                        ghosts2, seen2 = outcome[1], outcome[2]
                if ch == step and left:
                    if t < pu:
                        ch, left, last = GHOST_START, False, t + GHOST_RESPAWN_COOLDOWN_MS
                        kills += 1
                    else:
                        continue
                if kills:
                    gain += PURSUER_ROUTE_KILL * kills
                start_step = step if first is None else first
                if not rem2 and not pp2:
                    # Everything eaten: the game is won on this move.
                    if want_path:
                        _ROUTE_BEST["path"] = [start_step]
                    result[start_step] = 0.0
                    for other in result:
                        if other != start_step:
                            result[other] = -PURSUER_ROUTE_CLIP
                    return result
                if left and t >= pu:
                    stay_ahead = table.get(step, {}).get(ch, cap)
                    if stay_ahead > cap:
                        stay_ahead = cap
                else:
                    stay_ahead = cap
                if seal:
                    # Against the sealer what counts is the room he is left
                    # with, not how long he could outrun a follower.
                    if not left:
                        room = room_cap
                    elif t >= pu:
                        room = room_of(step, ch)
                    elif not power_room:
                        room = room_cap
                    elif safe_kind and way_out.get(step, 99) * PACMAN_MOVE_DELAY + exit_spare <= pu - t:
                        room = room_cap  # he can still walk out under power
                    else:
                        room = room_of(step, ch)
                    if room > room_cap:
                        room = room_cap
                    if smooth > 1:
                        before, before_that = parent[14]
                        rooms = (room, before)
                        if before < room:
                            room = before
                        if smooth > 2 and before_that < room:
                            room = before_that
                    if covering:
                        covered = t < pu or not left
                        if not covered and pp2:
                            pp_tiles = _ROUTE_PP_TILES.get(pp2)
                            if pp_tiles is None:
                                pp_tiles = _ROUTE_PP_TILES[pp2] = tuple(
                                    PACMAN_DISTANCE_TABLE[q] for q in bits if bits[q] & pp2)
                            mine = PACMAN_MOVE_DELAY
                            for to_pp in pp_tiles:
                                if to_pp.get(step, 999) * mine + cover_slack < to_pp.get(ch, 0) * GHOST_MOVE_DELAY:
                                    covered = True
                                    break
                        if not covered:
                            gain -= expose
                        value = gain + room_value * room + (cover_value if covered else 0.0)
                    else:
                        value = gain + room_value * room
                else:
                    value = gain + lead_value * stay_ahead
                if area_goal is not None:
                    value -= area * area_goal.get(step, 30)  # the end of the route: how far from the next pocket
                key = (step, ch, rem2, pp2)
                old = candidates.get(key)
                if old is None or value > old[0]:
                    candidates[key] = (
                        value, step, t, ch, last, left, pu, rem2, pp2, gain, start_step, ghosts2, seen2,
                        parent if want_path else None, rooms,
                    )
        if not candidates:
            break
        beam = sorted(candidates.values(), key=_route_node_value, reverse=True)
        if graded:
            top = beam[0][0]
            seen_firsts = set()
            for node in beam:
                if node[10] not in seen_firsts:
                    seen_firsts.add(node[10])
                    behind[node[10]] = node[0] - top
                    if len(seen_firsts) == len(options):
                        break
            for step in alive_firsts - seen_firsts:
                behind.pop(step, None)  # every route it still had was caught: the worst mark stands
        if PURSUER_ROUTE_DIVERSE and seal and len(beam) > PURSUER_ROUTE_WIDTH:
            firsts, seconds, tiles_seen = [], [], set()
            for node in beam:
                if node[1] in tiles_seen:
                    seconds.append(node)
                else:
                    tiles_seen.add(node[1])
                    firsts.append(node)
            beam = firsts + seconds
        if PURSUER_ROUTE_FAIR and len(beam) > PURSUER_ROUTE_WIDTH and len(options) > 1:
            share = PURSUER_ROUTE_WIDTH // len(options)
            head, tail, taken = [], [], {}
            for node in beam:
                count = taken.get(node[10], 0)
                if count < share:
                    taken[node[10]] = count + 1
                    head.append(node)
                else:
                    tail.append(node)
            beam = head + tail
        if len(beam) > PURSUER_ROUTE_WIDTH:
            rest = beam[PURSUER_ROUTE_WIDTH:]
            del beam[PURSUER_ROUTE_WIDTH:]
            if scouts:
                kept = []
                for pp_bit, to_pp in scouts:
                    nearest_d = 999
                    for node in beam:
                        if node[8] & pp_bit:
                            d = to_pp.get(node[1], 999)
                            if d < nearest_d:
                                nearest_d = d
                    nearest = None
                    strikers = 0
                    for node in rest:
                        if node[8] & pp_bit:
                            d = to_pp.get(node[1], 999)
                            if d < nearest_d:
                                nearest, nearest_d = node, d
                        elif strikers < PURSUER_ROUTE_SCOUT and node[2] < node[6]:
                            strikers += 1
                            kept.append(node)
                    if nearest is not None:
                        kept.append(nearest)
                seen_nodes = set()
                for node in kept:
                    if id(node) not in seen_nodes:
                        seen_nodes.add(id(node))
                        beam.append(node)
    if beam[0][10] is None:
        return {step: 0.0 for step in options}  # no route survives at all: no opinion
    best = max(node[0] for node in beam)
    if want_path:
        node = max(beam, key=_route_node_value)
        path = []
        while node is not None and node[10] is not None:
            path.append(node[1])
            node = node[13]
        path.reverse()
        _ROUTE_BEST["path"] = path
    if graded:
        # (A first move with no route ever, or none since it was caught,
        # keeps the worst mark.)
        for step, shortfall in behind.items():
            if shortfall > result[step]:
                result[step] = shortfall
        still_there = {node[10] for node in beam}
        for step in still_there:
            result[step] = -PURSUER_ROUTE_CLIP
        if seal:
            # First moves with no route at all any more: every one was caught.
            _ROUTE_BEST["dead"] = {step for step in options if step not in behind and step not in still_there}
    for node in beam:
        value = node[0] - best
        if value > result[node[10]]:
            result[node[10]] = value
    return result


def _route_node_value(node):
    return node[0]


_ROUTE_BEST = {"path": None, "dead": set()}
_ROUTE_COMMITTED = {"path": None, "left": 0, "now": -1}


def _commit_route(position, route_values, fresh_path, now, dead=()):
    """The route marks with the next step of the route Pac-Man is committed
    to put on top, PURSUER_ROUTE_COMMIT_BONUS above the best of the others
    (see PURSUER_ROUTE_COMMIT). `fresh_path` is the best route just planned
    (the tiles it visits, first step first); it becomes the commitment when
    there is none in force. `dead`: first steps that are out - every route
    caught, or ruled out by the look-ahead."""
    state = _ROUTE_COMMITTED
    values = dict(route_values)
    path = state["path"]
    if (
        path and state["left"] > 0 and len(path) > 1 and path[0] == position
        and 0 < now - state["now"] <= 2 * PACMAN_MOVE_DELAY + 50
        and path[1] in values and path[1] not in dead
    ):
        values[path[1]] = max(values.values()) + PURSUER_ROUTE_COMMIT_BONUS
        state["path"], state["left"], state["now"] = path[1:], state["left"] - 1, now
        return values
    if fresh_path and fresh_path[0] in values:
        values[fresh_path[0]] += PURSUER_ROUTE_COMMIT_BONUS
        state["path"], state["left"], state["now"] = list(fresh_path), PURSUER_ROUTE_COMMIT, now
    else:
        state["path"] = None
    return values


def _door_distance(tile):
    """Pac-Man's walking distance from `tile` to the ghost-house door."""
    return pacman_bfs_distances(HOUSE_EXIT_TARGET).get(tile, 10_000)


def _pursuer_race(position, pellet, pursuer, pacman_direction, ai_ghost_positions, player_ghost_weights):
    """Would Pac-Man reach `pellet` by the shortest route before the player
    ghost catches him? Walks the route one tile at a time while moving the
    ghost by its own decision model (or straight at Pac-Man when no model
    is loaded - a human player), at its real speed advantage."""
    to_pellet = pacman_bfs_distances(pellet)
    here = position
    ghost = pursuer
    ghost_facing = (0, 0)
    facing = pacman_direction
    clock = 0
    while here != pellet:
        remaining = to_pellet.get(here)
        if remaining is None:
            return False
        next_tile = None
        for tile in _outside_neighbours(here):
            if to_pellet.get(tile, 10_000) == remaining - 1 and tile != ghost:
                next_tile = tile
                break
        if next_tile is None:
            return False
        facing = direction_between(here, next_tile)
        here = next_tile
        clock += PACMAN_MOVE_DELAY
        while clock >= GHOST_MOVE_DELAY:
            clock -= GHOST_MOVE_DELAY
            if player_ghost_weights is not None:
                ghost_facing = choose_player_ghost_direction(
                    ghost, ghost_facing, here, facing, ai_ghost_positions, player_ghost_weights,
                )
                ghost = step_position(ghost, ghost_facing)
            else:
                ghost = _standin_step(ghost, here)
            if ghost == here and here != pellet:
                return False
    return True


# A frightened player ghost is slower than Pac-Man (17 frames a move against
# 12), so stepping along the shortest way to it closes the gap by the
# difference in speed whatever it does: each of his moves takes a tile off,
# each of its moves puts at most one back. Once the power time left only just
# covers the gap, the kill is certain and he goes and takes it - as late as
# that is still certain (PACMAN_SURE_KILL_SLACK_MS of spare time at most), so
# the pellets on the way are eaten first. Eaten, the ghost waits three seconds
# in the house. In 109 recorded real games' power pellets he ate the player's
# ghost 30 times; in simulation this lifts it to about 70-75%. Measured on
# 800 games each: +1.1 points of maze against the stand-in for the person
# playing, +2.1 against a sharper "pouncing" ghost, no change against a
# plain chaser. PACMAN_SURE_KILL=0 switches it off.
PACMAN_SURE_KILL = os.environ.get("PACMAN_SURE_KILL", "1") == "1"
PACMAN_SURE_KILL_MARGIN_MS = float(os.environ.get("PACMAN_SURE_KILL_MARGIN_MS", "400"))
PACMAN_SURE_KILL_MS_PER_TILE = 1.0 / (1.0 / PACMAN_MOVE_DELAY - 1.0 / GHOST_FRIGHTENED_MOVE_DELAY)
PACMAN_SURE_KILL_SLACK_MS = float(os.environ.get("PACMAN_SURE_KILL_SLACK_MS", "1400"))
SURE_KILL_STATS = {"chase": 0}


def choose_pacman_step(
    position, ghost_positions, pellets, power_pellets, frightened, weights,
    pacman_direction=(0, 0), prev_ghost_positions=None, perceived_pacman_position=None,
    player_ghost_weights=None, scattering=False, recent_positions=None,
    scatter_time_remaining=0, time_until_perception_refresh=0, respawning_indices=(),
    permanent_chase=False, sim_state=None,
):
    """Pick the safest/best move using maze-true distances and a short
    lookahead that anticipates each ghost's best chasing response. Pac-Man
    never considers the ghost house a usable tile or shortcut.

    `pacman_direction`, `prev_ghost_positions`, `perceived_pacman_position`
    and `player_ghost_weights` are optional - passed by move_pacman for the
    reversal/predicted-target/closing-threat/lookahead-perception-lag/
    lookahead-player-ghost-prediction features below, but every feature
    degrades gracefully without them (e.g. a standalone caller that
    doesn't track ghost history).

    `scattering` is whether the AI ghosts are currently in a global
    scatter window (see scatter_or_chase) - without it, both the
    predicted-target avoidance below and the recursive lookahead would
    keep assuming every AI ghost is always hunting, even during a window
    where they've all actually turned away to their corners. That costs
    real exploitable value (Pac-Man staying needlessly cautious around a
    ghost that isn't coming), not correctness - the ghosts' own real
    movement already honors scatter mode regardless of what Pac-Man's
    heuristics assume.

    `recent_positions` is Pac-Man's own last PACMAN_RECENT_POSITIONS_
    WINDOW real tiles (see Game.pacman_recent_positions), oldest first -
    feeds loop_escape_weight, the only feature here with any memory of
    where Pac-Man has actually already been. Everything else is purely
    reactive to the current instant, which is exactly how a goose-guard
    trap can stall Pac-Man forever: every single tick, oscillating
    between the same two "locally safest" tiles can look like the
    correct reactive choice, even though repeating it is actively
    letting the net close. None/empty degrades to no effect, like every
    other optional feature here.

    `scatter_time_remaining` is ms left in the current scatter window
    (0 if already chasing - see time_until_chase_resumes), feeding
    scatter_timing_weight. `time_until_perception_refresh` is ms until
    the AI ghosts next re-sight Pac-Man (see AI_GHOST_PERCEPTION_INTERVAL),
    feeding perception_window_weight. Both default to 0, which disables
    their respective feature rather than misfiring on a caller that
    doesn't track the game clock.

    `respawning_indices` is which of the 5 ghost_positions slots (0 =
    player, 1-4 = blinky/pinky/inky/clyde) are currently still caged or
    walking out of the house for the first time or after an eat_ghost
    respawn - feeds respawn_discount_weight. Empty by default (no
    discount), for the same graceful-degradation reason as every other
    optional parameter here.

    `permanent_chase` is whether SCATTER_CHASE_SCHEDULE has permanently
    run out for the rest of this match (elapsed >= PERMANENT_CHASE_START)
    - a longer-horizon signal than `scattering` (which is only about
    this exact instant): feeds permanent_chase_caution_weight (raises
    danger once no more relief is coming) and early_rush_weight (extra
    pellet urgency while relief is still scheduled). False by default."""
    options = pacman_neighbours(position)
    if not options:
        return position

    targets = pellets | power_pellets
    if not targets and not frightened:
        return random.choice(options)

    # The player ghost as an active pursuer (never when it is passive).
    pursuer = ghost_positions[0] if ghost_positions else None
    pursuer_active = sim_state is not None and sim_state["player_mode"] != "static" and pursuer is not None
    pursuer_outside = pursuer_active and pursuer not in HOUSE_TILES
    evasion_key = _evasion_key(player_ghost_weights)
    if frightened and pursuer_active and sim_state["power_until"] - sim_state["now"] <= PURSUER_EXPIRY_CAUTION_MS:
        # Power is about to run out: go back to normal, look-ahead play (the
        # lookahead knows exactly when the ghosts turn dangerous again) and
        # start for the next power pellet instead of being caught mid-hunt.
        frightened = False
    pursuer_gap = 10_000
    refuge_pellets = ()
    route = pursuer_active and PURSUER_PLAN == "route"
    route_values = None
    pp_planned = False
    sealing = False
    route_path = None
    committed = False
    if route:
        blocked = set()
        if sim_state["now"] >= sim_state["power_until"]:
            for other in ghost_positions[1:]:
                if other not in HOUSE_TILES:
                    blocked.add(other)
                    blocked.update(neighbours(other))
        if all(option in blocked for option in options):
            blocked = set()
        classic = None
        if PURSUER_ROUTE_AI != "0" and PURSUER_ROUTE_AI_DEPTH > 0:
            # The player ghost's own slot is a stand-in that never moves:
            # the planner moves the real one itself.
            classic = (
                ((GHOST_START, GHOST_START, 0, False),) + tuple(zip(
                    ghost_positions[1:], sim_state["ghost_prev"][1:], sim_state["ghost_last"][1:],
                    sim_state["left_house"][1:],
                )),
                (
                    perceived_pacman_position if perceived_pacman_position is not None else position,
                    sim_state["perceived_direction"], sim_state["perception_last"],
                ),
                {
                    "start": sim_state["start"], "release": sim_state["release"], "player_mode": "static",
                    "ai_direct": PURSUER_ROUTE_AI == "direct",
                },
            )
        sealing = (
            PURSUER_ROUTE_SEAL != "0" and player_ghost_policy_key(player_ghost_weights) is None
            and not sim_state.get("told")
        )
        if not (sealing and PURSUER_ROUTE_SEAL == "only"):
            route_values = _route_values(
                position, options, pellets, power_pellets, pursuer, sim_state["ghost_last"][0],
                sim_state["left_house"][0], sim_state["power_until"], sim_state["now"],
                max(sim_state["start"] + GHOST_HEAD_START, sim_state.get("hold_until", 0)), player_ghost_policy_key(player_ghost_weights), evasion_key,
                blocked, classic,
            )
        pp_planned = PURSUER_PP_PLAN and sealing
        turn_back = None
        if PURSUER_ROUTE_INERTIA and pacman_direction != (0, 0) and len(options) > 1:
            turn_back = (position[0] - pacman_direction[0], position[1] - pacman_direction[1])
        if sealing:
            sealed_values = _route_values(
                position, options, pellets, power_pellets, pursuer, sim_state["ghost_last"][0],
                sim_state["left_house"][0], sim_state["power_until"], sim_state["now"],
                max(sim_state["start"] + GHOST_HEAD_START, sim_state.get("hold_until", 0)), None, evasion_key, blocked, classic, seal=True,
            )
            if PURSUER_ROUTE_SEAL == "only":
                route_values = sealed_values
            elif PURSUER_ROUTE_SEAL == "mean":
                route_values = {step: (route_values[step] + sealed_values[step]) / 2.0 for step in route_values}
            else:
                route_values = {step: min(route_values[step], sealed_values[step]) for step in route_values}
        if turn_back is not None and turn_back in route_values:
            route_values = dict(route_values)
            route_values[turn_back] = max(-PURSUER_ROUTE_CLIP, route_values[turn_back] - PURSUER_ROUTE_INERTIA)
        if sealing and PURSUER_ROUTE_SEAL == "only":
            route_path = _ROUTE_BEST["path"]
            if PURSUER_ROUTE_COMMIT and frightened:
                # (Powered: there is no look-ahead veto to wait for.)
                route_values = _commit_route(
                    position, route_values, route_path, sim_state["now"], _ROUTE_BEST["dead"])
                committed = True
    if pursuer_active and not frightened and not pursuer_outside and pellets:
        targets = set(pellets)  # it is still caged: nothing to spend a power pellet on yet
    if pursuer_outside and not frightened:
        from_here = pacman_bfs_distances(position)
        pursuer_gap = from_here.get(pursuer, 10_000)
        reachable_first = [] if (route and not PURSUER_ROUTE_REFUGE) else [
            t for t in power_pellets
            if t in from_here and _pursuer_race(
                position, t, pursuer, pacman_direction, ghost_positions[1:], player_ghost_weights,
            )
        ]
        if reachable_first:
            nearest_refuge = min(from_here[t] for t in reachable_first)
            # The ghost gains about one tile per 18 of Pac-Man's moves, so a
            # farther pellet needs an earlier start.
            if pursuer_gap <= max(pursuer_profile(player_ghost_weights)[0], 2 + nearest_refuge / 15.0):
                refuge_pellets = tuple(reachable_first)
        if pellets and not refuge_pellets and pursuer_gap > PURSUER_SAVE_GAP:
            targets = set(pellets)  # don't path toward a power pellet just to eat it

    camp_pellet = None
    if PURSUER_CAMP_FACTOR and pursuer_active and not frightened and power_pellets:
        camp_pellet = min(power_pellets, key=_door_distance)
    if frightened and pursuer_active and pellets:
        targets = set(pellets)  # a second power pellet eaten while already powered is wasted
    if frightened and PACMAN_SURE_KILL and pursuer_outside:
        gap = pacman_bfs_distances(position).get(pursuer)
        needed = None if gap is None else gap * PACMAN_SURE_KILL_MS_PER_TILE + PACMAN_SURE_KILL_MARGIN_MS
        remaining = sim_state["power_until"] - sim_state["now"]
        if needed is not None and needed <= remaining <= needed + PACMAN_SURE_KILL_SLACK_MS:
            SURE_KILL_STATS["chase"] += 1
            return min(options, key=lambda s: (
                pacman_bfs_distances(s).get(pursuer, 10_000), 0 if s in pellets else 1, s in power_pellets))
    if frightened:
        # Ghosts are fleeing and harmless: ignore danger, hunt pellets, and
        # opportunistically close in on a nearby ghost for a bonus. Both
        # coefficients are learned now instead of fixed, and a small
        # safety term guards against the frighten timer expiring mid-move.
        #
        # Pellet-hunting and ghost-hunting above are still two independent
        # pulls, though - nothing prefers a pellet specifically *because*
        # a ghost happens to be loitering near it, which is free value
        # (eat the pellet, likely also catch the ghost on the way) over
        # just picking whichever pellet is nearest full stop. baited_targets
        # is every target with a ghost within straight-line
        # LOOKAHEAD_AMBUSH_RADIUS of it; frightened_bait_pellet_weight
        # pulls toward the nearest one of those on top of the plain pellet
        # pull, without changing where Pac-Man goes when no pellet happens
        # to have a ghost camped near it.
        radius_sq = LOOKAHEAD_AMBUSH_RADIUS ** 2
        baited_targets = {
            t for t in targets
            if any((g[0] - t[0]) ** 2 + (g[1] - t[1]) ** 2 <= radius_sq for g in ghost_positions)
        }
        best_step, best_score = options[0], float("-inf")
        for step in options:
            distances = pacman_bfs_distances(step)
            target_distances = [distances[t] for t in targets if t in distances]
            target_distance = min(target_distances) if target_distances else 10_000
            ghost_distances = [distances.get(g, 10_000) for g in ghost_positions]
            nearest_ghost = min(ghost_distances, default=10_000)
            baited_distances = [distances[t] for t in baited_targets if t in distances]
            baited_distance = min(baited_distances) if baited_distances else 10_000
            # Keeping a *second* kill chainable - frightened_hunt_weight
            # above only ever looks at the single nearest ghost, so a
            # route that beelines for it while leaving every other ghost
            # scattered far behind scores the same as one that keeps two
            # of them within reach of each other.
            two_nearest_sum = sum(sorted(ghost_distances)[:2])

            score = -weights["frightened_pellet_weight"] * target_distance
            score -= weights["frightened_hunt_weight"] * nearest_ghost
            score -= weights["frightened_safety_weight"] * max(0, 5 - nearest_ghost)
            score -= weights["frightened_bait_pellet_weight"] * baited_distance
            score -= weights["frightened_sequence_weight"] * two_nearest_sum
            if pursuer_active and pellets and step in power_pellets:
                score -= weights["power_pellet_save_weight"]
            if route:
                # The planned route already knows the chaser is edible, when
                # it comes back out, and where the pellets are.
                score += weights["pursuer_route_weight"] * route_values.get(step, -PURSUER_ROUTE_CLIP)
            elif pursuer_active:
                # Each kill sends the player ghost home, so where the LAST
                # kill of this power window happens decides how big a head
                # start Pac-Man gets afterwards. Early in the window: get
                # far from the house (a ghost that keeps chasing follows,
                # slower than Pac-Man). Late in the window: turn and eat it
                # if it can still be reached in time, else keep away from it.
                remaining_ms = sim_state["power_until"] - sim_state["now"]
                pursuer_distance = distances.get(pursuer, 10_000) if pursuer_outside else 10_000
                catch_ms = pursuer_distance * PURSUER_CLOSING_MS_PER_TILE
                can_kill = pursuer_outside and catch_ms + PURSUER_HUNT_MARGIN_MS < remaining_ms
                depart_distances = [distances[t] for t in power_pellets if t in distances] if remaining_ms <= PURSUER_DEPART_MS else []
                if depart_distances:
                    # The window is nearly over: standing at the house door
                    # when it ends means every ghost re-emerges on top of
                    # Pac-Man. Set off for the next power pellet instead.
                    score -= weights["pursuer_hunt_weight"] * min(depart_distances)
                elif PURSUER_TACTIC == "evade":
                    # Eat the chaser if it can be reached in time (it then
                    # sits out its respawn cooldown); otherwise use the
                    # window to get to wherever it will take longest to
                    # catch Pac-Man from, measured from the chaser or from
                    # the house door it will come out of.
                    if can_kill:
                        score -= weights["pursuer_hunt_weight"] * pursuer_distance
                    else:
                        chaser_tile = pursuer if pursuer_outside else HOUSE_EXIT_TARGET
                        stay_ahead = _evasion_table(evasion_key).get(step, {}).get(chaser_tile, 0)
                        if PURSUER_PLAN == "harvest":
                            # Far enough is far enough: past the comfort line
                            # the rest of the window goes on eating.
                            stay_ahead = PURSUER_HARVEST_FACTOR * min(
                                stay_ahead, PURSUER_COMFORT if power_pellets else PURSUER_COMFORT_BARE)
                        score += weights["pursuer_evasion_weight"] * stay_ahead
                        # A frightened classic ghost close enough to catch in
                        # time is worth a detour: eating it takes about six
                        # seconds off the clock (as much as two tiles of head
                        # start) and sends one threat home for a while.
                        prey = [
                            distances[g] for g in ghost_positions[1:]
                            if g not in HOUSE_TILES and g in distances
                        ]
                        if prey and PURSUER_AI_HUNT_FACTOR:
                            nearest_prey = min(prey)
                            if (nearest_prey <= PURSUER_AI_HUNT_RANGE
                                    and nearest_prey * PURSUER_AI_CATCH_MS + PURSUER_HUNT_MARGIN_MS < remaining_ms):
                                score -= weights["pursuer_evasion_weight"] * 18 * PURSUER_AI_HUNT_FACTOR * nearest_prey
                elif PURSUER_TACTIC == "farm":
                    # Kill it as often as possible: chase it, or wait at the
                    # house door if it has just been sent home, while there
                    # is time to get there and clear again.
                    hunt_tile = pursuer if pursuer_outside else HOUSE_EXIT_TARGET
                    hunt_distance = distances.get(hunt_tile, 10_000)
                    # A ghost that is out is walking towards Pac-Man too, so
                    # the two meet in about half the walking time.
                    reach_ms = catch_ms if (pursuer_outside and PURSUER_FARM_CLOSING) else hunt_distance * PACMAN_MOVE_DELAY
                    if reach_ms + PURSUER_FARM_MARGIN_MS < remaining_ms:
                        score -= weights["pursuer_hunt_weight"] * hunt_distance
                elif can_kill and (remaining_ms <= PURSUER_LATE_WINDOW_MS or pursuer_distance <= 2):
                    score -= weights["pursuer_hunt_weight"] * pursuer_distance
                elif remaining_ms > PURSUER_LATE_WINDOW_MS:
                    score += weights["pursuer_hunt_weight"] * 0.5 * distances.get(HOUSE_EXIT_TARGET, 0)
                else:
                    score += weights["pursuer_hunt_weight"] * min(pursuer_distance, 20)
            if POWER_REVERSAL_FACTOR and pacman_direction != (0, 0) and direction_between(position, step) == (
                    -pacman_direction[0], -pacman_direction[1]):
                score -= weights["reversal_penalty"] * POWER_REVERSAL_FACTOR
            score += random.random() * 0.2
            if PACMAN_DECISION_TRACE is not None:
                PACMAN_DECISION_TRACE.append([step[0], step[1], round(float(score), 1)])
            if score > best_score:
                best_score, best_step = score, step
        return best_step

    # Anticipate each ghost's best single chasing move towards a candidate
    # step, instead of just reacting to its current position. This stops
    # Pac-Man from wandering into spots that look safe now but let a ghost
    # cut off the escape route on its next move.
    ghost_escape_options = [[ghost, *neighbours(ghost)] for ghost in ghost_positions]
    safe_radius = weights["safe_radius"]
    # ghost_positions is always [player, blinky, pinky, inky, clyde] (see
    # move_pacman) - this lines each one up with its own learned danger
    # weight, so Pac-Man can fear a direct chaser more than a wanderer.
    per_ghost_danger_weight = [
        weights["danger_weight_player"],
        weights["danger_weight_blinky"],
        weights["danger_weight_pinky"],
        weights["danger_weight_inky"],
        weights["danger_weight_clyde"],
    ]
    per_ghost_far_weight = (
        weights["danger_far_weight_player"],
        weights["danger_far_weight_blinky"],
        weights["danger_far_weight_pinky"],
        weights["danger_far_weight_inky"],
        weights["danger_far_weight_clyde"],
    )
    # How bunched-up the ghosts currently are: eating a power pellet while
    # they're clustered hits every ghost's flee/slow-down at once, so that's
    # the opportune moment to go out of the way for one. Only worth
    # computing when a power pellet is actually among this tick's
    # candidate moves - skips it on the large majority of ticks where it'd
    # just be discarded unused.
    cluster_score = ghost_cluster_score(ghost_positions) if power_pellets.intersection(options) else 0.0

    # Multi-tile recursive lookahead (see _pacman_lookahead): simulates
    # several tiles further ahead per candidate, rather than just this
    # single step, to catch routes that look fine one tile out but run
    # into a dead end or a closing ghost shortly after.
    if pursuer_active and PURSUER_LEAF_FACTOR and (not route or PURSUER_ROUTE_LEAF):
        sim_state = dict(sim_state, leaf_weight=weights["pursuer_evasion_weight"] * PURSUER_LEAF_FACTOR)
    lookahead_scores = pacman_lookahead_branch_scores(
        position, options, ghost_positions, pellets, power_pellets, frightened=False,
        perceived_pacman_position=perceived_pacman_position, player_ghost_weights=player_ghost_weights,
        lookahead_weights=(
            weights["lookahead_survival_weight"], weights["lookahead_pellet_weight"], weights["lookahead_ambush_weight"],
        ),
        scattering=scattering,
        sim_state=sim_state,
    )
    if route_values is not None and sealing and PURSUER_ROUTE_SEAL == "only" and not committed:
        route_dead = set(_ROUTE_BEST["dead"])
        if PURSUER_ROUTE_VETO and len(options) > 1:
            survives = {step: lookahead_scores.get(step, (0,))[0] for step in options}
            longest = max(survives.values())
            planned = max(options, key=lambda step: route_values.get(step, -PURSUER_ROUTE_CLIP))
            if longest >= PURSUER_ROUTE_VETO and survives[planned] * 2 <= longest:
                allowed = [step for step in options if survives[step] * 2 > longest]
                if allowed and len(allowed) < len(options):
                    replanned = _route_values(
                        position, allowed, pellets, power_pellets, pursuer, sim_state["ghost_last"][0],
                        sim_state["left_house"][0], sim_state["power_until"], sim_state["now"],
                        max(sim_state["start"] + GHOST_HEAD_START, sim_state.get("hold_until", 0)), None, evasion_key, blocked, classic, seal=True,
                    )
                    route_values = {step: replanned.get(step, -PURSUER_ROUTE_CLIP) for step in options}
                    route_path = _ROUTE_BEST["path"]
                    route_dead = set(_ROUTE_BEST["dead"]) | (set(options) - set(allowed))
        if PURSUER_ROUTE_COMMIT:
            route_values = _commit_route(position, route_values, route_path, sim_state["now"], route_dead)
    # Second, pessimistic search against an active player ghost: the same
    # routes, but with every classic ghost heading straight for Pac-Man. A
    # route that only survives if they keep to their usual habits is how he
    # gets squeezed between one of them and the chaser.
    pessimistic_scores = {}
    if pursuer_active and PURSUER_PESSIMISM_FACTOR and EXACT_LOOKAHEAD:
        pessimistic_scores = pacman_lookahead_branch_scores(
            position, options, ghost_positions, pellets, power_pellets, frightened=False,
            perceived_pacman_position=perceived_pacman_position, player_ghost_weights=player_ghost_weights,
            lookahead_weights=(weights["lookahead_survival_weight"], 0.0, 0.0),
            scattering=scattering,
            sim_state=dict(sim_state, ai_direct=True, max_depth=PURSUER_PESSIMISM_DEPTH),
        )

    # Second opinions on the player ghost itself (see PURSUER_DOUBT).
    doubt_scores = []
    if pursuer_active and PURSUER_DOUBT and PURSUER_DOUBT_FACTOR and EXACT_LOOKAHEAD:
        for kind in PURSUER_DOUBT:
            if player_ghost_weights is None and kind == PURSUER_STANDIN:
                continue  # that is already his main assumption
            doubt_scores.append(pacman_lookahead_branch_scores(
                position, options, ghost_positions, pellets, power_pellets, frightened=False,
                perceived_pacman_position=perceived_pacman_position, player_ghost_weights=None,
                lookahead_weights=(weights["lookahead_survival_weight"], 0.0, 0.0),
                scattering=scattering,
                sim_state=dict(sim_state, player_mode="chase", standin=kind, max_depth=PURSUER_DOUBT_DEPTH),
            ))

    # And the worst case: what each way guarantees whatever the player ghost
    # does (see PURSUER_ADVERSARY_DEPTH).
    adversary_scores = None
    if (
        pursuer_active and PURSUER_ADVERSARY_DEPTH > 0 and PURSUER_ADVERSARY_FACTOR and EXACT_LOOKAHEAD
        and not sim_state.get("told")
    ):
        adversary_scores = _adversary_values(
            position, options, ghost_positions, power_pellets, perceived_pacman_position, sim_state,
        )

    # Room to move (see PURSUER_ROOM_WEIGHT).
    room_setup = None
    if pursuer_outside and PURSUER_ROOM_WEIGHT and sim_state["now"] >= sim_state["power_until"]:
        room_setup = _room_setup(ghost_positions, sim_state["left_house"])

    # A ghost whose distance to Pac-Man's *current* tile shrank since last
    # tick is actively closing in, not just incidentally nearby - boost its
    # effective danger weight for this decision on top of its base fear.
    if prev_ghost_positions is not None:
        distances_from_here = pacman_bfs_distances(position)
        for i, (prev_pos, curr_pos) in enumerate(zip(prev_ghost_positions, ghost_positions)):
            prev_d = distances_from_here.get(prev_pos)
            curr_d = distances_from_here.get(curr_pos)
            if prev_d is None or curr_d is None:
                # A tile inside the ghost house has no walking distance for
                # Pac-Man; treating it as "10,000 away" made a ghost that
                # had just stepped out look like it closed 9,988 tiles.
                continue
            closing = max(0.0, prev_d - curr_d)
            per_ghost_danger_weight[i] += weights["closing_threat_weight"] * closing

    # Once the scatter/chase schedule has permanently run out, no more
    # relief is coming for the rest of the match - raise every ghost's
    # effective danger weight accordingly (see PERMANENT_CHASE_START's
    # own note on why this, not pellet count, is the real endgame risk).
    if permanent_chase:
        for i in range(len(per_ghost_danger_weight)):
            per_ghost_danger_weight[i] += weights["permanent_chase_caution_weight"]

    # The AI ghost currently farthest from its nearest ally can't get
    # immediately backed up into a pincer - exploits exactly what the
    # user described from playing the goose-guarding ghost themselves
    # (Clyde/Inky are the ones you can't always trust to back you up).
    # A flat, local-only reduction to that one ghost's effective danger
    # weight, never below zero, never touching the learned weights dict
    # itself.
    ai_positions = ghost_positions[1:5]
    support_gaps = [
        min(
            (abs(gpos[0] - other[0]) + abs(gpos[1] - other[1]) for j, other in enumerate(ai_positions) if j != i),
            default=0,
        )
        for i, gpos in enumerate(ai_positions)
    ]
    weakest_index = support_gaps.index(max(support_gaps))
    weakest_gap = support_gaps[weakest_index]
    if weakest_gap > WEAK_LINK_GAP_THRESHOLD:
        reduction = weights["weak_link_weight"] * (weakest_gap - WEAK_LINK_GAP_THRESHOLD)
        per_ghost_danger_weight[weakest_index + 1] = max(0.0, per_ghost_danger_weight[weakest_index + 1] - reduction)

    # A ghost still caged/walking out of the house (first release or an
    # eat_ghost respawn) physically can't chase yet - eat_ghost resets
    # ai_ghost_has_left_house but never gives it a fresh release-time
    # countdown, so there's no timer to count down here, just this
    # direct flag. Without this, Pac-Man can end up needlessly wary of a
    # ghost's raw maze-distance while it's still stuck walking out,
    # which only matters when Pac-Man happens to be near the house exit.
    for i in respawning_indices:
        per_ghost_danger_weight[i] = max(0.0, per_ghost_danger_weight[i] - weights["respawn_discount_weight"])

    # One-tick-ahead ghost positions (the same cheap generic model the
    # recursive lookahead uses) - feeds future_pincer_weight below, the
    # one-tick-ahead version of pincer_penalty_weight.
    future_ghost_positions = _advance_ghosts_toward(
        tuple(ghost_positions),
        perceived_pacman_position if perceived_pacman_position is not None else position,
        False,
        pacman_position=position, pacman_direction=pacman_direction, player_ghost_weights=player_ghost_weights,
        scattering=scattering,
    )

    # Which power pellets the player ghost is currently close enough to
    # be actively guarding - feeds pellet_contest_weight below.
    player_ghost_position = ghost_positions[0]
    guarded_pellets = {
        pp for pp in power_pellets
        if player_ghost_position not in HOUSE_TILES  # a caged ghost guards nothing
        and abs(player_ghost_position[0] - pp[0]) + abs(player_ghost_position[1] - pp[1]) <= PELLET_GUARD_RADIUS
    }

    # Each AI ghost's own predicted personality target (Pinky's ambush
    # point, Inky's pincer reflection, Clyde's chase target) - avoiding
    # where a ghost is *aiming*, not just where it currently stands, is a
    # step toward out-thinking them instead of only reacting. During a
    # global scatter window every ghost is actually heading for its own
    # corner instead (see scatter_or_chase/move_ai_ghosts), so predicting
    # a chase-personality target they're not using would just make
    # Pac-Man avoid empty threats.
    predicted_targets = (
        list(AI_GHOST_SCATTER_CORNERS[1:])  # pinky, inky, clyde - matches AI_GHOST_PERSONALITIES[1:] below
        if scattering
        else [
            ghost_target_tile(personality, position, pacman_direction, ghost_position=gpos, blinky_position=ghost_positions[1])
            for personality, gpos in zip(AI_GHOST_PERSONALITIES[1:], ghost_positions[2:5])
        ]
    )

    clyde_position = ghost_positions[4]

    reversal_factor = 1.0
    if FREE_REVERSAL_FACTOR != 1.0 and pursuer_active and not frightened:
        here = PACMAN_DISTANCE_TABLE.get(position, {})
        if pursuer in HOUSE_TILES or pursuer not in here:
            free = True
        else:
            free = here[pursuer] >= FREE_GAP and _room_pair(position, pursuer) >= FREE_ROOM
        if free:
            free = all(
                other in HOUSE_TILES or here.get(other, 99) >= FREE_CLASSIC for other in ghost_positions[1:]
            )
        if free:
            reversal_factor = FREE_REVERSAL_FACTOR
    if (POWER_REVERSAL_CAUTION and pursuer_active and sim_state["now"] < sim_state["power_until"]
            and POWER_REVERSAL_FACTOR > reversal_factor):
        reversal_factor = POWER_REVERSAL_FACTOR
    # What each way guarantees against the worst the player ghost can do
    # (see PACMAN_WORST_WEIGHT). Not while the power lasts.
    worst_marks = None
    if PACMAN_WORST_WEIGHT and pursuer_active and sim_state["now"] >= sim_state["power_until"]:
        worst_marks = _worst_case_marks(
            options, pursuer, sim_state["left_house"][0], sim_state["ghost_last"][0], sim_state["now"],
            power_pellets,
        )
        held_ms = sim_state.get("hold_until", 0) - sim_state["now"]
        if held_ms > 0 and sim_state.get("hold_credit", True):
            # An idle ghost is taken to stand where it is that long (see
            # PACMAN_HOLD_FACTOR): those moves are in hand.
            credit = int(held_ms / PACMAN_MOVE_TRUE)
            worst_marks = {step: min(PACMAN_WORST_SAFE, sure + credit) for step, sure in worst_marks.items()}
        worst_best = max(worst_marks.values())
    # How many times running he has just stepped straight back (A, B, A, B
    # ...), and the tile another such step would take him to.
    flip_streak, flip_tile = 0, None
    if PACMAN_FLIP_WEIGHT and recent_positions and len(recent_positions) >= 3 and recent_positions[-1] == position:
        trail = recent_positions
        while flip_streak + 3 <= len(trail) and trail[-1 - flip_streak] == trail[-3 - flip_streak]:
            flip_streak += 1
        if flip_streak:
            flip_tile = trail[-2]
    # What the cloud of likely ghosts says about each way (see PACMAN_CLOUD_MODE). Not while the power lasts.
    cloud_risk = None
    if _CLOUD_MARKS["at"] == position and pursuer_active and sim_state["now"] >= sim_state["power_until"]:
        cloud_risk = _CLOUD_MARKS["risk"]
        cloud_best = min(cloud_risk.get(step, 1.0) for step in options)
    best_step, best_score = options[0], float("-inf")
    for step in options:
        distances = pacman_bfs_distances(step)

        target_distances = [distances[t] for t in targets if t in distances]
        target_distance = min(target_distances) if target_distances else 10_000

        # Discourages re-treading a tile visited recently - the explicit
        # anti-goose-guard-loop pressure nothing else here provides (see
        # recent_positions' docstring note above).
        loop_revisit_count = recent_positions.count(step) if recent_positions else 0

        anticipated_ghost_distances = [
            min((distances.get(tile, 10_000) for tile in escapes), default=10_000)
            for escapes in ghost_escape_options
        ]
        nearest_ghost_distance = min(anticipated_ghost_distances, default=10_000)

        danger_score = sum(
            (safe_radius - d) ** 2 * ghost_weight
            for d, ghost_weight in zip(anticipated_ghost_distances, per_ghost_danger_weight)
            if d < safe_radius
        )
        far_danger_score = sum(
            (FAR_DANGER_RADIUS - d) * far_weight
            for d, far_weight in zip(anticipated_ghost_distances, per_ghost_far_weight)
            if safe_radius <= d < FAR_DANGER_RADIUS
        )

        # Extra urgency once the maze is nearly cleared - don't let a
        # handful of stragglers go unclaimed. Scales the existing pull
        # toward the nearest target rather than adding a flat bonus: a
        # constant added equally to every candidate step can never change
        # which step wins the argmax below, which is what this term
        # actually did before this fix (it was provably inert - verified
        # by loss-pattern analysis showing losses cluster heavily in the
        # 70-100% pellet-fraction endgame, where this "urgency" feature
        # should have been doing real work pulling toward stragglers).
        endgame_urgency = max(0, 15 - len(targets)) / 15.0
        # The flip side of permanent_chase_caution_weight above: extra
        # pellet-seeking urgency specifically while the schedule is
        # still cycling (relief is still scheduled), front-loading
        # collection during the structurally safer part of the match -
        # same scaling pattern as endgame_rush_weight, for the same
        # reason (a flat bonus can't move the argmax; scaling the
        # existing distance pull can).
        early_rush_urgency = weights["early_rush_weight"] if not permanent_chase else 0.0
        score = -(weights["target_distance_weight"] + weights["endgame_rush_weight"] * endgame_urgency + early_rush_urgency) * target_distance - danger_score - far_danger_score
        score -= weights["loop_escape_weight"] * loop_revisit_count
        if cloud_risk is not None:
            score -= PACMAN_CLOUD_WEIGHT * (cloud_risk.get(step, 1.0) - cloud_best)
        if worst_marks is not None:
            score -= min(PACMAN_WORST_CLIP, PACMAN_WORST_WEIGHT * (worst_best - worst_marks[step]))
            if PACMAN_WORST_DEAD and step in _WORST_DEAD_STEPS:
                score -= PACMAN_WORST_DEAD
        if step == flip_tile and worst_marks is not None and worst_marks[step] < worst_best:
            # Only the half of the back-and-forth that the worst-case check
            # already calls the worse way: the step back out of it stays free.
            score -= PACMAN_FLIP_WEIGHT * flip_streak
        harvest = pursuer_active and PURSUER_PLAN == "harvest"
        if harvest:
            # Safe is safe: a power pellet still reachable ahead of the chaser
            # (measured from the house door while it is caged), or a long
            # lead. Only the shortfall below that line costs anything, so the
            # rest of the time the pellet terms decide where Pac-Man goes.
            chaser_tile = pursuer if pursuer_outside else HOUSE_EXIT_TARGET
            covered = bool(power_pellets) and (step in power_pellets or _covered(step, chaser_tile, power_pellets))
            if not covered:
                stay_ahead = _evasion_table(evasion_key).get(step, {}).get(chaser_tile)
                if stay_ahead is not None:
                    comfort = PURSUER_COMFORT if power_pellets else PURSUER_COMFORT_BARE
                    score -= weights["pursuer_evasion_weight"] * PURSUER_HARVEST_FACTOR * max(0, comfort - stay_ahead)
        if route:
            score += weights["pursuer_route_weight"] * route_values.get(step, -PURSUER_ROUTE_CLIP)
        if pursuer_outside and not harvest and not route:
            score += weights["pursuer_kite_weight"] * min(distances.get(pursuer, PURSUER_KITE_RANGE), PURSUER_KITE_RANGE)
        if (pursuer_outside or (pursuer_active and PURSUER_HOUSE_EVASION)) and not harvest and not route:
            # Penalise a move by how much stay-ahead time it gives up compared
            # with the best move available - but only up to a limit, so this
            # never outweighs the lookahead's "this way gets you killed by
            # another ghost" (strong evasion alone walked into the AI ghosts).
            # A chaser still in the house is measured from the door it will
            # come out of, so its cooldown is spent getting well clear.
            chaser_tile = pursuer if pursuer_outside else HOUSE_EXIT_TARGET
            table = _evasion_table(evasion_key)
            evasion_moves = table.get(step, {}).get(chaser_tile)
            if evasion_moves is not None:
                best_evasion = max(table.get(o, {}).get(chaser_tile, 0) for o in options)
                if best_evasion < PURSUER_EVASION_CAP:
                    score -= weights["pursuer_evasion_weight"] * min(best_evasion - evasion_moves, PURSUER_EVASION_CLIP)
        if pursuer_outside and PURSUER_COVER_FACTOR and power_pellets and not route:
            # Stay where a power pellet can still be reached ahead of the
            # chaser. Free to roam while that holds; as the chaser closes
            # in, fewer and fewer moves keep it true, until the only ones
            # left lead to the pellet - with the chaser right behind.
            if step in power_pellets or _covered(step, pursuer, power_pellets):
                score += weights["pursuer_evasion_weight"] * PURSUER_COVER_FACTOR
        if pursuer_active:
            if step in power_pellets and pellets and pursuer_gap > PURSUER_SAVE_GAP and not (
                    pp_planned and route_values is not None
                    and route_values.get(step, -PURSUER_ROUTE_CLIP) >= -PURSUER_PP_PLAN_MARGIN):
                score -= weights["power_pellet_save_weight"]
            if refuge_pellets:
                # A power pellet near the ghost-house door is worth far more
                # (time to farm the door), so it counts as closer than it is.
                score -= weights["pursuer_refuge_weight"] * min(
                    distances.get(t, 10_000) + PURSUER_DOOR_BIAS * _door_distance(t) for t in refuge_pellets
                )
            elif PURSUER_CAMP_FACTOR and camp_pellet is not None:
                # No emergency yet: wait near the power pellet closest to the
                # ghost-house door, where a power window is worth the most.
                score -= weights["pursuer_refuge_weight"] * PURSUER_CAMP_FACTOR * distances.get(camp_pellet, 10_000)

        # Multi-tile recursive lookahead: how long this branch keeps
        # surviving, how many dots it picks up, and how many ghost-ambush
        # opportunities (a power pellet reached with ghosts nearby) it
        # sets up over the next several tiles, not just this one step.
        branch = lookahead_scores.get(step, (0, 0, 0))
        survived, lookahead_dots, lookahead_ambush = branch[:3]
        if len(branch) > 3 and pursuer_active:
            score += weights["pursuer_evasion_weight"] * PURSUER_LEAF_FACTOR * branch[3]
        score += weights["lookahead_survival_weight"] * survived
        if pessimistic_scores:
            score += weights["lookahead_survival_weight"] * PURSUER_PESSIMISM_FACTOR * pessimistic_scores.get(step, (0,))[0]
        for doubt in doubt_scores:
            score += weights["lookahead_survival_weight"] * PURSUER_DOUBT_FACTOR * doubt.get(step, (0,))[0]
        if adversary_scores is not None:
            score += weights["lookahead_survival_weight"] * PURSUER_ADVERSARY_FACTOR * adversary_scores.get(step, 0)
        if room_setup is not None:
            score += PURSUER_ROOM_WEIGHT * min(_room(room_setup, step), PURSUER_ROOM_CAP)
        score += weights["lookahead_pellet_weight"] * lookahead_dots
        score += weights["lookahead_ambush_weight"] * lookahead_ambush

        # Raw count of ghosts currently in near-field range, regardless of
        # direction - separate from danger_score, which already weighs how
        # dangerous each one individually is.
        threats_in_range = sum(1 for d in anticipated_ghost_distances if d < safe_radius)
        score -= weights["total_threat_count_weight"] * threats_in_range

        # How many distinct directions those near threats are coming from -
        # the actual signature of a coordinated pincer, as opposed to
        # several ghosts all approaching from the same side.
        near_indices = [i for i, d in enumerate(anticipated_ghost_distances) if d < safe_radius]
        if len(near_indices) >= 2:
            directions = set()
            for i in near_indices:
                gx, gy = ghost_positions[i]
                dx = 0 if gx == step[0] else (1 if gx > step[0] else -1)
                dy = 0 if gy == step[1] else (1 if gy > step[1] else -1)
                directions.add((dx, dy))
            score -= weights["pincer_penalty_weight"] * (len(directions) ** 2)

        # The one-tick-ahead version of the pincer check just above:
        # penalizes a pincer that's about to assemble (using the generic
        # one-step-advance model), not just one that's already formed.
        future_near_indices = [i for i, g in enumerate(future_ghost_positions) if distances.get(g, 10_000) < safe_radius]
        if len(future_near_indices) >= 2:
            future_directions = set()
            for i in future_near_indices:
                gx, gy = future_ghost_positions[i]
                dx = 0 if gx == step[0] else (1 if gx > step[0] else -1)
                dy = 0 if gy == step[1] else (1 if gy > step[1] else -1)
                future_directions.add((dx, dy))
            score -= weights["future_pincer_weight"] * (len(future_directions) ** 2)

        # Avoid each AI ghost's predicted target tile too, not just its
        # current position.
        predicted_distances = [distances.get(t, 10_000) for t in predicted_targets]
        nearest_predicted = min(predicted_distances, default=10_000)
        if nearest_predicted < safe_radius:
            score -= weights["predicted_target_avoidance_weight"] * (safe_radius - nearest_predicted)

        # Inky's target is a reflection of Blinky's position through a
        # point ahead of Pac-Man - while Blinky is close enough that his
        # position is actually driving that reflection, reward distance
        # from wherever it lands. Smooth and Inky-specific, unlike the
        # thresholded, all-three-lumped-together avoidance just above.
        blinky_distance = distances.get(ghost_positions[1], 10_000)
        if blinky_distance < INKY_DECOY_BLINKY_RADIUS:
            inky_target_distance = min(distances.get(predicted_targets[1], 10_000), INKY_DECOY_DISTANCE_CAP)
            score += weights["inky_decoy_weight"] * inky_target_distance

        # More open neighboring tiles at the candidate step is a graded
        # safety margin beyond the binary deadend check below.
        exits = len(pacman_neighbours(step))
        score += weights["mobility_bonus_weight"] * exits

        # Staying close to a real intersection (3+ exits) keeps maximum
        # future escape options open.
        nearest_junction = min((distances.get(j, 10_000) for j in JUNCTION_TILES), default=10_000)
        score -= weights["junction_proximity_weight"] * nearest_junction

        # Rewards whichever candidate has the best lead to its own
        # nearest junction/escape point over the nearest ghost's
        # straight-line distance to that same point - lets
        # commitment_weight actually commit to the *right* direction,
        # not just whichever one Pac-Man already happened to face.
        nearest_junction_tile = min(JUNCTION_TILES, key=lambda j: distances.get(j, 10_000), default=None)
        if nearest_junction_tile is not None and nearest_junction < 10_000:
            nearest_ghost_to_junction = min(
                (abs(g[0] - nearest_junction_tile[0]) + abs(g[1] - nearest_junction_tile[1]) for g in ghost_positions),
                default=10_000,
            )
            exit_race_lead = nearest_ghost_to_junction - nearest_junction
            score += weights["exit_race_weight"] * exit_race_lead

        # Stepping into a dead end while a ghost is closing in is a death
        # trap; avoid corridors with no other way out (a doorway into the
        # ghost house doesn't count as an exit Pac-Man can use). A 2-exit
        # tile isn't a full trap but is still worth some extra caution.
        if exits <= 1 and nearest_ghost_distance < PACMAN_DEADEND_RADIUS:
            score -= weights["deadend_penalty"]
        elif exits == 2 and nearest_ghost_distance < PACMAN_DEADEND_RADIUS:
            score -= weights["semi_deadend_penalty"]

        # Preventive: avoid walking into a tight pocket at all once
        # multiple ghosts are converging on it from further out, even
        # before any single one is close enough to trip the reactive
        # penalties just above.
        if exits <= 2:
            ghosts_converging = sum(1 for d in anticipated_ghost_distances if d < POCKET_CROWD_RADIUS)
            if ghosts_converging >= 2:
                score -= weights["trap_crowd_penalty"] * ghosts_converging

        # The tunnel row can break a chase outright by wrapping Pac-Man
        # across the map.
        if step[1] in TUNNEL_ROWS and nearest_ghost_distance < PACMAN_DEADEND_RADIUS:
            score += weights["tunnel_escape_bonus"]

        # Doubling back without real cause makes for erratic, inefficient
        # play - only worth it when it's genuinely the best option anyway.
        if pacman_direction != (0, 0):
            reverse_direction = (-pacman_direction[0], -pacman_direction[1])
            if direction_between(position, step) == reverse_direction:
                score -= weights["reversal_penalty"] * reversal_factor

            # Under real pressure, keep going the way already committed
            # to instead of flipping to whichever option looks marginally
            # better this exact tick - reversal_penalty alone only
            # catches a literal 180-degree reversal, not the general
            # indecisive wavering between two close-scoring options at a
            # junction that can cost a real, slipping-past-the-guard gap
            # (see commitment_weight's own note above for why).
            if threats_in_range >= 1 and direction_between(position, step) == pacman_direction:
                score += weights["commitment_weight"] * threats_in_range

            # A direction change timed for right after the AI ghosts'
            # last perception refresh is worth more than one right
            # before their next one - they won't notice the turn for
            # longer, instead of revealing the new direction just as
            # they're about to re-sight anyway.
            if direction_between(position, step) != pacman_direction:
                score += weights["perception_window_weight"] * (
                    time_until_perception_refresh / AI_GHOST_PERCEPTION_INTERVAL
                )

        # Prefers routes through pellet-dense pockets, and keeps a second
        # target in mind so Pac-Man doesn't tunnel-vision on the nearest
        # pellet alone.
        nearby_pellet_count = sum(1 for t in targets if distances.get(t, 10_000) <= 3)
        score += weights["pellet_density_bonus"] * nearby_pellet_count
        if len(target_distances) > 1:
            second_nearest = sorted(target_distances)[1]
            score -= weights["second_target_distance_weight"] * second_nearest

        # A gentle, constant pull toward power pellets even without
        # immediate danger, on top of the reactive detour bonus below.
        power_distances = [distances[t] for t in power_pellets if t in distances]
        nearest_power = min(power_distances, default=10_000)
        if nearest_power < 10_000:
            score -= weights["power_pellet_proactive_weight"] * nearest_power

            # A much stronger version of the same pull, specifically when
            # actually cornered right now (tight on exits AND a ghost
            # already close) - the "use a power pellet as a bail-out"
            # skill, distinct from the constant pull above and from
            # cluster_bonus's "time it for a multi-kill" skill further
            # down, neither of which is conditioned on being trapped.
            if exits <= 2 and nearest_ghost_distance < PACMAN_DEADEND_RADIUS:
                score -= weights["corner_bailout_weight"] * nearest_power

            # A stronger pull specifically as a scatter window is about
            # to end - timed so the frightened effect covers the chase
            # window that's about to resume, instead of being wasted
            # mid-scatter when little is actually hunting.
            if 0 < scatter_time_remaining <= POWER_DURATION:
                scatter_urgency = 1.0 - (scatter_time_remaining / POWER_DURATION)
                score -= weights["scatter_timing_weight"] * scatter_urgency * nearest_power

            # Softens (or cancels, at a high enough value) the pull above
            # specifically when the nearest power pellet is actually
            # being guarded by the player ghost right now, instead of
            # detouring into a pellet that's actively contested.
            nearest_power_tile = min(power_pellets, key=lambda t: distances.get(t, 10_000), default=None)
            if nearest_power_tile in guarded_pellets:
                score += weights["pellet_contest_weight"] * nearest_power

        # A power pellet is worth detouring for when under real pressure, and
        # even more so when it will catch every ghost clustered together.
        if step in power_pellets:
            score += weights["cluster_bonus"] * cluster_score
            if nearest_ghost_distance < 8:
                score += 6

        # Staying just outside Clyde's scatter-trigger radius keeps him
        # actively chasing (and so away from camping with the others)
        # instead of retreating to regroup.
        clyde_distance = distances.get(clyde_position, 10_000)
        if clyde_distance > CLYDE_SCATTER_RADIUS:
            score += weights["clyde_bait_weight"]

        if step in ghost_positions:
            score -= weights["occupied_tile_penalty_weight"]
        elif sim_state and step in sim_state.get("hold_tiles", ()):
            # A ghost going back and forth between two tiles is on either of
            # them at any moment (see PACMAN_HOLD_SHUFFLE).
            score -= PACMAN_HOLD_SHUFFLE_TILE

        # A little variation prevents identical, mechanical routes.
        score += random.random() * 0.2
        if PACMAN_DECISION_TRACE is not None:
            PACMAN_DECISION_TRACE.append([
                step[0], step[1], round(float(score), 1),
                None if worst_marks is None else int(worst_marks[step]),
                round(float(route_values.get(step, -PURSUER_ROUTE_CLIP)), 1) if route else None,
                int(survived),
            ])
        if score > best_score:
            best_score, best_step = score, step

    return best_step


# ---------------------------------------------------------------------------
# Sound
# ---------------------------------------------------------------------------
# Arcade-style effects of this game's own, synthesised here when a real game
# starts: no sound files, and none of the old arcade game's recordings or
# tunes. Headless runs (SDL_AUDIODRIVER=dummy) get no sounds at all, so
# training and testing are untouched. PACMAN_SOUND=0 turns them off,
# PACMAN_SOUND_VOLUME (0-1) sets how loud, and M mutes/unmutes while playing.
SOUND_ON = os.environ.get("PACMAN_SOUND", "1") != "0"
SOUND_VOLUME = float(os.environ.get("PACMAN_SOUND_VOLUME", "0.6"))
# name: the tones played one after another, each (start Hz, end Hz, ms,
# loudness 0-1, "square" or "triangle"[, vibrato depth Hz, vibrato rate Hz]).
# A start pitch of 0 is a pause.
SOUND_RECIPES = {
    # Pac-Man eats a pellet (the two alternate).
    "chomp_a": [(480, 700, 45, 0.45, "triangle")],
    "chomp_b": [(700, 480, 45, 0.45, "triangle")],
    # He eats a power pellet; then the wobble that lasts as long as the power
    # does (175 Hz for 480 ms is a whole number of waves, so it loops cleanly).
    "power": [(220, 880, 260, 0.5, "square")],
    "power_loop": [(175, 175, 480, 0.3, "triangle", 40, 6.25)],
    "eat_ghost": [(300, 1500, 220, 0.5, "square"), (1500, 1900, 80, 0.4, "square")],
    # The player's ghost leaves the house: yours to steer now.
    "release": [(660, 990, 90, 0.35, "square")],
    "start": [(440.0, 440.0, 80, 0.4, "square"), (554.4, 554.4, 80, 0.4, "square"),
              (659.3, 659.3, 80, 0.4, "square"), (880.0, 880.0, 170, 0.4, "square")],
    # Caught: Pac-Man winds down, then the ghost team's flourish.
    "caught": [(900, 120, 520, 0.5, "triangle", 60, 14), (0, 0, 70, 0, "square"),
               (523.3, 523.3, 90, 0.45, "square"), (659.3, 659.3, 90, 0.45, "square"),
               (784.0, 784.0, 90, 0.45, "square"), (1046.5, 1046.5, 240, 0.45, "square")],
    # He cleared the maze: the ghost team lost.
    "cleared": [(392.0, 392.0, 170, 0.5, "triangle"), (311.1, 311.1, 170, 0.5, "triangle"),
                (261.6, 261.6, 170, 0.5, "triangle"), (196.0, 196.0, 400, 0.5, "triangle")],
    "stalemate": [(330, 330, 160, 0.45, "triangle"), (0, 0, 50, 0, "square"),
                  (330, 330, 160, 0.45, "triangle"), (0, 0, 50, 0, "square"), (262, 262, 340, 0.45, "triangle")],
}


# === whole-game planner: begin ===
# Pac-Man's whole-game planner (PACMAN_PLANNER, default on).
#
# Everything above decides one move at a time from what the next few seconds
# look like. Against a ghost that simply runs him down that is not enough:
# the player's ghost is 9% faster than he is, so a plain chase always ends
# the same way, and the only thing that resets it is a power pellet eaten
# with the ghost close enough to be eaten too. Getting four such resets to
# fall where they are needed, with every pellet eaten in between, is a
# problem about the whole game, not about the next move.
#
# So when he knows how the player's ghost moves, he plans the whole rest of
# the game: a beam search over his own moves on an exact frame-by-frame copy
# of the rules (all five ghosts, real move times, the scatter schedule, the
# classic ghosts' stale sightings, house exits, power pellets, respawns),
# kept going until every pellet is eaten. The classic ghosts are taken to
# make the move they intend (their random moves cannot be known); the plan
# is checked against the real game at every move and searched again when a
# ghost has done something else.
#
# "Knows how the ghost moves" means one of:
#   - he was told (a headless test with PACMAN_PLAYER_GHOST_HIDDEN=0) and
#     the model only looks at where it and he stand;
#   - he has recognised it: each move the ghost makes is compared with what
#     two simple pictures of a ghost would have done (a shortest-path
#     chaser, and a chaser that also cuts off his exits), and a picture
#     that has called PACMAN_PLANNER_TRUST_RUN moves in a row is believed
#     until it gets one wrong.
#   - neither: it is then a person, or plays like one. He goes on deciding
#     move by move, helped by a cloud of likely ghosts (see PACMAN_CLOUD
#     below).
# A plan built on a wrong
# picture is far worse than no plan (measured against the stand-in for the
# person playing: 46-53% of the maze eaten, against 58% for the
# move-by-move play above), which is why the rule is strict: a first
# version that believed a picture after 10 good moves lost 5 points to it.
PACMAN_PLANNER = os.environ.get("PACMAN_PLANNER", "1") == "1"
# Search steps one plan may cost; the beam is made as wide as that allows.
PACMAN_PLANNER_NODES = int(os.environ.get("PACMAN_PLANNER_NODES", "20000"))
PACMAN_PLANNER_MAX_WIDTH = int(os.environ.get("PACMAN_PLANNER_MAX_WIDTH", "400"))
PACMAN_PLANNER_GAP = float(os.environ.get("PACMAN_PLANNER_GAP", "4"))  # per tile of lead over the player ghost
PACMAN_PLANNER_GAP_CAP = float(os.environ.get("PACMAN_PLANNER_GAP_CAP", "30"))
# What a power pellet still on the board is worth to the search, in pellets'
# worth x 100 (a pellet eaten is 100). This is the setting that matters
# most: at 120 he ate them much as he came to them and cleared 26% of games
# against the chaser when told its moves; at 1000 - held back until nothing
# else will do - 58% (3000: 47%). 208 games each on the same seeds.
PACMAN_PLANNER_PP = float(os.environ.get("PACMAN_PLANNER_PP", "1000"))
PACMAN_PLANNER_RISK = float(os.environ.get("PACMAN_PLANNER_RISK", "400"))  # a classic ghost's random move could catch him
PACMAN_PLANNER_RISK_NOW = float(os.environ.get("PACMAN_PLANNER_RISK_NOW", "3000"))  # the same, on the very next move
PACMAN_PLANNER_NEAR = float(os.environ.get("PACMAN_PLANNER_NEAR", "3"))  # per tile to the nearest pellet left
# A picture of the ghost is believed once it has called this many of the
# ghost's moves in a row. One wrong call ends that, and the next spell of
# belief then takes twice as many: a ghost that has once stopped moving like
# a plain chaser is not one. Measured at 40: no loss against the stand-in
# for the person playing (-0.5 points of maze, 208 games) or a sharper
# "pouncing" ghost (-0.2); 37.5% of games cleared against the chaser when
# not told its moves (60: 29%). In 169 recorded games of the real player a
# run of 40 was reached during 1% of their moves.
PACMAN_PLANNER_TRUST_RUN = int(os.environ.get("PACMAN_PLANNER_TRUST_RUN", "40"))
# What he does when the search finds no way to clear the maze: "follow" the
# line that lives longest and eats most, or "old" - play as before.
PACMAN_PLANNER_PARTIAL = os.environ.get("PACMAN_PLANNER_PARTIAL", "follow")
PACMAN_PLANNER_MIN_LIFE = int(os.environ.get("PACMAN_PLANNER_MIN_LIFE", "24"))
# Keep searching while a plan that clears the maze is already in hand, and
# switch to a quicker one when the search finds it.
PACMAN_PLANNER_IMPROVE = os.environ.get("PACMAN_PLANNER_IMPROVE", "0") == "1"
PACMAN_PLANNER_STATIC = os.environ.get("PACMAN_PLANNER_STATIC", "0") == "1"  # also with no player ghost at all
# Planning against a ghost that is not a plain chaser - a person, or a model
# of one - with ONE predicted line for it (PACMAN_PLANNER_FITTED, default off:
# measured and not adopted. It clears mazes against a ghost whose moves the
# model calls 97% or more of the time; against one it calls about 91% of the
# time it was no better than no plan, or worse, and the player's own moves
# are called less often than that. Kept for comparisons; PACMAN_CLOUD is what
# is used against a person.) He then plans with the model
# fitted to the player's recorded games (PACMAN_PLANNER_FITTED_MODEL, a file
# in player_ghost_versions/), which calls about 85% of the real player's
# moves (71% where three or more ways are open). Three things make that
# usable:
#   - The safeguard (PACMAN_PLANNER_WC). A plan built on "the ghost will
#     probably go there" walks straight into a ghost that goes somewhere
#     else on purpose: against a test ghost that leaves the model whenever
#     it can force a catch, the plan without a safeguard was caught at a
#     median 12 seconds, with 28% of the maze eaten (the move-by-move play
#     above: 53%). So every position in a plan is also looked up in the
#     exact one-on-one table (_worst_case_table): if a ghost playing
#     perfectly from there could catch him within PACMAN_PLANNER_WC_SAFE
#     moves, and there is no power pellet he reaches first, the position is
#     charged in proportion - in full for his next PACMAN_PLANNER_WC_NEAR
#     moves, a quarter beyond. At 60 the plan still lost to that ghost; from
#     120 up it gains against it and against the stand-in for the player.
#   - The fitted model costs about eight times a chaser's move to work out,
#     and how far the search gets decides everything (a third of the search:
#     no gain; a ninth: a loss). So it is used for his next
#     PACMAN_PLANNER_FITTED_DEPTH moves only, and beyond that the model's
#     saved moves table (<model>_hops.json), a plain chaser where the table
#     is silent. 30 moves keeps the whole gain; 12 and 20 do not.
#   - A ghost that is standing still (the idle-ghost rule above) is not
#     planned against: the model never stands still.
# Measured with a search the size a real game affords, him not told how the
# ghost moves and planning with a model fitted on other games than the
# ghost's, 104 games on the same seeds: against the stand-in for the player
# 5 mazes cleared (none without) and 61.6% of the maze eaten (58.4%);
# against the test ghost that leaves the model 6 cleared (none) and 58.8%
# (53.0%).
PACMAN_PLANNER_FITTED = os.environ.get("PACMAN_PLANNER_FITTED", "0") == "1"
PACMAN_PLANNER_FITTED_MODEL = os.environ.get("PACMAN_PLANNER_FITTED_MODEL", "refit_20261125")
PACMAN_PLANNER_FITTED_DEPTH = int(os.environ.get("PACMAN_PLANNER_FITTED_DEPTH", "30"))
# Search steps for one such plan in a headless test: what the real game's
# search process gets through in its PACMAN_PLANNER_SECONDS.
PACMAN_PLANNER_FITTED_NODES = int(os.environ.get("PACMAN_PLANNER_FITTED_NODES", "5500"))
PACMAN_PLANNER_FITTED_CAP = int(os.environ.get("PACMAN_PLANNER_FITTED_CAP", "9000"))  # and never more than this
PACMAN_PLANNER_WORKERS = int(os.environ.get("PACMAN_PLANNER_WORKERS", "1"))  # search processes for such a plan
# What a power pellet still on the board is worth in such a plan (see PACMAN_PLANNER_PP, which is for a
# plain chaser).
PACMAN_PLANNER_FITTED_PP = float(os.environ.get("PACMAN_PLANNER_FITTED_PP", "1000"))
PACMAN_PLANNER_WC = float(os.environ.get("PACMAN_PLANNER_WC", "800"))
PACMAN_PLANNER_WC_SAFE = int(os.environ.get("PACMAN_PLANNER_WC_SAFE", "66"))
PACMAN_PLANNER_WC_NEAR = int(os.environ.get("PACMAN_PLANNER_WC_NEAR", "12"))
PACMAN_PLANNER_WC_FAR = float(os.environ.get("PACMAN_PLANNER_WC_FAR", "0.25"))

# --- against a person: a cloud of likely ghosts ------------------------------
# A person's ghost cannot be forecast as one line. Measured on the player's
# recorded games: the best single forecast is within 2 tiles of the real ghost
# 78% of the time after 4 of its moves, 59% after 8, 44% after 12. But the
# ODDS of each way it might go can be had honestly (the "timed odds" model:
# what was on screen now and two ghost moves ago, how long since Pac-Man turned
# round, how long since the ghost did - a person turns round 2-3 moves after
# Pac-Man does), and of eight ghosts drawn from those odds the real one is
# within 2 tiles of at least one 96% / 87% / 81% of the time after 4 / 8 / 12
# moves.
#
# So against a ghost that is not a plain chaser a cloud stands in for it:
# PACMAN_CLOUD_K ghosts, each drawn move by move from the model's odds, all
# starting where the real one is. After each of his moves every line of play
# open to him for his next PACMAN_CLOUD_DEPTH moves is run against all of
# them (and the classic ghosts' expected moves), and at his next decision each
# way open is marked by the share of the cloud that catches him down it on
# the line that loses fewest (see PACMAN_CLOUD_MODE). He goes on deciding
# move by move; the marks are one more thing weighed.
#
# Measured over 300 simulated games each on the same seeds, him planning with
# a model fitted on other recorded games than the stand-in's:
#   a stand-in that turns, hesitates and doubles back at the player's own
#   rates (drawn from the timed odds)      62.7% -> 66.6% of the maze eaten
#                                          (+3.9 points, z 3.9), caught after a
#                                          median 66 s -> 79 s
#   the sharper "pouncing" ghost           54.3% -> 57.4% (+2.8, z 2.6)
#   the older stand-in (one fixed move
#   for every position)                    61.1% -> 61.4% (no change)
# With PACMAN_CLOUD=0 he plays game for game as before.
PACMAN_CLOUD = os.environ.get("PACMAN_CLOUD", "1") == "1"
PACMAN_CLOUD_MODEL = os.environ.get("PACMAN_CLOUD_MODEL", "timed_odds_lite")
PACMAN_CLOUD_K = int(os.environ.get("PACMAN_CLOUD_K", "16"))
PACMAN_CLOUD_DEPTH = int(os.environ.get("PACMAN_CLOUD_DEPTH", "12"))
PACMAN_CLOUD_TOTAL = int(os.environ.get("PACMAN_CLOUD_TOTAL", "48"))  # how far a plan is searched in all, in his moves
PACMAN_CLOUD_WIDTH = int(os.environ.get("PACMAN_CLOUD_WIDTH", "40"))  # lines kept at each move of the search
PACMAN_CLOUD_DEATH = float(os.environ.get("PACMAN_CLOUD_DEATH", "3000"))
PACMAN_CLOUD_PP = float(os.environ.get("PACMAN_CLOUD_PP", "400"))  # a power pellet still on the board (see PACMAN_PLANNER_PP)
PACMAN_CLOUD_FAVOURITE = os.environ.get("PACMAN_CLOUD_FAVOURITE", "1") == "1"  # one of the cloud always takes the likeliest way
PACMAN_CLOUD_FAR = os.environ.get("PACMAN_CLOUD_FAR", "chaser")  # beyond the cloud: "chaser" (the nearest of them) or "none"
# How the cloud is used. "marks": as above - each way open is marked down by PACMAN_CLOUD_WEIGHT times the
# share of the cloud that catches him down it (less the same for his best way). "plan": he follows the search's
# own line of play instead of deciding move by move - measured first and 10 points of maze WORSE (54.1% against
# 64.2%, 150 games), most of it lost to the classic ghosts, which the move-by-move play dodges far better than
# the search does. Kept for comparisons only.
# Weights tried with 8 ghosts (300 games each, points gained): 300 +1.5, 800 +3.0, 1500 +4.4 (4000, 100
# games: +1.8). 16 ghosts in place of 8: +3.8 against +3.0. 8 moves deep in place of 12: +2.4; 20: no better.
PACMAN_CLOUD_MODE = os.environ.get("PACMAN_CLOUD_MODE", "marks")
PACMAN_CLOUD_WEIGHT = float(os.environ.get("PACMAN_CLOUD_WEIGHT", "1200"))
# A way down which no line of play survives the cloud is marked 1 to 2 by how soon the end comes (0: all such
# ways are marked 1 alike; measured +0.5 points better than that against the stand-in, no worse elsewhere).
PACMAN_CLOUD_LASTING = os.environ.get("PACMAN_CLOUD_LASTING", "1") == "1"
_CLOUD_MARKS = {"at": None, "risk": None}  # the marks for the decision in hand: his tile, {way: risk}

_CLOUD_BUCKET = (0, 1, 2, 3, 4, 4, 5, 5, 5, 5, 6)  # how "moves since" is grouped: 0, 1, 2, 3, 4-5, 6-9, 10 or more
# one ghost of the cloud, as a row of whole numbers
(_CF_TILE, _CF_PREV, _CF_LAST, _CF_LEFT, _CF_ALIVE, _CF_DIR, _CF_SPR, _CF_SPT, _CF_SOR, _CF_SOT, _CF_AWAY, _CF_LPD,
 _CF_TWO, _CF_H1P, _CF_H1D, _CF_H1T, _CF_H2P, _CF_H2D, _CF_H2T) = range(19)
_CF_COUNT = 19
_CLOUD_NEVER = -10 ** 9


class _CloudTrack(object):
    """What the timed-odds model needs to know about the game so far, kept up
    to date at every move the player's ghost makes outside the house: how many
    of its moves ago Pac-Man last turned and last turned right round, how many
    since the ghost itself did, how long it has been walking away from him,
    and where he was at its last two moves."""

    def __init__(self):
        self.spr = self.spt = self.sor = self.sot = 99
        self.away = 0
        self.lpd = 5  # his heading at the ghost's last move (5: none seen yet)
        self.two = 4  # the ghost's heading before its last move (4: none)
        self.h1 = (0, 4, _CLOUD_NEVER)  # (his tile, his heading, ms) at the ghost's last move
        self.h2 = (0, 4, _CLOUD_NEVER)  # and at the one before
        self.cur = 4  # the way of the ghost's last move

    def fields(self):
        return (self.spr, self.spt, self.sor, self.sot, self.away, self.lpd, self.two) + self.h1 + self.h2 + (self.cur,)

    def observe(self, planner, ghost, direction, chosen, pacman, pacman_direction, t_ms):
        """The ghost on `ghost` heading `direction` is stepping `chosen`."""
        dirs = _CLOUD_DIR_INDEX
        pd = dirs.get(tuple(pacman_direction), 4)
        if self.lpd == 5:
            self.lpd = pd
        elif pd != self.lpd and pd != 4:
            self.spt = 0
            if pd == _CLOUD_OPPOSITE[self.lpd]:
                self.spr = 0
            self.lpd = pd
        dr = dirs.get(tuple(direction), 4)
        ch = dirs.get(tuple(chosen), 4)
        rev = _CLOUD_OPPOSITE[dr]
        table = PACMAN_DISTANCE_TABLE
        d_fwd = d_rev = 99
        has_rev = has_fwd = False
        for k, way in enumerate(CARDINALS):
            tile = step_position(ghost, way)
            if not is_open(*tile) or tile in HOUSE_TILES:
                continue
            gap = table.get(tile, {}).get(pacman, 99)
            if k == rev:
                has_rev, d_rev = True, gap
            else:
                has_fwd = True
                if gap < d_fwd:
                    d_fwd = gap
        if has_rev and dr != 4 and has_fwd:
            self.away = self.away + 1 if (d_rev < d_fwd and ch != rev) else 0
        if ch == rev:
            self.sor = 0
            self.away = 0
        else:
            self.sor += 1
        self.sot = 0 if ch != dr else self.sot + 1
        self.spr += 1
        self.spt += 1
        self.two = dr
        self.cur = ch
        self.h2 = self.h1
        self.h1 = (planner.idx[pacman], pd, t_ms)


_CLOUD_DIR_INDEX = {way: k for k, way in enumerate(CARDINALS)}
_CLOUD_OPPOSITE = [_CLOUD_DIR_INDEX[(-way[0], -way[1])] for way in CARDINALS] + [4, 5]


def _cloud_observe(game, now, chosen):
    """Called as the player's ghost makes a move of its own choosing (before
    the move is applied): keeps the game's _CloudTrack."""
    if not (PACMAN_PLANNER and PACMAN_CLOUD):
        return
    if not game.player_ghost_seen:
        game._cloud_track = _CloudTrack()  # (its first such move of the game)
    track = getattr(game, "_cloud_track", None)
    if track is None:
        track = game._cloud_track = _CloudTrack()
    track.observe(
        _planner(), game.ghost_position, game.ghost_direction, chosen, game.pacman_position, game.pacman_direction,
        now - game.start_time,
    )


class _CloudLater(object):
    """A _Cloud not yet built: its tables take a few seconds, and in a real
    game only the search process ever needs them."""

    def __init__(self, planner, weights):
        self.planner, self.weights = planner, weights

    def built(self):
        return _Cloud(self.planner, self.weights)


class _Cloud(object):
    """The timed-odds model worked out for many ghosts at once (numpy), and
    the cloud's moves. The same answers as the code the model was fitted with
    (compared on every recorded move of the player: see the scratch check
    cloudcheck.py - change either and the comparison has to be run again)."""

    def __init__(self, planner, weights):
        import numpy as np
        self.np = np
        self.planner = planner
        N = self.N = planner.N
        XY = planner.tiles
        idx = planner.idx
        w = self.w = lambda name: float(weights.get(name, 0.0))
        self.CX = np.array([t[0] for t in XY], dtype=np.int64)
        self.CY = np.array([t[1] for t in XY], dtype=np.int64)
        self.DX = np.array([way[0] for way in CARDINALS] + [0, 0], dtype=np.int64)
        self.DY = np.array([way[1] for way in CARDINALS] + [0, 0], dtype=np.int64)
        self.OPP = np.array(_CLOUD_OPPOSITE, dtype=np.int64)
        self.BK = np.array(_CLOUD_BUCKET, dtype=np.int64)
        self.WAY = np.full((N, 4), -1, dtype=np.int64)
        for a, here in enumerate(XY):
            if here in HOUSE_TILES:
                continue
            for k, way in enumerate(CARDINALS):
                to = step_position(here, way)
                if is_open(*to) and to not in HOUSE_TILES and to in idx:
                    self.WAY[a, k] = idx[to]
        self.DIRIDX = {}
        for (a, b), way in planner.DIRS.items():
            self.DIRIDX[(a, b)] = _CLOUD_DIR_INDEX.get(tuple(way), 4)
        self.HOUSE = np.array(planner.HOUSE, dtype=bool)
        self.EXIT = np.array(planner.EXIT, dtype=np.int64)
        self.EXITDIR = np.array([self.DIRIDX.get((a, planner.EXIT[a]), 4) for a in range(N)], dtype=np.int64)
        self.HOME0 = planner.HOME[0]
        self.ROW23 = np.array([t[1] == 23 for t in XY], dtype=bool)
        raw = np.full((N, N), -1, dtype=np.int64)
        for a, row in PACMAN_DISTANCE_TABLE.items():
            ia = idx[a]
            for b, dist in row.items():
                raw[ia, idx[b]] = dist
        self.PD999 = np.where(raw < 0, 999, raw)
        self.PD99 = np.where(raw < 0, 99, raw)
        self.PD40 = np.where(raw < 0, 40, raw)
        self.INPD = np.array([t in PACMAN_DISTANCE_TABLE for t in XY], dtype=bool)
        self.BFS = np.zeros((N, N), dtype=np.int64)
        self.EXITD = np.zeros((N, N), dtype=np.int64)
        for a, here in enumerate(XY):
            row = bfs_distances(here)
            far = len(row) + 1
            self.BFS[a] = [row.get(t, far) for t in XY]
            exits = pacman_neighbours(here)
            if exits:
                self.EXITD[a] = np.min(
                    [(self.CX - ex) ** 2 + (self.CY - ey) ** 2 for ex, ey in exits], axis=0)
        self.HEADS = np.zeros((N, 6, 6), dtype=np.int64)
        self.AHEAD1 = np.zeros((N, 6), dtype=np.int64)
        for a, here in enumerate(XY):
            for k in range(6):
                way = CARDINALS[k] if k < 4 else (0, 0)
                self.AHEAD1[a, k] = a
                if here not in PACMAN_DISTANCE_TABLE:
                    continue
                ahead, behind, straight_on, lead3, lead6, lead10 = _pacman_heading(here, way)
                self.HEADS[a, k] = (
                    idx[ahead], idx[behind], -1 if straight_on is None else idx[straight_on],
                    idx[lead3], idx[lead6], idx[lead10],
                )
                if k < 4:
                    nxt = step_position(here, way)
                    if nxt in PACMAN_DISTANCE_TABLE:
                        self.AHEAD1[a, k] = idx[nxt]
        self.EX = np.zeros((N, N), dtype=np.int64)
        tables = _worst_case_table()
        if tables is not None:
            rows, index = tables
            where = [index.get(t) for t in XY]
            for a, ia in enumerate(where):
                if ia is None:
                    continue
                row = rows[ia]
                self.EX[a] = [0 if ib is None else row[ib] for ib in where]
        self.GDm = np.array(planner.GD, dtype=np.int64)
        wc = planner.worst_case()
        self.WCm = None if wc is None else np.array(wc, dtype=np.int64)
        names = lambda stem: np.array([w("%s_%d" % (stem, k)) for k in range(7)])
        self.W_U_PR, self.W_U_OR, self.W_U_PT = names("u_since_pac_rev"), names("u_since_own_rev"), names("u_since_pac_turn")
        self.W_T, self.W_S = names("t_nearest_pac_turn"), names("s_since_own_turn")
        self.W_U_AWAY = np.array([w("u_nearer_away0"), w("u_nearer_away1"), w("u_nearer_away2"), w("u_nearer_away3plus")])
        self.W_S_AWAY = np.array([w("s_away_run1"), w("s_away_run2"), w("s_away_run3plus")])
        self.WAYS4 = np.arange(4, dtype=np.int64)[None, :]
        self.DXW = self.DX[:4][None, :]
        self.DYW = self.DY[:4][None, :]
        self.GH_MS, self.PAC_MS = GHOST_MOVE_DELAY, PACMAN_MOVE_DELAY

    def built(self):
        return self

    # ---- the model's scores for each way, for many ghosts ------------------
    def _view(self, prefix, S, c0, valid, t, is_straight, is_rev, moving, pp, ppd, fr, rf, nopt):
        """Adds the inputs that look at where Pac-Man is (`pp`) and is heading
        (`ppd`) - now, or as it was two ghost moves ago (`prefix` "then_")."""
        np, w = self.np, self.w
        big = 10 ** 6
        col = pp[:, None]
        bfs = self.BFS[col, c0]
        calm = ~fr
        S += w(prefix + "dist_pacman") * bfs
        lead_x = self.CX[pp] + 4 * self.DX[ppd]
        lead_y = self.CY[pp] + 4 * self.DY[ppd]
        S += w(prefix + "dist_lead") * ((self.CX[c0] - lead_x[:, None]) ** 2 + (self.CY[c0] - lead_y[:, None]) ** 2)
        S += w(prefix + "dist_pacman_exit") * self.EXITD[col, c0]
        S += w(prefix + "reversal") * is_rev
        S += w(prefix + "dist_pacman_urgency") * (bfs * ((1.0 - rf) * fr)[:, None])
        S += w(prefix + "straight") * (is_straight & moving[:, None])
        nearest = np.where(valid, bfs, big).min(axis=1)
        S += w(prefix + "nearest_to_pacman") * (bfs <= nearest[:, None])
        # (player_ghost_hunt_terms)
        known = self.INPD[pp][:, None]
        PD = self.PD999
        gap = PD[col, c0]
        head = self.HEADS[pp, ppd]
        junction = (nopt >= 3)[:, None]
        near_all = np.where(valid, gap, big).min(axis=1)
        forward = valid & ~is_rev
        no_forward = ~forward.any(axis=1)
        near_forward = np.where(no_forward, near_all, np.where(forward, gap, big).min(axis=1))
        H = np.zeros_like(S)
        for k, name in ((3, "lead3_dist"), (4, "lead6_dist"), (5, "lead10_dist")):
            H += w(prefix + name) * PD[c0, head[:, k][:, None]]
        H += w(prefix + "nearest_without_reversing") * ((no_forward[:, None] | ~is_rev) & (gap == near_forward[:, None]))
        H += w(prefix + "nearest_at_junction") * (junction & (gap == near_all[:, None]))
        H += w(prefix + "straight_at_junction") * (is_straight & junction)
        if fr.any():
            F = w(prefix + "flee") * gap + w(prefix + "flee_near") * np.minimum(gap, 12) + w(prefix + "straight_frightened") * is_straight
            H += F * fr[:, None]
        if calm.any():
            ahead, behind, straight_on = head[:, 0], head[:, 1], head[:, 2]
            ahead_d = PD[c0, ahead[:, None]]
            behind_d = PD[c0, behind[:, None]]
            C = w(prefix + "junction_ahead_dist") * ahead_d
            C = C + w(prefix + "junction_ahead_first") * (ahead_d * self.GH_MS <= (PD[pp, ahead] * self.PAC_MS)[:, None])
            C += w(prefix + "junction_behind_dist") * behind_d
            C += w(prefix + "junction_behind_first") * (behind_d * self.GH_MS <= (PD[pp, behind] * self.PAC_MS)[:, None])
            best_ahead = np.where(valid, ahead_d, big).min(axis=1)
            C += w(prefix + "junction_ahead_nearest") * (ahead_d == best_ahead[:, None])
            closing = (straight_on >= 0) & (PD[np.maximum(straight_on, 0), t] < PD[pp, t])
            C += w(prefix + "reversal_closing") * (is_rev & closing[:, None])
            C += w(prefix + "reversal_away") * (is_rev & ~closing[:, None])
            H += C * calm[:, None]
        S += H * known

    def scores(self, t, dr, p, pd, q, qd, fr, rf, spr, spt, sor, sot, away, two):
        """(scores [B, 4] for the four ways in CARDINALS order, which of them
        are open, could it turn round, is turning round the nearer way) for B
        ghosts: on tile `t` heading `dr`, Pac-Man on `p` heading `pd` (two
        ghost moves ago: `q`, `qd`), frightened `fr` with the share `rf` of
        the power time left, and the counters of _CloudTrack."""
        np, w = self.np, self.w
        big = 10 ** 6
        c = self.WAY[t]
        valid = c >= 0
        c0 = np.where(valid, c, 0)
        nopt = valid.sum(axis=1)
        is_straight = self.WAYS4 == dr[:, None]
        rev = self.OPP[dr]
        is_rev = self.WAYS4 == rev[:, None]
        moving = dr != 4
        S = np.zeros(c.shape, dtype=float)
        self._view("", S, c0, valid, t, is_straight, is_rev, moving, p, pd, fr, rf, nopt)
        self._view("then_", S, c0, valid, t, is_straight, is_rev, moving, q, qd, fr, rf, nopt)
        # the exact chase value of each way, straight-line cues
        col = p[:, None]
        ex = self.EX[col, c0]
        best_ex = np.where(valid, ex, big).min(axis=1)[:, None]
        is_best = ex == best_ex
        S += w("exact") * (np.minimum(ex, 66) / 10.0)
        S += w("exact_best") * is_best
        S += w("exact_loss") * (np.minimum(ex - best_ex, 30) / 10.0)
        S += w("exact_near") * (is_best & (best_ex < 33))
        dx = (self.CX[p] - self.CX[t])[:, None]
        dy = (self.CY[p] - self.CY[t])[:, None]
        cuts_dx = self.DXW * dx > 0
        cuts_dy = self.DYW * dy > 0
        S += w("cuts_dx") * cuts_dx
        S += w("cuts_dy") * cuts_dy
        S += w("along_big_gap") * (((abs(dx) >= abs(dy)) & cuts_dx) | ((abs(dy) > abs(dx)) & cuts_dy))
        S += w("line_dist") * np.sqrt((self.CX[c0] - self.CX[p][:, None]) ** 2 + (self.CY[c0] - self.CY[p][:, None]) ** 2)
        S += w("same_as_two_ago") * (self.WAYS4 == two[:, None])
        a1 = self.AHEAD1[p, pd]
        S += w("pm_exact") * (np.minimum(self.EX[a1[:, None], c0], 66) / 10.0)
        S += w("pm_dist_after") * self.PD40[c0, a1[:, None]]
        # timing
        calm = ~fr
        g99 = self.PD99[c0, col]
        forward = valid & ~is_rev
        has_fwd = forward.any(axis=1)
        d_fwd = np.where(forward, g99, 99).min(axis=1)
        back = valid & is_rev
        has_rev = back.any(axis=1)
        d_rev = np.where(back, g99, 99).min(axis=1)
        rev_closer = d_rev < d_fwd
        gap = self.PD99[t, p]
        could = has_rev & moving & has_fwd
        nearer = rev_closer & calm
        BK = self.BK
        b_spr, b_spt = BK[np.minimum(spr, 10)], BK[np.minimum(spt, 10)]
        u = self.W_U_PR[b_spr] + self.W_U_OR[BK[np.minimum(sor, 10)]] + self.W_U_PT[b_spt]
        u = u + nearer * (w("u_nearer") + self.W_U_AWAY[np.minimum(away, 3)] + w("u_nearer_pacrev23") * ((spr >= 2) & (spr <= 5)))
        u += fr * (w("u_frightened") + w("u_frightened_near") * (gap <= 6))
        u += self.ROW23[t] * (w("u_row23") + w("u_row23_recent") * (sor <= 5))
        u += w("u_junction") * (nopt >= 3)
        u += w("u_gap_mid") * ((gap >= 7) & (gap <= 15)) + w("u_gap_far") * (gap > 15)
        u += w("u_pac_coming") * (self.PD99[a1, t] < gap)
        turn_round = back & could[:, None]
        S += turn_round * u[:, None]
        near99 = np.where(valid, g99, big).min(axis=1)
        turning = valid & ~turn_round & ~is_straight
        S += (turning & (g99 == near99[:, None]) & calm[:, None]) * self.W_T[b_spt][:, None]
        s = self.W_S[BK[np.minimum(sot, 10)]] + nearer * self.W_S_AWAY[np.minimum(away, 2)]
        S += (is_straight & valid) * s[:, None]
        return S, valid, could, rev_closer

    def odds(self, S, valid):
        np = self.np
        S = np.where(valid, S, -1e18)
        S = S - S.max(axis=1, keepdims=True)
        p = np.exp(S) * valid
        return p / p.sum(axis=1, keepdims=True)

    # ---- the cloud ----------------------------------------------------------
    def root(self, ghost, track, K):
        """K ghosts where the real one is: `ghost` = (tile, previous tile,
        frame of its last move, has left the house), `track` = _CloudTrack.fields()."""
        np = self.np
        tile, prev, last, left = ghost
        row = [0] * _CF_COUNT
        row[_CF_TILE], row[_CF_PREV], row[_CF_LAST], row[_CF_LEFT], row[_CF_ALIVE] = tile, prev, last, int(left), 1
        heading = self.DIRIDX.get((prev, tile), 4) if prev != tile else 4
        if heading == 4 and prev != tile and left:
            heading = track[13]  # (through the tunnel: the two tiles are not side by side)
        row[_CF_DIR] = heading
        row[_CF_SPR:_CF_SPR + 7] = track[:7]
        row[_CF_H1P:_CF_H1P + 6] = track[7:13]
        return np.tile(np.array(row, dtype=np.int64), (1, K, 1))

    def advance(self, A, step, sdir, now, P2, draws):
        """Every ghost of every cloud in `A` ([lines, K, fields]) over one move
        of Pac-Man: he steps to `step` (heading `sdir`) at frame `now`, with
        power until `P2` - one entry per line. `draws`: two rows of K numbers
        between 0 and 1, the luck of each ghost for its (at most two) moves.
        Returns the new array; a ghost that catches him has "alive" 0 from
        then on."""
        np = self.np
        planner = self.planner
        E, K, _ = A.shape
        a = A.reshape(E * K, _CF_COUNT).copy()
        stp = np.repeat(step, K)
        sd = np.repeat(sdir, K)
        P = np.repeat(P2, K)
        PAC, GH, GHF, RESPAWN, HEAD = planner.PAC, planner.GH, planner.GHF, planner.RESPAWN, planner.HEAD
        home = self.HOME0
        alive = a[:, _CF_ALIVE] == 1
        on = alive & (a[:, _CF_TILE] == stp)
        if on.any():
            eat = on & (now < P)
            die = on & ~eat
            a[die, _CF_ALIVE] = 0
            if eat.any():
                a[eat, _CF_TILE] = home
                a[eat, _CF_PREV] = home
                a[eat, _CF_LAST] = now + RESPAWN
                a[eat, _CF_LEFT] = 0
                a[eat, _CF_DIR] = 4
        end = now + PAC
        again = None
        for rnd in (0, 1):
            last = a[:, _CF_LAST]
            due = np.where(last + GHF >= P, np.maximum(last + GH, P), last + GHF)
            if rnd == 0:
                due = np.maximum(due, HEAD)
                due = np.where(due <= now, now + 1, due)
                m = (a[:, _CF_ALIVE] == 1) & (due <= end)
            else:
                m = again & (a[:, _CF_ALIVE] == 1) & (due <= end)
            if not m.any():
                break
            rows = np.nonzero(m)[0]
            d = due[rows]
            tile = a[rows, _CF_TILE]
            left = a[rows, _CF_LEFT] == 1
            frightened = d < P[rows]
            s_here = stp[rows]
            nt = np.empty_like(tile)
            ndir = np.empty_like(tile)
            # in the house: the walk out
            inside = ~left
            if inside.any():
                nt[inside] = self.EXIT[tile[inside]]
                ndir[inside] = self.EXITDIR[tile[inside]]
            if left.any():
                sub = rows[left]
                pd = sd[sub]
                pac = stp[sub]
                # Pac-Man's turns, as seen at this move
                lpd = a[sub, _CF_LPD]
                first = lpd == 5
                changed = ~first & (pd != lpd) & (pd != 4)
                turned_round = changed & (pd == self.OPP[np.minimum(lpd, 4)])
                spt = np.where(changed, 0, a[sub, _CF_SPT])
                spr = np.where(turned_round, 0, a[sub, _CF_SPR])
                lpd = np.where(first | changed, pd, lpd)
                t_ms = d[left] * 1000 // FPS
                recent = t_ms - a[sub, _CF_H2T] < 800
                q = np.where(recent, a[sub, _CF_H2P], pac)
                qd = np.where(recent, a[sub, _CF_H2D], pd)
                fr = frightened[left]
                rf = np.where(fr, np.clip((P[sub] * 1000 // FPS - t_ms) / float(POWER_DURATION), 0.0, 1.0), 0.0)
                t = tile[left]
                dr = a[sub, _CF_DIR]
                sor, sot, away = a[sub, _CF_SOR], a[sub, _CF_SOT], a[sub, _CF_AWAY]
                S, valid, could, rev_closer = self.scores(t, dr, pac, pd, q, qd, fr, rf, spr, spt, sor, sot, away, a[sub, _CF_TWO])
                p = self.odds(S, valid)
                top = p.argmax(axis=1)
                u = np.tile(draws[rnd], E)[sub]
                pick = (np.cumsum(p, axis=1) < u[:, None]).sum(axis=1)
                pick = np.where(pick > 3, top, pick)
                pick = np.where(valid[np.arange(len(pick)), pick], pick, top)
                if PACMAN_CLOUD_FAVOURITE:
                    pick = np.where(sub % K == 0, top, pick)
                stuck = ~valid.any(axis=1)
                to = self.WAY[t, pick]
                nt[left] = np.where(stuck, t, to)
                ndir[left] = pick
                # the counters, as the ghost's move is made (_CloudTrack.observe)
                rev = self.OPP[dr]
                chose_rev = pick == rev
                away = np.where(could, np.where(rev_closer & ~chose_rev, away + 1, 0), away)
                away = np.where(chose_rev, 0, away)
                a[sub, _CF_AWAY] = away
                a[sub, _CF_SOR] = np.where(chose_rev, 0, sor + 1)
                a[sub, _CF_SOT] = np.where(pick != dr, 0, sot + 1)
                a[sub, _CF_SPR] = spr + 1
                a[sub, _CF_SPT] = spt + 1
                a[sub, _CF_LPD] = lpd
                a[sub, _CF_TWO] = dr
                a[sub, _CF_H2P], a[sub, _CF_H2D], a[sub, _CF_H2T] = a[sub, _CF_H1P], a[sub, _CF_H1D], a[sub, _CF_H1T]
                a[sub, _CF_H1P], a[sub, _CF_H1D], a[sub, _CF_H1T] = pac, pd, t_ms
            stay = nt == tile
            hit = ~stay & (nt == s_here)
            eaten = hit & frightened
            caught = hit & ~frightened
            move = ~stay & ~hit
            a[rows[stay], _CF_LAST] = d[stay]
            if caught.any():
                a[rows[caught], _CF_ALIVE] = 0
            if eaten.any():
                r = rows[eaten]
                a[r, _CF_TILE] = home
                a[r, _CF_PREV] = home
                a[r, _CF_LAST] = d[eaten] + RESPAWN
                a[r, _CF_LEFT] = 0
                a[r, _CF_DIR] = 4
            if move.any():
                r = rows[move]
                a[r, _CF_PREV] = tile[move]
                a[r, _CF_TILE] = nt[move]
                a[r, _CF_LAST] = d[move]
                a[r, _CF_LEFT] = (left[move] | ~self.HOUSE[nt[move]]).astype(np.int64)
                a[r, _CF_DIR] = ndir[move]
            if rnd == 0:
                # the faster ghost: about once in eleven of his moves it gets a second one in
                again = np.zeros(E * K, dtype=bool)
                again[rows[move]] = (d[move] == now + 1) & (d[move] + GH <= end)
                if not again.any():
                    break
        return a.reshape(E, K, _CF_COUNT)


_PLAN_CAUGHT = "caught"
_PLANNER = None


def _plan_frames(ms):
    """Frames a move gated at `ms` takes, or the frame a duration ends on."""
    return int(math.ceil(ms / FRAME_MS - 1e-6))


def _plan_frame(ms):
    """Frame number of a clock reading (ms since the game started)."""
    return int(round(ms / FRAME_MS))


class _PlanModel(object):
    """One picture of how the player's ghost moves: for each (its tile,
    Pac-Man's tile) the tile it steps to - a table keyed tile * N +
    Pac-Man's tile, filled in as it is asked for. These ghosts move the
    same way frightened or not, so `scared` is the same table.

    A fitted model (`weights`) looks at the whole position, so its move is
    worked out each time (_Planner.fitted_step); its tables are the saved
    moves table used beyond PACMAN_PLANNER_FITTED_DEPTH."""

    def __init__(self, name, key=None, static=False, weights=None):
        self.name = name
        self.key = key  # (chase weight, exit weight): see simple_player_ghost_step
        self.static = static  # a ghost that never leaves the house
        self.weights = weights
        self.cloud = None  # a _Cloud: this picture is a cloud of likely ghosts (see PACMAN_CLOUD)
        self.ff = None  # the weights laid out for fitted_step
        self.moves = {}  # its moves worked out so far, by everything they depend on
        # how to name this picture to the search process (see _PlanWorker)
        self.spec = ("static",) if static else ("fitted", name) if weights is not None else ("chaser", key)
        self.normal = {}
        self.scared = {} if weights is not None else self.normal


class _Planner(object):
    def __init__(self):
        tiles = sorted(_ALL_OPEN_TILES)
        self.tiles = tiles
        self.idx = idx = {t: i for i, t in enumerate(tiles)}
        self.N = N = len(tiles)
        self.NB = [tuple(idx[t] for t in pacman_neighbours(t)) for t in tiles]
        self.EXIT = [idx[house_exit_step(t)] for t in tiles]
        self.HOUSE = [t in HOUSE_TILES for t in tiles]
        self.DIRS = {}
        for t in tiles:
            for n in neighbours(t):
                self.DIRS[(idx[t], idx[n])] = direction_between(t, n)
        self.HOME = [idx[GHOST_START]] + [idx[t] for t in AI_GHOST_STARTS]
        self.BIT = [1 << i for i in range(N)]
        self.NBMASK = [sum(1 << n for n in nb) for nb in self.NB]
        self.PAC = _plan_frames(PACMAN_MOVE_GATE)
        self.GH = _plan_frames(GHOST_MOVE_GATE)
        self.GHF = _plan_frames(GHOST_FRIGHTENED_MOVE_GATE)
        self.AI = _plan_frames(AI_GHOST_MOVE_GATE)
        self.AIF = _plan_frames(AI_GHOST_FRIGHTENED_MOVE_GATE)
        self.POWER = _plan_frames(POWER_DURATION)
        self.RESPAWN = _plan_frames(GHOST_RESPAWN_COOLDOWN_MS)
        self.PERC = _plan_frames(AI_GHOST_PERCEPTION_INTERVAL)
        self.HEAD = _plan_frames(GHOST_HEAD_START)
        self.STALE = _plan_frames(STALEMATE_LIMIT)
        self.sched = []
        t = 0
        for duration, mode in SCATTER_CHASE_SCHEDULE:
            t += duration
            self.sched.append((_plan_frames(t), mode == "scatter"))
        self.chase_end = self.sched[-1][0] if self.sched else 0
        self.target_cache = {}
        big = 999
        self.GD = [[big] * N for _ in range(N)]
        for a, row in GHOST_DISTANCE_TABLE.items():
            r = self.GD[idx[a]]
            for b, d in row.items():
                r[idx[b]] = d
        # every tile's other tiles, nearest first (for "the nearest pellet left")
        self.NEAR = []
        for a in range(N):
            row = PACMAN_DISTANCE_TABLE.get(tiles[a], {})
            order = sorted((d, idx[b]) for b, d in row.items() if b != tiles[a])
            self.NEAR.append(tuple((self.BIT[b], d) for d, b in order))
        self.rel = [0, 0, 0, 0, 0]
        self.model = None
        self._models = {}
        # The power pellets in the order the game's own set gives them: a
        # fitted model breaks a tie between two of them by that order.
        self.pp_order = ()
        self._pp_tuples = {}
        self._pp_lists = {}
        self.ff_ready = False
        self.ff_hits = self.ff_misses = 0
        self.WC = None

    # ---- pictures of the player's ghost ----------------------------------
    def chaser(self, key):
        name = "chaser %g/%g" % key
        model = self._models.get(name)
        if model is None:
            model = self._models[name] = _PlanModel(name, key=key)
        return model

    def fitted(self, name, weights=None):
        """The picture of the ghost given by a fitted model: the saved version
        `name`, or `weights` under that name. None if it cannot be had (no
        such file, or no numpy for its tables)."""
        model = self._models.get(name)
        if model is not None and (weights is None or model.weights is weights):
            return model
        if name in self._models and weights is None:
            return None
        if weights is None:
            weights = load_assumed_player_weights(name)
        if weights is not None and not self.ff_ready:
            try:
                self._ff_setup()
            except ImportError:
                weights = None
        if weights is None:
            self._models[name] = None
            return None
        model = self._models[name] = _PlanModel(name, key=(-1.0, 0.0), weights=weights)
        # the model's saved moves table, for the far end of a plan
        try:
            with open(os.path.join(PLAYER_GHOST_VERSIONS_DIR, name + "_hops.json")) as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            data = {}
        idx, N = self.idx, self.N
        for part, table in (("normal", model.normal), ("frightened", model.scared)):
            for key, step in (data.get(part) or {}).items():
                gx, gy, px, py = (int(v) for v in key.split(","))
                a, b, c = idx.get((gx, gy)), idx.get((px, py)), idx.get(tuple(step))
                if a is not None and b is not None and c is not None:
                    table[a * N + b] = c
        return model

    def _pp_new_order(self):
        """The power pellets' order has changed (a new game): forget what
        was worked out with the old one."""
        self._pp_tuples = {}
        for model in self._models.values():
            if model is not None and model.weights is not None:
                model.moves.clear()

    def worst_case(self):
        """WC[his tile][ghost tile]: the moves he can be sure of against a
        ghost that plays perfectly from there (see _worst_case_table), by the
        planner's tile numbers; None without the table."""
        if self.WC is None:
            tables = _worst_case_table()
            if tables is None:
                self.WC = False
            else:
                rows, index = tables
                pos = [index.get(t) for t in self.tiles]
                self.WC = [[999 if (a is None or b is None) else rows[a][b] for b in pos] for a in pos]
        return self.WC or None

    # ---- a fitted model's move ---------------------------------------------
    # The same answer as fast_player_ghost_direction (same terms, added in
    # the same order, same tie-breaks; compared on 419,629 positions out of
    # real searches without a difference), from tables indexed the
    # planner's way: nothing is looked up by coordinates that can be looked
    # up by number, and the "room" inputs are bit masks instead of arrays.
    # Change either and the comparison has to be run again.
    def _ff_setup(self):
        import numpy as np
        self.np = np
        N = self.N
        self.NBYTES = (N + 7) // 8
        self.PD = np.full((N, N), 999, dtype=np.int64)
        self.PDL = [[999] * N for _ in range(N)]
        for a, row in PACMAN_DISTANCE_TABLE.items():
            ia = self.idx[a]
            for b, dist in row.items():
                self.PD[ia, self.idx[b]] = dist
                self.PDL[ia][self.idx[b]] = dist
        self.ff_ways = {}
        self.ff_gd = {}
        self.ff_exits = {}
        self.ff_head = {}
        self.ff_ev = {}
        self.ff_first = {}
        self.ff_free = {}
        self.ff_room = _room_tables()
        if not _PLAIN_EVASION:
            _PLAIN_EVASION.append(_evasion_table(None))
        self.ff_ready = True

    def _ff_mask(self, array):
        return int.from_bytes(self.np.packbits(array).tobytes(), "big")

    def fitted_step(self, model, tile, prev, pac, pacdir, g, d, P, pm, ppm):
        """Where the fitted model steps (see _fitted_move), remembered: one
        search after another goes over much the same few seconds of the
        game, so most of what is asked has been asked before."""
        key = (
            tile, prev, pac, pacdir, g[1][0], g[2][0], g[3][0], g[4][0],
            (P, d) if d < P else None, pm & ~ppm, ppm,
        )
        cache = model.moves
        nt = cache.get(key)
        if nt is None:
            if len(cache) > 200000:
                cache.clear()
            nt = cache[key] = self._fitted_move(model, tile, prev, pac, pacdir, g, d, P, pm, ppm)
            self.ff_misses += 1
        else:
            self.ff_hits += 1
        return nt

    def _fitted_move(self, model, tile, prev, pac, pacdir, g, d, P, pm, ppm):
        if not self.ff_ready:
            self._ff_setup()
        XY = self.tiles
        plan = model.ff
        if plan is None:
            w = model.weights
            plan = model.ff = (
                tuple(w.get(name, 0.0) or 0.0 for name in PLAYER_GHOST_FEATURE_NAMES[:18]),
                tuple((i, w[name]) for i, name in enumerate(PLAYER_GHOST_HUNT_NAMES) if w.get(name, 0.0) != 0.0),
                any(w.get(name, 0.0) != 0.0 for name in _PLAYER_GHOST_ROOM_NAMES),
            )
        base, hunt_weights, wants_room = plan
        (w_pacman, w_lead, w_blinky, w_pinky, w_inky, w_clyde, w_exit, w_reversal, w_pacman_c, w_lead_c, w_exit_c,
         w_coverage, w_uncovered, w_guard, w_fraction, w_urgency, w_straight, w_nearest) = base
        ways = self.ff_ways.get(tile)
        if ways is None:
            here = XY[tile]
            found = []
            for way in CARDINALS:
                to = step_position(here, way)
                if is_open(*to) and to not in HOUSE_TILES:
                    found.append((way, self.idx[to], to[0], to[1]))
            ways = self.ff_ways[tile] = tuple(found)
        if not ways:
            return tile
        direction = self.DIRS[(prev, tile)] if prev != tile else (0, 0)
        frightened = d < P
        pac_xy = XY[pac]
        gd = self.ff_gd.get(pac)
        if gd is None:
            row = bfs_distances(pac_xy)
            unreachable = len(row) + 1
            gd = self.ff_gd[pac] = [row.get(t, unreachable) for t in XY]
        lead_x = pac_xy[0] + pacdir[0] * 4
        lead_y = pac_xy[1] + pacdir[1] * 4
        exits = self.ff_exits.get(pac)
        if exits is None:
            exits = self.ff_exits[pac] = tuple(_outside_neighbours(pac_xy))
        reverse = (-direction[0], -direction[1]) if direction != (0, 0) else None
        allies = (XY[g[1][0]], XY[g[2][0]], XY[g[3][0]], XY[g[4][0]])
        limit = CORNER_GHOST_DISTANCE
        cornered = False
        if w_pacman_c or w_lead_c or w_exit_c:
            if not exits:
                cornered = True
            else:
                hx, hy = XY[tile]
                safest = -1
                for ex, ey in exits:
                    near = (ex - hx) ** 2 + (ey - hy) ** 2
                    for ax, ay in allies:
                        other = (ex - ax) ** 2 + (ey - ay) ** 2
                        if other < near:
                            near = other
                    if near > safest:
                        safest = near
                cornered = safest <= limit
        guard_x = guard_y = None
        if w_guard and ppm:
            pps = self._pp_tuples.get(ppm)
            if pps is None:
                pps = self._pp_tuples[ppm] = tuple(XY[i] for i in self.pp_order if ppm >> i & 1)
            if pps:
                guard_x, guard_y = min(pps, key=lambda t: (t[0] - lead_x) ** 2 + (t[1] - lead_y) ** 2)
        remaining_fraction = 0.0
        if frightened and POWER_DURATION > 0:
            remaining_fraction = max(0.0, min(1.0, (P * 1000 // FPS - d * 1000 // FPS) / POWER_DURATION))
        gaps = [gd[way[1]] for way in ways]
        nearest = min(gaps)
        moving = direction != (0, 0)
        hunt = None
        if hunt_weights:
            # (player_ghost_hunt_terms)
            PDL = self.PDL
            far = 999
            to_pacman = PDL[pac]
            rows = [PDL[way[1]] for way in ways]
            d_pac = [to_pacman[way[1]] for way in ways]
            back = (-direction[0], -direction[1])
            near_all = min(d_pac)
            near_forward = min([d_pac[k] for k, way in enumerate(ways) if way[0] != back] or d_pac)
            no_forward = all(way[0] == back for way in ways)
            junction = 1.0 if len(ways) >= 3 else 0.0
            heading = self.ff_head.get((pac, pacdir))
            if heading is None:
                ahead, behind, straight_on, lead3, lead6, lead10 = _pacman_heading(pac_xy, pacdir)
                heading = self.ff_head[(pac, pacdir)] = (
                    self.idx[ahead], self.idx[behind], -1 if straight_on is None else self.idx[straight_on],
                    self.idx[lead3], self.idx[lead6], self.idx[lead10],
                )
            ahead, behind, straight_on, lead3, lead6, lead10 = heading
            ordinary = pm & ~ppm
            means = None
            if ordinary:
                np = self.np
                count = bin(ordinary).count("1")
                vec = np.unpackbits(
                    np.frombuffer(ordinary.to_bytes(self.NBYTES, "little"), dtype=np.uint8), bitorder="little",
                )[:self.N].astype(np.int64)
                PD = self.PD
                means = [int(PD[way[1]].dot(vec)) / count for way in ways]
            hunt = []
            if frightened:
                for k, way in enumerate(ways):
                    row = rows[k]
                    gap = d_pac[k]
                    straight = 1.0 if way[0] == direction else 0.0
                    hunt.append((
                        0.0, 0.0, 0.0, 0.0, 0.0,
                        float(row[lead3]), float(row[lead6]), float(row[lead10]),
                        1.0 if ((no_forward or way[0] != back) and gap == near_forward) else 0.0,
                        junction if gap == near_all else 0.0, straight * junction,
                        float(gap), float(gap if gap < 12 else 12), straight,
                        0.0, 0.0, 0.0, 0.0,
                        0.0 if means is None else means[k],
                        0.0, 0.0, 0.0, 0.0,
                    ))
            else:
                ev = self.ff_ev.get(pac)
                if ev is None:
                    evasion_row = _PLAIN_EVASION[0].get(pac_xy) or {}
                    ev = self.ff_ev[pac] = [float(min(evasion_row.get(t, 0), 120)) for t in XY]
                evasion = [ev[way[1]] for way in ways]
                ahead_d = [row[ahead] for row in rows]
                best_evasion = min(evasion)
                best_ahead = min(ahead_d)
                pacman_to_ahead = to_pacman[ahead] * PACMAN_MOVE_DELAY
                pacman_to_behind = to_pacman[behind] * PACMAN_MOVE_DELAY
                closing = 0.0
                if straight_on >= 0 and PDL[straight_on][tile] < to_pacman[tile]:
                    closing = 1.0
                rooms = None
                if wants_room and self.ff_room is not None:
                    np, index, pacman_ms, player_ms, classic_ms = self.ff_room
                    p = index.get(pac_xy)
                    if p is not None:
                        free = -1
                        for k in range(1, 5):
                            a = g[k][0]
                            mask = self.ff_free.get((pac, a))
                            if mask is None:
                                i = index.get(XY[a])
                                if i is None:
                                    reach = classic_ms[index[HOUSE_EXIT_TARGET]] + 2 * AI_GHOST_MOVE_DELAY
                                else:
                                    reach = classic_ms[i]
                                mask = self.ff_free[(pac, a)] = self._ff_mask(pacman_ms[p] < reach)
                            free &= mask
                        rooms = []
                        for way in ways:
                            c = way[1]
                            if c == pac:
                                rooms.append((0.0, 0.0))
                                continue
                            first = self.ff_first.get((pac, c))
                            if first is None:
                                i = index.get(XY[c])
                                if i is None:
                                    first = (0, -1)
                                else:
                                    array = pacman_ms[p] < player_ms[i] + GHOST_MOVE_DELAY
                                    first = (self._ff_mask(array), int(array.sum()))
                                self.ff_first[(pac, c)] = first
                            if first[1] < 0:
                                rooms.append((0.0, 0.0))
                            else:
                                rooms.append((first[1] / 10.0, bin(first[0] & free).count("1") / 10.0))
                        least_alone = min(r[0] for r in rooms)
                        least_all = min(r[1] for r in rooms)
                for k, way in enumerate(ways):
                    row = rows[k]
                    gap = d_pac[k]
                    behind_d = row[behind]
                    u_turn = 1.0 if way[0] == back else 0.0
                    hunt.append((
                        evasion[k] / 10.0,
                        float(ahead_d[k]), 1.0 if ahead_d[k] * GHOST_MOVE_DELAY <= pacman_to_ahead else 0.0,
                        float(behind_d), 1.0 if behind_d * GHOST_MOVE_DELAY <= pacman_to_behind else 0.0,
                        float(row[lead3]), float(row[lead6]), float(row[lead10]),
                        1.0 if ((no_forward or way[0] != back) and gap == near_forward) else 0.0,
                        junction if gap == near_all else 0.0, (1.0 if way[0] == direction else 0.0) * junction,
                        0.0, 0.0, 0.0,
                        1.0 if evasion[k] == best_evasion else 0.0, 1.0 if ahead_d[k] == best_ahead else 0.0,
                        u_turn * closing, u_turn * (1.0 - closing),
                        0.0 if means is None else means[k],
                    ) + ((0.0, 0.0, 0.0, 0.0) if rooms is None else (
                        rooms[k][0], rooms[k][1],
                        1.0 if rooms[k][0] == least_alone else 0.0, 1.0 if rooms[k][1] == least_all else 0.0,
                    )))
        open_exits = None
        if w_uncovered:
            open_exits = [
                (ex, ey) for ex, ey in exits
                if not any((ax - ex) ** 2 + (ay - ey) ** 2 <= limit for ax, ay in allies)
            ]
        best_tile, best_score = ways[0][1], float("-inf")
        for k, (way, c, cx, cy) in enumerate(ways):
            dist_pacman = gaps[k]
            dist_lead = (cx - lead_x) ** 2 + (cy - lead_y) ** 2
            dist_exit = 0
            if exits:
                dist_exit = min([(cx - ex) ** 2 + (cy - ey) ** 2 for ex, ey in exits])
            score = 0
            if w_pacman:
                score += w_pacman * dist_pacman
            if w_lead:
                score += w_lead * dist_lead
            if w_blinky:
                score += w_blinky * ((cx - allies[0][0]) ** 2 + (cy - allies[0][1]) ** 2)
            if w_pinky:
                score += w_pinky * ((cx - allies[1][0]) ** 2 + (cy - allies[1][1]) ** 2)
            if w_inky:
                score += w_inky * ((cx - allies[2][0]) ** 2 + (cy - allies[2][1]) ** 2)
            if w_clyde:
                score += w_clyde * ((cx - allies[3][0]) ** 2 + (cy - allies[3][1]) ** 2)
            if w_exit:
                score += w_exit * dist_exit
            if w_reversal:
                score += w_reversal * (1.0 if way == reverse else 0.0)
            if w_pacman_c:
                score += w_pacman_c * (dist_pacman if cornered else 0.0)
            if w_lead_c:
                score += w_lead_c * (dist_lead if cornered else 0.0)
            if w_exit_c:
                score += w_exit_c * (dist_exit if cornered else 0.0)
            if w_coverage:
                score += w_coverage * sum([1 for ex, ey in exits if (cx - ex) ** 2 + (cy - ey) ** 2 <= limit])
            if w_uncovered:
                score += w_uncovered * sum([1 for ex, ey in open_exits if (cx - ex) ** 2 + (cy - ey) ** 2 > limit])
            if w_guard:
                score += w_guard * (10_000 if guard_x is None else (cx - guard_x) ** 2 + (cy - guard_y) ** 2)
            if w_fraction:
                score += w_fraction * remaining_fraction
            if w_urgency:
                score += w_urgency * (dist_pacman * (1.0 - remaining_fraction) if frightened else 0.0)
            if w_straight:
                score += w_straight * (1.0 if (moving and way == direction) else 0.0)
            if w_nearest:
                score += w_nearest * (1.0 if dist_pacman <= nearest else 0.0)
            if hunt is not None:
                values = hunt[k]
                for index_, weight in hunt_weights:
                    score += weight * values[index_]
            if score > best_score:
                best_score, best_tile = score, c
        return best_tile

    # ---- planning against a cloud of likely ghosts (see PACMAN_CLOUD) -------
    def cloud_model(self, name):
        """The picture of a person's ghost given by a timed-odds model file
        (player_ghost_versions/<name>.json); None if it cannot be had."""
        key = "cloud " + name
        if key in self._models:
            return self._models[key]
        model = None
        try:
            with open(os.path.join(PLAYER_GHOST_VERSIONS_DIR, name + ".json")) as handle:
                data = json.load(handle)
            weights = {n: w / s for n, w, s in zip(data["names"], data["weights"], data["scale"]) if w}
            if not self.ff_ready:
                self._ff_setup()
            model = _PlanModel(key, key=(-1.0, 0.0))
            import numpy  # noqa: F401 (the cloud cannot be worked out without it)
            model.cloud = _CloudLater(self, weights)
            model.spec = ("cloud", name)
        except (OSError, ValueError, KeyError, ImportError):
            model = None
        self._models[key] = model
        return model

    def search_cloud(self, state, track, forced, width, per_tile, deadline=None, marks=False):
        """Beam search against the cloud. `state` is the game at his decision
        (the real ghost where it stands), `track` the _CloudTrack fields then,
        `forced` the step he has already taken from there (None: none yet).
        Returns (kind, steps, search steps, depth, eaten) as search() does;
        the steps begin with `forced`. With `marks` it stops where the cloud
        does and returns kind "marks" and, in place of the steps, one
        (tile, risk) for each way open to him at his next decision: the
        share of the cloud's ghosts that catch him within the search's depth
        on the line from there that loses fewest (1.0: no line survives)."""
        cloud = self.model.cloud = self.model.cloud.built()
        np = cloud.np
        pos, g, perc, P, now, pm, ppm = state
        K = PACMAN_CLOUD_K
        NB, BIT, PAC, POWER, GH, NBMASK = self.NB, self.BIT, self.PAC, self.POWER, self.GH, self.NBMASK
        ply, nearest = self.ply, self.nearest
        w_gap, gap_cap, w_risk, w_near = PACMAN_PLANNER_GAP, PACMAN_PLANNER_GAP_CAP, PACMAN_PLANNER_RISK, PACMAN_PLANNER_NEAR
        w_risk_now, w_pp, w_death = PACMAN_PLANNER_RISK_NOW, PACMAN_CLOUD_PP, PACMAN_CLOUD_DEATH
        w_wc, wc_safe, wc_near, wc_far = (
            PACMAN_PLANNER_WC, PACMAN_PLANNER_WC_SAFE, PACMAN_PLANNER_WC_NEAR, PACMAN_PLANNER_WC_FAR,
        )
        WCm = cloud.WCm if w_wc else None
        GDm, PD = cloud.GDm, cloud.PD999
        DIRIDX = cloud.DIRIDX
        rng = np.random.RandomState(12345)  # the same luck in every search: lines are compared like with like
        A = cloud.root(g[0], track, K)
        total = bin(pm).count("1")
        pp_tiles = [i for i in range(self.N) if ppm >> i & 1]
        # node: as in search()
        # (two more for a line of the cloud: his step at the decision the marks are for, the share lost so far)
        root = (0.0, pos, g, perc, P, pm, ppm, 0, None, -1, 0.0, bin(ppm).count("1"), -1, 0.0)
        level = [root]
        best_seen = root
        nodes = depth = 0
        stale = self.STALE
        ghost_frames = float(GH)
        mark_depth = 0 if forced is None else 1
        mark_ways = None
        lasted = {}  # way at the marked decision -> how deep some line down it got with him alive
        cloud_depth = PACMAN_CLOUD_DEPTH + mark_depth if marks else PACMAN_CLOUD_DEPTH
        while now < stale and depth < cloud_depth:
            later = now + PAC
            pend, par, step_l, sdir_l, P2_l = [], [], [], [], []
            for row, node in enumerate(level):
                _score, pos, g, perc, P, pm, ppm, eaten, _parent, _step, riskacc, npp_here = node[:12]
                if depth == mark_depth and mark_ways is None:
                    mark_ways = NB[pos]
                for step in NB[pos]:
                    if depth == 0 and forced is not None and step != forced:
                        continue
                    bit = BIT[step]
                    P2, pm2, ppm2, e2, npp = P, pm, ppm, eaten, npp_here
                    if pm & bit:
                        pm2 = pm ^ bit
                        e2 += 1
                        if ppm & bit:
                            ppm2 = ppm ^ bit
                            P2 = now + POWER
                            npp -= 1
                    r = ply(pos, step, g, perc, P2, now, pm2, ppm2, False, True)
                    nodes += 1
                    if r is None:
                        continue
                    if pm2 == 0 and not marks:
                        ghosts = A[row]
                        there = (ghosts[:, _CF_ALIVE] == 1) & (ghosts[:, _CF_TILE] == step)
                        if now < P2 or 2 * int(there.sum()) <= int((ghosts[:, _CF_ALIVE] == 1).sum()):
                            return "clear", self._path(node) + [step], nodes, depth + 1, e2
                        continue
                    if r is _PLAN_CAUGHT:
                        continue
                    g2, perc2, risk = r
                    r2 = riskacc
                    if risk:
                        r2 += risk * (w_risk_now if depth == 0 else w_risk if depth < 3 else w_risk * 0.1)
                    left = total - e2 - npp
                    base = (
                        100.0 * e2 - w_near * (1 if NBMASK[step] & pm2 else nearest(step, pm2))
                        + w_pp * npp * (left / 40.0 if left < 40 else 1.0)
                    )
                    par.append(row)
                    step_l.append(step)
                    sdir_l.append(DIRIDX.get((pos, step), 4))
                    P2_l.append(P2)
                    pend.append((base, step, g2, perc2, P2, pm2, ppm2, e2, node, npp, r2))
            if not pend:
                level = []
                break
            E = len(pend)
            par_a = np.array(par, dtype=np.int64)
            step_a = np.array(step_l, dtype=np.int64)
            P2_a = np.array(P2_l, dtype=np.int64)
            before = A[par_a]
            A2 = cloud.advance(before, step_a, np.array(sdir_l, dtype=np.int64), now, P2_a, rng.random_sample((2, K)))
            nodes += E
            alive = A2[:, :, _CF_ALIVE] == 1
            n_alive = alive.sum(axis=1)
            died = ((before[:, :, _CF_ALIVE] == 1).sum(axis=1) - n_alive) / float(K)
            share = np.maximum(n_alive, 1).astype(float)
            tiles2 = A2[:, :, _CF_TILE]
            # his lead over each of them, in tiles (see gap()), averaged over those he is still alive against
            lead = GDm[tiles2, step_a[:, None]].astype(float)
            wait = A2[:, :, _CF_LAST] + GH - later + np.where(later < P2_a, (P2_a - later) // 3, 0)[:, None]
            lead = np.minimum(lead + np.where(wait > 0, wait / ghost_frames, 0.0), gap_cap)
            lead_mean = (lead * alive).sum(axis=1) / share
            charge = w_death * died
            if WCm is not None:
                sure = WCm[step_a[:, None], tiles2]
                at_risk = alive & (A2[:, :, _CF_LEFT] == 1) & (later >= P2_a)[:, None] & (sure < wc_safe)
                if pp_tiles and at_risk.any():
                    for b in pp_tiles:
                        has = np.array([(item[6] >> b) & 1 for item in pend], dtype=bool)
                        if has.any():
                            first = (PD[step_a, b] * PAC + PAC)[:, None] < (PD[tiles2, b] - 2) * GH
                            at_risk &= ~(first & has[:, None])  # he is on a power pellet first
                worst = np.where(at_risk, (wc_safe - sure) / float(wc_safe), 0.0).sum(axis=1) / share
                charge = charge + w_wc * worst * (1.0 if depth < wc_near else wc_far)
            cand = {}
            for e in range(E):
                if n_alive[e] == 0:
                    continue
                base, step, g2, perc2, P2, pm2, ppm2, e2, node, npp, r2 = pend[e]
                r2 += float(charge[e])
                score = base + w_gap * float(lead_mean[e]) - r2
                first = step if depth == mark_depth else node[12]
                lasted[first] = depth + 1
                key = (step, pm2, first) if marks else (step, pm2)
                old = cand.get(key)
                if old is None or old[0][0] < score:
                    cand[key] = ((score, step, g2, perc2, P2, pm2, ppm2, e2, node, step, r2, npp, first, node[13] + float(died[e])), e)
            if not cand:
                level = []
                break
            items = sorted(cand.values(), key=_plan_score_first, reverse=True)
            level, rows, count = [], [], {}
            if marks and depth >= mark_depth:
                # every way open at the decision keeps its share of the lines: each has to be judged
                ways = max(1, len(set(pair[0][12] for pair in items)))
                quota, kept, rest = max(2, width // ways), {}, []
                for pair in items:
                    c = kept.get(pair[0][12], 0)
                    if c < quota:
                        kept[pair[0][12]] = c + 1
                        level.append(pair[0])
                        rows.append(pair[1])
                    else:
                        rest.append(pair)
                for pair in rest:
                    if len(level) >= width:
                        break
                    level.append(pair[0])
                    rows.append(pair[1])
                items = ()
            for item, e in items:
                if len(level) >= width:
                    break
                c = count.get(item[1], 0)
                if c >= per_tile:
                    continue
                count[item[1]] = c + 1
                level.append(item)
                rows.append(e)
            A = A2[np.array(rows, dtype=np.int64)]
            best_seen = level[0]
            depth += 1
            now += PAC
            if deadline is not None and time.perf_counter() > deadline:
                if marks:
                    break
                return "partial", self._path(best_seen), nodes, depth, best_seen[7]
        if marks:
            risk = {}
            for node in level:
                if node[12] >= 0 and node[13] < risk.get(node[12], 2.0):
                    risk[node[12]] = node[13]
            out = []
            reach = float(max(1, depth))
            for way in (mark_ways or ()):
                if way in risk:
                    out.append((way, min(1.0, risk[way])))
                elif PACMAN_CLOUD_LASTING:
                    # no line survives down this way: the sooner the end, the worse (1 to 2)
                    out.append((way, 1.0 + max(0.0, reach - lasted.get(way, mark_depth)) / reach))
                else:
                    out.append((way, 1.0))
            return "marks", out, nodes, depth, 0
        if depth < PACMAN_CLOUD_DEPTH or best_seen is root:
            return "partial", self._path(best_seen), nodes, depth, best_seen[7]
        # Beyond the cloud nobody knows where a person's ghost will be: carry on with the nearest of each
        # line's ghosts as a plain chaser (or with none), and the worst-case charge.
        chase = PACMAN_CLOUD_FAR == "chaser"
        WC = self.worst_case() if (w_wc and chase) else None
        pp_lists = self._pp_lists
        GD = self.GD
        if chase:
            carried = []
            for row, node in enumerate(level):
                ghosts = A[row]
                live = np.nonzero(ghosts[:, _CF_ALIVE] == 1)[0]
                k = live[np.argmin(GDm[ghosts[live, _CF_TILE], node[1]])]
                rep = (int(ghosts[k, _CF_TILE]), int(ghosts[k, _CF_PREV]), int(ghosts[k, _CF_LAST]), bool(ghosts[k, _CF_LEFT]))
                carried.append(node[:2] + ((rep,) + tuple(node[2][1:]),) + node[3:])
            level = carried
        while now < stale and depth < PACMAN_CLOUD_TOTAL:
            cand = {}
            later = now + PAC
            for node in level:
                _score, pos, g, perc, P, pm, ppm, eaten, _parent, _step, riskacc, npp_here = node[:12]
                for step in NB[pos]:
                    bit = BIT[step]
                    P2, pm2, ppm2, e2, npp = P, pm, ppm, eaten, npp_here
                    if pm & bit:
                        pm2 = pm ^ bit
                        e2 += 1
                        if ppm & bit:
                            ppm2 = ppm ^ bit
                            P2 = now + POWER
                            npp -= 1
                    r = ply(pos, step, g, perc, P2, now, pm2, ppm2, False, not chase)
                    nodes += 1
                    if r is None:
                        continue
                    if pm2 == 0:
                        return "clear", self._path(node) + [step], nodes, depth + 1, e2
                    if r is _PLAN_CAUGHT:
                        continue
                    g2, perc2, risk = r
                    r2 = riskacc
                    if risk:
                        r2 += risk * w_risk * 0.1
                    left = total - e2 - npp
                    lead = 0.0
                    if chase:
                        chaser = g2[0]
                        lead = GD[chaser[0]][step]
                        wait = chaser[2] + GH - later
                        if later < P2:
                            wait += (P2 - later) // 3
                        if wait > 0:
                            lead += wait / ghost_frames
                        if WC is not None and chaser[3] and later >= P2:
                            sure = WC[step][chaser[0]]
                            if sure < wc_safe:
                                covered = False
                                if ppm2:
                                    pps = pp_lists.get(ppm2)
                                    if pps is None:
                                        pps = pp_lists[ppm2] = tuple(i for i in range(self.N) if ppm2 >> i & 1)
                                    mine, theirs = self.PDL[step], self.PDL[chaser[0]]
                                    for b in pps:
                                        if mine[b] * PAC + PAC < (theirs[b] - 2) * GH:
                                            covered = True
                                            break
                                if not covered:
                                    r2 += w_wc * (wc_safe - sure) / wc_safe * (1.0 if depth < wc_near else wc_far)
                    score = (
                        100.0 * e2 - w_near * (1 if NBMASK[step] & pm2 else nearest(step, pm2))
                        + w_gap * (lead if lead < gap_cap else gap_cap)
                        + w_pp * npp * (left / 40.0 if left < 40 else 1.0)
                        - r2
                    )
                    key = (step, pm2, g2[0][0])
                    old = cand.get(key)
                    if old is None or old[0] < score:
                        cand[key] = (score, step, g2, perc2, P2, pm2, ppm2, e2, node, step, r2, npp)
            if not cand:
                break
            items = sorted(cand.values(), key=_plan_score, reverse=True)
            level = []
            count = {}
            for item in items:
                if len(level) >= width:
                    break
                c = count.get(item[1], 0)
                if c >= per_tile:
                    continue
                count[item[1]] = c + 1
                level.append(item)
            best_seen = level[0]
            depth += 1
            now += PAC
            if deadline is not None and time.perf_counter() > deadline:
                break
        return "partial", self._path(best_seen), nodes, depth, best_seen[7]

    def plan_cloud(self, state, track, forced, seconds=None, width=None):
        """The plan against the cloud from `state` once he has stepped to
        `forced`: (kind, his steps after that one, search steps used) - or,
        with PACMAN_CLOUD_MODE "marks", ("marks", [(tile, risk), ...], search
        steps used): see search_cloud."""
        width = width or PACMAN_CLOUD_WIDTH
        deadline = None if seconds is None else time.perf_counter() + seconds
        kind, steps, nodes, depth, eaten = self.search_cloud(
            state, track, forced, width, max(2, min(6, width // 20 + 2)), deadline, PACMAN_CLOUD_MODE == "marks")
        self.last_key = (kind == "clear", depth, eaten)
        if kind == "marks":
            return kind, steps, nodes
        if forced is not None:
            steps = steps[1:] if steps and steps[0] == forced else []
        return kind, steps, nodes

    def static(self):
        if "static" not in self._models:
            self._models["static"] = _PlanModel("static", static=True)
        return self._models["static"]

    def step_of(self, model, tile, pacman, frightened):
        """Where this picture of the ghost steps from `tile` with Pac-Man on
        `pacman` (its own tile: it stays)."""
        table = model.scared if frightened else model.normal
        nt = table.get(tile * self.N + pacman)
        if nt is None:
            if model.static or (frightened and model.weights is not None):
                nt = tile  # (a fitted model's moves table has nothing for this while frightened: it waits)
            else:
                nt = self.idx[simple_player_ghost_step(self.tiles[tile], self.tiles[pacman], model.key)]
            table[tile * self.N + pacman] = nt
        return nt

    # ---- one move of Pac-Man and all the ghosts do before his next --------
    def ply(self, pos, step, g, perc, P, now, pm=0, ppm=0, far=False, skip0=False):
        """Pac-Man steps pos -> step at frame `now` (P already counts a
        power pellet eaten there). `g` is one (tile, previous tile, frame
        of its last move, has left the house) per ghost, player ghost
        first; `perc` the classic ghosts' sighting of him (tile, direction,
        frame). None: a ghost was standing there. _PLAN_CAUGHT: one reached
        him before his next move. Else (ghosts, sighting, risk) - risk is
        the chance that a classic ghost's random move lands on him.
        `pm` / `ppm` are the pellets / power pellets left once he has
        stepped: a fitted model of the player's ghost looks at them. With
        `far` it is moved by its saved table instead (see
        PACMAN_PLANNER_FITTED_DEPTH). With `skip0` the player's ghost is left
        out altogether: a cloud of ghosts stands in for it."""
        HOME, rel = self.HOME, self.rel
        PAC, GH, GHF, AI, AIF, RESPAWN, HEAD = self.PAC, self.GH, self.GHF, self.AI, self.AIF, self.RESPAWN, self.HEAD
        g = list(g)
        for i in range(1 if skip0 else 0, 5):
            if g[i][0] == step:
                if i and now < rel[i]:
                    continue  # still caged
                if now < P:
                    h = HOME[i]
                    g[i] = (h, h, now + RESPAWN, False)
                else:
                    return None
        end = now + PAC
        ptile, pdir, plast = perc
        R = plast + self.PERC
        if R < HEAD:
            R = HEAD
        sdir = self.DIRS[(pos, step)]
        model = self.model
        fitted = model.weights is not None and not far
        ev = []
        for i in range(5):
            last = g[i][2]
            if i:
                due = last + AIF
                if due >= P:
                    due = last + AI
                    if due < P:
                        due = P
                if due < rel[i]:
                    due = rel[i]
            else:
                if model.static or skip0:
                    continue
                due = last + GHF
                if due >= P:
                    due = last + GH
                    if due < P:
                        due = P
                if due < HEAD:
                    due = HEAD
            if due <= now:
                due = now + 1
            if due <= end:
                ev.append((due, i))
        if len(ev) > 1:
            ev.sort()
        NB, EXIT, HOUSE, XY, N = self.NB, self.EXIT, self.HOUSE, self.tiles, self.N
        risk = 0.0
        k = 0
        cur = -1
        claimed = None
        while k < len(ev):
            d, i = ev[k]
            k += 1
            tile, prev, last, left = g[i]
            frightened = d < P
            if not left:
                nt = EXIT[tile]
                if nt == tile:
                    g[i] = (tile, prev, d, left)
                    continue
            elif i == 0:
                if fitted:
                    nt = self.fitted_step(model, tile, prev, step, sdir, g, d, P, pm, ppm)
                else:
                    table = model.scared if frightened else model.normal
                    nt = table.get(tile * N + step)
                    if nt is None:
                        nt = self.step_of(model, tile, step, frightened)
                if nt == tile:
                    g[i] = (tile, prev, d, left)
                    continue
            else:
                if frightened:
                    g[i] = (tile, prev, d, left)  # really a random walk; it cannot hurt him
                    continue
                opts = NB[tile]
                n = len(opts)
                if n == 0:
                    continue
                if n == 1:
                    nt = opts[0]
                elif n == 2 and opts[0] == prev:
                    nt = opts[1]
                elif n == 2 and opts[1] == prev:
                    nt = opts[0]
                else:
                    cands = [t for t in opts if t != prev] or opts
                    if d == cur and claimed and len(cands) > 1:
                        # two classic ghosts moving off the same tile in the same frame do not take the same way
                        stacked = False
                        for j in range(1, 5):
                            if j != i:
                                o = g[j]
                                if o[0] == tile or (o[2] == d and o[1] == tile):
                                    stacked = True
                        if stacked:
                            unclaimed = [t for t in cands if t not in claimed]
                            if unclaimed:
                                cands = unclaimed
                    if d < self.chase_end and self._scattering(d):
                        tx, ty = AI_GHOST_SCATTER_CORNERS[i - 1]
                    else:
                        if R <= d:
                            st, sd = step, sdir
                        else:
                            st, sd = ptile, pdir
                        key = (i, st, sd, tile if i == 4 else 0, g[1][0] if i == 3 else 0)
                        target = self.target_cache.get(key)
                        if target is None:
                            target = self.target_cache[key] = ghost_target_tile(
                                AI_GHOST_PERSONALITIES[i - 1], XY[st], sd,
                                ghost_position=XY[tile], blinky_position=XY[g[1][0]],
                            )
                        tx, ty = target
                    nt = -1
                    best = None
                    for t in cands:
                        x, y = XY[t]
                        dist = (x - tx) ** 2 + (y - ty) ** 2
                        if best is None or dist < best:
                            best, nt = dist, t
                    if nt != step and step in cands:
                        risk += AI_GHOST_MISTAKE_CHANCE / len(cands)
                if d != cur:
                    cur = d
                    claimed = [nt]
                else:
                    claimed.append(nt)
            if nt == step:
                if frightened:
                    h = HOME[i]
                    g[i] = (h, h, d + RESPAWN, False)
                    continue
                return _PLAN_CAUGHT
            g[i] = (nt, tile, d, left or not HOUSE[nt])
            if i == 0 and d == now + 1 and d + GH <= end:
                # the player ghost is the faster: about once in eleven of his moves it gets a second one in
                d2 = d + GHF
                if d2 >= P:
                    d2 = d + GH
                    if d2 < P:
                        d2 = P
                if d2 <= end:
                    ev.append((d2, 0))
                    ev[k:] = sorted(ev[k:])
        if R <= end:
            perc = (step, sdir, R)
        return tuple(g), perc, risk

    def _scattering(self, d):
        for end, scatter in self.sched:
            if d < end:
                return scatter
        return False

    def gap(self, pos, g0, now, P):
        """Tiles between him and the player ghost, counting any wait it
        still has (respawn, the slow walk while frightened)."""
        d = self.GD[g0[0]][pos]
        wait = g0[2] + self.GH - now
        if now < P:
            wait += (P - now) // 3
        if wait > 0:
            d += wait / float(self.GH)
        return d

    def nearest(self, pos, pm):
        if self.NBMASK[pos] & pm:
            return 1
        for bit, d in self.NEAR[pos]:
            if pm & bit:
                return d
        return 0

    def search(self, state, width, per_tile, node_cap=0, deadline=None):
        """Beam search from `state` = (tile, ghosts, sighting, power until,
        frame, pellets left as a bit mask, power pellets left) to the end
        of the game. Returns (kind, steps, nodes, depth, eaten): kind
        "clear" when the steps eat every pellet, else "partial" - the line
        that lives longest and scores best (cut short if `node_cap` search
        steps or the clock time `deadline` run out first)."""
        pos, g, perc, P, now, pm, ppm = state
        NB, BIT, PAC, POWER = self.NB, self.BIT, self.PAC, self.POWER
        ply, nearest = self.ply, self.nearest
        w_gap, gap_cap, w_pp, w_risk, w_near = (
            PACMAN_PLANNER_GAP, PACMAN_PLANNER_GAP_CAP, PACMAN_PLANNER_PP, PACMAN_PLANNER_RISK, PACMAN_PLANNER_NEAR,
        )
        w_risk_now = PACMAN_PLANNER_RISK_NOW
        # against a fitted model: how far it is used, and the charge for positions a ghost that leaves
        # the model could catch him from (see PACMAN_PLANNER_FITTED)
        fitted = self.model.weights is not None
        if fitted:
            w_pp = PACMAN_PLANNER_FITTED_PP
        far_from = PACMAN_PLANNER_FITTED_DEPTH if fitted else 0
        WC = self.worst_case() if fitted and PACMAN_PLANNER_WC else None
        w_wc, wc_safe, wc_near, wc_far = (
            PACMAN_PLANNER_WC, PACMAN_PLANNER_WC_SAFE, PACMAN_PLANNER_WC_NEAR, PACMAN_PLANNER_WC_FAR,
        )
        pp_lists = self._pp_lists
        GD, GH, NBMASK = self.GD, self.GH, self.NBMASK
        ghost_frames = float(GH)
        total = bin(pm).count("1")
        # node: (score, tile, ghosts, sighting, power until, pellets, power pellets, eaten, parent, step, risk,
        #        power pellets left)
        root = (0.0, pos, g, perc, P, pm, ppm, 0, None, -1, 0.0, bin(ppm).count("1"))
        level = [root]
        best_seen = root
        nodes = depth = 0
        stale = self.STALE
        while now < stale:
            cand = {}
            later = now + PAC
            for node in level:
                _score, pos, g, perc, P, pm, ppm, eaten, _parent, _step, riskacc, npp_here = node
                for step in NB[pos]:
                    bit = BIT[step]
                    P2, pm2, ppm2, e2, npp = P, pm, ppm, eaten, npp_here
                    if pm & bit:
                        pm2 = pm ^ bit
                        e2 += 1
                        if ppm & bit:
                            ppm2 = ppm ^ bit
                            P2 = now + POWER
                            npp -= 1
                    r = ply(pos, step, g, perc, P2, now, pm2, ppm2, fitted and depth >= far_from)
                    nodes += 1
                    if r is None:
                        continue
                    if pm2 == 0:
                        return "clear", self._path(node) + [step], nodes, depth + 1, e2
                    if r is _PLAN_CAUGHT:
                        continue
                    g2, perc2, risk = r
                    r2 = riskacc
                    if risk:
                        r2 += risk * (w_risk_now if depth == 0 else w_risk if depth < 3 else w_risk * 0.1)
                    left = total - e2 - npp  # ordinary pellets left
                    # his lead over the player ghost, in tiles (see gap())
                    chaser = g2[0]
                    lead = GD[chaser[0]][step]
                    wait = chaser[2] + GH - later
                    if later < P2:
                        wait += (P2 - later) // 3
                    if wait > 0:
                        lead += wait / ghost_frames
                    if WC is not None and chaser[3] and later >= P2:
                        sure = WC[step][chaser[0]]
                        if sure < wc_safe:
                            covered = False
                            if ppm2:
                                pps = pp_lists.get(ppm2)
                                if pps is None:
                                    pps = pp_lists[ppm2] = tuple(i for i in range(self.N) if ppm2 >> i & 1)
                                mine, theirs = self.PDL[step], self.PDL[chaser[0]]
                                for b in pps:
                                    if mine[b] * PAC + PAC < (theirs[b] - 2) * GH:
                                        covered = True  # he is on a power pellet first
                                        break
                            if not covered:
                                r2 += w_wc * (wc_safe - sure) / wc_safe * (1.0 if depth < wc_near else wc_far)
                    score = (
                        100.0 * e2 - w_near * (1 if NBMASK[step] & pm2 else nearest(step, pm2))
                        + w_gap * (lead if lead < gap_cap else gap_cap)
                        + w_pp * npp * (left / 40.0 if left < 40 else 1.0)
                        - r2
                    )
                    key = (step, pm2, chaser[0])
                    old = cand.get(key)
                    if old is None or old[0] < score:
                        cand[key] = (score, step, g2, perc2, P2, pm2, ppm2, e2, node, step, r2, npp)
            if not cand:
                break
            items = sorted(cand.values(), key=_plan_score, reverse=True)
            level = []
            count = {}
            for item in items:
                if len(level) >= width:
                    break
                c = count.get(item[1], 0)
                if c >= per_tile:
                    continue
                count[item[1]] = c + 1
                level.append(item)
            best_seen = level[0]
            depth += 1
            now += PAC
            if node_cap and nodes > node_cap:
                break
            if deadline is not None and time.perf_counter() > deadline:
                break
        return "partial", self._path(best_seen), nodes, depth, best_seen[7]

    @staticmethod
    def _path(node):
        out = []
        while node is not None and node[9] >= 0:
            out.append(node[9])
            node = node[8]
        out.reverse()
        return out

    def plan(self, state, budget, deadline=None, cap=0):
        """The best plan `budget` search steps can find: the beam is as
        wide as the budget allows for the pellets left, and what is left
        of the budget after a search that found no way to clear the maze
        goes on a wider one. Never more than `cap` steps in all, if given
        (a headless test's stand-in for the clock time `deadline` of a real
        game). Returns (kind, steps, search steps used)."""
        left = bin(state[5]).count("1")
        per_level = 2.3 * (1.45 * left + 15)  # search steps one unit of beam width costs over the rest of the game
        width = max(6, min(PACMAN_PLANNER_MAX_WIDTH, int(budget / per_level)))
        best = None
        spent = 0
        while True:
            kind, steps, nodes, depth, eaten = self.search(
                state, width, max(2, min(6, width // 20 + 2)),
                min(budget * 3, cap - spent) if cap else budget * 3, deadline,
            )
            spent += nodes
            key = (kind == "clear", depth, eaten)
            if best is None or key > best[0]:
                best = (key, kind, steps)
            if kind == "clear" or width >= PACMAN_PLANNER_MAX_WIDTH or spent * 2.5 > budget or (cap and spent >= cap):
                break
            if deadline is not None and time.perf_counter() > deadline:
                break
            width = min(PACMAN_PLANNER_MAX_WIDTH, max(width * 2, int(width * (budget - spent) / max(1, nodes))))
        self.last_key = best[0]
        return best[1], best[2], spent

    def openings(self, state, count):
        """Up to `count` different ways for him to begin from `state` - his
        first move, or his first two or three while there are still no
        more than `count` of them - as (steps, the state after them,
        pellets eaten on the way). Each is searched on its own (see
        plan_split): several searches side by side, each from a different
        beginning, in place of one search several times the size."""
        front = [((), state, 0)]
        NB, BIT = self.NB, self.BIT
        for _ in range(3):
            wider = []
            for path, st, eaten in front:
                pellets = st[5]
                for step in NB[st[0]]:
                    if pellets == BIT[step]:
                        return front  # the last pellet is one step away: nothing to divide up
                    nxt = self.after(st, step)
                    if nxt is not None:
                        wider.append((path + (step,), nxt, eaten + (1 if pellets & BIT[step] else 0)))
            if not wider or len(wider) > count:
                break
            front = wider
        return front

    def plan_split(self, state, budget, cap, count):
        """The best of the plans found from each of up to `count` beginnings
        (see openings), `budget` / `cap` search steps apiece - what that
        many search processes find between them in a real game."""
        best = None
        spent = 0
        for path, st, eaten in self.openings(state, count):
            kind, steps, nodes = self.plan(st, budget, None, cap)
            spent += nodes
            clear, depth, ate = self.last_key
            key = (1, -(depth + len(path)), 0) if clear else (0, depth + len(path), ate + eaten)
            if best is None or key > best[0]:
                best = (key, kind, list(path) + steps)
        return best[1], best[2], spent

    def model_from(self, spec):
        """The picture of the ghost a (kind, key) pair names (see _PlanModel.spec)."""
        if spec[0] == "chaser":
            return self.chaser(tuple(spec[1]))
        if spec[0] == "fitted":
            return self.fitted(spec[1])
        if spec[0] == "cloud":
            return self.cloud_model(spec[1])
        return self.static()

    def replay(self, state, steps):
        """Run `steps` from `state`: (how many he survives, whether they
        clear the maze, the risk in the first of them)."""
        pos, g, perc, P, now, pm, ppm = state
        first_risk = 0.0
        NB, BIT = self.NB, self.BIT
        far_from = PACMAN_PLANNER_FITTED_DEPTH if self.model.weights is not None else 1 << 30
        for n, step in enumerate(steps):
            if step not in NB[pos]:
                return n, False, first_risk
            bit = BIT[step]
            if pm & bit:
                pm ^= bit
                if ppm & bit:
                    ppm ^= bit
                    P = now + self.POWER
            r = self.ply(pos, step, g, perc, P, now, pm, ppm, n >= far_from, self.model.cloud is not None)
            if r is None:
                return n, False, first_risk
            if pm == 0:
                return n + 1, True, first_risk
            if r is _PLAN_CAUGHT:
                return n, False, first_risk
            g, perc, risk = r
            if n == 0:
                first_risk = risk
            pos = step
            now += self.PAC
        return len(steps), False, first_risk

    def state_of(self, game, now):
        """The game as the planner sees it at frame-time `now`."""
        idx = self.idx
        start = game.start_time
        self.rel = [0] + [_plan_frame(r) for r in game.ai_release_times]
        ghosts = [(
            idx[game.ghost_position], idx[game.ghost_prev_position],
            _plan_frame(game.ghost_last_move - start), bool(game.ghost_has_left_house),
        )]
        for tile, prev, last, left in zip(
            game.ai_ghost_positions, game.ai_ghost_prev_positions, game.ai_ghost_last_moves, game.ai_ghost_has_left_house,
        ):
            ghosts.append((idx[tile], idx[prev], _plan_frame(last - start), bool(left)))
        perc = (
            idx[game.ai_perceived_pacman], tuple(game.ai_perceived_pacman_direction),
            _plan_frame(game.ai_perception_last_update - start),
        )
        ppm = 0
        order = tuple(idx[t] for t in game.power_pellets)
        if [i for i in self.pp_order if i in order] != list(order):
            self.pp_order = order  # (a new game: eating one never changes the order of the rest)
            self._pp_new_order()
        for t in game.power_pellets:
            ppm |= 1 << idx[t]
        pm = ppm
        for t in game.pellets:
            pm |= 1 << idx[t]
        return (
            idx[game.pacman_position], tuple(ghosts), perc, _plan_frame(game.power_until - start),
            _plan_frame(now - start), pm, ppm,
        )

    def after(self, state, step):
        """`state` one move of his later, every ghost doing as expected;
        None if that move is the end of him or of the game."""
        pos, g, perc, P, now, pm, ppm = state
        if step not in self.NB[pos]:
            return None
        bit = self.BIT[step]
        if pm & bit:
            pm ^= bit
            if ppm & bit:
                ppm ^= bit
                P = now + self.POWER
        r = self.ply(pos, step, g, perc, P, now, pm, ppm, False, self.model.cloud is not None)
        if r is None or r is _PLAN_CAUGHT or pm == 0:
            return None
        return (step, r[0], r[1], P, now + self.PAC, pm, ppm)


def _plan_score(item):
    return item[0]


def _plan_score_first(item):
    return item[0][0]


def _plan_same(a, b, cloudy):
    """Is the game where the plan expected it? Against a cloud the player's
    ghost itself is left out of the comparison: the plan was made for
    wherever it might have gone."""
    if a is None or not cloudy:
        return a == b
    return a[0] == b[0] and a[1][1:] == b[1][1:] and a[2:] == b[2:]


def _cloud_step_safe(planner, state, step):
    """False when the real ghost, where it stands now, can simply be on
    `step` before he moves again (the plan was made a move ago, for a cloud
    of places it might have gone to)."""
    pos, g, perc, P, now, pm, ppm = state
    tile, prev, last, left = g[0]
    if not left or now < P or (ppm >> step) & 1:
        return True
    replies = max(1, min(2, (now - last + planner.PAC) // planner.GH))
    return _guaranteed_moves(planner.tiles[step], planner.tiles[tile], replies) > 0


# In a real game a search takes about as long as one of Pac-Man's moves -
# far too long to run inside one 60th of a second - so it runs in a process
# of its own: after each of his moves the game sends the position expected
# at his next decision, and the answer is waiting when that decision comes.
# The headless test scripts never start it: they search on the spot, with
# the same size of search (PACMAN_PLANNER_NODES), so what they measure is
# what the real game does.
PACMAN_PLANNER_SECONDS = float(os.environ.get("PACMAN_PLANNER_SECONDS", "0.15"))  # thinking time per move in a real game


def _plan_worker_main(conn):
    """The search process: answers each (number, picture of the ghost,
    release frames, position, seconds, power pellet order) with (number, kind, steps, search
    steps used, seconds taken). Only the newest question waiting is
    answered. Ends when the game closes the pipe or sends None."""
    try:
        planner = _planner()
        _plan_warm()
        if PACMAN_CLOUD:
            model = planner.cloud_model(PACMAN_CLOUD_MODEL)
            if model is not None:
                model.cloud = model.cloud.built()  # (a few seconds, once, before the first question)
        conn.send(("ready", os.getpid()))
        rates = {}  # search steps a second on this machine, by kind of picture; measured as it goes
        widths = {}
        while True:
            message = conn.recv()
            while conn.poll():
                message = conn.recv()
            if message is None:
                return
            number, spec, rel, state, seconds, order = message
            planner.model = planner.model_from(spec)
            planner.rel = list(rel)
            if tuple(order) != planner.pp_order:
                planner.pp_order = tuple(order)
                planner._pp_new_order()
            if planner.model is None:
                conn.send((number, "partial", [], 0, 0.0))
                continue
            if spec[0] == "cloud":
                started = time.perf_counter()
                width = widths.get("cloud", PACMAN_CLOUD_WIDTH)
                kind, steps, nodes = planner.plan_cloud(state[0], state[1], state[2], seconds * 1.5, width)
                took = time.perf_counter() - started
                # as wide a search as the thinking time allows on this machine, never wider than asked for
                if took > 0:
                    widths["cloud"] = int(max(8, min(PACMAN_CLOUD_WIDTH, 0.7 * width + 0.3 * width * seconds * 0.8 / took)))
                conn.send((number, kind, steps, nodes, took))
                continue
            started = time.perf_counter()
            rate = rates.get(spec[0], 20000.0 if spec[0] == "fitted" else 40000.0)
            kind, steps, nodes = planner.plan(state, max(1500, int(rate * seconds)), started + seconds)
            took = time.perf_counter() - started
            if nodes >= 1500 and took > 0:
                rates[spec[0]] = 0.7 * rate + 0.3 * nodes / took
            conn.send((number, kind, steps, nodes, took))
    except (EOFError, OSError, KeyboardInterrupt):
        return


class _PlanWorker(object):
    """The game's end of the search process (see _plan_worker_main)."""

    def __init__(self):
        import multiprocessing
        os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")  # the process imports this file: no second banner
        context = multiprocessing.get_context("spawn")
        self.conn, child = context.Pipe()
        self.process = context.Process(target=_plan_worker_main, args=(child,))
        self.process.daemon = True
        self.process.start()
        child.close()
        self.ready = False
        self.number = 0
        self.asked = None  # (number, position asked about)
        self.answers = 0
        self.late = 0
        self.nodes = 0
        self.seconds = 0.0

    def ask(self, model, rel, state, root=None):
        if not self.ready:
            self.poll()
            if not self.ready:
                return
        self.number += 1
        self.asked = (self.number, state if root is None else root)
        try:
            self.conn.send((self.number, model.spec, list(rel), state, PACMAN_PLANNER_SECONDS, _planner().pp_order))
        except (OSError, ValueError):
            self.asked = None

    def poll(self):
        """The answer to the last question asked, if it has come:
        {"root": position, "steps": [...], "kind": ...}; else None."""
        found = None
        try:
            while self.conn.poll():
                message = self.conn.recv()
                if message[0] == "ready":
                    self.ready = True
                    continue
                number, kind, steps, nodes, took = message
                self.answers += 1
                self.nodes += nodes
                self.seconds += took
                if self.asked is not None and number == self.asked[0]:
                    found = {"root": self.asked[1], "steps": steps, "kind": kind}
                else:
                    self.late += 1
        except (EOFError, OSError):
            self.ready = False
        return found

    def close(self):
        try:
            self.conn.send(None)
            self.conn.close()
        except (OSError, ValueError):
            pass
        try:
            self.process.join(0.5)
            if self.process.is_alive():
                self.process.terminate()
        except (OSError, ValueError, AttributeError):
            pass


def _plan_warm():
    """Build what planning against the fitted model needs (its tables, the
    worst-case table by the planner's numbers) now, not at the first
    decision that uses them: about a tenth of a second."""
    if PACMAN_PLANNER_FITTED:
        planner = _planner()
        if planner.fitted(PACMAN_PLANNER_FITTED_MODEL) is not None and PACMAN_PLANNER_WC:
            planner.worst_case()
    if PACMAN_CLOUD:
        _planner().cloud_model(PACMAN_CLOUD_MODEL)


def start_plan_worker():
    """A _PlanWorker, or None if the planner is off or the process cannot
    be started (Pac-Man then plays move by move, as before)."""
    if not PACMAN_PLANNER:
        return None
    try:
        _plan_warm()
        return _PlanWorker()
    except Exception as exc:  # no second process available: play without the planner rather than not at all
        print("Planner process not started (%s): Pac-Man plays without it." % exc, file=sys.stderr)
        return None


def _planner():
    global _PLANNER
    if _PLANNER is None:
        _PLANNER = _Planner()
    return _PLANNER


def _plan_new_game():
    """Per-game memory of the planner (kept on the Game as _plan)."""
    return {
        "current": None,  # the plan being followed: {"root": state it starts from, "steps": [...], "kind": ...}
        "pending": None,  # the plan made since his last move, for the position expected at this one
        "state": None,  # the game as it stood at his last decision
        "seen": 0,  # how many of the player ghost's moves have been compared with the pictures of it
        "hits": {},  # picture name -> [moves it has called right in a row, how many it takes to be believed]
        "last_ghost_move": None,
        "model": None,
        "stats": {"planned": 0, "old": 0, "plans": 0, "clear_plans": 0, "nodes": 0, "max_nodes": 0, "model": None},
    }


def _plan_recognise(game, planner, memory, now):
    """The picture of the player's ghost that is believed at the moment
    (see PACMAN_PLANNER_TRUST_RUN), or None. Every move the ghost has made
    outside the house since the last call - frightened or not - is compared
    with what each picture would have done; a ghost that could have moved
    and did not is a miss for all of them (none of them stands still)."""
    pictures = [planner.chaser((-1.0, 0.0)), planner.chaser((-1.0, -0.3))]
    runs = memory["hits"]  # picture name -> [moves called right in a row, how many it takes to be believed]
    idx = planner.idx
    records = game.player_ghost_seen
    idle = (
        game.ghost_has_left_house and now >= game.power_until
        and memory["last_ghost_move"] == game.ghost_last_move
        and now - game.ghost_last_move >= 2 * GHOST_MOVE_TRUE
    )
    memory["last_ghost_move"] = game.ghost_last_move
    best = None
    for picture in pictures:
        run = runs.get(picture.name)
        if run is None:
            run = runs[picture.name] = [0, PACMAN_PLANNER_TRUST_RUN]
        outcomes = []
        for source, target, pacman, _frightened in records[memory["seen"]:]:
            a, b, c = idx.get(source), idx.get(pacman), idx.get(target)
            if a is not None and b is not None and c is not None:
                outcomes.append(planner.step_of(picture, a, b, False) == c)
        if idle:
            outcomes.append(False)
        for hit in outcomes:
            if hit:
                run[0] += 1
            else:
                if run[0] >= run[1]:
                    run[1] *= 2  # it was believed and then did something else: twice as long next time
                run[0] = 0
        if run[0] >= run[1] and (best is None or run[0] > best[0]):
            best = (run[0], picture)
    memory["seen"] = len(records)
    return best[1] if best else None


def _plan_model(game, planner, memory, now):
    """How the player's ghost is taken to move for planning; None: not known
    well enough to plan against (he then plays as before)."""
    if game.player_ghost_passive and game.ghost_autoplay_weights is None:
        return planner.static() if PACMAN_PLANNER_STATIC else None
    if game.ghost_autoplay_weights is not None and not game.ghost_autoplay_hidden:
        key = player_ghost_policy_key(game.ghost_autoplay_weights)
        return planner.chaser(key) if key is not None else None
    picture = _plan_recognise(game, planner, memory, now)
    if picture is not None or not (PACMAN_PLANNER_FITTED or PACMAN_CLOUD):
        return picture
    if getattr(game, "_pg_idle", False):
        return None  # it is standing still: the models of a person have no such ghost in them
    if PACMAN_CLOUD:
        return planner.cloud_model(PACMAN_CLOUD_MODEL)
    return planner.fitted(PACMAN_PLANNER_FITTED_MODEL)


def plan_pacman_step(game, now):
    """The planned move for Pac-Man at this decision, or None when there is
    no usable plan (no trusted picture of the ghost, or nothing that
    survives). Call plan_pacman_after with the move actually made."""
    memory = game._plan
    if memory is None:
        memory = game._plan = _plan_new_game()
    memory["by_plan"] = False
    _CLOUD_MARKS["at"] = None
    worker = game._plan_worker
    if game._plan_live and worker is None:
        return None  # a real game without the search process: no planner
    planner = _planner()
    model = _plan_model(game, planner, memory, now)
    memory["model"] = model
    if worker is not None:
        answer = worker.poll()
        if answer is not None:
            memory["pending"] = answer
    if model is None:
        memory["current"] = memory["pending"] = memory["state"] = None
        memory["stats"]["old"] += 1
        return None
    memory["stats"]["model"] = model.name
    planner.model = model
    state = memory["state"] = planner.state_of(game, now)
    cloudy = model.cloud is not None
    if cloudy:
        track = getattr(game, "_cloud_track", None) if game.player_ghost_seen else None
        memory["track"] = (track or _CloudTrack()).fields()
        if PACMAN_CLOUD_MODE == "marks":
            # he decides move by move; the cloud's marks for this decision (worked out since his last
            # move) go into that decision's scoring (see choose_pacman_step)
            pending = memory["pending"]
            memory["current"] = None
            if (pending is not None and pending["kind"] == "marks" and pending["steps"]
                    and pending["root"] is not None and pending["root"][0] == state[0]):
                _CLOUD_MARKS["at"] = game.pacman_position
                _CLOUD_MARKS["risk"] = {planner.tiles[tile]: risk for tile, risk in pending["steps"]}
                memory["stats"]["marked"] = memory["stats"].get("marked", 0) + 1
                memory["stats"]["risky"] = memory["stats"].get("risky", 0) + (
                    1 if max(risk for _tile, risk in pending["steps"]) > min(risk for _tile, risk in pending["steps"]) else 0)
            memory["stats"]["old"] += 1
            return None
    best = None
    for plan in (memory["current"], memory["pending"]):
        if plan is None or not plan["steps"]:
            continue
        steps, kind = plan["steps"], plan["kind"]
        if not _plan_same(plan["root"], state, cloudy):
            # the game is not where this plan expected it: does the plan still hold from here?
            lived, cleared, risk = planner.replay(state, steps)
            if cleared and risk == 0.0:
                kind = "clear"
            elif risk == 0.0 and lived >= min(len(steps), PACMAN_PLANNER_MIN_LIFE):
                steps, kind = steps[:lived], "partial"
            else:
                continue
        elif plan is memory["current"] and planner.replay(state, steps[:1])[2] > 0.0:
            # (a plan searched for exactly this position has already weighed that risk: PACMAN_PLANNER_RISK_NOW)
            continue
        # a plan that clears the maze beats one that does not; of two that clear, the quicker; of two that
        # do not, the one that lives longer
        rank = (1, -len(steps)) if kind == "clear" else (0, len(steps))
        if best is None or rank > best[0]:
            best = (rank, steps, kind)
    if best is None or (best[2] != "clear" and PACMAN_PLANNER_PARTIAL != "follow"):
        memory["current"] = None
        memory["stats"]["old"] += 1
        return None
    _rank, steps, kind = best
    if cloudy and not _cloud_step_safe(planner, state, steps[0]):
        memory["current"] = None
        memory["stats"]["old"] += 1
        memory["stats"]["unsafe"] = memory["stats"].get("unsafe", 0) + 1
        return None
    memory["current"] = {"root": None, "steps": steps[1:], "kind": kind}
    memory["stats"]["planned"] += 1
    memory["by_plan"] = True
    return planner.tiles[steps[0]]


def plan_pacman_after(game, now, moved_to):
    """Pac-Man has just stepped to `moved_to` (by the plan or not): work out
    what the game should look like at his next decision and, unless the
    plan in hand already clears the maze from there, search for one."""
    memory = game._plan
    if memory is None or memory["model"] is None or memory["state"] is None:
        return
    planner = _planner()
    planner.model = memory["model"]
    memory["pending"] = None
    expected = planner.after(memory["state"], planner.idx[moved_to])
    current = memory["current"]
    if expected is None:
        memory["current"] = None
        return
    if current is not None:
        current["root"] = expected
        if current["kind"] == "clear" and current["steps"] and not PACMAN_PLANNER_IMPROVE:
            return
    worker = game._plan_worker
    cloudy = memory["model"].cloud is not None
    if worker is not None:
        if cloudy:
            worker.ask(memory["model"], planner.rel, (memory["state"], memory["track"], planner.idx[moved_to]), expected)
        else:
            worker.ask(memory["model"], planner.rel, expected)  # the answer is picked up at his next decision
        return
    if game._plan_live:
        return
    if cloudy:
        kind, steps, nodes = planner.plan_cloud(memory["state"], memory["track"], planner.idx[moved_to])
    elif memory["model"].weights is not None:
        if PACMAN_PLANNER_WORKERS > 1:
            kind, steps, nodes = planner.plan_split(
                expected, PACMAN_PLANNER_FITTED_NODES, PACMAN_PLANNER_FITTED_CAP, PACMAN_PLANNER_WORKERS)
        else:
            kind, steps, nodes = planner.plan(expected, PACMAN_PLANNER_FITTED_NODES, None, PACMAN_PLANNER_FITTED_CAP)
    else:
        kind, steps, nodes = planner.plan(expected, PACMAN_PLANNER_NODES)
    stats = memory["stats"]
    stats["plans"] += 1
    stats["nodes"] += nodes
    stats["max_nodes"] = max(stats["max_nodes"], nodes)
    if kind == "clear":
        stats["clear_plans"] += 1
    memory["pending"] = {"root": expected, "steps": steps, "kind": kind}
# === whole-game planner: end ===


def _synth(tones, rate, channels):
    """The bytes of 16-bit sound for one SOUND_RECIPES entry."""
    samples = array.array("h")
    phase = 0.0
    for tone in tones:
        start, end, ms, loudness, shape = tone[:5]
        vibrato_depth, vibrato_rate = tone[5:7] if len(tone) > 5 else (0.0, 0.0)
        count = int(rate * ms / 1000)
        fade = max(1, min(count // 2, int(rate * 0.006)))  # a few ms in and out: no clicks
        for i in range(count):
            frequency = start + (end - start) * i / count
            if vibrato_depth:
                frequency += vibrato_depth * math.sin(2 * math.pi * vibrato_rate * i / rate)
            phase += frequency / rate
            part = phase % 1.0
            if not start:
                level = 0.0
            elif shape == "square":
                level = 0.6 if part < 0.5 else -0.6
            else:
                level = 4.0 * abs(part - 0.5) - 1.0
            envelope = min(1.0, i / fade, (count - i) / fade)
            value = int(32767 * loudness * envelope * level)
            samples.append(value)
            if channels == 2:
                samples.append(value)
    return samples.tobytes()


def build_sounds():
    """{name: pygame.mixer.Sound} for SOUND_RECIPES, or None when there is
    nothing to play them on (a headless run, sound switched off, no sound
    card, a mixer format this does not write)."""
    if not SOUND_ON or os.environ.get("SDL_AUDIODRIVER") == "dummy":
        return None
    try:
        if pygame.mixer.get_init() is None:
            pygame.mixer.init()
        rate, size, channels = pygame.mixer.get_init()
        if size != -16 or channels not in (1, 2):
            return None
        sounds = {}
        for name, tones in SOUND_RECIPES.items():
            sounds[name] = pygame.mixer.Sound(buffer=_synth(tones, rate, channels))
            sounds[name].set_volume(max(0.0, min(1.0, SOUND_VOLUME)))
        return sounds
    except Exception:  # sound is a nicety: no fault in it may stop the game from starting
        return None


class Game:
    # Filled in by __init__ for a real game (see build_sounds); None: silent.
    sounds = None
    sound_muted = False
    _power_loop_on = False

    def play_sound(self, name):
        if self.sounds and not self.sound_muted:
            self.sounds[name].play()

    def mute_button_rect(self):
        """Where the speaker button sits: the right-hand end of the bar under
        the maze."""
        size = 34
        return pygame.Rect(SCREEN_WIDTH - size - 10, GRID_H * CELL_SIZE + (HUD_HEIGHT - size) // 2, size, size)

    def toggle_mute(self):
        """M, or a click on the speaker button: every sound off, or on again.
        Muting also cuts off whatever is playing at that moment."""
        self.sound_muted = not self.sound_muted
        if self.sound_muted and self.sounds:
            try:
                pygame.mixer.stop()
            except Exception:
                pass
            self._power_loop_on = False  # update_power_sound starts it again if he is still powered when unmuted

    def draw_mute_button(self):
        """The speaker button: sound waves while the sound is on, a red cross
        while it is muted (or when this computer has no sound at all)."""
        rect = self.mute_button_rect()
        silent = self.sound_muted or not self.sounds
        hover = rect.collidepoint(pygame.mouse.get_pos())
        pygame.draw.rect(self.screen, (52, 52, 80) if hover else (40, 40, 64), rect, border_radius=8)
        pygame.draw.rect(self.screen, GHOST_BLUE if hover else (90, 90, 130), rect, 2, border_radius=8)
        colour = (150, 150, 170) if silent else WHITE
        cx, cy = rect.centerx - 4, rect.centery
        pygame.draw.polygon(self.screen, colour, [
            (cx - 8, cy - 4), (cx - 3, cy - 4), (cx + 3, cy - 9), (cx + 3, cy + 9), (cx - 3, cy + 4), (cx - 8, cy + 4),
        ])
        if silent:
            pygame.draw.line(self.screen, (255, 90, 90), (cx + 7, cy - 5), (cx + 15, cy + 5), 2)
            pygame.draw.line(self.screen, (255, 90, 90), (cx + 7, cy + 5), (cx + 15, cy - 5), 2)
        else:
            for radius in (6, 11):
                pygame.draw.arc(
                    self.screen, colour, pygame.Rect(cx + 3 - radius, cy - radius, 2 * radius, 2 * radius),
                    -math.pi / 4, math.pi / 4, 2,
                )

    def update_power_sound(self, now):
        """Keep the power-pellet wobble going exactly while the power lasts
        (called once a frame by run)."""
        if not self.sounds:
            return
        wanted = (
            not self.sound_muted and self.start_time is not None and not self.game_over
            and now < self.power_until
        )
        if wanted != self._power_loop_on:
            if wanted:
                self.sounds["power_loop"].play(loops=-1)
            else:
                self.sounds["power_loop"].stop()
            self._power_loop_on = wanted

    def __init__(self):
        self.run_id = _next_run_id()
        # Set by a headless training script (never by real play) to drive
        # the player's ghost with a fitted imitation model instead of
        # leaving it passive - see choose_player_ghost_direction. A dict
        # of PLAYER_GHOST_FEATURE_NAMES weights, or None for normal
        # keyboard control. Persists across reset_game, since it's a
        # per-run setting, not per-game.
        self.ghost_autoplay_weights = None
        # Set True (together with ghost_autoplay_weights) to have that model
        # drive the ghost WITHOUT Pac-Man being told what it will do - the
        # way a real game against a person goes. Pac-Man then plans against
        # assumed_player_weights, exactly as he does against a person, so a
        # headless test measures what a real game would.
        self.ghost_autoplay_hidden = False
        # What Pac-Man assumes a player ghost he has not been told about
        # will do (see PACMAN_ASSUMED_PLAYER); None: the stand-in chaser.
        self.assumed_player_weights = load_assumed_player_weights()
        warm_pacman_tables()
        # Set True by headless training when nobody is driving the player's
        # ghost at all (the four-classic-ghost stage). It then stays inside
        # the ghost house for the whole game - see move_ghost.
        self.player_ghost_passive = False
        # Chance (0.0-1.0) that an autoplayed move is a random blunder
        # instead of the real model's choice - lets a training curriculum
        # bring the player ghost in at reduced strength first instead of
        # full difficulty all at once (see PLAYER_GHOST_MISTAKE_CHANCE in
        # train_pacman.py). No effect when ghost_autoplay_weights is None.
        self.ghost_autoplay_mistake_chance = 0.0
        # The whole-game planner's search process (see _PlanWorker). Only a
        # real game has one: run() starts it; headless scripts search on the
        # spot instead.
        self._plan_worker = None
        self._plan_live = False
        self._plan_failed = False
        try:
            pygame.mixer.pre_init(22050, -16, 1, 512)  # small buffer: effects land on the move, not after it
        except Exception:
            pass  # no mixer in this pygame: build_sounds will find none either
        pygame.init()
        self.sounds = build_sounds()
        self.screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
        pygame.display.set_caption("Ghost Team vs Pac-Man")
        self.clock = pygame.time.Clock()
        self.font = pygame.font.SysFont(None, 34)
        self.small_font = pygame.font.SysFont(None, 23)
        self.brain = PacmanBrain()
        self.reset_game()

    def reset_game(self):
        self.pacman_weights = self.brain.current_weights()
        self._pg_hold = None  # how long the player ghost has stood still (see PACMAN_HOLD_FACTOR)
        self._plan = None  # the whole-game planner's memory of this game (see PACMAN_PLANNER)
        # Every move the player's ghost has made of its own choosing (outside
        # the house): (from, to, Pac-Man's tile, frightened). The planner
        # recognises how the ghost moves from these.
        self.player_ghost_seen = []
        self.ghost_position = GHOST_START
        self.ai_ghost_positions = list(AI_GHOST_STARTS)
        self.ai_ghost_directions = [(0, 0) for _ in AI_GHOST_STARTS]
        self.ai_ghost_prev_positions = list(AI_GHOST_STARTS)
        self.ai_ghost_last_moves = [0 for _ in AI_GHOST_STARTS]
        # The player's ghost is released at GHOST_HEAD_START; each ally is
        # released AI_RELEASE_INTERVAL ms after the one before it.
        self.ai_release_times = [
            GHOST_HEAD_START + AI_RELEASE_INTERVAL * (index + 1)
            for index in range(len(AI_GHOST_STARTS))
        ]
        self.ai_perceived_pacman = PACMAN_START
        self.ai_perceived_pacman_direction = (0, 0)
        self.ai_perception_last_update = 0
        # Once a ghost has left the house for the first time, it may never
        # walk back in - only a respawn (see eat_ghost) resets this.
        self.ghost_has_left_house = False
        self.ai_ghost_has_left_house = [False for _ in AI_GHOST_STARTS]
        self.pacman_position = PACMAN_START
        self.pacman_prev_position = PACMAN_START
        # Feeds loop_escape_weight (see choose_pacman_step) - Pac-Man's
        # own last PACMAN_RECENT_POSITIONS_WINDOW real tiles, oldest
        # first, so it can notice (and get pushed to break out of)
        # oscillating within the same small pocket under a goose-guard
        # trap instead of just reacting to the current instant forever.
        self.pacman_recent_positions = deque(maxlen=PACMAN_RECENT_POSITIONS_WINDOW)
        self.pacman_direction = (0, 0)
        self.ghost_prev_position = GHOST_START
        self.ghost_direction = (0, 0)
        self.pending_direction = (0, 0)
        self.ghost_last_move = 0
        self.pacman_last_move = 0
        self.start_time = None
        self.game_over = False
        self.result_message = ""
        self.outcome = None
        self.finished_game_meta = None
        self.replay_saved_by_player = False
        self.save_status_message = ""
        self.save_status_until = 0
        self.power_until = 0
        self.ghosts_eaten = 0
        self.eat_flash_until = 0
        self.power_pellets_eaten = 0
        self.power_pellet_timing_bonus = 0.0
        self.near_miss_count = 0

        # Move-by-move replay recording (see _record_replay_frame /
        # finish_game). Only kept on disk for wins and near wins.
        self.replay_frames = []
        self.replay_pellet_events = []
        self.replay_power_pellet_events = []
        self.replay_ghost_eaten_events = []
        self._replay_last_key = None

        self.pellets = {
            (x, y)
            for y, row in enumerate(MAZE)
            for x, char in enumerate(row)
            if char == "."
        }

        # Place four power pellets on open, reachable tiles away from the
        # starting positions. Remove any ordinary pellet at those locations.
        reachable = pacman_bfs_distances(PACMAN_START)
        candidates = [
            p for p in reachable
            if p not in (GHOST_START, PACMAN_START, *AI_GHOST_STARTS)
            and abs(p[0] - PACMAN_START[0]) + abs(p[1] - PACMAN_START[1]) >= 8
        ]
        random.shuffle(candidates)
        self.power_pellets = set(candidates[:4])
        self.pellets.difference_update(self.power_pellets)
        self.initial_pellet_total = len(self.pellets) + len(self.power_pellets)
        self.replay_initial_pellets = set(self.pellets)
        self.replay_initial_power_pellets = set(self.power_pellets)

    def _record_replay_frame(self, elapsed):
        """Append a replay frame only when something actually moved/changed.
        Consecutive recorded frames are exactly the move events for every
        character, so a replay can later reuse animated_position() to glide
        between them, the same way live rendering does - no separate replay
        interpolation logic needed."""
        key = (
            self.pacman_position, self.pacman_direction,
            self.ghost_position, self.ghost_direction,
            tuple(self.ai_ghost_positions), tuple(self.ai_ghost_directions),
            self.power_until,
        )
        if key == self._replay_last_key:
            return
        self._replay_last_key = key
        self.replay_frames.append({
            "t": elapsed,
            "pacman": list(self.pacman_position),
            "pacman_dir": list(self.pacman_direction),
            "ghost": list(self.ghost_position),
            "ghost_dir": list(self.ghost_direction),
            "ai": [list(p) for p in self.ai_ghost_positions],
            "ai_dir": [list(d) for d in self.ai_ghost_directions],
            # Stored relative to game start, same timeline as "t", so a
            # replay can compare it directly against its own elapsed clock.
            "power_until": self.power_until - self.start_time,
        })

    def start(self):
        if self.start_time is None and not self.game_over:
            self.start_time = pygame.time.get_ticks()
            self.play_sound("start")

    def set_direction(self, direction):
        self.pending_direction = direction
        if self.start_time is None:
            self.start()

    def _legal_ghost_options(self, position):
        """Every direction the player-controlled ghost could legally take
        from position right now (for recording what was actually
        available at a real decision - see _record_player_ghost_move)."""
        return [
            d for d in CARDINALS
            if is_open(*step_position(position, d)) and step_position(position, d) not in HOUSE_TILES
        ]

    def _record_player_ghost_move(self, now, position, prev_direction, chosen_direction, options):
        """Append one real (human-driven) decision by the player-
        controlled ghost to PLAYER_MOVES_FILE: the game state at the
        moment of choice, every direction that was actually legal, and
        which one was picked. fit_player_ghost.py later fits
        PLAYER_GHOST_WEIGHTS_FILE from this log so a training run can
        drive the player's ghost the way its player actually plays (see
        choose_player_ghost_direction)."""
        record = {
            "t": now - self.start_time,
            "ghost": list(position),
            "prev_dir": list(prev_direction),
            "chosen_dir": list(chosen_direction),
            "options": [list(d) for d in options],
            "pacman": list(self.pacman_position),
            "pacman_dir": list(self.pacman_direction),
            "ai_ghosts": [list(p) for p in self.ai_ghost_positions],
            "frightened": now < self.power_until,
            # Added for the exit-coverage/power-pellet-guard/frightened-
            # timer features (see player_ghost_features) - absent on
            # anything recorded before they existed, which
            # fit_player_ghost.py's build_examples falls back for.
            "power_pellets": [list(p) for p in self.power_pellets],
            "power_remaining_ms": max(0, self.power_until - now),
            # Version 2 fields: the rest of what the player could see when
            # choosing, so a later model is free to use inputs that
            # player_ghost_features doesn't compute yet. None of them are
            # read by fit_player_ghost.py today; older records lack them.
            #   pending      - the direction key last pressed (may differ
            #                  from chosen_dir: a turn asked for early, or
            #                  one that was blocked)
            #   pellets      - which ordinary pellets are still uneaten, as
            #                  a hex bitmask over pellet_tile_order()
            #   ai_out       - per classic ghost, whether it is out of the
            #                  house (False = waiting to leave or respawning)
            #   game / wall  - game number and wall-clock second, to group
            #                  moves into games and to tell recording
            #                  sessions (and versions of Pac-Man) apart
            "v": 2,
            "pending": list(self.pending_direction),
            "pacman_prev": list(self.pacman_prev_position),
            "ai_dirs": [list(d) for d in self.ai_ghost_directions],
            "ai_out": [bool(left) for left in self.ai_ghost_has_left_house],
            "pellets": "%x" % sum(1 << i for i, tile in enumerate(pellet_tile_order()) if tile in self.pellets),
            "ghosts_eaten": self.ghosts_eaten,
            "scatter": scatter_or_chase(now - self.start_time) == "scatter",
            "game": self.brain.games_played,
            "wall": int(time.time()),
        }
        try:
            with open(PLAYER_MOVES_FILE, "a") as handle:
                handle.write(json.dumps(record) + "\n")
        except OSError:
            pass

    def move_ghost(self, now):
        if self.player_ghost_passive:
            # Nobody is driving it. It used to walk out and stand in the
            # corridor above the door for the rest of the game - a permanent
            # roadblock no real game has, which Pac-Man also took for a live
            # threat and so left the pellets beside it uneaten. It now stays
            # in the house, so this stage is exactly the four classic ghosts.
            return
        delay = GHOST_FRIGHTENED_MOVE_GATE if now < self.power_until else GHOST_MOVE_GATE
        if now - self.ghost_last_move < delay:
            return

        if not self.ghost_has_left_house:
            # Auto-pilot straight out of the house before handing control
            # to the player - the house interior is too cramped to expect
            # manual input to reliably find the single way out.
            candidate = house_exit_step(self.ghost_position)
            if candidate != self.ghost_position:
                self.ghost_prev_position = self.ghost_position
                self.ghost_direction = direction_between(self.ghost_position, candidate)
                self.ghost_position = candidate
                self.ghost_last_move = now
                if candidate not in HOUSE_TILES:
                    self.ghost_has_left_house = True
                    self.play_sound("release")
            return

        if self.ghost_autoplay_weights is not None:
            # Headless training opted in to driving the player's ghost
            # with a model fit from recorded human play, instead of
            # leaving it passive - this just picks pending_direction the
            # way a real key press would have arrived, so everything
            # below is identical either way; only the recording at the
            # bottom of this loop is skipped, since this isn't a real
            # human decision. A nonzero ghost_autoplay_mistake_chance
            # swaps in a random legal direction instead, some fraction of
            # moves, for a training curriculum that eases this ghost in
            # at reduced strength before full difficulty.
            if self.ghost_autoplay_mistake_chance > 0 and random.random() < self.ghost_autoplay_mistake_chance:
                blunder_options = self._legal_ghost_options(self.ghost_position)
                self.pending_direction = random.choice(blunder_options) if blunder_options else self.ghost_direction
            else:
                self.pending_direction = choose_player_ghost_direction(
                    self.ghost_position, self.ghost_direction,
                    self.pacman_position, self.pacman_direction,
                    self.ai_ghost_positions, self.ghost_autoplay_weights,
                    power_pellets=self.power_pellets, frightened=now < self.power_until,
                    power_remaining_ms=max(0, self.power_until - now), pellets=self.pellets,
                )

        options = self._legal_ghost_options(self.ghost_position)

        # Try the most recently requested direction first. If blocked, keep
        # the current direction so the player can hold a key before a turn.
        directions = [self.pending_direction, self.ghost_direction]
        for direction in directions:
            if direction == (0, 0):
                continue
            candidate = step_position(self.ghost_position, direction)
            if not is_open(*candidate):
                continue
            if self.ghost_has_left_house and candidate in HOUSE_TILES:
                continue  # can't walk back into the house once released
            if self.ghost_autoplay_weights is None and options:
                self._record_player_ghost_move(now, self.ghost_position, self.ghost_direction, direction, options)
            _cloud_observe(self, now, direction)
            self.player_ghost_seen.append((self.ghost_position, candidate, self.pacman_position, now < self.power_until))
            self.ghost_prev_position = self.ghost_position
            self.ghost_position = candidate
            self.ghost_direction = direction
            self.ghost_last_move = now
            if candidate not in HOUSE_TILES:
                self.ghost_has_left_house = True
            return

        # Blocked in every direction: stay put. Leave ghost_last_move alone
        # so the animation doesn't replay the last successful glide, and so
        # the next valid direction can be taken immediately rather than
        # waiting out a delay that was never actually used to move.

    def move_ai_ghosts(self, now):
        """Move the four allied AI ghosts (Blinky, Pinky, Inky, Clyde) using
        each one's original-game personality (see AI_GHOST_PERSONALITIES /
        ghost_target_tile), gated by the global scatter/chase cycle (see
        SCATTER_CHASE_SCHEDULE) - during a scatter window every ghost
        ignores Pac-Man and heads for its own corner instead. Like the
        original, a ghost never reverses direction unless that's its only
        option, and a frightened ghost just picks a random valid
        direction. The AI still reacts to a slightly stale, occasionally
        mistaken sighting of Pac-Man, which keeps it beatable at these
        (deliberately slower than the arcade original) speeds."""
        if now - self.ai_perception_last_update >= AI_GHOST_PERCEPTION_INTERVAL:
            self.ai_perceived_pacman = self.pacman_position
            self.ai_perceived_pacman_direction = self.pacman_direction
            self.ai_perception_last_update = now

        frightened = now < self.power_until
        delay = AI_GHOST_FRIGHTENED_MOVE_GATE if frightened else AI_GHOST_MOVE_GATE
        elapsed = now - self.start_time
        scattering = scatter_or_chase(elapsed) == "scatter"

        # Snapshot of where every AI ghost stood before any of them moves
        # this tick, so a ghost can tell whether it's currently stacked
        # exactly on top of another one (checking the live, partway-
        # mutated list further down would miss ghosts stacked with an
        # ally that's already been processed this same tick).
        starting_positions = list(self.ai_ghost_positions)
        claimed_next_tiles = set()

        for index, position in enumerate(self.ai_ghost_positions):
            if elapsed < self.ai_release_times[index]:
                continue  # still caged, waiting its turn to leave the house
            if now - self.ai_ghost_last_moves[index] < delay:
                continue

            if not self.ai_ghost_has_left_house[index]:
                # Still inside: walk straight out deterministically instead
                # of letting chase-logic or random mistakes wander/get
                # confused in the cramped house interior.
                next_tile = house_exit_step(position)
            else:
                options = [tile for tile in neighbours(position) if tile not in HOUSE_TILES]
                if not options:
                    continue

                # Never reverse into the tile just left unless that's the
                # only way forward (a dead end) - like the original ghosts.
                prev = self.ai_ghost_prev_positions[index]
                non_reversing = [tile for tile in options if tile != prev]
                candidates = non_reversing if non_reversing else options

                # Two ghosts standing on the exact same tile (e.g. one just
                # got eaten and respawned onto a still-caged ally's spot)
                # share an identical candidate list here, so without this
                # they'd keep landing on the same next tile forever -
                # frightened/random or chasing/targeted, it doesn't matter,
                # there's nothing left to tell them apart - and stay
                # permanently fused for the rest of the game. Once there's
                # an actual choice, steer away from whatever tile another
                # stacked ghost already claimed this tick so they get a
                # real chance to split apart again.
                if len(candidates) > 1 and starting_positions.count(position) > 1:
                    unclaimed = [tile for tile in candidates if tile not in claimed_next_tiles]
                    if unclaimed:
                        candidates = unclaimed

                if frightened or random.random() < AI_GHOST_MISTAKE_CHANCE:
                    next_tile = random.choice(candidates)
                elif scattering:
                    # Global scatter window: ignore Pac-Man entirely and
                    # head for home, like every ghost in the original -
                    # Clyde's own proximity-based retreat (below) only
                    # matters during chase windows, since scatter already
                    # sends him to the same corner.
                    target = AI_GHOST_SCATTER_CORNERS[index]
                    next_tile = min(
                        candidates,
                        key=lambda tile: (tile[0] - target[0]) ** 2 + (tile[1] - target[1]) ** 2,
                    )
                else:
                    target = ghost_target_tile(
                        AI_GHOST_PERSONALITIES[index],
                        self.ai_perceived_pacman,
                        self.ai_perceived_pacman_direction,
                        ghost_position=position,
                        blinky_position=self.ai_ghost_positions[0],
                    )
                    next_tile = min(
                        candidates,
                        key=lambda tile: (tile[0] - target[0]) ** 2 + (tile[1] - target[1]) ** 2,
                    )

            claimed_next_tiles.add(next_tile)
            self.ai_ghost_directions[index] = direction_between(position, next_tile)
            self.ai_ghost_prev_positions[index] = position
            self.ai_ghost_positions[index] = next_tile
            self.ai_ghost_last_moves[index] = now
            if next_tile not in HOUSE_TILES:
                self.ai_ghost_has_left_house[index] = True

    def _real_game(self):
        """True when a person is driving the player's ghost (the games the
        move and decision logs are for) - not a headless game."""
        return self.ghost_autoplay_weights is None and not self.player_ghost_passive

    def _append_decision_line(self, record):
        try:
            with open(PACMAN_DECISIONS_FILE, "a") as handle:
                handle.write(json.dumps(record) + "\n")
        except (OSError, TypeError, ValueError):
            pass  # a log line must never be able to stop a game

    def _record_pacman_decision(self, now, chosen, options, took_ms):
        """One line of PACMAN_DECISIONS_FILE: the decision Pac-Man has just
        made, before he is moved (see PACMAN_DECISIONS_FILE)."""
        ghost_out = self.ghost_has_left_house and self.ghost_position not in HOUSE_TILES
        sure = None
        if PACMAN_WORST_WEIGHT:
            chaser = self.ghost_position if ghost_out else HOUSE_EXIT_TARGET
            sure = _guaranteed_moves(chosen, chaser, 1 if ghost_out else 0)
        self._append_decision_line({
            "game": self.brain.games_played,
            "wall": int(time.time()),
            "t": now - self.start_time,
            "pacman": list(self.pacman_position),
            "pacman_dir": list(self.pacman_direction),
            "chosen": list(chosen),
            "options": options,
            "sure": sure,
            "ghost": list(self.ghost_position),
            "ghost_out": bool(ghost_out),
            "ai_ghosts": [list(p) for p in self.ai_ghost_positions],
            "ai_out": [bool(left) for left in self.ai_ghost_has_left_house],
            "power_remaining_ms": max(0, self.power_until - now),
            "power_pellets": [list(p) for p in self.power_pellets],
            "pellets_left": len(self.pellets),
            "took_ms": round(took_ms, 1),
            "planned": bool(self._plan is not None and self._plan.get("by_plan")),
        })

    def _record_pacman_ending(self, elapsed, outcome, caught, pellet_fraction):
        """The closing line of a real game in PACMAN_DECISIONS_FILE: how it
        ended and which ghost was on his tile."""
        caught_by = []
        if caught:
            if self.pacman_position == self.ghost_position:
                caught_by.append("player")
            caught_by.extend(
                index for index, position in enumerate(self.ai_ghost_positions) if position == self.pacman_position
            )
        self._append_decision_line({
            "game": self.brain.games_played,
            "wall": int(time.time()),
            "t": elapsed,
            "end": outcome,
            "caught_by": caught_by,
            "pellet_fraction": round(pellet_fraction, 4),
            "pacman": list(self.pacman_position),
            "ghost": list(self.ghost_position),
            "ai_ghosts": [list(p) for p in self.ai_ghost_positions],
            "worst_weight": PACMAN_WORST_WEIGHT,
            "worst_safe": PACMAN_WORST_SAFE,
        })

    def _planned_step(self, now, moved=False):
        """The whole-game planner's move for this decision, or None when it
        has none (see PACMAN_PLANNER); with `moved`, tells the planner where
        Pac-Man has just stepped instead. In a real game a fault in the
        planner switches it off for the rest of the session rather than
        stopping the game; a headless test lets the error through."""
        if not PACMAN_PLANNER or self._plan_failed:
            return None
        try:
            if moved:
                plan_pacman_after(self, now, self.pacman_position)
                return None
            return plan_pacman_step(self, now)
        except Exception as exc:
            if not self._plan_live:
                raise
            self._plan_failed = True
            print("Planner switched off after an error (%r): Pac-Man plays without it." % (exc,), file=sys.stderr)
            return None

    def move_pacman(self, now):
        global PACMAN_DECISION_TRACE
        if now - self.pacman_last_move < PACMAN_MOVE_GATE:
            return

        frightened = now < self.power_until
        elapsed = now - self.start_time
        scattering = scatter_or_chase(elapsed) == "scatter"
        scatter_time_remaining = time_until_chase_resumes(elapsed)
        permanent_chase = elapsed >= PERMANENT_CHASE_START
        time_until_perception_refresh = max(0, AI_GHOST_PERCEPTION_INTERVAL - (now - self.ai_perception_last_update))
        respawning_indices = tuple(
            i for i, left in enumerate([self.ghost_has_left_house, *self.ai_ghost_has_left_house])
            if not left
        )
        model_horizon = 0
        if self.ghost_autoplay_weights is not None and not self.ghost_autoplay_hidden:
            ghost_model, player_mode = self.ghost_autoplay_weights, "model"  # told exactly what it will do
        elif self.player_ghost_passive and self.ghost_autoplay_weights is None:
            ghost_model, player_mode = None, "static"
        elif self.assumed_player_weights is not None:
            ghost_model, player_mode = self.assumed_player_weights, "model"  # his picture of the person playing
            model_horizon = PACMAN_ASSUMED_PLAYER_HORIZON
        else:
            ghost_model, player_mode = None, "chase"
        # How long the player ghost has stood on one tile although free to
        # move, and how much longer he takes it to stay there (see
        # PACMAN_HOLD_FACTOR).
        hold = getattr(self, "_pg_hold", None)
        if hold is None or hold[0] != self.start_time:
            hold = (self.start_time, (self.ghost_position,), now)
        elif self.ghost_position not in hold[1]:
            if (PACMAN_HOLD_SHUFFLE and len(hold[1]) == 1 and self.ghost_has_left_house
                    and self.ghost_position in pacman_neighbours(hold[1][0])):
                # One step to the tile next door: still "in the same place"
                # if it then only goes back and forth between the two.
                hold = (hold[0], (hold[1][0], self.ghost_position), hold[2])
            else:
                hold = (self.start_time, (self.ghost_position,), now)
        # (Its respawn wait shows as a last-move time still to come; the
        # latest one seen is kept, so the wait is never counted as idling.)
        free_from = max(
            hold[2], self.start_time + GHOST_HEAD_START, self.power_until,
            self.ghost_last_move if self.ghost_last_move > now else 0,
        )
        hold = self._pg_hold = (hold[0], hold[1], free_from)
        hold_until = 0
        hold_in_house = not self.ghost_has_left_house
        if PACMAN_HOLD_FACTOR and now - free_from >= (PACMAN_HOLD_MIN_MS if hold_in_house else PACMAN_HOLD_MIN_OUT_MS):
            hold_until = now + min(PACMAN_HOLD_CAP_MS, PACMAN_HOLD_FACTOR * (now - free_from))
        log_decision = PACMAN_DECISION_LOG and self._real_game()
        if log_decision:
            PACMAN_DECISION_TRACE = []
            decision_started = time.perf_counter()
        # The whole-game plan decides when there is one (see PACMAN_PLANNER);
        # otherwise he decides move by move, as he always has.
        self._pg_idle = bool(hold_until)  # the idle-ghost rule is in force: no plan against the fitted model
        next_position = self._planned_step(now)
        if next_position is None:
            next_position = choose_pacman_step(
                self.pacman_position,
                [self.ghost_position, *self.ai_ghost_positions],
                self.pellets,
                self.power_pellets,
                frightened,
                self.pacman_weights,
                pacman_direction=self.pacman_direction,
                prev_ghost_positions=[self.ghost_prev_position, *self.ai_ghost_prev_positions],
                perceived_pacman_position=self.ai_perceived_pacman,
                player_ghost_weights=ghost_model,
                scattering=scattering,
                recent_positions=tuple(self.pacman_recent_positions),
                scatter_time_remaining=scatter_time_remaining,
                time_until_perception_refresh=time_until_perception_refresh,
                respawning_indices=respawning_indices,
                permanent_chase=permanent_chase,
                sim_state={
                    "now": now,
                    "start": self.start_time,
                    "power_until": self.power_until,
                    "release": tuple(self.ai_release_times),
                    "ghost_prev": (self.ghost_prev_position, *self.ai_ghost_prev_positions),
                    "ghost_last": (self.ghost_last_move, *self.ai_ghost_last_moves),
                    "left_house": (self.ghost_has_left_house, *self.ai_ghost_has_left_house),
                    "perceived_direction": self.ai_perceived_pacman_direction,
                    "perception_last": self.ai_perception_last_update,
                    "player_mode": player_mode,
                    "model_horizon": model_horizon,
                    "told": self.ghost_autoplay_weights is not None and not self.ghost_autoplay_hidden,
                    "hold_until": hold_until,
                    "hold_tiles": hold[1] if (hold_until and len(hold[1]) > 1) else (),
                    "hold_credit": hold_in_house or PACMAN_HOLD_CREDIT_OUT,
                },
            )
        if log_decision:
            options_seen, PACMAN_DECISION_TRACE = PACMAN_DECISION_TRACE, None
            self._record_pacman_decision(
                now, next_position, options_seen, (time.perf_counter() - decision_started) * 1000.0
            )
        if next_position != self.pacman_position:
            self.pacman_direction = direction_between(self.pacman_position, next_position)
            self.pacman_prev_position = self.pacman_position
            self.pacman_position = next_position
        self.pacman_recent_positions.append(self.pacman_position)
        self.pacman_last_move = now
        self._planned_step(now, moved=True)

        # Count dangerous close calls (not while frightened, when ghosts are
        # harmless) so reckless play is punished even on games Pac-Man wins
        # or escapes purely by luck, not just on games it gets caught in.
        if not frightened:
            ghost_positions = (self.ghost_position, *self.ai_ghost_positions)
            if any(
                abs(self.pacman_position[0] - gx) + abs(self.pacman_position[1] - gy) <= NEAR_MISS_RADIUS
                for gx, gy in ghost_positions
            ):
                self.near_miss_count += 1

    def collect_items(self, now):
        elapsed = now - self.start_time
        if self.pacman_position in self.pellets:
            self.pellets.remove(self.pacman_position)
            self.replay_pellet_events.append({"t": elapsed, "pos": list(self.pacman_position)})
            self.play_sound("chomp_a" if len(self.pellets) % 2 else "chomp_b")
        if self.pacman_position in self.power_pellets:
            self.power_pellets.remove(self.pacman_position)
            self.power_until = now + POWER_DURATION
            self.play_sound("power")
            self.replay_power_pellet_events.append({"t": elapsed, "pos": list(self.pacman_position)})
            # Reward good timing: eating this while the ghosts are bunched
            # up together affects all of them at once.
            ghost_positions = [self.ghost_position, *self.ai_ghost_positions]
            self.power_pellet_timing_bonus += ghost_cluster_score(ghost_positions)
            self.power_pellets_eaten += 1

    def eat_ghost(self, now, index=None):
        """Pac-Man touched a frightened ghost: send it home instead of
        ending the game. Respawning puts
        it back inside the house, so it must leave again before the
        no-re-entry rule applies to it once more."""
        if index is None:
            self.ghost_position = GHOST_START
            self.ghost_prev_position = GHOST_START
            self.ghost_direction = (0, 0)
            self.pending_direction = (0, 0)
            self.ghost_has_left_house = False
            self.ghost_last_move = now + GHOST_RESPAWN_COOLDOWN_MS
        else:
            self.ai_ghost_last_moves[index] = now + GHOST_RESPAWN_COOLDOWN_MS
            self.ai_ghost_positions[index] = AI_GHOST_STARTS[index]
            self.ai_ghost_prev_positions[index] = AI_GHOST_STARTS[index]
            self.ai_ghost_directions[index] = (0, 0)
            self.ai_ghost_has_left_house[index] = False
        self.ghosts_eaten += 1
        self.play_sound("eat_ghost")
        self.eat_flash_until = now + 1_200
        self.replay_ghost_eaten_events.append({
            "t": now - self.start_time,
            "who": "player" if index is None else "ai",
            "index": index,
        })

    def finish_game(self, elapsed, outcome, caught):
        """End the game and let Pac-Man's brain learn from how it went.
        `outcome` is a key into GAME_OVER_STYLES, picking that ending's
        title/subtitle/accent/icon on the game-over screen.

        Clearing the maze is Pac-Man's only way to win, so the reward is
        about exactly that and nothing else: a win is worth 2.5 (a little
        more for a quick one), and every other ending - caught, or the
        stalemate backstop - is a loss worth between -1.0 and -0.2
        depending only on how much of the maze he had eaten. Eating ghosts,
        timing power pellets well and keeping clear of close calls are all
        still useful, but only as means to that end, so none of them earns
        anything on its own: a brain cannot score well by farming ghosts or
        by staying alive without eating.

        The gap between the best possible loss and the worst possible win
        is deliberately wide, so a batch of games with even one win in it
        can never be outscored by a batch of graceful losses.
        """
        self.game_over = True
        self.outcome = outcome
        self.result_message = GAME_OVER_STYLES[outcome]["title"]
        self.play_sound({"ghost_win": "caught", "maze_cleared": "cleared"}.get(outcome, "stalemate"))

        remaining = len(self.pellets) + len(self.power_pellets)
        pellet_fraction = 1.0 - remaining / max(1, self.initial_pellet_total)
        won = outcome == "maze_cleared"

        if won:
            reward = 2.5 + (1.0 - min(1.0, elapsed / STALEMATE_LIMIT)) * 0.3
        else:
            reward = -1.0 + pellet_fraction * 0.8

        if PACMAN_DECISION_LOG and self._real_game():
            self._record_pacman_ending(elapsed, outcome, caught, pellet_fraction)
        self.brain.learn(reward)
        near_win = not won and pellet_fraction >= NEAR_WIN_PELLET_FRACTION
        record_game_history(
            self.brain.games_played,
            outcome,
            won=won,
            reward=reward,
            elapsed_ms=elapsed,
            pellet_fraction=pellet_fraction,
            ghosts_eaten=self.ghosts_eaten,
            near_misses=self.near_miss_count,
        )

        if won or near_win:
            save_replay(self, outcome, won, near_win, reward, elapsed, pellet_fraction)

        # Stashed so the player can still choose to save+pin this replay
        # from the game-over screen (see save_current_replay) even if it
        # wasn't a win/near-win and so wasn't auto-saved above.
        self.finished_game_meta = {
            "outcome": outcome, "won": won, "near_win": near_win, "reward": reward,
            "elapsed_ms": elapsed, "pellet_fraction": pellet_fraction,
        }
        self.replay_saved_by_player = False

    def save_current_replay(self):
        """Manually save+pin the just-finished game's replay (see the 'S'
        prompt on the game-over screen) - kept regardless of outcome and
        never evicted, unlike the automatic win/near-win saves. A no-op
        if there's no finished game to save, or it's already been saved
        once this game."""
        if not self.game_over or self.finished_game_meta is None or self.replay_saved_by_player:
            return False
        m = self.finished_game_meta
        save_pinned_replay(
            self, m["outcome"], m["won"], m["near_win"], m["reward"],
            m["elapsed_ms"], m["pellet_fraction"],
        )
        self.replay_saved_by_player = True
        return True

    def check_catch(self, now, elapsed):
        """Pac-Man/ghost contact, shared by both catch checks in update().
        Also handles eating a frightened ghost on the spot. Returns True if
        a non-frightened ghost just caught Pac-Man."""
        frightened = now < self.power_until
        caught = False

        if self.pacman_position == self.ghost_position:
            if frightened:
                self.eat_ghost(now)
            else:
                caught = True

        for index, position in enumerate(self.ai_ghost_positions):
            if elapsed < self.ai_release_times[index]:
                continue  # still caged: not part of the chase yet
            if self.pacman_position == position:
                if frightened:
                    self.eat_ghost(now, index)
                else:
                    caught = True

        return caught

    def update(self, now):
        if self.start_time is None or self.game_over:
            return

        elapsed = now - self.start_time
        caught = False

        # Pac-Man gets a short head start; after that the player controls the
        # ghost. Each character moves in exactly one place in the update loop.
        if elapsed >= GHOST_HEAD_START:
            self.move_ghost(now)
            self.move_ai_ghosts(now)
            # Check right away: if a ghost just walked onto Pac-Man's tile,
            # that's a catch, full stop - Pac-Man doesn't get a free tick to
            # dodge away before the contact is even checked.
            caught = self.check_catch(now, elapsed)

        if not caught:
            self.move_pacman(now)

        self.collect_items(now)
        self._record_replay_frame(elapsed)

        if elapsed >= GHOST_HEAD_START and not caught:
            caught = self.check_catch(now, elapsed)

        if caught:
            self.finish_game(elapsed, "ghost_win", caught=True)
            return

        if not self.pellets and not self.power_pellets:
            self.finish_game(elapsed, "maze_cleared", caught=False)
            return

        if elapsed >= STALEMATE_LIMIT:
            self.finish_game(elapsed, "stalemate", caught=False)

    def draw_tile(self, x, y, char):
        rect = pygame.Rect(x * CELL_SIZE, y * CELL_SIZE, CELL_SIZE, CELL_SIZE)
        if char == "#":
            pygame.draw.rect(self.screen, WALL, rect)
            pygame.draw.line(self.screen, WALL_EDGE, rect.topleft, rect.topright, 2)
            pygame.draw.line(self.screen, WALL_EDGE, rect.topleft, rect.bottomleft, 2)
        elif (x, y) in self.pellets:
            pygame.draw.circle(self.screen, PELLET, rect.center, 3)
        elif (x, y) in self.power_pellets:
            pygame.draw.circle(self.screen, POWER_PELLET, rect.center, 7)

    def draw_pacman_icon(self, center, radius, color, direction=(1, 0), mouth_fraction=0.0):
        """Pac-Man glyph at an arbitrary pixel center/size, used both for
        the in-maze sprite and the game-over screen's icons. The wedge
        faces `direction` and its angle follows `mouth_fraction` (0 = shut,
        1 = wide open), so the caller can animate a classic chomp cycle."""
        pygame.draw.circle(self.screen, color, center, radius)
        dx, dy = direction
        if dx == 0 and dy == 0:
            dx, dy = 1, 0
        facing_angle = math.atan2(dy, dx)
        half_angle = math.radians(46) * max(0.0, min(1.0, mouth_fraction))
        if half_angle > 0.02:
            p1 = (
                center[0] + radius * math.cos(facing_angle - half_angle),
                center[1] + radius * math.sin(facing_angle - half_angle),
            )
            p2 = (
                center[0] + radius * math.cos(facing_angle + half_angle),
                center[1] + radius * math.sin(facing_angle + half_angle),
            )
            pygame.draw.polygon(self.screen, BLACK, [center, p1, p2])

    def draw_ghost_icon(self, center, radius, color, direction, frightened):
        """Ghost glyph at an arbitrary pixel center/size, used both for the
        in-maze sprite and the game-over screen's icons."""
        cx, cy = center
        dome_cy = cy - 3
        body_bottom = cy + radius - 2
        left = cx - radius
        right = cx + radius

        pygame.draw.circle(self.screen, color, (cx, dome_cy), radius)
        pygame.draw.rect(
            self.screen, color, (left, dome_cy, right - left, body_bottom - dome_cy)
        )

        # Scalloped skirt: a row of downward-pointing triangles.
        feet = 4
        foot_width = (right - left) / feet
        for i in range(feet):
            x0 = left + i * foot_width
            x1 = x0 + foot_width
            pygame.draw.polygon(
                self.screen,
                color,
                [(x0, body_bottom), (x1, body_bottom), ((x0 + x1) / 2, body_bottom + foot_width * 0.85)],
            )

        if frightened:
            # Classic "scared" face: a wavy white mouth, no pupils.
            mouth_y = cy - 2
            mouth_points = []
            segments = 6
            start_x = cx - radius * 0.7
            step = (radius * 1.4) / segments
            for i in range(segments + 1):
                mouth_points.append((start_x + i * step, mouth_y + (4 if i % 2 == 0 else -2)))
            pygame.draw.lines(self.screen, WHITE, False, mouth_points, 2)
            for offset in (-radius * 0.6, radius * 0.6):
                pygame.draw.circle(self.screen, WHITE, (cx + offset, dome_cy + 1), max(2, radius * 0.3))
        else:
            dx, dy = direction
            eye_radius = max(2, radius * 0.4)
            pupil_radius = max(1, radius * 0.2)
            for offset in (-radius * 0.6, radius * 0.6):
                eye = (cx + offset, dome_cy + 1)
                pygame.draw.circle(self.screen, WHITE, eye, eye_radius)
                pupil = (eye[0] + dx * eye_radius * 0.5, eye[1] + dy * eye_radius * 0.5)
                pygame.draw.circle(self.screen, BLACK, pupil, pupil_radius)

    def draw_clawd(self, center, now, frightened):
        """Clyde's reskin: a pixel-art crab styled after Claude Code's
        mascot, Claw'd - a clay-orange shell with a white outline and black
        eyes. Its legs alternate every CLAWD_WALK_FRAME_MS to give it a
        scuttling walk as it moves through the maze. Purely cosmetic -
        Clyde's chase-then-shy-away AI is unchanged."""
        cx, cy = center
        body_color = FRIGHTENED_BLUE if frightened else CLAWD_BODY_COLOR
        eye_color = WHITE if frightened else BLACK

        frame = (now // CLAWD_WALK_FRAME_MS) % 2
        shell_cy = cy + (-1 if frame == 0 else 1)

        shell_w, shell_h = 17, 10
        outline_rect = pygame.Rect(0, 0, shell_w + 3, shell_h + 3)
        outline_rect.center = (cx, shell_cy)
        pygame.draw.ellipse(self.screen, WHITE, outline_rect)

        shell_rect = pygame.Rect(0, 0, shell_w, shell_h)
        shell_rect.center = (cx, shell_cy)
        pygame.draw.ellipse(self.screen, body_color, shell_rect)

        # Claws, one on each side of the shell.
        for side in (-1, 1):
            claw = (cx + side * (shell_w // 2 + 1), shell_cy)
            pygame.draw.circle(self.screen, body_color, claw, 4)
            pygame.draw.circle(self.screen, WHITE, claw, 4, 1)

        if frightened:
            mouth = [(cx - 4, shell_cy + 1), (cx - 1, shell_cy - 1), (cx + 2, shell_cy + 1)]
            pygame.draw.lines(self.screen, WHITE, False, mouth, 2)
        else:
            for offset in (-4, 4):
                pygame.draw.circle(self.screen, eye_color, (cx + offset, shell_cy - 2), 2)

        # Little scuttling legs: three per side, alternating tilt every
        # frame so adjacent legs appear to move in a walking wave.
        leg_y = shell_rect.bottom - 2
        for index, lx in enumerate((-6, -1, 4)):
            tilt = 4 if (index + frame) % 2 == 0 else 1
            end_dx = -2 if lx < -2 else 2
            pygame.draw.line(self.screen, body_color, (cx + lx, leg_y), (cx + lx + end_dx, leg_y + tilt), 2)

    def draw_outcome_icon(self, icon, center, accent):
        """The glyph shown above the title on the game-over screen."""
        radius = 22
        if icon == "pacman":
            self.draw_pacman_icon(center, radius, accent)
        elif icon == "ghost":
            self.draw_ghost_icon(center, radius, accent, (0, 1), False)
        elif icon == "clock":
            cx, cy = center
            pygame.draw.circle(self.screen, accent, center, radius, 3)
            pygame.draw.line(self.screen, WHITE, center, (cx, cy - radius * 0.6), 3)
            pygame.draw.line(self.screen, WHITE, center, (cx + radius * 0.4, cy), 3)
            pygame.draw.circle(self.screen, accent, center, 3)

    def draw(self, now):
        self.screen.fill(BLACK)
        for y, row in enumerate(MAZE):
            for x, char in enumerate(row):
                self.draw_tile(x, y, char)

        frightened = now < self.power_until
        ghost_radius = CELL_SIZE // 2 - 3
        pacman_radius = CELL_SIZE // 2 - 4

        ghost_delay = GHOST_FRIGHTENED_MOVE_DELAY if frightened else GHOST_MOVE_DELAY
        ghost_center, _ = animated_position(
            self.ghost_prev_position, self.ghost_position, self.ghost_last_move, ghost_delay, now
        )
        ghost_color = FRIGHTENED_BLUE if frightened else PLAYER_GHOST_COLOR
        self.draw_ghost_icon(ghost_center, ghost_radius, ghost_color, self.ghost_direction, frightened)

        # Draw the four AI-controlled allied ghosts, turning blue and
        # fleeing while Pac-Man's power pellet is active. Clyde's slot is
        # reskinned to a walking crab (see AI_GHOST_SKINS). All of them
        # glide smoothly between tiles instead of snapping.
        ai_delay = AI_GHOST_FRIGHTENED_MOVE_DELAY if frightened else AI_GHOST_MOVE_DELAY
        for index, (position, prev_position, color, direction) in enumerate(zip(
            self.ai_ghost_positions, self.ai_ghost_prev_positions, AI_GHOST_COLORS, self.ai_ghost_directions
        )):
            center, _ = animated_position(prev_position, position, self.ai_ghost_last_moves[index], ai_delay, now)
            if AI_GHOST_SKINS[index] == "clawd":
                self.draw_clawd(center, now, frightened)
            else:
                self.draw_ghost_icon(center, ghost_radius, FRIGHTENED_BLUE if frightened else color, direction, frightened)

        # Pac-Man glides between tiles too, and chomps once per tile: mouth
        # shut at each tile center, wide open at the midpoint of the move.
        pacman_center, pacman_t = animated_position(
            self.pacman_prev_position, self.pacman_position, self.pacman_last_move, PACMAN_MOVE_DELAY, now
        )
        mouth_fraction = math.sin(pacman_t * math.pi)
        self.draw_pacman_icon(pacman_center, pacman_radius, YELLOW, self.pacman_direction, mouth_fraction)

        hud_y = GRID_H * CELL_SIZE
        pygame.draw.rect(self.screen, HUD, (0, hud_y, SCREEN_WIDTH, HUD_HEIGHT))
        pellets_left = len(self.pellets) + len(self.power_pellets)
        left_surface = self.font.render(f"Pellets left: {pellets_left}", True, WHITE)
        self.screen.blit(left_surface, (10, hud_y + 8))
        if self.start_time is None:
            clock_ms = 0
        elif self.finished_game_meta is not None:
            clock_ms = self.finished_game_meta["elapsed_ms"]
        else:
            clock_ms = now - self.start_time
        detail = f"{clock_ms // 60000}:{clock_ms // 1000 % 60:02d}"
        if self.ghosts_eaten:
            detail += f"   Eaten: {self.ghosts_eaten}"
        detail_surface = self.small_font.render(detail, True, GHOST_BLUE)
        self.screen.blit(detail_surface, (left_surface.get_width() + 24, hud_y + 15))

        if self.start_time is None:
            status = "Press ENTER or a movement key to start"
        elif self.game_over:
            status = self.result_message
        elif now < self.eat_flash_until:
            status = "PAC-MAN ATE A GHOST!"
        elif now < self.power_until:
            status = f"POWER-UP! Ghosts fleeing: {(self.power_until - now) / 1000:.1f}s"
        elif now - self.start_time < GHOST_HEAD_START:
            remaining = (GHOST_HEAD_START - (now - self.start_time)) / 1000
            status = f"Pac-Man head start: {remaining:.1f}s"
        elif (now - self.start_time) < max(self.ai_release_times, default=0):
            elapsed_now = now - self.start_time
            pending = next(t for t in self.ai_release_times if elapsed_now < t)
            released = sum(1 for t in self.ai_release_times if elapsed_now >= t)
            remaining = (pending - elapsed_now) / 1000
            status = f"Ally ghost {released + 1}/{len(self.ai_release_times)} leaving the house in {remaining:.1f}s"
        else:
            status = "Catch Pac-Man before he clears the maze!"

        status_surface = self.small_font.render(status, True, WHITE)
        self.screen.blit(
            status_surface,
            (self.mute_button_rect().left - status_surface.get_width() - 10, hud_y + 15),
        )
        self.draw_mute_button()

        if self.game_over:
            style = GAME_OVER_STYLES[self.outcome]
            accent = style["accent"]
            center_x = SCREEN_WIDTH // 2

            overlay = pygame.Surface((SCREEN_WIDTH, GRID_H * CELL_SIZE), pygame.SRCALPHA)
            overlay.fill(style["tint"])
            self.screen.blit(overlay, (0, 0))

            # Every line is rendered first and the panel is then sized to
            # hold all of them, so no text can spill out of it whatever its
            # wording (the title and the "brain" line were both wider than
            # the fixed 440-pixel panel this used to be). Each entry is the
            # rendered line and the gap left above it.
            lines = [
                (self.font.render(style["title"], True, accent), 0),
                (self.small_font.render(style["subtitle"], True, WHITE), 10),
                (self.small_font.render("Press R to restart   Press S to save this replay", True, WHITE), 18),
            ]
            if self.save_status_message and pygame.time.get_ticks() < self.save_status_until:
                lines.append((self.small_font.render(self.save_status_message, True, YELLOW), 10))
            lines.append((
                self.small_font.render(
                    f"Pac-Man's brain: {self.brain.games_played} games learned from "
                    f"(avg outcome {self.brain.average_reward:+.2f})",
                    True,
                    GHOST_BLUE,
                ),
                12,
            ))
            pad_x, pad_top, icon_space, pad_bottom = 28, 14, 78, 22
            widest_allowed = SCREEN_WIDTH - 24 - 2 * pad_x
            fitted = []
            for surface, gap in lines:
                if surface.get_width() > widest_allowed:
                    # Wider than the window itself: shrink it to fit.
                    height = max(1, surface.get_height() * widest_allowed // surface.get_width())
                    surface = pygame.transform.smoothscale(surface, (widest_allowed, height))
                fitted.append((surface, gap))

            # A solid, rounded panel behind the text keeps it crisp even
            # where the (now centrally-housed) ghosts sit right behind it.
            panel_rect = pygame.Rect(
                0, 0,
                max(440, max(surface.get_width() for surface, _gap in fitted) + 2 * pad_x),
                pad_top + icon_space + sum(surface.get_height() + gap for surface, gap in fitted) + pad_bottom,
            )
            panel_rect.center = (center_x, GRID_H * CELL_SIZE // 2 - 10)
            panel = pygame.Surface(panel_rect.size, pygame.SRCALPHA)
            panel.fill((10, 10, 18, 215))
            self.screen.blit(panel, panel_rect)
            pygame.draw.rect(self.screen, accent, panel_rect, 2, border_radius=14)

            icon_center = (center_x, panel_rect.top + pad_top + 28)
            self.draw_outcome_icon(style["icon"], icon_center, accent)

            line_top = panel_rect.top + pad_top + icon_space
            for surface, gap in fitted:
                line_top += gap
                self.screen.blit(surface, (center_x - surface.get_width() // 2, line_top))
                line_top += surface.get_height()

        pygame.display.flip()

    def run(self):
        running = True
        self._plan_live = True
        self._plan_worker = start_plan_worker()
        key_directions = {
            pygame.K_UP: (0, -1), pygame.K_w: (0, -1),
            pygame.K_DOWN: (0, 1), pygame.K_s: (0, 1),
            pygame.K_LEFT: (-1, 0), pygame.K_a: (-1, 0),
            pygame.K_RIGHT: (1, 0), pygame.K_d: (1, 0),
        }

        while running:
            now = pygame.time.get_ticks()
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    if self.mute_button_rect().collidepoint(event.pos):
                        self.toggle_mute()
                elif event.type == pygame.KEYDOWN:
                    if event.key in (pygame.K_ESCAPE, pygame.K_q):
                        running = False
                    elif event.key == pygame.K_RETURN:
                        self.start()
                    elif event.key == pygame.K_r:
                        self.reset_game()
                    elif event.key == pygame.K_m:
                        self.toggle_mute()
                    elif event.key == pygame.K_s and self.game_over:
                        if self.replay_saved_by_player:
                            self.save_status_message = "Already saved this replay."
                        elif self.save_current_replay():
                            self.save_status_message = "Saved! Pinned in the replay browser."
                        self.save_status_until = now + 3000
                    elif event.key in key_directions and not self.game_over:
                        self.set_direction(key_directions[event.key])

            self.update(now)
            self.update_power_sound(now)
            self.draw(now)
            self.clock.tick(FPS)

        if self._plan_worker is not None:
            self._plan_worker.close()
        pygame.quit()


if __name__ == "__main__":
    try:
        game = Game()
        if os.environ.get("PACMAN_AUTOPLAY_GHOST") == "1":
            # Spectator mode: watch the fitted imitation model drive the
            # player's ghost instead of needing real key presses (see
            # fit_player_ghost.py). Still press Enter to start.
            try:
                with open(PLAYER_GHOST_WEIGHTS_FILE) as handle:
                    game.ghost_autoplay_weights = json.load(handle)
                print(f"Watching {PLAYER_GHOST_WEIGHTS_FILE} drive the player's ghost - press Enter to start.")
            except (OSError, ValueError) as exc:
                print(f"Could not load {PLAYER_GHOST_WEIGHTS_FILE}: {exc}", file=sys.stderr)
        game.run()
    except Exception as exc:
        pygame.quit()
        print(f"Game error: {exc}", file=sys.stderr)
        raise
