#!/usr/bin/env python3
"""Pac-Man Replay Browser: list and play back saved wins/near-wins from
Ghost_Pacman.py.

A "near win" is a game Pac-Man lost having almost cleared the maze (see
NEAR_WIN_PELLET_FRACTION in Ghost_Pacman.py). Replays saved before the score
and clock were removed may also show the old clock endings.

Controls:
  List screen : Up/Down select (hold to scroll fast), Enter play,
                click a column header to sort by it (click again to
                flip direction), X pin, N rename, E export to MP4,
                S statistics, Del delete, Q/Esc quit
  Playback    : Space pause/resume, Left/Right scrub 2s, R restart,
                Esc back to the list
  Statistics  : Esc back to the list, Q quit

Requires: pygame (python -m pip install pygame), and ffmpeg on PATH for
MP4 export. Run from the same folder as Ghost_Pacman.py (reuses its maze
data, colors, and drawing code so a replay always looks exactly like the
live game).
"""

import datetime
import json
import math
import os
import shutil
import subprocess
import sys

import pygame

import Ghost_Pacman as gp

# GAME_OVER_STYLES titles are full sentences meant for the game-over
# overlay; the list table needs something short enough to fit its column.
SHORT_OUTCOME_LABELS = {
    "ghost_win": "Caught by ghosts",
    "maze_cleared": "Cleared the maze",
    "stalemate": "Stalemate",
    "survived": "Survived the clock",
    "timeout": "Ran the clock out",
}

# Colorblind-safe win/loss pair (validated earlier this session with the
# dataviz skill's palette checker: CVD/normal-vision separation both pass).
WIN_COLOR = (47, 111, 237)
LOSS_COLOR = (224, 123, 26)
AXIS_COLOR = (90, 90, 100)
MUTED_COLOR = (150, 150, 160)
PIN_COLOR = (230, 190, 60)


def _write_replay_file(path, data):
    """Rewrite a replay's JSON after a local change (pin toggle or
    rename) - the browser is the only thing that ever edits these fields,
    so there's no concurrent-writer risk to guard against."""
    try:
        with open(path, "w") as handle:
            json.dump(data, handle)
        return True
    except OSError:
        return False


_history_cache_records = []
_history_cache_offset = 0


def load_history():
    """Every completed game (win and loss), from Ghost_Pacman's history
    log, in play order. Unlike the saved replays (wins/near-wins only),
    this is every game ever finished, which is what the stats need for
    accurate win/loss totals.

    Caches what's already been parsed and only reads whatever's been
    appended since the last call (HISTORY_FILE is append-only by design -
    see its own comment) - re-parsing the whole file from scratch every
    time S was pressed got slower every call as the history grew into
    the hundreds of thousands of games; this keeps repeat opens instant
    regardless of total file size. Only consumes whole lines out of
    whatever it reads, so a read that lands mid-write (training running
    in the background while the browser's open) just leaves the trailing
    partial line for the next call to pick up once it's complete, rather
    than parsing a torn fragment and losing that game's record for good."""
    global _history_cache_records, _history_cache_offset
    if not os.path.isfile(gp.HISTORY_FILE):
        _history_cache_records = []
        _history_cache_offset = 0
        return list(_history_cache_records)

    try:
        size = os.path.getsize(gp.HISTORY_FILE)
        if size < _history_cache_offset:
            # File got smaller (replaced/truncated) - the cache can't be
            # trusted, start over.
            _history_cache_records = []
            _history_cache_offset = 0
        with open(gp.HISTORY_FILE, "rb") as handle:
            handle.seek(_history_cache_offset)
            chunk = handle.read()
    except OSError:
        return list(_history_cache_records)

    consumed = 0
    for raw_line in chunk.splitlines(keepends=True):
        if not raw_line.endswith(b"\n"):
            break  # incomplete trailing line - wait for it to finish
        consumed += len(raw_line)
        line = raw_line.decode("utf-8", "replace").strip()
        if not line:
            continue
        try:
            _history_cache_records.append(json.loads(line))
        except ValueError:
            continue
    _history_cache_offset += consumed

    # A fresh list each call (not the live cache) - same contract as the
    # old implementation, so a caller holding an earlier result never
    # sees it silently grow out from under them.
    return list(_history_cache_records)


# Cycled by T on the stats screen (see handle_stats_input/draw_stats):
# (scope key, display label, how many of the most recent history_records
# to use - None means every game ever recorded).
STATS_SCOPES = [
    ("all", "All games", None),
    ("50k", "Last 50,000 games", 50_000),
    ("10k", "Last 10,000 games", 10_000),
]

# Cycled by V on the stats screen, independent of STATS_SCOPES above - which
# series the line graph plots over the same windowed games (see
# draw_stats): (mode key, chart title, y-axis caption).
GRAPH_MODES = [
    ("cumulative", "Wins Over Time", "Cumulative wins"),
    ("deviation", "Wins vs Average", "Deviation from average pace"),
]


def draw_pie_chart(screen, center, radius, segments, font=None):
    """segments: [(value, color), ...]. Slices are drawn clockwise from
    the top, sized proportionally to each value's share of the total. If
    font is given, each slice's percentage is labeled - in that slice's
    own color, for an unambiguous match - just outside the circle at the
    slice's midpoint angle."""
    total = sum(value for value, _ in segments)
    if total <= 0:
        pygame.draw.circle(screen, AXIS_COLOR, center, radius, 2)
        return
    start_angle = -90.0
    for value, color in segments:
        if value <= 0:
            continue
        sweep = 360.0 * value / total
        steps = max(2, int(sweep / 3) + 1)
        points = [center]
        for i in range(steps + 1):
            angle = math.radians(start_angle + sweep * i / steps)
            points.append((
                center[0] + radius * math.cos(angle),
                center[1] + radius * math.sin(angle),
            ))
        pygame.draw.polygon(screen, color, points)

        if font is not None:
            mid_angle = math.radians(start_angle + sweep / 2)
            label_r = radius + 18
            label_x = center[0] + label_r * math.cos(mid_angle)
            label_y = center[1] + label_r * math.sin(mid_angle)
            label_surf = font.render(f"{100.0 * value / total:.1f}%", True, color)
            screen.blit(label_surf, (label_x - label_surf.get_width() // 2, label_y - label_surf.get_height() // 2))

        start_angle += sweep


def draw_line_graph(screen, rect, points, color, small_font, ticks=5):
    """points: [(x, y), ...]. Neither axis needs to start at 0 - x scales
    to the data's own current min/max so the plot keeps rescaling
    correctly as more attempts/wins happen, or as a window shifts,
    without squeezing the whole line into a sliver at one edge; y does
    too, so a series that dips negative (e.g. deviation-from-average -
    see GRAPH_MODES) still spans the chart properly instead of getting
    clipped or flattened against the bottom. `ticks` evenly-spaced
    notches are drawn on each axis, with a faint gridline per notch so
    in-between values are easy to read off. If the data actually
    straddles y=0, that gridline is drawn brighter than the rest as an
    explicit zero-reference line. Labels are abbreviated (e.g. 12.3k)
    once they'd otherwise run past 4 digits, so a wide range doesn't
    crowd its own tick marks."""
    if len(points) < 2:
        pygame.draw.line(screen, AXIS_COLOR, rect.bottomleft, rect.bottomright, 1)
        pygame.draw.line(screen, AXIS_COLOR, rect.bottomleft, rect.topleft, 1)
        return

    x_min = points[0][0]
    x_max = max(x_min + 1, points[-1][0])
    x_span = x_max - x_min
    y_values = [y for _, y in points]
    y_min = min(0, min(y_values))
    y_max = max(1, max(y_values))
    y_span = y_max - y_min
    grid_color = (45, 45, 55)
    zero_line_color = (90, 90, 105)

    def to_px(x, y):
        return (
            rect.left + ((x - x_min) / x_span) * rect.width,
            rect.bottom - ((y - y_min) / y_span) * rect.height,
        )

    def format_tick(value):
        value = round(value)
        if value >= 10_000 or value <= -10_000:
            return f"{value / 1000:.1f}k"
        return str(value)

    for i in range(ticks + 1):
        frac = i / ticks

        px = rect.left + frac * rect.width
        pygame.draw.line(screen, grid_color, (px, rect.top), (px, rect.bottom), 1)
        pygame.draw.line(screen, AXIS_COLOR, (px, rect.bottom), (px, rect.bottom + 4), 1)
        x_label = small_font.render(format_tick(x_min + frac * x_span), True, MUTED_COLOR)
        screen.blit(x_label, (px - x_label.get_width() // 2, rect.bottom + 6))

        py = rect.bottom - frac * rect.height
        pygame.draw.line(screen, grid_color, (rect.left, py), (rect.right, py), 1)
        pygame.draw.line(screen, AXIS_COLOR, (rect.left - 4, py), (rect.left, py), 1)
        y_label = small_font.render(format_tick(y_min + frac * y_span), True, MUTED_COLOR)
        screen.blit(y_label, (rect.left - y_label.get_width() - 6, py - y_label.get_height() // 2))

    if y_min < 0 < y_max:
        _, zero_py = to_px(x_min, 0)
        pygame.draw.line(screen, zero_line_color, (rect.left, zero_py), (rect.right, zero_py), 2)

    pygame.draw.line(screen, AXIS_COLOR, rect.bottomleft, rect.bottomright, 1)
    pygame.draw.line(screen, AXIS_COLOR, rect.bottomleft, rect.topleft, 1)
    pygame.draw.lines(screen, color, False, [to_px(x, y) for x, y in points], 2)


class ReplayState:
    """Stands in for a Game instance just well enough to call Game's
    drawing methods (draw_tile, draw_ghost_icon, draw_pacman_icon,
    draw_clawd) as unbound methods against this object - avoids duplicating
    any rendering code, so a replay always matches the live game exactly."""

    def __init__(self, screen):
        self.screen = screen
        self.pellets = set()
        self.power_pellets = set()


def list_replays():
    """All saved replays, newest first, as (path, parsed_json) pairs."""
    if not os.path.isdir(gp.REPLAY_DIR):
        return []
    entries = []
    for name in os.listdir(gp.REPLAY_DIR):
        if not name.endswith(".json"):
            continue
        path = os.path.join(gp.REPLAY_DIR, name)
        try:
            with open(path) as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            continue
        entries.append((path, data))
    entries.sort(key=lambda entry: entry[1]["meta"]["timestamp"], reverse=True)
    return entries


def find_frame_index(frames, t):
    """Index of the last frame with frame["t"] <= t."""
    lo, hi = 0, len(frames) - 1
    result = 0
    while lo <= hi:
        mid = (lo + hi) // 2
        if frames[mid]["t"] <= t:
            result = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return result


def reconstruct_pellets(meta, pellet_events, power_pellet_events, t):
    eaten = {tuple(event["pos"]) for event in pellet_events if event["t"] <= t}
    eaten_power = {tuple(event["pos"]) for event in power_pellet_events if event["t"] <= t}
    pellets = set(map(tuple, meta["initial_pellets"])) - eaten
    power_pellets = set(map(tuple, meta["initial_power_pellets"])) - eaten_power
    return pellets, power_pellets


def last_change(frames, idx, value_fn):
    """Walk backward from idx to the frame where value_fn(frame) actually
    last changed, returning (prev_value, value, move_time).

    A recorded frame is a full snapshot of every character's position, but
    it only gets appended when SOMETHING changes - which might be a
    different character entirely. Naively using frames[idx]/frames[idx-1]
    for every character borrows whichever frame happened to be recorded
    most recently, which gives each character the wrong "move started at"
    timestamp (and wrong interpolation fraction) whenever it wasn't the
    character that triggered that particular frame - most visibly, Pac-Man's
    mouth kept chomping even while he was standing still. This walks back to
    the frame that was actually this character's own last move."""
    current = value_fn(frames[idx])
    k = idx
    while k > 0 and value_fn(frames[k - 1]) == current:
        k -= 1
    move_time = frames[k]["t"]
    prev = value_fn(frames[k - 1]) if k > 0 else current
    return prev, current, move_time


def entity_positions(frames, idx):
    """(prev, current, move_time) for every character, each using its own
    actual last-move frame (see last_change), ready for
    Ghost_Pacman.animated_position() to glide between them."""
    def track(pos_key, dir_key):
        prev, cur, move_time = last_change(frames, idx, lambda f: tuple(f[pos_key]))
        _, direction, _ = last_change(frames, idx, lambda f: tuple(f[dir_key]))
        return prev, cur, direction, move_time

    pacman = track("pacman", "pacman_dir")
    ghost = track("ghost", "ghost_dir")
    ai = []
    for i in range(len(frames[idx]["ai"])):
        prev, cur, move_time = last_change(frames, idx, lambda f, i=i: tuple(f["ai"][i]))
        _, direction, _ = last_change(frames, idx, lambda f, i=i: tuple(f["ai_dir"][i]))
        ai.append((prev, cur, direction, move_time))
    return pacman, ghost, ai


class ReplayBrowser:
    def __init__(self):
        pygame.init()
        self.screen = pygame.display.set_mode((gp.SCREEN_WIDTH, gp.SCREEN_HEIGHT))
        pygame.display.set_caption("Pac-Man Replay Browser")
        self.clock = pygame.time.Clock()
        self.font = pygame.font.SysFont(None, 34)
        self.small_font = pygame.font.SysFont(None, 23)
        self.state = ReplayState(self.screen)
        self.entries = list_replays()
        self.selected = 0
        self.scroll_offset = 0
        self.mode = "list"  # "list", "playback", or "stats"
        self.playback_t = 0.0
        self.playback_speed = 1.0
        self.paused = False
        self.status_message = ""
        self.status_until = 0
        self.history_records = []
        # Which slice of history_records both the wins/losses pie chart
        # and the line graph (see draw_stats) summarize - cycled together
        # with T on the stats screen.
        self.stats_scope = "all"
        # Which series the line graph itself plots - cycled with V,
        # independent of the timeframe above and only affecting the line
        # graph (see GRAPH_MODES/draw_stats).
        self.graph_mode = "cumulative"
        self.rank_by_path = {}
        self.sort_column = "rank"
        self.sort_ascending = True
        self._held_key = None
        self._held_since = 0
        self._next_repeat = 0
        self.naming = False
        self.name_input = ""
        self._refresh_ranking_and_sort()

    # -- list screen ---------------------------------------------------
    # Fixed column (id, label, x, width, sort key fn, default-ascending),
    # shared by the header row and every entry row, so everything lines up
    # like a table and every header can be clicked to sort by it. RANK
    # sorts by the precomputed composite rank (see gp.replay_rank_key);
    # every other column sorts by its own displayed value.
    COLUMNS = [
        ("rank", "RANK", 20, 75, lambda meta, rank: rank, True),
        ("result", "RESULT", 95, 92, lambda meta, rank: 0 if meta["won"] else 1, True),
        ("outcome", "OUTCOME", 187, 147,
         lambda meta, rank: SHORT_OUTCOME_LABELS.get(meta["outcome"], meta["outcome"]), True),
        ("pellets", "PELLETS", 334, 96, lambda meta, rank: meta["pellet_fraction"], False),
        ("time", "TIME", 430, 70, lambda meta, rank: meta["elapsed_ms"], True),
        ("run", "RUN", 500, 73, lambda meta, rank: meta.get("run") or 0, True),
        ("date", "DATE", 573, 140, lambda meta, rank: meta["timestamp"], False),
    ]

    HEADER_Y = 64
    LIST_TOP = HEADER_Y + 32
    LIST_ROW_H = 34
    LIST_BOTTOM_MARGIN = 124  # room for the naming/status row, the selected clip's name, and a 2-line key hint
    INITIAL_REPEAT_DELAY = 350  # ms a key must be held before auto-repeat starts

    def _visible_rows(self):
        available = gp.SCREEN_HEIGHT - self.LIST_TOP - self.LIST_BOTTOM_MARGIN
        return max(1, available // self.LIST_ROW_H)

    def _clamp_scroll(self):
        """Keep the selected row inside the visible window, and the window
        inside the entry list, so Up/Down never selects something that has
        scrolled off-screen."""
        if not self.entries:
            self.scroll_offset = 0
            return
        self.selected = max(0, min(self.selected, len(self.entries) - 1))
        visible = self._visible_rows()
        max_offset = max(0, len(self.entries) - visible)
        if self.selected < self.scroll_offset:
            self.scroll_offset = self.selected
        elif self.selected >= self.scroll_offset + visible:
            self.scroll_offset = self.selected - visible + 1
        self.scroll_offset = max(0, min(self.scroll_offset, max_offset))

    def _refresh_ranking_and_sort(self):
        """Recompute each entry's composite rank (independent of whatever
        column the table is currently sorted by), then re-sort for
        display according to the active sort column/direction."""
        order = sorted(range(len(self.entries)), key=lambda i: gp.replay_rank_key(self.entries[i][1]["meta"]))
        self.rank_by_path = {}
        for rank, i in enumerate(order, start=1):
            self.rank_by_path[self.entries[i][0]] = rank
        self._apply_sort()

    def _apply_sort(self):
        key_fn = next(c[4] for c in self.COLUMNS if c[0] == self.sort_column)

        def sort_key(entry):
            path, data = entry
            return key_fn(data["meta"], self.rank_by_path.get(path, 0))

        self.entries.sort(key=sort_key, reverse=not self.sort_ascending)
        self._clamp_scroll()

    def _header_rects(self):
        return [
            (col_id, pygame.Rect(x - 4, self.HEADER_Y - 4, width, 24))
            for col_id, _label, x, width, _key_fn, _default_asc in self.COLUMNS
        ]

    def _draw_sort_arrow(self, x, y, pointing_up, color):
        if pointing_up:
            points = [(x, y + 3), (x + 7, y + 3), (x + 3, y - 3)]
        else:
            points = [(x, y - 3), (x + 7, y - 3), (x + 3, y + 3)]
        pygame.draw.polygon(self.screen, color, points)

    def _draw_scrollbar(self, visible_rows):
        total = len(self.entries)
        if total <= visible_rows:
            return
        track_x = gp.SCREEN_WIDTH - 14
        track_top = self.LIST_TOP - 4
        track_height = visible_rows * self.LIST_ROW_H
        pygame.draw.rect(self.screen, (50, 50, 60), (track_x, track_top, 6, track_height), border_radius=3)
        thumb_height = max(16, int(track_height * visible_rows / total))
        max_scroll = total - visible_rows
        thumb_travel = track_height - thumb_height
        thumb_y = track_top + (int(thumb_travel * self.scroll_offset / max_scroll) if max_scroll else 0)
        pygame.draw.rect(self.screen, (130, 130, 145), (track_x, thumb_y, 6, thumb_height), border_radius=3)

    def draw_list(self):
        self.screen.fill(gp.BLACK)
        title = self.font.render("Pac-Man Replays - Wins & Near Wins", True, gp.WHITE)
        self.screen.blit(title, title.get_rect(centerx=gp.SCREEN_WIDTH // 2, y=20))

        header_color = (150, 150, 160)
        for col_id, label, x, _width, _key_fn, _default_asc in self.COLUMNS:
            text = self.small_font.render(label, True, header_color)
            self.screen.blit(text, (x, self.HEADER_Y))
            arrow_x = x + text.get_width() + 8
            center_y = self.HEADER_Y + 8
            if col_id == self.sort_column:
                self._draw_sort_arrow(arrow_x, center_y, self.sort_ascending, gp.WHITE)
            else:
                self._draw_sort_arrow(arrow_x, center_y - 4, True, (70, 70, 80))
                self._draw_sort_arrow(arrow_x, center_y + 4, False, (70, 70, 80))
        pygame.draw.line(
            self.screen, (70, 70, 80), (20, self.HEADER_Y + 22), (gp.SCREEN_WIDTH - 20, self.HEADER_Y + 22), 1
        )

        if not self.entries:
            empty = self.small_font.render(
                "No replays saved yet - Pacman must win or almost win.",
                True, gp.WHITE,
            )
            self.screen.blit(empty, (20, self.LIST_TOP + 4))
        else:
            visible = self._visible_rows()
            visible_entries = self.entries[self.scroll_offset:self.scroll_offset + visible]
            y = self.LIST_TOP
            for offset, (path, data) in enumerate(visible_entries):
                i = self.scroll_offset + offset
                meta = data["meta"]
                style = gp.GAME_OVER_STYLES[meta["outcome"]]
                tag = "WIN" if meta["won"] else ("NEAR WIN" if meta["near_win"] else "")
                has_recording = bool(data["frames"])
                if not has_recording:
                    color = MUTED_COLOR
                else:
                    color = style["accent"] if i == self.selected else gp.WHITE
                if i == self.selected:
                    highlight = pygame.Rect(10, y - 4, gp.SCREEN_WIDTH - 20, self.LIST_ROW_H)
                    pygame.draw.rect(self.screen, gp.HUD, highlight, border_radius=6)

                outcome_label = SHORT_OUTCOME_LABELS.get(meta["outcome"], style["title"])
                if not has_recording:
                    outcome_label += " (no recording)"
                date_field = time_label(meta["timestamp"])
                run_field = f"Run {meta['run']}" if meta.get("run") else "-"
                fields = [
                    str(self.rank_by_path.get(path, "")),
                    tag,
                    outcome_label,
                    f"{meta['pellet_fraction']*100:.1f}%",
                    f"{meta['elapsed_ms']/1000:.1f}s",
                    run_field,
                    date_field,
                ]
                for field, (col_id, _label, x, width, _key_fn, _default_asc) in zip(fields, self.COLUMNS):
                    if col_id == "date":
                        field = self._truncate(field, width - 8)
                    text = self.small_font.render(field, True, color)
                    self.screen.blit(text, (x, y))

                if meta.get("pinned"):
                    rank_x = self.COLUMNS[0][2]
                    pygame.draw.circle(self.screen, PIN_COLOR, (rank_x + 46, y + 8), 5)

                y += self.LIST_ROW_H

            self._draw_scrollbar(visible)

        top_row_y = gp.SCREEN_HEIGHT - 118
        if self.naming:
            box_rect = pygame.Rect(20, top_row_y, gp.SCREEN_WIDTH - 40, 28)
            pygame.draw.rect(self.screen, (40, 40, 50), box_rect, border_radius=6)
            pygame.draw.rect(self.screen, gp.WHITE, box_rect, 1, border_radius=6)
            prompt = self.small_font.render(f"Name: {self.name_input}|", True, gp.WHITE)
            self.screen.blit(prompt, (box_rect.x + 8, box_rect.y + 5))
        elif self.status_message and pygame.time.get_ticks() < self.status_until:
            status = self.small_font.render(self.status_message, True, gp.YELLOW)
            self.screen.blit(status, (20, top_row_y + 5))
        elif self.entries:
            # The DATE column always shows the real date now, so a custom
            # name (if the selected clip has one) gets its own permanent
            # spot here instead of overwriting it.
            selected_name = self.entries[self.selected][1]["meta"].get("name", "")
            if selected_name:
                name_text = self.small_font.render(f"Name: {selected_name}", True, (190, 190, 200))
                self.screen.blit(name_text, (20, top_row_y + 5))

        if self.naming:
            hint_rows = [["Enter save", "Esc cancel"]]
        else:
            hint_rows = [
                ["Up/Down hold=fast", "Enter play", "Click header to sort"],
                ["X pin", "N rename", "E export MP4", "S stats", "Del delete", "Q/Esc quit"],
            ]
        hint_top = gp.SCREEN_HEIGHT - 58
        for row_i, segments in enumerate(hint_rows):
            slot_width = (gp.SCREEN_WIDTH - 40) / len(segments)
            y = hint_top + row_i * 28
            for seg_i, segment in enumerate(segments):
                text = self.small_font.render(segment, True, gp.WHITE)
                self.screen.blit(text, (20 + seg_i * slot_width, y))
        pygame.display.flip()

    def _truncate(self, text, max_width):
        if self.small_font.size(text)[0] <= max_width:
            return text
        while text and self.small_font.size(text + "...")[0] > max_width:
            text = text[:-1]
        return text + "..." if text else text

    def move_selection(self, delta):
        if not self.entries:
            return
        self.selected = (self.selected + delta) % len(self.entries)
        self._clamp_scroll()

    def _repeat_interval(self, held_ms):
        """How long to wait before the next auto-repeat move: starts slow
        (~160ms/row) and accelerates to fast (~25ms/row) by ~1.5s held, so
        holding the arrow key scrolls progressively faster down the list."""
        ramp = min(1.0, held_ms / 1500.0)
        return max(25, int(160 - ramp * 135))

    def update_key_repeat(self, now):
        if self._held_key is None or not self.entries:
            return
        if now >= self._next_repeat:
            delta = -1 if self._held_key == pygame.K_UP else 1
            self.move_selection(delta)
            self._next_repeat = now + self._repeat_interval(now - self._held_since)

    def handle_list_input(self, event):
        if event.key in (pygame.K_ESCAPE, pygame.K_q):
            return False
        if event.key == pygame.K_UP and self.entries:
            self.move_selection(-1)
            self._held_key = event.key
            self._held_since = pygame.time.get_ticks()
            self._next_repeat = self._held_since + self.INITIAL_REPEAT_DELAY
        elif event.key == pygame.K_DOWN and self.entries:
            self.move_selection(1)
            self._held_key = event.key
            self._held_since = pygame.time.get_ticks()
            self._next_repeat = self._held_since + self.INITIAL_REPEAT_DELAY
        elif event.key == pygame.K_RETURN and self.entries:
            if self.entries[self.selected][1]["frames"]:
                self.mode = "playback"
                self.playback_t = 0.0
                self.paused = False
            else:
                self.status_message = "This replay has no recorded movement - nothing to play back."
                self.status_until = pygame.time.get_ticks() + 3000
        elif event.key == pygame.K_DELETE and self.entries:
            self.delete_selected()
        elif event.key == pygame.K_x and self.entries:
            self.toggle_pin_selected()
        elif event.key == pygame.K_n and self.entries:
            self.start_naming()
        elif event.key == pygame.K_e and self.entries:
            self.export_selected_to_mp4()
        elif event.key == pygame.K_s:
            self.history_records = load_history()
            self.mode = "stats"
        return True

    def handle_list_keyup(self, event):
        if event.key == self._held_key:
            self._held_key = None

    def toggle_pin_selected(self):
        """X pins/unpins the selected clip. A pinned clip can never be
        evicted from the leaderboard by rank (see _reconcile_leaderboard
        in Ghost_Pacman.py) - it just doesn't need a bonus slot if it's
        already good enough to rank in the natural top MAX_LEADERBOARD.

        Toggling in either direction marks the clip "formerly_pinned" for
        good - once something has ever been pinned, unpinning it later
        never makes it disappear on its own (even if a training run adds
        enough better replays to push its rank past 10 by the time the
        eviction check runs). It just goes back to showing up unpinned;
        only Del actually removes it."""
        path, data = self.entries[self.selected]
        data["meta"]["pinned"] = not data["meta"].get("pinned", False)
        data["meta"]["formerly_pinned"] = True
        if not _write_replay_file(path, data):
            self.status_message = "Could not save pin status"
            self.status_until = pygame.time.get_ticks() + 2500
            return

        now_pinned = data["meta"]["pinned"]
        gp._reconcile_leaderboard()
        evicted = not os.path.isfile(path)

        self.entries = list_replays()
        if evicted:
            self.selected = min(self.selected, max(0, len(self.entries) - 1))
        else:
            for i, (p, _d) in enumerate(self.entries):
                if p == path:
                    self.selected = i
                    break
        self._refresh_ranking_and_sort()

        if now_pinned:
            self.status_message = "Pinned - can never be evicted by rank"
        elif evicted:
            self.status_message = "Unpinned and evicted (file missing)"
        else:
            self.status_message = "Unpinned - kept permanently now; Del to remove it"
        self.status_until = pygame.time.get_ticks() + 2500

    def start_naming(self):
        _path, data = self.entries[self.selected]
        self.naming = True
        self.name_input = data["meta"].get("name", "")

    def handle_naming_input(self, event):
        if event.key == pygame.K_RETURN:
            self._commit_name()
            self.naming = False
        elif event.key == pygame.K_ESCAPE:
            self.naming = False
        elif event.key == pygame.K_BACKSPACE:
            self.name_input = self.name_input[:-1]
        elif event.unicode and event.unicode.isprintable() and len(self.name_input) < 40:
            self.name_input += event.unicode

    def _commit_name(self):
        path, data = self.entries[self.selected]
        data["meta"]["name"] = self.name_input.strip()
        if _write_replay_file(path, data):
            label = data["meta"]["name"] or "(cleared)"
            self.status_message = f"Name set: {label}"
        else:
            self.status_message = "Could not save name"
        self.status_until = pygame.time.get_ticks() + 2500

    def handle_list_click(self, pos):
        for col_id, rect in self._header_rects():
            if not rect.collidepoint(pos):
                continue
            if col_id == self.sort_column:
                self.sort_ascending = not self.sort_ascending
            else:
                self.sort_column = col_id
                self.sort_ascending = next(c[5] for c in self.COLUMNS if c[0] == col_id)
            selected_path = self.entries[self.selected][0] if self.entries else None
            self._apply_sort()
            if selected_path is not None:
                for i, (path, _data) in enumerate(self.entries):
                    if path == selected_path:
                        self.selected = i
                        break
            self._clamp_scroll()
            return

    def delete_selected(self):
        path, data = self.entries[self.selected]
        title = gp.GAME_OVER_STYLES[data["meta"]["outcome"]]["title"]
        try:
            os.remove(path)
        except OSError as exc:
            self.status_message = f"Could not delete: {exc}"
            self.status_until = pygame.time.get_ticks() + 3000
            return
        del self.entries[self.selected]
        if self.selected >= len(self.entries):
            self.selected = max(0, len(self.entries) - 1)
        self._refresh_ranking_and_sort()
        self.status_message = f"Deleted: {title}"
        self.status_until = pygame.time.get_ticks() + 2000

    EXPORT_FPS = 30

    def export_selected_to_mp4(self):
        """E renders the selected replay out to an MP4 in the user's
        Downloads folder, reusing the exact same per-frame drawing as live
        playback (_draw_replay_scene) so the video matches what watching it
        in the browser looks like. Needs ffmpeg on PATH - it's fed raw RGB
        frames over stdin and does the actual encoding."""
        path, data = self.entries[self.selected]
        meta = data["meta"]
        frames = data["frames"]
        if not frames:
            self.status_message = "This replay has no recorded movement - nothing to export."
            self.status_until = pygame.time.get_ticks() + 3000
            return

        ffmpeg_path = shutil.which("ffmpeg")
        if not ffmpeg_path:
            self.status_message = "Export failed: ffmpeg not found on PATH."
            self.status_until = pygame.time.get_ticks() + 4000
            return

        downloads_dir = os.path.join(os.path.expanduser("~"), "Downloads")
        try:
            os.makedirs(downloads_dir, exist_ok=True)
        except OSError as exc:
            self.status_message = f"Export failed: {exc}"
            self.status_until = pygame.time.get_ticks() + 4000
            return

        label = meta.get("name") or SHORT_OUTCOME_LABELS.get(meta["outcome"], meta["outcome"])
        safe_label = "".join(c if c.isalnum() else "_" for c in label).strip("_") or "replay"
        out_path = os.path.join(downloads_dir, f"pacman_replay_{safe_label}_{int(meta['timestamp'])}.mp4")

        total_t = frames[-1]["t"]
        frame_interval = 1000.0 / self.EXPORT_FPS
        frame_count = max(1, int(total_t / frame_interval) + 1)

        # Let the "Exporting..." message actually hit the screen before the
        # (brief, but blocking) encode loop below takes over the window.
        self.status_message = f"Exporting {frame_count} frames to {out_path} ..."
        self.status_until = pygame.time.get_ticks() + 60_000
        self.draw_list()

        cmd = [
            ffmpeg_path, "-y", "-loglevel", "error",
            "-f", "rawvideo", "-pixel_format", "rgb24",
            "-video_size", f"{gp.SCREEN_WIDTH}x{gp.SCREEN_HEIGHT}", "-framerate", str(self.EXPORT_FPS),
            "-i", "-",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            out_path,
        ]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        ok = False
        stderr_tail = ""
        try:
            for i in range(frame_count):
                t = min(total_t, i * frame_interval)
                self._draw_replay_scene(data, t)
                self._draw_export_hud(meta, t, total_t)
                proc.stdin.write(pygame.image.tobytes(self.screen, "RGB"))
            proc.stdin.close()
            proc.wait(timeout=120)
            ok = proc.returncode == 0
            if not ok:
                stderr_tail = proc.stderr.read().decode(errors="ignore").strip()[-200:]
        except (OSError, BrokenPipeError, subprocess.TimeoutExpired) as exc:
            stderr_tail = str(exc)
        finally:
            if proc.stdin and not proc.stdin.closed:
                try:
                    proc.stdin.close()
                except OSError:
                    pass

        if ok:
            self.status_message = f"Exported to {out_path}"
        else:
            self.status_message = f"Export failed{': ' + stderr_tail if stderr_tail else ''}"
        self.status_until = pygame.time.get_ticks() + 5000

    def _draw_export_hud(self, meta, t, total_t):
        """A simpler HUD than draw_playback_hud for exported video - just
        the outcome banner and a running timer, no live-UI-only bits like
        pause state or key hints."""
        hud_y = gp.GRID_H * gp.CELL_SIZE
        pygame.draw.rect(self.screen, gp.HUD, (0, hud_y, gp.SCREEN_WIDTH, gp.HUD_HEIGHT))
        style = gp.GAME_OVER_STYLES[meta["outcome"]]
        title = self.small_font.render(style["title"], True, style["accent"])
        self.screen.blit(title, (10, hud_y + 6))
        progress = f"{t/1000:5.1f}s / {total_t/1000:5.1f}s"
        progress_surface = self.small_font.render(progress, True, gp.WHITE)
        self.screen.blit(progress_surface, (10, hud_y + 28))

    # -- playback screen -------------------------------------------------
    def _draw_replay_scene(self, data, t):
        """Render one instant of a replay (maze, pellets, every ghost,
        Pac-Man) onto self.screen at playback time `t` - shared by live
        playback (draw_playback) and video export (export_selected_to_mp4),
        so the exported frames always look exactly like what you'd see
        watching it in the browser."""
        meta = data["meta"]
        frames = data["frames"]
        idx = find_frame_index(frames, t)
        pacman_tuple, ghost_tuple, ai_tuples = entity_positions(frames, idx)
        cur = frames[idx]

        pellets, power_pellets = reconstruct_pellets(
            meta, data["pellet_events"], data["power_pellet_events"], t
        )
        self.state.pellets = pellets
        self.state.power_pellets = power_pellets

        self.screen.fill(gp.BLACK)
        for y, row in enumerate(gp.MAZE):
            for x, char in enumerate(row):
                gp.Game.draw_tile(self.state, x, y, char)

        frightened = t < cur["power_until"]
        ghost_radius = gp.CELL_SIZE // 2 - 3
        pacman_radius = gp.CELL_SIZE // 2 - 4

        ghost_delay = gp.GHOST_FRIGHTENED_MOVE_DELAY if frightened else gp.GHOST_MOVE_DELAY
        g_prev, g_cur, g_dir, g_move_time = ghost_tuple
        ghost_center, _ = gp.animated_position(g_prev, g_cur, g_move_time, ghost_delay, t)
        ghost_color = gp.FRIGHTENED_BLUE if frightened else gp.PLAYER_GHOST_COLOR
        gp.Game.draw_ghost_icon(self.state, ghost_center, ghost_radius, ghost_color, g_dir, frightened)

        ai_delay = gp.AI_GHOST_FRIGHTENED_MOVE_DELAY if frightened else gp.AI_GHOST_MOVE_DELAY
        for i, (prev, cur_pos, direction, move_time) in enumerate(ai_tuples):
            center, _ = gp.animated_position(prev, cur_pos, move_time, ai_delay, t)
            if gp.AI_GHOST_SKINS[i] == "clawd":
                gp.Game.draw_clawd(self.state, center, int(t), frightened)
            else:
                color = gp.FRIGHTENED_BLUE if frightened else gp.AI_GHOST_COLORS[i]
                gp.Game.draw_ghost_icon(self.state, center, ghost_radius, color, direction, frightened)

        p_prev, p_cur, p_dir, p_move_time = pacman_tuple
        pacman_center, pacman_t = gp.animated_position(p_prev, p_cur, p_move_time, gp.PACMAN_MOVE_DELAY, t)
        mouth_fraction = math.sin(pacman_t * math.pi)
        gp.Game.draw_pacman_icon(self.state, pacman_center, pacman_radius, gp.YELLOW, p_dir, mouth_fraction)

    def draw_playback(self, dt_ms):
        path, data = self.entries[self.selected]
        meta = data["meta"]
        frames = data["frames"]
        total_t = frames[-1]["t"] if frames else 0

        if not self.paused:
            self.playback_t = min(total_t, self.playback_t + dt_ms * self.playback_speed)

        self._draw_replay_scene(data, self.playback_t)
        self.draw_playback_hud(meta, total_t)
        pygame.display.flip()

    def draw_playback_hud(self, meta, total_t):
        hud_y = gp.GRID_H * gp.CELL_SIZE
        pygame.draw.rect(self.screen, gp.HUD, (0, hud_y, gp.SCREEN_WIDTH, gp.HUD_HEIGHT))
        style = gp.GAME_OVER_STYLES[meta["outcome"]]

        title = self.small_font.render(style["title"], True, style["accent"])
        self.screen.blit(title, (10, hud_y + 6))

        state = "PAUSED" if self.paused else f"{self.playback_speed:.0f}x"
        progress = f"{self.playback_t/1000:5.1f}s / {total_t/1000:5.1f}s  [{state}]"
        progress_surface = self.small_font.render(progress, True, gp.WHITE)
        self.screen.blit(progress_surface, (10, hud_y + 28))

        hint = self.small_font.render(
            "Space pause   Left/Right scrub   R restart   Esc back", True, gp.WHITE
        )
        self.screen.blit(
            hint, (gp.SCREEN_WIDTH - hint.get_width() - 10, hud_y + 17)
        )

    def handle_playback_input(self, event):
        if event.key == pygame.K_ESCAPE:
            self.mode = "list"
        elif event.key in (pygame.K_q,):
            return False
        elif event.key == pygame.K_SPACE:
            self.paused = not self.paused
        elif event.key == pygame.K_r:
            self.playback_t = 0.0
            self.paused = False
        elif event.key == pygame.K_LEFT:
            self.playback_t = max(0.0, self.playback_t - 2000)
        elif event.key == pygame.K_RIGHT:
            total_t = self.entries[self.selected][1]["frames"][-1]["t"]
            self.playback_t = min(total_t, self.playback_t + 2000)
        return True

    # -- statistics screen -------------------------------------------------
    def _draw_fact_block(self, rect, value_text, label_text, accent_color):
        """A small stat card: a big number over a muted label, with a
        colored accent strip identifying what it's counting."""
        pygame.draw.rect(self.screen, (30, 30, 38), rect, border_radius=8)
        pygame.draw.rect(self.screen, accent_color, (rect.x, rect.y, 4, rect.height), border_radius=2)

        value_surf = self.font.render(value_text, True, gp.WHITE)
        self.screen.blit(value_surf, (rect.centerx - value_surf.get_width() // 2, rect.y + 14))

        label_surf = self.small_font.render(label_text, True, MUTED_COLOR)
        self.screen.blit(label_surf, (rect.centerx - label_surf.get_width() // 2, rect.y + 52))

    def draw_stats(self):
        self.screen.fill(gp.BLACK)
        title = self.font.render("Pac-Man Statistics", True, gp.WHITE)
        self.screen.blit(title, title.get_rect(centerx=gp.SCREEN_WIDTH // 2, y=20))

        records = self.history_records
        total = len(records)

        if total == 0:
            empty = self.small_font.render(
                "No games recorded yet - play a round first.", True, gp.WHITE
            )
            self.screen.blit(empty, (20, 80))
        else:
            # The pie chart, its three stat cards, and the line graph
            # further down all summarize whichever timeframe T has
            # selected (see STATS_SCOPES/handle_stats_input) - the most
            # recent N games, or everything.
            scope_label = next(label for key, label, _ in STATS_SCOPES if key == self.stats_scope)
            window = next(window for key, _, window in STATS_SCOPES if key == self.stats_scope)
            scoped_records = records if window is None else records[-window:]
            scoped_total = len(scoped_records)
            wins = sum(1 for r in scoped_records if r.get("won"))
            losses = scoped_total - wins
            win_rate = 100.0 * wins / scoped_total if scoped_total else 0.0

            # Pie chart: wins vs losses for the current scope, kept small
            # and up top with its legend beside it (not below) so the line
            # graph below has the rest of the screen - most of it,
            # width-wise - to itself.
            pie_center = (170, 190)
            pie_radius = 80
            draw_pie_chart(
                self.screen, pie_center, pie_radius,
                [(wins, WIN_COLOR), (losses, LOSS_COLOR)], font=self.small_font,
            )

            chart_title = self.small_font.render(f"Wins vs Losses - {scope_label}", True, gp.WHITE)
            # However small a slice gets, its percentage label always ends
            # up right at the seam where the two slices meet (near the
            # top) - so the title needs enough clearance above the circle
            # to never collide with it, not just enough to clear the rim.
            self.screen.blit(chart_title, (pie_center[0] - chart_title.get_width() // 2, pie_center[1] - pie_radius - 44))

            # Fact blocks: total/won/lost as their own stat cards beside the
            # pie chart, rather than plain legend lines - same scope as the
            # pie chart they sit next to.
            card_w, card_h, gap = 130, 90, 14
            card_y = pie_center[1] - card_h // 2
            card_x = pie_center[0] + pie_radius + 46  # clears the pie's outer percentage labels
            total_card = pygame.Rect(card_x, card_y, card_w, card_h)
            won_card = pygame.Rect(total_card.right + gap, card_y, card_w, card_h)
            lost_card = pygame.Rect(won_card.right + gap, card_y, card_w, card_h)

            self._draw_fact_block(total_card, str(scoped_total), "Total games", (190, 190, 200))
            self._draw_fact_block(won_card, str(wins), f"Won ({win_rate:.1f}%)", WIN_COLOR)
            self._draw_fact_block(lost_card, str(losses), f"Lost ({100 - win_rate:.1f}%)", LOSS_COLOR)

            # Line graph: axes always rescaled to the current data so it
            # keeps adjusting as more games (and wins) happen in the
            # future. Spans nearly the full width across the bottom of
            # the screen - roughly double the old width - so the
            # attempt-number axis has much more room.
            #
            # Shares the pie chart's scope - zooming into the last 50k/10k
            # resets the series to start fresh at the window's first game
            # instead of carrying the full history's total into it, so a
            # recent trend isn't just a flat tail dwarfed by everything
            # before it. The x-axis still shows real attempt numbers (not
            # restarted at 1) so it's clear which stretch of history is
            # being shown.
            if window is None:
                windowed_records, start_attempt = records, 1
            else:
                windowed_records = scoped_records
                start_attempt = total - len(windowed_records) + 1

            graph_label = next(label for key, label, _ in GRAPH_MODES if key == self.graph_mode)
            y_axis_label = next(caption for key, _, caption in GRAPH_MODES if key == self.graph_mode)
            # "cumulative" is the plain running total of wins so far (the
            # original view). "deviation" instead plots how far that
            # running total has strayed from the straight-line pace a
            # constant average rate would have produced over this same
            # window - so a steady run near the window's own average win
            # rate hovers near zero, and only genuine hot/cold streaks
            # that diverge from it show up as the peaks and troughs, with
            # the y-axis extending below zero for the cold ones.
            #
            # The baseline is a proper least-squares trendline fit to the
            # win/loss scatter (attempt index, 1 or 0), the same thing a
            # scatter-plot trendline fits - not just a straight line
            # connecting the window's first and last point, which would
            # assume a perfectly constant win rate throughout and ignore
            # whether the window itself is actually trending up or down.
            # Fitting the line to every point instead of just the two
            # endpoints means a real improving/declining trend shows up
            # as a sloped baseline, and only the deviation *from that
            # trend* - not from a flat average - shows as a peak or
            # trough. (The fit's own least-squares property still forces
            # the deviation back to exactly 0 at the window's last game,
            # same as a flat average would - that part isn't a choice.)
            n = scoped_total
            if n > 1:
                mean_k = (n + 1) / 2
                mean_y = wins / n
                sum_k_y = sum(k + 1 for k, r in enumerate(windowed_records) if r.get("won"))
                sxy = sum_k_y - n * mean_k * mean_y
                sxx = n * (n * n - 1) / 12
                slope = sxy / sxx if sxx > 0 else 0.0
                intercept = mean_y - slope * mean_k
            else:
                slope = intercept = 0.0

            series = []
            wins_so_far = 0
            for offset, record in enumerate(windowed_records):
                if record.get("won"):
                    wins_so_far += 1
                if self.graph_mode == "deviation":
                    k = offset + 1
                    expected_cumulative = intercept * k + slope * k * (k + 1) / 2
                    value = wins_so_far - expected_cumulative
                else:
                    value = wins_so_far
                series.append((start_attempt + offset, value))

            graph_rect = pygame.Rect(70, 330, gp.SCREEN_WIDTH - 90, 400)
            graph_title = self.small_font.render(f"{graph_label} - {scope_label}", True, gp.WHITE)
            self.screen.blit(graph_title, (graph_rect.centerx - graph_title.get_width() // 2, graph_rect.top - 30))
            draw_line_graph(self.screen, graph_rect, series, WIN_COLOR, self.small_font)

            x_caption = self.small_font.render("Attempt number", True, MUTED_COLOR)
            self.screen.blit(x_caption, (graph_rect.centerx - x_caption.get_width() // 2, graph_rect.bottom + 22))

            y_caption = self.small_font.render(y_axis_label, True, MUTED_COLOR)
            y_caption = pygame.transform.rotate(y_caption, 90)
            self.screen.blit(y_caption, (graph_rect.left - 50, graph_rect.centery - y_caption.get_height() // 2))

        hint = self.small_font.render(
            "Esc back to list   T toggle timeframe   V toggle graph view   Q quit", True, gp.WHITE
        )
        self.screen.blit(hint, (20, gp.SCREEN_HEIGHT - 34))
        pygame.display.flip()

    def handle_stats_input(self, event):
        if event.key == pygame.K_ESCAPE:
            self.mode = "list"
        elif event.key == pygame.K_q:
            return False
        elif event.key == pygame.K_t:
            scope_keys = [key for key, _, _ in STATS_SCOPES]
            next_index = (scope_keys.index(self.stats_scope) + 1) % len(scope_keys)
            self.stats_scope = scope_keys[next_index]
        elif event.key == pygame.K_v:
            mode_keys = [key for key, _, _ in GRAPH_MODES]
            next_index = (mode_keys.index(self.graph_mode) + 1) % len(mode_keys)
            self.graph_mode = mode_keys[next_index]
        return True

    # -- main loop -------------------------------------------------------
    def run(self):
        running = True
        while running:
            dt_ms = self.clock.tick(gp.FPS)
            now = pygame.time.get_ticks()
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN:
                    if self.mode == "list" and self.naming:
                        self.handle_naming_input(event)
                    elif self.mode == "list":
                        running = self.handle_list_input(event)
                    elif self.mode == "playback":
                        running = self.handle_playback_input(event)
                    else:
                        running = self.handle_stats_input(event)
                elif event.type == pygame.KEYUP and self.mode == "list" and not self.naming:
                    self.handle_list_keyup(event)
                elif (
                    event.type == pygame.MOUSEBUTTONDOWN
                    and self.mode == "list"
                    and not self.naming
                    and event.button == 1
                ):
                    self.handle_list_click(event.pos)

            if self.mode == "list":
                if not self.naming:
                    self.update_key_repeat(now)
                self.draw_list()
            elif self.mode == "playback":
                self.draw_playback(dt_ms)
            else:
                self.draw_stats()

        pygame.quit()


def time_label(timestamp):
    return datetime.datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")


if __name__ == "__main__":
    try:
        ReplayBrowser().run()
    except Exception as exc:
        pygame.quit()
        print(f"Replay browser error: {exc}", file=sys.stderr)
        raise
