#!/usr/bin/env python3
"""Render faithful ESP32 Headroom glance previews (448×368 or round 466).

Matches firmware/src/main.cpp drawGlancePage palette + layout closely enough
for README screenshots. Text goes through gfx_font, which blits the same
classic 5×7 glyphs Arduino_GFX draws, at the same 6×8 × textSize metrics.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys

from PIL import Image, ImageDraw

import gfx_font

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "host"))
import device_view

# Logical canvas (what the selected board draws into). main() switches these
# globals before rendering when --panel round-466 is selected; helpers mirror
# the firmware and intentionally use the active dimensions.
W, H = 448, 368
PAD = 28
ROUND_PANEL = False

# Mirrors PANEL_SEAL_ROWS in firmware/src/main.cpp: rows the panel edge seal
# repaints in the frame's own background colour, which no layout can use. Zero
# now that the seal only wipes GRAM past our last column. Keep the two in step —
# a preview that promises rows the board paints over is a preview that lies.
SEAL_ROWS = 0

COL_BG = (0, 0, 0)
COL_WHITE = (240, 238, 234)
COL_DIM = (120, 116, 110)
COL_BAR = (42, 40, 38)
COL_GREEN = (95, 155, 115)
COL_RED = (175, 105, 100)
COL_CRT = (0, 214, 236)
COL_CRT_DIM = (150, 40, 120)
COL_CRT_YELLOW = (236, 214, 0)
COL_CRT_BG = (6, 4, 14)


# A "font" here is just an Arduino_GFX textSize — gfx_font blits the same 5×7
# bitmap glyphs the panel does, so the preview isn't a lookalike in Courier.
FONT1 = 1
FONT2 = 2
FONT3 = 3
FONT4 = 4


def round_chord_half(y: int) -> int:
    radius = min(W, H) // 2 - 6
    dy = y - H // 2
    inside = radius * radius - dy * dy
    return int(math.sqrt(inside)) if inside > 0 else 0


def round_band(y0: int, y1: int, inset: int) -> tuple[int, int]:
    half = min(round_chord_half(y0), round_chord_half(y1)) - inset
    half = max(1, half)
    return W // 2 - half, half * 2


def text_w(draw: ImageDraw.ImageDraw, s: str, font) -> int:
    return gfx_font.text_width(s, font)


def draw_text(draw: ImageDraw.ImageDraw, s: str, x: int, y: int, font, fill):
    gfx_font.draw_text(draw, s, x, y, font, fill)


def draw_centered(draw: ImageDraw.ImageDraw, s: str, y: int, font, fill):
    draw_text(draw, s, (W - text_w(draw, s, font)) // 2, y, font, fill)


def clip_fit(draw, s: str, max_w: int, font) -> str:
    if text_w(draw, s, font) <= max_w:
        return s
    out = s
    while out:
        out = out[:-1]
        if text_w(draw, out, font) <= max_w:
            return out
    return ""


def draw_arc(draw, cx, cy, r, thick, start_deg, end_deg, fill, steps=64):
    """Approximate fillArc (outer r → inner r-thick) from start to end degrees."""
    if end_deg <= start_deg:
        return
    outer = []
    inner = []
    for i in range(steps + 1):
        t = i / steps
        a = math.radians(start_deg + (end_deg - start_deg) * t)
        outer.append((cx + r * math.cos(a), cy + r * math.sin(a)))
        ri = r - thick
        inner.append((cx + ri * math.cos(a), cy + ri * math.sin(a)))
    poly = outer + list(reversed(inner))
    draw.polygon(poly, fill=fill)


def draw_round_arc(draw, cx, cy, r, thick, start_deg, sweep_deg, fill, steps=64):
    """Usage arc with half-round ends. Mirror of fillRoundArc() in main.cpp."""
    mid = r - thick / 2
    cap = thick / 2
    cap_deg = math.degrees(cap / mid)
    s = start_deg + cap_deg
    e = start_deg + sweep_deg - cap_deg
    if e > s:
        draw_arc(draw, cx, cy, r, thick, s, e, fill, steps=steps)
    else:
        s = e = start_deg + sweep_deg / 2
    for a in (math.radians(s), math.radians(e)):
        px = cx + mid * math.cos(a)
        py = cy + mid * math.sin(a)
        draw.ellipse([px - cap, py - cap, px + cap, py + cap], fill=fill)


def dim(color, factor):
    """Blend toward the background. Mirror of dimToward() in main.cpp."""
    return tuple(
        int(bg + (c - bg) * factor + 0.5) for c, bg in zip(color, COL_BG)
    )


def draw_pace_ring(draw, cx, cy, r, thick, pct, pace_pct, accent):
    """One ring band. Mirror of drawPaceRing() in firmware/src/main.cpp."""
    # A neutral track at 0% is indistinguishable from background, so two empty
    # rings merge into one dark blob. Tinting the track keeps each ring legible
    # as a ring even before any of it fills.
    draw_arc(draw, cx, cy, r, thick, -90, 270, dim(accent, 0.20), steps=90)
    if pct is not None and pct >= 0:
        p = min(100.0, float(pct))
        sweep = p * 3.6
        if p > 0 and sweep < 2:
            sweep = 2
        # Round-cap even at 100% so the two ends meet as a ")(" seam at 12
        # o'clock — same as SwiftUI StrokeStyle(.round), not a solid fill.
        draw_round_arc(draw, cx, cy, r, thick, -90, sweep, accent,
                       steps=max(8, int(sweep)))
    if pace_pct is not None and pace_pct >= 0:
        pp = min(100.0, float(pace_pct))
        a = math.radians(-90 + pp * 3.6)
        mid = r - thick / 2
        dot = round(thick * 5 / 14)
        px = cx + mid * math.cos(a)
        py = cy + mid * math.sin(a)
        draw.ellipse([px - dot, py - dot, px + dot, py + dot], fill=COL_WHITE)


def draw_quota_ring(draw, cx, cy, r, layers, accent, label):
    """Concentric pace layers. Mirror of drawQuotaRing() in main.cpp."""
    thick, gap = 6, 4
    if not layers:
        draw_pace_ring(draw, cx, cy, r, thick, None, None, accent)
    else:
        rr = r
        for pct, pace in layers[:2]:
            draw_pace_ring(draw, cx, cy, rr, thick, pct, pace, accent)
            rr = rr - thick - gap
    tw = text_w(draw, label, FONT2)
    draw_text(draw, label, cx - tw // 2, cy + r + 8, FONT2, accent)


# Mirror of the PACE_* constants in firmware/src/main.cpp, which in turn carry
# the proportions of the macOS menu-bar Pace glyph. PACE_SCALE is the same 8
# points MenuBarIconStyle.paceScale uses.
PACE_SCALE = 8.0
PACE_COL_W = 14
PACE_COL_H = 68
PACE_DOT_R = 5
PACE_PAD = 7
PACE_RAIL_H = 3


def pace_layer(layers):
    """Layer 0 — the longer window, the same pool the menu bar reads."""
    if not layers:
        return None
    pct, pace = layers[0]
    if pct is None or pace is None or pct < 0 or pace < 0:
        return None
    return float(pct), float(pace)


def draw_pace_track(draw, cx, cy, layers, accent, label):
    """Pill, even-spend line, label. Mirror of drawPaceTrack() in main.cpp."""
    layer = pace_layer(layers)
    x = cx - PACE_COL_W // 2
    y = cy - PACE_COL_H // 2
    draw.rounded_rectangle(
        [x, y, x + PACE_COL_W - 1, y + PACE_COL_H - 1],
        radius=PACE_COL_W // 2,
        fill=dim(accent, 0.20 if layer else 0.10),
        outline=dim(accent, 0.45 if layer else 0.22),
    )
    if layer:
        draw.rectangle(
            [x, cy - PACE_RAIL_H // 2,
             x + PACE_COL_W - 1, cy - PACE_RAIL_H // 2 + PACE_RAIL_H - 1],
            fill=COL_DIM,
        )
    tw = text_w(draw, label, FONT2)
    draw_text(draw, label, cx - tw // 2, cy + PACE_COL_H // 2 + 6, FONT2, accent)


def draw_pace_mark(draw, cx, cy, layers, accent):
    """The mark. Mirror of drawPaceMark() in main.cpp."""
    layer = pace_layer(layers)
    if layer is None:
        return
    pct, pace = layer
    t = math.tanh((pct - pace) / PACE_SCALE)
    travel = PACE_COL_H // 2 - PACE_PAD - PACE_DOT_R
    dy = round(-t * travel)
    draw.ellipse(
        [cx - PACE_DOT_R, cy + dy - PACE_DOT_R,
         cx + PACE_DOT_R, cy + dy + PACE_DOT_R],
        fill=accent,
    )


def draw_pace_glyph(draw, pad_x, span, cy, columns):
    """Tracks, then marks. Mirror of drawPaceGlyph() in main.cpp."""
    if not columns:
        return
    slot = span // len(columns)
    for index, (layers, accent, label) in enumerate(columns):
        draw_pace_track(
            draw, pad_x + index * slot + slot // 2, cy, layers, accent, label
        )
    for index, (layers, accent, _) in enumerate(columns):
        draw_pace_mark(draw, pad_x + index * slot + slot // 2, cy, layers, accent)


def parse_accent(value: str | None):
    if isinstance(value, str) and len(value) == 7 and value.startswith("#"):
        try:
            return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))
        except ValueError:
            pass
    return COL_DIM


# Screenshot-only burndown stories. The fixture's weekly projections share one
# timestamp (fine for contracts, a vertical stroke on a chart). Marketing
# shots use three distinct curves instead: steady spend, a late cliff, and a
# gentle plateau. Applied after the real projection so live /usage captures
# still render literally.
DEMO_BURN_PROFILES = (
    # Steady spend, with the small pauses visible in the Mac screenshot.
    (
        ((-3.0, 100), (-2.72, 92), (-2.35, 92), (-1.82, 84),
         (-1.48, 84), (-1.02, 76), (-0.55, 70), (0.0, None)),
        3.20,
        -29,
        3.25,
        False,
    ),
    # Quiet most of the week, then a sharp late burn and early exhaustion.
    (
        ((-3.0, 100), (-1.25, 100), (-0.72, 96), (-0.38, 96),
         (-0.16, 82), (0.0, None)),
        0.48,
        -100,
        3.70,
        True,
    ),
    # A measured drop followed by a long, shallow forecast.
    (
        ((-3.0, 100), (-2.20, 100), (-1.78, 85), (-0.62, 85),
         (0.0, None)),
        3.20,
        -6,
        4.00,
        False,
    ),
)


def _apply_demo_burn_profile(pool: dict, profile, now: int, *, device: bool):
    """Rewrite one pool's curve in either /usage or device_view shape."""
    day = 86400
    actual_pts, forecast_days, forecast_delta, reset_days, warn = profile
    key = "pts" if device else "actual"
    samples = pool.get(key) or []
    if not samples:
        return
    remaining = float(samples[-1][1])
    pool[key] = [
        [int(now + offset * day), remaining if value is None else value]
        for offset, value in actual_pts
    ]
    forecast_remaining = max(0.0, remaining + forecast_delta)
    proj = [
        [now, remaining],
        [int(now + forecast_days * day), forecast_remaining],
    ]
    if device:
        pool["proj"] = proj
        pool["t1"] = int(now + reset_days * day)
        pool["warn"] = warn
    else:
        pool["projected"] = proj
        pool["window_end"] = float(now + reset_days * day)
        if warn:
            pool["exhausts_before_reset"] = True
            pool["exhausted"] = forecast_remaining <= 0
        used = 100.0 - remaining
        pool["remaining_pct"] = remaining
        pool["used_pct"] = used


def shape_demo_usage_burndown(doc: dict):
    """Shape full /usage burndown curves for Apple + ESP32 screenshots."""
    focus = list(doc.get("focus") or [])[:3]
    if not focus:
        focus = [
            row.get("id")
            for row in (doc.get("providers") or [])
            if row.get("id")
        ][:3]
    burns = doc.get("burndown") or {}
    targets = []
    for provider_id in focus:
        pools = burns.get(provider_id) or {}
        # Same pick OverviewBurndownCard / overviewBurndown use.
        pool_key = "total" if provider_id == "cursor" else "week"
        pool = pools.get(pool_key)
        if pool is None and pool_key != "week":
            pool = pools.get("week")
            pool_key = "week"
        if not pool or not (pool.get("actual") or []):
            continue
        targets.append((provider_id, pool_key, pool))
    if not targets:
        return

    now = max(int(pool["actual"][-1][0]) for _, _, pool in targets)
    for index, (_, _, pool) in enumerate(targets):
        profile = DEMO_BURN_PROFILES[min(index, len(DEMO_BURN_PROFILES) - 1)]
        _apply_demo_burn_profile(pool, profile, now, device=False)

    # Keep the primary pointer in step with the first shaped pool.
    primary_id, primary_key, primary_pool = targets[0]
    primary = dict(primary_pool)
    primary["provider"] = primary_id
    primary["pool"] = primary_key
    doc["burndown_primary"] = primary


def shape_demo_burndown(device: dict):
    """Shape device_view burndown curves for ESP32 marketing shots."""
    providers = (device.get("providers") or [])[:3]
    burns = device.get("burndown") or {}
    ready = [
        burns.get(provider.get("id"), {})
        for provider in providers
        if (burns.get(provider.get("id"), {}).get("pts") or [])
    ]
    if not ready:
        return

    now = max(int(burn["pts"][-1][0]) for burn in ready)
    for index, provider in enumerate(providers):
        burn = burns.get(provider.get("id"), {})
        if not burn.get("pts"):
            continue
        profile = DEMO_BURN_PROFILES[min(index, len(DEMO_BURN_PROFILES) - 1)]
        _apply_demo_burn_profile(burn, profile, now, device=True)


def timezone_offset_seconds(updated: str) -> int:
    if len(updated) < 5 or updated[-5] not in "+-":
        return 0
    try:
        offset = int(updated[-4:-2]) * 3600 + int(updated[-2:]) * 60
    except ValueError:
        return 0
    return -offset if updated[-5] == "-" else offset


def clip_burn_segment(ta, ra, tb, rb, t_lo, t_hi):
    if ta == tb:
        return None
    if ta > tb:
        ta, tb, ra, rb = tb, ta, rb, ra
    if tb < t_lo or ta > t_hi:
        return None
    oa, ora, ob, orb = ta, ra, tb, rb
    span = tb - ta
    if ta < t_lo:
        u = (t_lo - ta) / span
        oa, ora = t_lo, ra + u * (rb - ra)
    if tb > t_hi:
        u = (t_hi - ta) / span
        ob, orb = t_hi, ra + u * (rb - ra)
    return oa, ora, ob, orb


def stroke_dashed(draw, p0, p1, fill):
    x0, y0 = p0
    x1, y1 = p1
    dx, dy = x1 - x0, y1 - y0
    adx, ady = abs(dx), abs(dy)
    length = adx + ady // 2 if adx > ady else ady + adx // 2
    if length < 1:
        return
    for i in range(0, length, 12):
        i1 = min(length, i + 3)
        a = (x0 + dx * i // length, y0 + dy * i // length)
        b = (x0 + dx * i1 // length, y0 + dy * i1 // length)
        draw.line([a, b], fill=fill, width=3)


def draw_overall_series(draw, burn, accent, x, y, w, h, t_lo, t_hi):
    points = burn.get("pts") or []
    history = burn.get("hist") or []
    if (not points and not history) or t_hi <= t_lo:
        return
    span = t_hi - t_lo

    def px(t):
        if t <= t_lo:
            return x
        if t >= t_hi:
            return x + w - 1
        return x + int((t - t_lo) * (w - 1) / span)

    def py(remaining):
        remaining = max(0.0, min(100.0, float(remaining)))
        return y + h - 1 - int(remaining * (h - 1) / 100.0)

    # Spent windows first, so the live curve covers them where they overlap.
    # Mirrors drawOverallSeries() in firmware/src/main.cpp — a segment whose
    # two samples straddle a grant is skipped rather than joined.
    grants = [int(mark[0]) for mark in (burn.get("rsts") or []) if mark]
    if len(history) > 1:
        ghost = dim(accent, 0.70)
        for a, b in zip(history, history[1:]):
            # A pair straddling a grant is the recharge — squared off on the
            # grant instant rather than drawn as a raw diagonal. Mirrors
            # drawOverallSeries() in firmware/src/main.cpp.
            spans = [at for at in grants if a[0] < at <= b[0]]
            if spans:
                at = spans[-1]
                for seg in (
                    (int(a[0]), float(a[1]), int(at), float(a[1])),
                    (int(at), float(b[1]), int(b[0]), float(b[1])),
                ):
                    clipped = clip_burn_segment(*seg, t_lo, t_hi)
                    if clipped:
                        ta, ra, tb, rb = clipped
                        draw.line(
                            [(px(ta), py(ra)), (px(tb), py(rb))],
                            fill=ghost, width=1,
                        )
                if t_lo < at < t_hi:
                    draw.line(
                        [(px(at), py(a[1])), (px(at), py(b[1]))],
                        fill=ghost, width=1,
                    )
                continue
            clipped = clip_burn_segment(
                int(a[0]), float(a[1]), int(b[0]), float(b[1]), t_lo, t_hi
            )
            if clipped:
                ta, ra, tb, rb = clipped
                draw.line(
                    [(px(ta), py(ra)), (px(tb), py(rb))], fill=ghost, width=1
                )
        for at in grants:
            if not t_lo < at < t_hi:
                continue
            gx = px(at)
            for yy in range(y, y + h, 4):
                draw.line([(gx, yy), (gx, yy + 1)], fill=ghost)

    line = accent
    for a, b in zip(points, points[1:]):
        clipped = clip_burn_segment(
            int(a[0]), float(a[1]), int(b[0]), float(b[1]), t_lo, t_hi
        )
        if clipped:
            ta, ra, tb, rb = clipped
            draw.line(
                [(px(ta), py(ra)), (px(tb), py(rb))],
                fill=line,
                width=3,
            )

    projected = burn.get("proj") or []
    if len(projected) == 2:
        p0t, p0r = int(projected[0][0]), float(projected[0][1])
        p1t, p1r = int(projected[1][0]), float(projected[1][1])
        reset = int(burn.get("t1") or 0)
        if reset > 0 and p1t > reset and p1t > p0t:
            u = (reset - p0t) / (p1t - p0t)
            p1t, p1r = reset, p0r + u * (p1r - p0r)
        p0r, p1r = max(0.0, p0r), max(0.0, p1r)
        if abs(p1r - p0r) > 0.5 or burn.get("warn"):
            clipped = clip_burn_segment(
                p0t, p0r, p1t, p1r, t_lo, t_hi
            )
            if clipped:
                ta, ra, tb, rb = clipped
                p0, p1 = (px(ta), py(ra)), (px(tb), py(rb))
                if abs(p1r - p0r) > 0.5:
                    stroke_dashed(draw, p0, p1, line)
                if tb == p1t:
                    radius = 3 if burn.get("warn") and p1r <= 0.5 else 2
                    draw.ellipse(
                        [p1[0] - radius, p1[1] - radius,
                         p1[0] + radius, p1[1] + radius],
                        fill=line,
                    )

    reset = int(burn.get("t1") or 0)
    if t_lo < reset < t_hi:
        reset_x = px(reset)
        for yy in range(y + 1, y + h - 1, 4):
            draw.line(
                [(reset_x, yy), (reset_x, min(yy + 1, y + h - 2))],
                fill=accent,
            )

    for t, remaining in reversed(points):
        if t_lo <= int(t) <= t_hi:
            nx, ny = px(int(t)), py(float(remaining))
            draw.ellipse([nx - 3, ny - 3, nx + 3, ny + 3], fill=line)
            draw.ellipse(
                [nx - 4, ny - 4, nx + 4, ny + 4],
                outline=COL_BG,
            )
            break


def draw_glance_burndown(draw, providers, burns, updated, mid_y, low_bottom):
    span = W - PAD * 2
    # History alone counts as ready — mirrors drawGlanceBurndown() in main.cpp.
    ready = [
        burns.get(provider.get("id"), {})
        for provider in providers
        if (burns.get(provider.get("id"), {}).get("pts")
            or burns.get(provider.get("id"), {}).get("hist"))
    ]
    if not ready:
        draw_text(draw, "Collecting history", PAD + 8, mid_y + 36,
                  FONT2, COL_DIM)
        return

    # "Now" comes from the live series only; the spent curve reaches back.
    live = [burn for burn in ready if burn.get("pts")]
    now_t = max(int(burn["pts"][-1][0]) for burn in live) if live else int(
        max(int(burn["hist"][-1][0]) for burn in ready))
    axis_h = 12
    row_h = 16
    legend_h = len(providers) * row_h + 2
    chart_y = mid_y + 6
    chart_h = low_bottom - legend_h - axis_h - chart_y
    chart_x, chart_w = (
        round_band(chart_y, chart_y + chart_h - 1, 10)
        if ROUND_PANEL else (PAD, span)
    )

    tz = timezone_offset_seconds(updated)
    local_now = now_t + tz
    local_day = local_now - local_now % 86400
    today_utc = local_day - tz
    t_lo = today_utc - 3 * 86400
    t_hi = t_lo + 7 * 86400

    track = dim(COL_WHITE, 0.35)
    grid = dim(COL_WHITE, 0.22)
    draw.rectangle(
        [chart_x, chart_y, chart_x + chart_w - 1, chart_y + chart_h - 1],
        outline=track,
    )
    draw.line(
        [(chart_x + 1, chart_y + chart_h // 2),
         (chart_x + chart_w - 2, chart_y + chart_h // 2)],
        fill=grid,
    )

    weekdays = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")
    start_weekday = (local_day // 86400 + 4) % 7
    axis_y = chart_y + chart_h + 2
    for day in range(7):
        day_t = t_lo + day * 86400
        day_x = chart_x + int((day_t - t_lo) * (chart_w - 1) / (t_hi - t_lo))
        if day:
            draw.line(
                [(day_x, chart_y + 1), (day_x, chart_y + chart_h - 2)],
                fill=grid,
            )
        weekday = weekdays[(start_weekday - 3 + day + 70) % 7]
        draw_text(draw, weekday, day_x + 2, axis_y, FONT1, COL_DIM)

    if t_lo < now_t < t_hi:
        now_x = chart_x + int((now_t - t_lo) * (chart_w - 1) / (t_hi - t_lo))
        draw.line(
            [(now_x, chart_y + 1), (now_x, chart_y + chart_h - 2)],
            fill=dim(COL_WHITE, 0.45),
        )

    for provider in providers:
        burn = burns.get(provider.get("id"), {})
        draw_overall_series(
            draw,
            burn,
            parse_accent(provider.get("accent")),
            chart_x,
            chart_y,
            chart_w,
            chart_h,
            t_lo,
            t_hi,
        )

    legend_y = low_bottom - legend_h + 1
    text_x = PAD + 14
    text_width = span - 14
    for index, provider in enumerate(providers):
        row_y = legend_y + index * row_h
        burn = burns.get(provider.get("id"), {})
        accent = parse_accent(provider.get("accent"))
        draw.ellipse(
            [PAD, row_y + 3, PAD + 6, row_y + 9],
            fill=accent if burn.get("pts") else COL_DIM,
        )
        verdict = burn.get("verdict")
        if verdict:
            label = clip_fit(draw, str(verdict), text_width, FONT2)
            draw_text(draw, label, text_x, row_y, FONT2, COL_DIM)
        elif burn.get("pts"):
            remaining = float(burn["pts"][-1][1])
            draw_text(draw, f"{int(remaining + 0.5)}%",
                      text_x, row_y, FONT2, accent)
        else:
            draw_text(draw, "-", text_x, row_y, FONT2, COL_DIM)


def activity_cell_color(level):
    if level <= 0:
        return (0, 0, 0)
    if level == 1:
        return tuple(int(c * 0.28 + 0.5) for c in COL_GREEN)
    if level == 2:
        return tuple(int(c * 0.48 + 0.5) for c in COL_GREEN)
    if level == 3:
        return tuple(int(c * 0.72 + 0.5) for c in COL_GREEN)
    return COL_GREEN


def draw_glance_history(draw, device, mid_y, low_bottom):
    history = device.get("activity_history") or {}
    levels = [max(0, min(4, int(value)))
              for value in (history.get("levels") or [])]
    draw_text(draw, "History", PAD, mid_y + 8, FONT2, COL_WHITE)
    if not levels:
        draw_text(draw, "Collecting activity", PAD, mid_y + 38, FONT2, COL_DIM)
        return

    summary = f"{int(history.get('active_days') or 0)} active · " \
              f"{int(history.get('current_streak') or 0)}d streak"
    summary_font = FONT1 if ROUND_PANEL else FONT2
    summary_y = mid_y + 11 if ROUND_PANEL else mid_y + 8
    draw_text(draw, summary,
              W - PAD - text_w(draw, summary, summary_font), summary_y,
              summary_font, COL_DIM)

    cell, gap = ((20, 3) if ROUND_PANEL else (14, 2))
    start_weekday = int(history.get("start_weekday") or 0) % 7
    visible_days = 84 if ROUND_PANEL else device_view.MAX_ACTIVITY_DAYS
    history_offset = max(0, len(levels) - visible_days)
    levels = levels[history_offset:]
    start_weekday = (start_weekday + history_offset) % 7
    leading_days = max(0, visible_days - len(levels))
    grid_start_weekday = (start_weekday - leading_days % 7) % 7
    cols = (grid_start_weekday + visible_days + 6) // 7
    grid_w = cols * cell + (cols - 1) * gap
    grid_y = mid_y + 30
    if ROUND_PANEL:
        grid_bottom = grid_y + 7 * cell + 6 * gap - 1
        grid_band_x, grid_band_w = round_band(grid_y, grid_bottom, 8)
        grid_x = grid_band_x + (grid_band_w - grid_w) // 2
    else:
        grid_x = PAD + (W - PAD * 2 - grid_w)
    for index, level in enumerate(levels):
        slot = grid_start_weekday + leading_days + index
        col, row = slot // 7, slot % 7
        x = grid_x + col * (cell + gap)
        y = grid_y + row * (cell + gap)
        draw.rounded_rectangle(
            [x, y, x + cell - 1, y + cell - 1],
            radius=3,
            fill=activity_cell_color(level),
        )
    if not ROUND_PANEL:
        draw.line([(PAD, low_bottom), (W - PAD - 1, low_bottom)],
                  fill=dim(COL_DIM, 0.35), width=1)


def format_usd(value):
    try:
        return f"${float(value or 0):,.0f}"
    except (TypeError, ValueError):
        return "$0"


def draw_glance_daily_burn(draw, device, mid_y, low_bottom):
    daily = device.get("daily_burn") or {}
    days = daily.get("days") or []
    draw_text(draw, "Daily burn", PAD, mid_y + 8, FONT2, COL_WHITE)
    if not days:
        draw_text(draw, "Collecting burn", PAD, mid_y + 42, FONT2, COL_DIM)
        return

    today_total = float(days[-1].get("total") or 0)
    summary = f"Today {today_total:.0f}%"
    draw_text(draw, summary,
              W - PAD - text_w(draw, summary, FONT2), mid_y + 8, FONT2, COL_DIM)

    providers = (device.get("providers") or [])[:3]
    max_total = max([float(day.get("total") or 0) for day in days] + [1])
    label_y = low_bottom - 12
    chart_top = mid_y + 34
    chart_bottom = label_y - 5
    chart_h = chart_bottom - chart_top
    gap = 6
    bar_w = (W - PAD * 2 - gap * (len(days) - 1)) // len(days)
    if chart_h <= 4 or bar_w <= 2:
        return

    for index, day in enumerate(days):
        x = PAD + index * (bar_w + gap)
        draw.rounded_rectangle(
            [x, chart_top, x + bar_w - 1, chart_bottom - 1],
            radius=3,
            fill=COL_BAR,
        )
        total = float(day.get("total") or 0)
        filled_h = max(1, round(chart_h * total / max_total)) if total > 0 else 0
        y = chart_bottom
        burns = day.get("burns") or {}
        for provider in reversed(providers):
            amount = float(burns.get(provider.get("id"), 0) or 0)
            if amount <= 0 or filled_h <= 0:
                continue
            segment = max(1, round(chart_h * amount / max_total))
            segment = min(segment, y - chart_top)
            if segment <= 0:
                break
            draw.rectangle(
                [x, y - segment, x + bar_w - 1, y - 1],
                fill=parse_accent(provider.get("accent")),
            )
            y -= segment
        label = str(day.get("label") or "")
        if label:
            draw_text(draw, label,
                      x + (bar_w - text_w(draw, label, FONT1)) // 2,
                      label_y, FONT1, COL_DIM)


def draw_glance_spend(draw, device, mid_y, low_bottom):
    spend = device.get("spend") or {}
    draw_text(draw, "Spend", PAD, mid_y + 8, FONT2, COL_WHITE)
    estimated = "Estimated" if spend.get("estimated", True) else "Observed"
    draw_text(draw, estimated,
              W - PAD - text_w(draw, estimated, FONT2), mid_y + 8, FONT2,
              COL_DIM)
    total = float(spend.get("total") or 0)
    today = float(spend.get("today") or 0)
    if total <= 0 and today <= 0:
        draw_text(draw, "No spend yet", PAD, mid_y + 45, FONT2, COL_DIM)
        return

    captions = ("today", "per active day", "month")
    values = (format_usd(today), format_usd(spend.get("avg")),
              format_usd(total))

    if not ROUND_PANEL:
        col_w = (W - PAD * 2) // 3
        for index, (caption, value) in enumerate(zip(captions, values)):
            col_x = PAD + index * col_w
            draw_text(draw, caption, col_x, mid_y + 24, FONT1, COL_DIM)
            draw_text(draw, value, col_x, mid_y + 41, FONT3, COL_WHITE)
        draw.line([(PAD, low_bottom), (W - PAD - 1, low_bottom)],
                  fill=dim(COL_DIM, 0.35), width=1)
        return

    # Rows, not columns. Mirror of drawGlanceSpend() in main.cpp — see the
    # comments there for why the circle rules out three columns, why each row
    # is budgeted against its own caption, and why every row shares one chord.
    band_top = mid_y + 30
    row_gap = 7
    size = 6
    while size > 2:
        row_h = size * 8
        total_h = 3 * row_h + 2 * row_gap
        if band_top + total_h > low_bottom:
            size -= 1
            continue
        top = band_top + (low_bottom - band_top - total_h) // 2
        number_x, number_w = round_band(top, top + total_h, 10)
        if all(text_w(draw, values[i], size)
               <= number_w - text_w(draw, captions[i], FONT2) - 14
               for i in range(3)):
            break
        size -= 1
    row_h = size * 8
    total_h = 3 * row_h + 2 * row_gap
    top = band_top + (low_bottom - band_top - total_h) // 2
    number_x, number_w = round_band(top, top + total_h, 10)
    for index, (caption, value) in enumerate(zip(captions, values)):
        row_y = top + index * (row_h + row_gap)
        draw_text(draw, caption, number_x, row_y + (row_h - 16) // 2, FONT2,
                  COL_DIM)
        draw_text(draw, value, number_x + number_w - text_w(draw, value, size),
                  row_y, size, COL_WHITE)


def seal_edges(img: Image.Image, bg) -> Image.Image:
    """Repaint the bottom band the way the panel does, in the frame's own bg."""
    if SEAL_ROWS > 0:
        ImageDraw.Draw(img).rectangle([0, H - SEAL_ROWS, W - 1, H - 1], fill=bg)
    return img


def render_glance(
    doc: dict,
    link_via: str = "wifi",
    link_error_minutes: int | None = None,
    demo_burndown: bool = False,
    power: str = "usb",
    battery_percent: int | None = None,
    home_mode: str = "daily",
    glance_style: str = "rings",
) -> Image.Image:
    # Feed the same trimmed payload to the preview that the ESP32 receives.
    device = device_view.build(doc)
    if demo_burndown:
        shape_demo_burndown(device)
    providers = (device.get("providers") or [])[:3]
    burns = device.get("burndown") or {}
    updated = str(device.get("updated") or "")

    img = Image.new("RGB", (W, H), COL_BG)
    draw = ImageDraw.Draw(img)
    mode_name = {"daily": "Daily burn", "burndown": "Burndown",
                 "history": "History", "spend": "Spend"}.get(
                     home_mode, "Daily burn")
    when = updated[11:16] if len(updated) >= 16 else ""
    if ROUND_PANEL:
        draw_centered(draw, "Headroom", 24, FONT3, COL_WHITE)
        if when:
            updated_label = f"UPDATED {when}"
            draw_centered(draw, updated_label, 52, FONT1, COL_DIM)
        chip_w = text_w(draw, mode_name, FONT2) + 16
        chip_x = (W - chip_w) // 2
        draw.rounded_rectangle(
            [chip_x, 70, chip_x + chip_w - 1, 93], radius=6, outline=COL_DIM
        )
        draw_text(draw, mode_name, chip_x + 8, 76, FONT2, COL_DIM)
        ring_pad, ring_span = round_band(105, 197, 8)
        ring_r, ring_cy = 30, 143
        mid_y, low_bottom = 205, 397
    else:
        # Home takes more air above the wordmark than the page inset.
        top = PAD + 10
        draw_text(draw, "Headroom", PAD, top, FONT3, COL_WHITE)
        chip_x = PAD + 152
        chip_w = text_w(draw, mode_name, FONT2) + 16
        draw.rounded_rectangle(
            [chip_x - 8, top + 2, chip_x - 8 + chip_w - 1, top + 25],
            radius=6,
            outline=COL_DIM,
        )
        draw_text(draw, mode_name, chip_x, top + 6, FONT2, COL_DIM)
        if when:
            draw_text(
                draw,
                when,
                W - PAD - text_w(draw, when, FONT2),
                top + 6,
                FONT2,
                COL_DIM,
            )
        ring_pad, ring_span = PAD, W - PAD * 2
        ring_r = 32
        ring_cy = top + 74
        mid_y = ring_cy + ring_r + 38
        low_bottom = H - PAD - 15

    span = W - PAD * 2
    slot = ring_span // len(providers) if providers else ring_span

    columns = []
    for provider in providers:
        layers = [
            (pool.get("p"), pool.get("c"))
            for pool in (provider.get("pools") or [])[:2]
            if pool.get("p") is not None
        ] if provider.get("ok") else []
        columns.append((
            layers,
            parse_accent(provider.get("accent")),
            str(provider.get("title") or provider.get("id") or "?"),
        ))

    if glance_style == "pace":
        draw_pace_glyph(draw, ring_pad, ring_span, ring_cy, columns)
    else:
        for index, (layers, accent, label) in enumerate(columns):
            draw_quota_ring(
                draw,
                ring_pad + index * slot + slot // 2,
                ring_cy,
                ring_r,
                layers,
                accent,
                label,
            )

    if not ROUND_PANEL:
        draw.line([(PAD, mid_y), (PAD + span - 1, mid_y)],
                  fill=COL_DIM, width=1)
    if home_mode == "daily":
        draw_glance_daily_burn(draw, device, mid_y, low_bottom)
    elif home_mode == "history":
        draw_glance_history(draw, device, mid_y, low_bottom)
    elif home_mode == "spend":
        draw_glance_spend(draw, device, mid_y, low_bottom)
    elif providers:
        draw_glance_burndown(
            draw, providers, burns, updated, mid_y, low_bottom
        )

    footer_x, footer_w = (round_band(418, 436, 8)
                          if ROUND_PANEL else (PAD, span))
    footer_y = 436 if ROUND_PANEL else H - PAD
    draw_link_glyph(
        draw,
        footer_x + footer_w,
        footer_y,
        link_via=link_via,
        error_minutes=link_error_minutes,
    )
    draw_power_glyph(
        draw,
        footer_x,
        footer_y,
        power=power,
        battery_percent=battery_percent,
    )
    return seal_edges(img, COL_BG)


def draw_link_glyph(
    draw: ImageDraw.ImageDraw,
    right_x: int,
    bottom_y: int,
    link_via: str = "wifi",
    error_minutes: int | None = None,
):
    """Mirror drawLinkGlyph(), including its last-good age on failure."""
    glyph_w, glyph_h = 18, 15
    gx = right_x - glyph_w
    gy = bottom_y - glyph_h
    color = COL_DIM if error_minutes is None else COL_CRT_YELLOW

    if error_minutes is not None:
        age = f"{max(0, error_minutes)}m"
        draw_text(
            draw,
            age,
            gx - 6 - text_w(draw, age, FONT2),
            gy,
            FONT2,
            color,
        )

    if link_via == "usb":
        # SF Symbol-style cable.connector: tip, housing, cable.
        cx = gx + glyph_w // 2
        tip = tuple(int(c + (w - c) * 0.55 + 0.5)
                    for c, w in zip(color, COL_WHITE))
        draw.rounded_rectangle([cx - 4, gy, cx + 3, gy + 2],
                               radius=1, fill=tip)
        draw.rounded_rectangle([cx - 5, gy + 3, cx + 4, gy + 9],
                               radius=2, fill=color)
        draw.rounded_rectangle([cx - 1, gy + 10, cx, gy + 14],
                               radius=1, fill=color)
        return

    cx = gx + glyph_w // 2
    cy = bottom_y - 2
    draw.arc([cx - 12, cy - 12, cx + 12, cy + 12],
             start=225, end=315, fill=color, width=2)
    draw.arc([cx - 7, cy - 7, cx + 7, cy + 7],
             start=225, end=315, fill=color, width=2)
    draw.ellipse([cx - 1, cy - 2, cx + 1, cy], fill=color)


def draw_charge_bolt(draw: ImageDraw.ImageDraw, cx: int, cy: int, color):
    """Mirror drawChargeBolt() — three stacked wedges at SF Symbol scale."""
    draw.polygon(
        [(cx + 1, cy - 5), (cx - 3, cy + 0), (cx + 1, cy + 0)],
        fill=color,
    )
    draw.polygon(
        [(cx - 1, cy + 0), (cx + 3, cy + 0), (cx - 1, cy + 5)],
        fill=color,
    )


def draw_power_glyph(
    draw: ImageDraw.ImageDraw,
    left_x: int,
    bottom_y: int,
    power: str = "usb",
    battery_percent: int | None = None,
):
    """Mirror drawPowerGlyph() — plug on USB, cell on battery / charging."""
    glyph_w, glyph_h = 22, 14
    gx = left_x
    gy = bottom_y - glyph_h
    charging = power == "charging"
    # "usb" is VBUS; optional --battery-percent still prints beside the plug.
    on_usb = power == "usb"
    batt = power in ("battery", "charging") or battery_percent is not None
    pct = battery_percent
    if batt and pct is None and power != "usb":
        pct = 72
    low = pct is not None and pct <= 20
    color = COL_CRT_YELLOW if low else COL_DIM

    def draw_plug():
        bx, by = gx + 8, gy + 2
        draw.rectangle([gx + 1, by + 1, gx + 7, by + 2], fill=color)
        draw.rectangle([gx + 1, by + 6, gx + 7, by + 7], fill=color)
        draw.rounded_rectangle(
            [bx, by, bx + 11, by + 9], radius=2, fill=color
        )

    def draw_pct():
        if pct is None:
            return
        label = f"{min(100, max(0, pct))}%"
        draw_text(draw, label, gx + glyph_w + 4, gy, FONT2, color)

    # Match firmware: plug while on VBUS and not charging; cell otherwise.
    if on_usb and not charging:
        draw_plug()
        draw_pct()
        return

    if not batt:
        draw_plug()
        return

    bw, bh = 16, 10
    bx, by = gx, gy + 2
    draw.rounded_rectangle(
        [bx, by, bx + bw - 1, by + bh - 1], radius=2, outline=color
    )
    draw.rounded_rectangle(
        [bx + bw, by + 3, bx + bw + 1, by + 6], radius=1, fill=color
    )

    if pct is not None and pct > 0:
        inner_w = bw - 4
        fill_w = max(1, min(inner_w, (inner_w * pct + 50) // 100))
        draw.rounded_rectangle(
            [bx + 2, by + 2, bx + 1 + fill_w, by + bh - 3],
            radius=1,
            fill=color,
        )

    if charging:
        draw.rectangle(
            [bx + 5, by + 1, bx + 10, by + bh - 2], fill=COL_BG
        )
        draw_charge_bolt(draw, bx + bw // 2, by + bh // 2, color)

    draw_pct()


def render_no_host() -> Image.Image:
    """Mirror drawNetDiag() for reviewing its type scale and CMYK palette."""
    img = Image.new("RGB", (W, H), COL_CRT_BG)
    draw = ImageDraw.Draw(img)
    draw.rectangle(
        [PAD, PAD, W - PAD - 1, H - PAD - 1],
        outline=COL_CRT_DIM,
    )
    draw_centered(draw, "NO HOST", PAD + 10, FONT2, COL_CRT_YELLOW)

    x = PAD + 12
    xv = x + 6 * FONT2 * 6
    y = PAD + 42
    step = 20
    max_chars = (W - PAD - 4 - xv) // (6 * FONT2)

    def row(label: str, value: str, color):
        nonlocal y
        draw_text(draw, label, x, y, FONT2, COL_CRT_DIM)
        draw_text(draw, value[:max_chars], xv, y, FONT2, color)
        y += step

    row("WIFI", "Studio", COL_CRT)
    row("IP", "192.168.1.42  -58dBm", COL_CRT)
    row("HOST", "headroom.local:8787", COL_CRT)
    row("ADDR", "unresolved", COL_CRT_YELLOW)
    row("TOKEN", "set", COL_CRT)
    row("LAST", "never", COL_CRT_YELLOW)
    row("WHY", "connection refused", COL_CRT_YELLOW)

    draw_centered(
        draw,
        "START HOST ON MAC, THEN WAIT",
        H - PAD - 24,
        FONT2,
        COL_CRT_DIM,
    )
    return seal_edges(img, COL_CRT_BG)


def frame_device(panel: Image.Image, scale: int = 3) -> Image.Image:
    """Drop the selected panel into an AMOLED-style bezel."""
    panel = panel.resize((W * scale, H * scale), Image.Resampling.NEAREST)
    if ROUND_PANEL:
        bezel = 14 * scale
        outer = panel.width + bezel * 2
        canvas = Image.new("RGBA", (outer + 56, outer + 64), (0, 0, 0, 0))
        shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        ImageDraw.Draw(shadow).ellipse(
            [28, 32, 28 + outer - 1, 32 + outer - 1], fill=(0, 0, 0, 76)
        )
        canvas = Image.alpha_composite(canvas, shadow)

        device = Image.new("RGBA", (outer, outer), (0, 0, 0, 0))
        dd = ImageDraw.Draw(device)
        dd.ellipse([0, 0, outer - 1, outer - 1], fill=(8, 8, 8, 255))
        dd.ellipse(
            [bezel - 4, bezel - 4, outer - bezel + 3, outer - bezel + 3],
            fill=(0, 0, 0, 255),
        )
        mask = Image.new("L", panel.size, 0)
        ImageDraw.Draw(mask).ellipse(
            [0, 0, panel.width - 1, panel.height - 1], fill=255
        )
        face = panel.convert("RGBA")
        face.putalpha(mask)
        device.paste(face, (bezel, bezel), face)
        canvas.paste(device, (20, 16), device)
        return canvas

    bezel = 18 * scale
    radius = 28 * scale
    outer_w = panel.width + bezel * 2
    outer_h = panel.height + bezel * 2
    # Transparent canvas with soft desk shadow
    canvas = Image.new("RGBA", (outer_w + 40, outer_h + 48), (0, 0, 0, 0))
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle(
        [24, 28, 24 + outer_w, 28 + outer_h],
        radius=radius + 4,
        fill=(0, 0, 0, 70),
    )
    canvas = Image.alpha_composite(canvas, shadow)

    device = Image.new("RGBA", (outer_w, outer_h), (0, 0, 0, 0))
    dd = ImageDraw.Draw(device)
    dd.rounded_rectangle(
        [0, 0, outer_w - 1, outer_h - 1],
        radius=radius,
        fill=(8, 8, 8, 255),
    )
    dd.rounded_rectangle(
        [bezel - 4, bezel - 4, outer_w - bezel + 3, outer_h - bezel + 3],
        radius=radius - 8,
        fill=(0, 0, 0, 255),
    )
    # Panel with rounded clip
    mask = Image.new("L", panel.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, panel.width - 1, panel.height - 1],
        radius=12 * scale,
        fill=255,
    )
    rounded = Image.new("RGBA", panel.size)
    rounded.paste(panel.convert("RGBA"), (0, 0))
    rounded.putalpha(mask)
    device.paste(rounded, (bezel, bezel), rounded)
    canvas.paste(device, (16, 12), device)
    return canvas


def main():
    global W, H, PAD, ROUND_PANEL
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", help="Path to /usage JSON (glance state)")
    parser.add_argument("--out", help="Output PNG path")
    parser.add_argument(
        "--state",
        choices=("glance", "no-host"),
        default="glance",
        help="Panel state to render",
    )
    parser.add_argument(
        "--link-via",
        choices=("wifi", "usb"),
        default="wifi",
        help="Last successful glance connection",
    )
    parser.add_argument(
        "--link-error-minutes",
        type=int,
        help="Render a failed glance connection with this last-good age",
    )
    parser.add_argument(
        "--power",
        choices=("usb", "battery", "charging"),
        default="usb",
        help="Bottom-left power source (AXP2101): USB-only plug, or battery",
    )
    parser.add_argument(
        "--battery-percent",
        type=int,
        help="Battery fill %% when --power is battery/charging (default 72)",
    )
    parser.add_argument(
        "--demo-burndown",
        action="store_true",
        help="Shape screenshot-only burndown stories like the Apple previews",
    )
    parser.add_argument(
        "--write-shaped-fixture",
        metavar="PATH",
        help="Write /usage JSON with demo burndown curves and exit (no PNG)",
    )
    parser.add_argument(
        "--home-mode",
        choices=("daily", "history", "spend", "burndown"),
        default="daily",
        help="Home lower-pane mode to render",
    )
    parser.add_argument(
        "--glance-style",
        choices=("rings", "pace"),
        default="rings",
        help="Home upper-half glyph: quota rings, or the menu bar's pace mark",
    )
    parser.add_argument(
        "--panel",
        choices=("landscape", "round-466"),
        default="landscape",
        help="ESP32 display geometry to preview",
    )
    parser.add_argument("--raw", action="store_true", help="Skip device bezel")
    parser.add_argument("--scale", type=int, default=3)
    args = parser.parse_args()

    if args.panel == "round-466":
        W = H = 466
        ROUND_PANEL = True
        # Narrowest chord of the lower reading pane, matching roundBand()
        # in drawGlancePage. Helpers use PAD as that pane's left edge.
        PAD, _ = round_band(206, 397, 10)

    if args.write_shaped_fixture:
        if not args.input:
            parser.error("--input is required with --write-shaped-fixture")
        doc = json.loads(Path(args.input).read_text())
        shape_demo_usage_burndown(doc)
        out_path = Path(args.write_shaped_fixture)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(doc))
        print(f"wrote {out_path}")
        return

    if not args.out:
        parser.error("--out is required unless --write-shaped-fixture is set")

    if args.state == "no-host":
        panel = render_no_host()
    else:
        if not args.input:
            parser.error("--input is required for the glance state")
        doc = json.loads(Path(args.input).read_text())
        panel = render_glance(
            doc,
            link_via=args.link_via,
            link_error_minutes=args.link_error_minutes,
            demo_burndown=args.demo_burndown,
            power=args.power,
            battery_percent=args.battery_percent,
            home_mode=args.home_mode,
            glance_style=args.glance_style,
        )
    out = panel if args.raw else frame_device(panel, scale=args.scale)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    out.save(args.out)
    print(f"wrote {args.out} ({out.size[0]}×{out.size[1]})")


if __name__ == "__main__":
    main()
