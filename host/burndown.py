"""Burndown + time-to-exhaustion forecast for one quota pool.

The gauge every other tool shows ("62%") answers a question nobody actually
asks continuously. The useful question is whether the budget survives the
window, which needs three things: where an even spend would put you (free —
that is `pace_pct`), where you actually are (free — the current reading), and
the shape of how you got here (not free — that is `quota_samples`).

Everything here is expressed in *remaining* percent so the chart reads as a
burndown: 100 at the window start, 0 at exhaustion, and a straight ideal line
between the window's start and its reset. Actual below ideal means burning
faster than even pace.

The forecast is a least-squares fit over the trailing slice of the current
window, extended to the x-axis. Deliberately linear: with a few hundred samples
per window, anything fancier fits noise, and a wrong confident forecast is
worse than an honest rough one. When the fit has too little to work with, every
forecast field comes back None and the caller shows history only.

Series are emitted as [[t, remaining_pct], ...] pairs rather than objects. At
48 points per pool that is the difference between ~600 bytes and ~1.5KB, which
matters because this rides the same document the ESP32 pulls over USB CDC.

One source of truth: the firmware and the menu bar draw these numbers, they
never compute them. Stdlib only.
"""

from __future__ import annotations

import bisect
import time
from datetime import datetime

import cache_util
import oauth_usage
import quota_samples

# Points per emitted series. Enough to see the shape on a 368px panel.
DEFAULT_POINTS = 48
# A fit needs at least this many samples spanning at least this much time
# before it is allowed to claim a forecast.
MIN_FIT_SAMPLES = 3
MIN_FIT_SPAN_S = 20 * 60
# Trailing slice used for the fit. Floor keeps a 5h session from fitting on
# minutes of noise; ceiling lets a month-long Cursor cycle see more than a
# day of history (Cursor's meter often jumps, then sits flat for >24h).
FIT_LOOKBACK_MIN_S = 3600
FIT_LOOKBACK_MAX_S = 7 * 24 * 3600
# Remaining-percent gap that counts as meaningfully off even pace.
DEFICIT_PCT = 5.0
# How far back the forgiven curve reaches. Matches the overview chart's
# lookback, since that is the only chart wide enough to draw it.
PREVIOUS_LOOKBACK_S = 3 * 24 * 3600
# How far back `history` reaches. The overview chart's 3.5-day lookback plus
# half a day of slack, so its left edge is always fed rather than starting
# mid-air. Not the full retention window: this rides the same document the
# board pulls over CDC, and nothing draws a domain wider than that week.
HISTORY_LOOKBACK_S = 4 * 24 * 3600

STATUS_OK = "ok"
STATUS_AHEAD = "ahead"
STATUS_CRITICAL = "critical"
# Already spent. Distinct from `critical` because there is nothing left to act
# on: the UI desaturates it rather than raising an alarm.
STATUS_EXHAUSTED = "exhausted"


def _round(value, digits=1):
    return None if value is None else round(value, digits)


def _wire_reset(event):
    """Burndown `resets[]` row — additive fields only when present."""
    out = {
        "t": int(event["t"]),
        "kind": event.get("kind") or "granted",
    }
    if event.get("forgiven_pct") is not None:
        out["forgiven_pct"] = round(float(event["forgiven_pct"]), 2)
    if event.get("source"):
        out["source"] = event["source"]
    if event.get("tweet_url"):
        out["tweet_url"] = event["tweet_url"]
    if event.get("tweet_id"):
        out["tweet_id"] = str(event["tweet_id"])
    return out


def _rate_unit(window_s):
    """Points-per-hour reads sensibly for a 5h window; per-day does not."""
    return "hour" if window_s <= 24 * 3600 else "day"


def _rate_seconds(unit):
    return 3600.0 if unit == "hour" else 86400.0


def _fmt_rate(value):
    """Rates span three orders of magnitude across pools; keep them legible."""
    if value is None:
        return None
    if value >= 10:
        return f"{value:.0f}"
    if value >= 1:
        return f"{value:.1f}"
    # Sub-1 rates need two places to say anything, but "0.20" reads worse
    # than "0.2" in a sentence.
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _spare(value):
    """Pace delta as a signed reading, in the same unit as everything else.

    The old wording ran `abs()` over a signed number and called the result
    "4 points", so a pool 4 behind read exactly like a pool 4 ahead. It also
    spelled percentage points as "points", which collides with the credits and
    premium requests the providers themselves bill in. Percent, with the
    direction attached, or nothing.
    """
    if value is None or abs(value) < 1:
        return None
    if value >= 1:
        return f"{value:.0f}% to spare"
    return f"{abs(value):.0f}% over"


def _slope_per_s(points):
    """Least-squares slope (remaining pct per second). None when degenerate."""
    n = len(points)
    if n < 2:
        return None
    mean_t = sum(p[0] for p in points) / n
    mean_r = sum(p[1] for p in points) / n
    numerator = sum((t - mean_t) * (r - mean_r) for t, r in points)
    denominator = sum((t - mean_t) ** 2 for t, _ in points)
    if denominator <= 0:
        return None
    return numerator / denominator


def _downsample(points, limit, start, end, keep=None):
    """Thin `points` to at most `limit`, evenly by time, keeping both ends.

    The ends are pinned because they carry meaning the middle does not: the
    newest point is the current reading, and the oldest is where the window
    opened. Letting slot rounding swallow either one leaves a curve that starts
    or stops short of the axis it is drawn against.

    `keep` pins interior timestamps for the same reason — see
    `_boundary_samples`. Pinned points come out of the same budget rather than
    on top of it, because this rides the document the board pulls over CDC and
    a series that grows with how often a pool reset is the one that grows on
    the machine least able to carry it.
    """
    if len(points) <= limit:
        return list(points)
    pinned = [p for p in points if p[0] in keep] if keep else []
    budget = max(2, limit - len(pinned))
    span = max(1.0, float(end - start))
    slots = {}
    for t, r in points:
        idx = int(min(budget - 1, max(0, (t - start) / span * (budget - 1))))
        slots[idx] = (t, r)  # last write per slot wins
    out = sorted(set(slots.values()) | set(pinned), key=lambda p: p[0])
    oldest = min(points, key=lambda p: p[0])
    if out and out[0][0] != oldest[0]:
        out.insert(0, oldest)
    newest = max(points, key=lambda p: p[0])
    if out and out[-1][0] != newest[0]:
        out.append(newest)
    return out


def _boundary_samples(points, cuts):
    """Timestamps of the two samples flanking each cut — a riser's two ends.

    A reset is a vertical climb, and the only two readings that say how tall
    it is are the last one of the spent window and the first one of the new
    window. Even thinning does not know that: on four days of history at 48
    points a slot is two hours wide, so the pair either side of a boundary
    comes out two hours apart and the climb is drawn as a diagonal — two hours
    of imaginary slow refill, right where the chart should read as a step.
    Pinning both ends keeps the riser vertical and at its true height.
    """
    times = [t for t, _ in points]
    keep = set()
    for cut in cuts:
        index = bisect.bisect_left(times, cut)
        if index > 0:
            keep.add(times[index - 1])
        if index < len(times):
            keep.add(times[index])
    return keep


def _when(timestamp, tz, now):
    """Short human time for a forecast instant, in the user's timezone."""
    if timestamp is None:
        return None
    try:
        moment = datetime.fromtimestamp(timestamp, tz)
        today = datetime.fromtimestamp(now, tz).date()
    except (OverflowError, OSError, ValueError):
        return None
    delta_days = (moment.date() - today).days
    if delta_days <= 0:
        return moment.strftime("today %H:%M")
    if delta_days == 1:
        return moment.strftime("tomorrow %H:%M")
    if delta_days < 7:
        return moment.strftime("%a %H:%M")
    return moment.strftime("%b %-d")


def _headline(remaining, resets_at_label, status, burn_rate, allowance,
              exhausts_label, delta, unit, rate_source=None):
    """One short line stating the situation. This is the product.

    Both times in here are clock form ("Thu 14:00"), never duration form
    ("4d 44m"). Prose says *when*; the compact surfaces that draw `resets_in`
    say *how long*. Mixing the two inside one sentence is what produced
    "58% left · 4d 44m. Out tomorrow 04:18" — two time facts, two shapes, one
    line. See docs/glossary.md, "Telling time".
    """
    if status == STATUS_EXHAUSTED:
        return (f"Exhausted · resets {resets_at_label}" if resets_at_label
                else "Exhausted")

    left = f"{remaining:.0f}% left"
    head = f"{left} · resets {resets_at_label}" if resets_at_label else left
    # An estimate off token history is worth marking; a measured rate is not.
    estimated = rate_source == "estimated"
    rate = f"~{_fmt_rate(burn_rate)}" if estimated else _fmt_rate(burn_rate)

    if status == STATUS_CRITICAL and exhausts_label and burn_rate:
        return f"{head}. Out {exhausts_label} · {rate}%/{unit}"
    if burn_rate is not None and allowance is not None and status == STATUS_AHEAD:
        return f"{head}. Burn {rate} vs {_fmt_rate(allowance)}%/{unit}"
    if delta is not None and burn_rate is not None:
        spare = _spare(delta)
        return f"{head}. On pace · {spare}" if spare else f"{head}. On pace"
    return f"{head}. Collecting history"


def _verdict(status, resets_in_label, exhausts_label, delta, has_forecast):
    """The one line answering "do I make it to the reset, and if not, when".

    Deliberately not a sentence. It fills a slot next to a stat row rather than
    carrying the numbers itself, so it never repeats what the row already
    shows — that duplication is what made the old copy read as a paragraph.
    `headline` stays the prose version for VoiceOver.

    This is the ~25-byte string the board draws, so it is the one place
    duration form survives: `resets_in_label` is "4d 44m", because a glance
    wants how long, not a weekday it then has to subtract from today. The two
    forms never share a line here — each branch returns one time fact.
    """
    if status == STATUS_EXHAUSTED:
        return f"Spent, back in {resets_in_label}" if resets_in_label else "Spent"
    if status == STATUS_CRITICAL:
        return (f"Runs out {exhausts_label}" if exhausts_label
                else "Runs out before reset")
    if not has_forecast:
        return "Collecting history"
    if status == STATUS_AHEAD:
        # "Ahead of pace" cuts both ways to a casual reader — ahead of schedule
        # sounds like good news. "Over" only means the one thing.
        return "Over pace"
    # Paired with "Over pace": one axis, one noun. The headline says the same
    # two words for the same two states, so a reader moving between the board
    # and the Mac is not learning a second vocabulary for one fact.
    if delta is not None and delta >= 1:
        return f"On pace · {delta:.0f}%"
    return "On pace"


def compute(provider, pool, payload, *, now=None, points=DEFAULT_POINTS,
            tz=None, rows=None, prior_pct_per_day=None):
    """Burndown for one pool, or None when the pool has no usable reading.

    `rows` overrides the sample lookup (tests, and callers batching one read).
    Pass the pool's whole log, oldest first — not a pre-filtered window.

    `prior_pct_per_day` is a burn estimate derived from token history, used
    only while the window is too fresh to fit a slope. Measured samples always
    win once they exist, and anything resting on the prior is marked
    `rate_source: "estimated"` so the copy can hedge honestly.
    """
    now = time.time() if now is None else float(now)
    reading = quota_samples.extract(provider, pool, payload)
    if reading is None:
        return None

    window_s = reading["window_s"]
    resets_in_s = reading["resets_in_s"]
    used_pct = reading["pct"]
    remaining = max(0.0, 100.0 - used_pct)

    # The pool's whole log, not just this window: the series below selects by
    # sample *time* inside the window anyway — including rows stamped with a
    # forked window_start from resets_in jitter, which equality on the label
    # alone used to hide behind a flat "today only" line — and the grants the
    # chart marks are boundaries between windows, so they need both sides.
    if rows is None:
        rows = quota_samples.read(provider=provider, pool=pool)
    window_start, window_end = quota_samples.window_for(
        now, window_s, resets_in_s, pct=used_pct,
        previous=(rows[-1] if rows else None),
    )

    # The axis ends on the held reset, so the countdown has to come from the
    # same number. Raw `resets_in_s` decays against the clock loosely enough on
    # some sources that reading it straight is how a "6d 23h to reset" caption
    # ends up over an axis that runs out two days earlier.
    resets_in_s = max(0, int(window_end - now))

    # Even-spend position right now. Equivalent to 100 - pace_pct, kept in
    # remaining space so it lines up with the chart.
    pace = oauth_usage.pace_pct(resets_in_s, window_s)
    ideal_remaining = 100.0 - pace if pace is not None else None
    delta = (remaining - ideal_remaining) if ideal_remaining is not None else None

    # Resets granted out of band, oldest first. A scheduled roll is already
    # drawn by the axis; these are the ones that would otherwise look like the
    # chart forgetting yesterday. `rolls_for` also keeps them in a durable
    # journal so the heatmap outlives the sample window. Codex week additionally
    # merges the public announcement feed so history reaches past what this Mac
    # happened to observe.
    announced = None
    if provider == "codex" and pool == "week":
        import codex_resets
        announced = codex_resets.events()
    resets = quota_samples.rolls_for(
        provider, pool, rows, now=now, announced=announced)
    # Wire shape stays additive: drop null forgiven_pct rather than sending it,
    # and only include tweet fields when present so older clients ignore them.
    resets = [_wire_reset(event) for event in resets]

    history_floor = now - HISTORY_LOOKBACK_S
    # A provider can refill a quota counter without moving its scheduled
    # reset. Keep the scheduled window/ideal line intact, but treat the latest
    # refill inside it as the start of the live burn. Otherwise the regression
    # sees the upward jump, reports 0%/day, and projects a flat line to reset.
    boundaries = quota_samples.boundaries(rows, since=history_floor)
    live_start = max(
        [window_start]
        + [t for t in boundaries if window_start < t <= int(now)]
    )

    # Clipped to the current quota epoch, so the curve can never span a reset.
    # Samples from the other side of one describe a budget that no longer
    # exists, even when the provider kept the scheduled window label.
    series = [
        (int(row["t"]), max(0.0, 100.0 - float(row["pct"])))
        for row in rows
        if row.get("t") is not None and row.get("pct") is not None
        and live_start <= int(row["t"]) <= window_end
    ]
    series.sort(key=lambda p: p[0])
    # The live reading is newer than the newest persisted bucket.
    if not series or series[-1][0] < int(now) - quota_samples.BUCKET_S:
        series.append((int(now), remaining))

    # The run a grant cut short, for a window that only exists because one
    # landed. Emitted apart from `actual` rather than joined to it: this budget
    # is spent and gone, so nothing may fit a slope through it or measure pace
    # against it. It is drawn faint, and only here, because a window that
    # simply ran out needs no explaining — putting a ghost behind every rolled
    # session would double the strokes on the overview to say nothing.
    forgiven = []
    if resets and abs(resets[-1]["t"] - window_start) <= quota_samples.BUCKET_S:
        # Cut on the grant's own sample, not on `window_start`: the start is
        # derived from a live `resets_in_s` and lands a few seconds off the
        # bucket, which is enough to pull the new window's first reading into
        # the ghost and flatten its last point back up to 100.
        grant_t = resets[-1]["t"]
        # Stop at the grant before this one. Reaching further back splices two
        # windows into one curve, and the ghost then *rises* where the earlier
        # grant refilled it — a burndown that climbs reads as a bug, and the
        # older run was not what this reset forgave anyway.
        floor = grant_t - PREVIOUS_LOOKBACK_S
        if len(resets) > 1:
            floor = max(floor, resets[-2]["t"])
        forgiven = sorted(
            (int(row["t"]), max(0.0, 100.0 - float(row["pct"])))
            for row in rows
            if row.get("t") is not None and row.get("pct") is not None
            and floor <= int(row["t"]) < grant_t
        )
        if forgiven:
            forgiven = _downsample(
                forgiven, points, forgiven[0][0], forgiven[-1][0])

    # Every reading in the recent past, window boundaries and all.
    #
    # `actual` is clipped to the live window because that is what the budget
    # diagonal is drawn against, and a curve that spanned a reset would be
    # measured against a budget that no longer exists. But the moment a window
    # rolls that clip leaves a single point, and a chart with one point draws
    # nothing — which is why spending a reset credit looks like the app
    # forgetting the week you just had.
    #
    # This is the same readings unclipped: a sawtooth that climbs at every
    # reset, because that is what happened. Nothing fits a slope through it and
    # nothing measures pace against it. Consumers split it at `boundaries` so
    # no stroke spans a boundary, and draw it faint behind `actual`.
    history = sorted(
        (int(row["t"]), max(0.0, 100.0 - float(row["pct"])))
        for row in rows
        if row.get("t") is not None and row.get("pct") is not None
        and int(row["t"]) >= history_floor
    )
    # Where that sawtooth climbs. `resets` holds only the grants, which are
    # the rare kind — a Claude session rolling on schedule is not in there,
    # and drawing its riser from `resets` alone is what left a plain restart
    # looking like a two-hour refill. `boundaries` also catches a provider
    # refill that leaves the scheduled window label unchanged.
    if history:
        history = _downsample(history, points, history[0][0], now,
                              keep=_boundary_samples(history, boundaries))

    # --- forecast -------------------------------------------------------
    lookback = max(FIT_LOOKBACK_MIN_S, min(FIT_LOOKBACK_MAX_S, window_s // 3))
    # Never reach back past the window's own start. A slice that straddles a
    # reset fits a line through a vertical jump *upward*, which reads as a
    # slope of zero and hides the burn that came after it.
    recent = [p for p in series if p[0] >= max(now - lookback, window_start)]
    span = (recent[-1][0] - recent[0][0]) if len(recent) >= 2 else 0
    slope = None
    if len(recent) >= MIN_FIT_SAMPLES and span >= MIN_FIT_SPAN_S:
        slope = _slope_per_s(recent)

    unit = _rate_unit(window_s)
    per = _rate_seconds(unit)
    burn_rate = None      # remaining-pct consumed per `unit`
    exhausts_at = None
    exhausts_in_s = None
    projected = []
    rate_source = None
    # Fall back to the token-history estimate only while there is no fit.
    rate_per_s = None
    if slope is not None:
        rate_source = "measured"
        rate_per_s = -slope if slope < 0 else 0.0
    elif prior_pct_per_day and prior_pct_per_day > 0:
        rate_source = "estimated"
        rate_per_s = float(prior_pct_per_day) / 86400.0

    if rate_per_s is not None:
        burn_rate = rate_per_s * per
        # Draw the projection only as far as the reset; past that it is moot.
        end = window_end
        if rate_per_s > 0:
            exhausts_in_s = remaining / rate_per_s
            exhausts_at = int(now + exhausts_in_s)
            end = min(exhausts_at, window_end)
        # A flat pace still lands somewhere — level, at the reset — and saying
        # so is not the same as saying nothing. An absent line reads as "no
        # forecast yet", which is the one thing a measured zero is not.
        if end > now:
            projected = [[int(now), round(remaining, 2)],
                         [int(end),
                          round(max(0.0, remaining - rate_per_s * (end - now)),
                                2)]]

    units_left = max(resets_in_s, 1) / per
    allowance = remaining / units_left if units_left > 0 else None
    # Inside the last rate-unit the ratio runs away: 91% with an hour left is
    # not a "2184%/day budget". Past the size of the pool itself the number has
    # stopped saying anything, so drop it and let the headline fall through to
    # points-to-spare, which stays true right up to the reset.
    if allowance is not None and allowance > 100.0:
        allowance = None
    exhausted = remaining <= 0
    if exhausted:
        # Forecasting the exhaustion of an already-exhausted pool is noise.
        exhausts_at = None
        exhausts_in_s = None
        projected = []
    exhausts_before_reset = bool(
        exhausts_in_s is not None and exhausts_in_s < resets_in_s)

    if exhausted:
        status = STATUS_EXHAUSTED
    elif exhausts_before_reset:
        status = STATUS_CRITICAL
    elif delta is not None and delta <= -DEFICIT_PCT:
        status = STATUS_AHEAD
    else:
        status = STATUS_OK

    # Duration for the compact surfaces that draw `resets_in`, clock form for
    # the prose. Same instant, two readings, and which one a surface gets is a
    # rule rather than an accident — docs/glossary.md, "Telling time".
    resets_label = oauth_usage.fmt_resets(resets_in_s)
    resets_at_label = _when(window_end, tz, now)
    exhausts_label = _when(exhausts_at, tz, now)

    return {
        "provider": provider,
        "pool": pool,
        "window_start": window_start,
        "window_end": window_end,
        "window_s": window_s,
        "now": int(now),
        "remaining_pct": _round(remaining),
        "used_pct": _round(used_pct),
        "ideal_remaining_pct": _round(ideal_remaining),
        "delta_pct": _round(delta),
        "in_deficit": bool(delta is not None and delta < 0),
        "exhausted": exhausted,
        "status": status,
        "resets_in_s": resets_in_s,
        "resets_in": resets_label,
        # [[epoch_s, remaining_pct], ...] — ideal is a straight line, so two
        # points is the whole of it.
        "ideal": [[window_start, 100.0], [window_end, 0.0]],
        "resets": resets,
        # The burn a grant wiped out, drawn faint behind the live curve. Empty
        # unless this window began with one. Superseded by `history`, which
        # covers the same ground without needing a grant to exist — kept on the
        # wire because a phone or a board can be a year behind this host.
        "forgiven": [[t, round(r, 2)] for t, r in forgiven],
        # The unclipped sawtooth behind `actual` — see where it is built.
        # Spans window boundaries on purpose; square it off at `boundaries`
        # before stroking.
        "history": [[t, round(r, 2)] for t, r in history],
        # Every instant `history` climbs, scheduled rolls included. Draw the
        # riser here, not at `resets` — that list is grants only, so a pool
        # that simply rolled had no cut to square against and came out as a
        # diagonal across whatever the thinning left. Bare epochs, because
        # nothing labels these: a window ending on time is not news, it is
        # only geometry.
        "boundaries": boundaries,
        # Thinned across the range that actually has samples, not across the
        # whole window — a window we only joined halfway through would
        # otherwise spend most of the point budget on empty time.
        "actual": [[t, round(r, 2)]
                   for t, r in _downsample(series, points, series[0][0], now)],
        "projected": projected,
        # Both rates are in `rate_unit`, which tracks the window length: a 5h
        # session is points-per-hour, a weekly window is points-per-day.
        "rate_unit": unit,
        # "measured" from real samples, "estimated" from token history, or
        # None when there is nothing to go on yet.
        "rate_source": rate_source,
        "burn_rate_pct": _round(burn_rate, 2),
        "allowance_pct": _round(allowance, 2),
        "exhausts_at": exhausts_at,
        "exhausts_in_s": None if exhausts_in_s is None else int(exhausts_in_s),
        "exhausts_in": (None if exhausts_in_s is None
                        else oauth_usage.fmt_resets(exhausts_in_s)),
        "exhausts_before_reset": exhausts_before_reset,
        "samples": len(series),
        # Prose, for VoiceOver and any surface with a full line to give it.
        "headline": _headline(remaining, resets_at_label, status, burn_rate,
                              allowance, exhausts_label, delta, unit,
                              rate_source),
        # The same situation as a slot: a short phrase that sits above a stat
        # row instead of restating it.
        "verdict": _verdict(status, resets_label, exhausts_label, delta,
                            rate_source is not None),
    }


def compute_all(state, *, now=None, points=DEFAULT_POINTS, tz=None,
                priors=None):
    """Burndowns for every configured pool: {provider: {pool: {...}}}.

    Reads the sample log once per pool. Sources that are off, unconfigured, or
    failing simply do not appear. `priors` maps provider id to a %/day burn
    estimate used only where samples are too thin to fit.

    A source stuck on a stale reading drops out too, once past the blip
    window. Everything this returns is measured from *now* — the countdown, the
    pace delta, the time to exhaustion — so computing it against a reading from
    last night does not produce a slightly old chart, it produces a confident
    wrong one. No chart, plus the staleness the sources list already reports,
    is the honest version.
    """
    now = time.time() if now is None else float(now)
    priors = priors or {}
    out = {}
    for spec in quota_samples.POOLS:
        payload = (state or {}).get(spec.provider)
        if not cache_util.trusted(payload, now):
            continue
        try:
            result = compute(spec.provider, spec.pool, payload,
                             now=now, points=points, tz=tz,
                             prior_pct_per_day=priors.get(spec.provider))
        except Exception as exc:  # a chart must never break the poll tick
            print(f"burndown {spec.provider}.{spec.pool} error:", exc)
            continue
        if result is not None:
            out.setdefault(spec.provider, {})[spec.pool] = result
    return out


def primary(burndowns):
    """The pool most worth showing, ranked by how actionable it is.

    About to run out beats already out: an exhausted pool is a fact you can
    only wait out, while a critical one is still a decision. Ties break toward
    the shorter window, since a 5h session running dry is more actionable than
    a weekly window drifting.
    """
    candidates = [
        result
        for pools in (burndowns or {}).values()
        for result in pools.values()
    ]
    if not candidates:
        return None

    rank = {STATUS_CRITICAL: 0, STATUS_EXHAUSTED: 1,
            STATUS_AHEAD: 2, STATUS_OK: 3}

    def key(result):
        return (
            rank.get(result.get("status"), 4),
            result.get("exhausts_in_s") if result.get("exhausts_in_s") is not None
            else float("inf"),
            result.get("delta_pct") if result.get("delta_pct") is not None
            else float("inf"),
            result.get("window_s") or 0,
        )

    return min(candidates, key=key)
