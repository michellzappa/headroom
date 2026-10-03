"""Per-model token pricing for Claude Code and Codex usage accounting.

Rates are USD per 1,000,000 tokens. Cache rates follow Anthropic's caching
economics: cache read ~= 0.1x base input, 5m cache write ~= 1.25x base input,
1h cache write ~= 2x base input. The base input/output numbers are the sticker
rates from the model catalog; adjust here if intro pricing applies to you.

One source of truth: the display firmware never computes cost, it only renders
the numbers this server produces.

Rates last checked against the published model catalog on 2026-07-25. These
drift — when a `by_model` cost looks wrong, check this table first, and update
RATES_CHECKED so the next reader knows how stale it is.
"""

RATES_CHECKED = "2026-07-25"

# base_input / base_output per 1M tokens
BASE = {
    "claude-opus-5": (5.00, 25.00),
    "claude-mythos-5": (10.00, 50.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-opus-4-6": (5.00, 25.00),
    "claude-fable-5": (10.00, 50.00),
    "claude-sonnet-5": (3.00, 15.00),   # intro $2/$10 through 2026-08-31 — edit if you qualify
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}

# Fallback for an unrecognized model id: treat as Sonnet-tier so cost is
# approximate rather than zero. Logged models outside BASE are rare.
_DEFAULT = (3.00, 15.00)

CACHE_READ_MULT = 0.10
CACHE_WRITE_5M_MULT = 1.25
CACHE_WRITE_1H_MULT = 2.00


def is_known(model):
    """True when this model has rates here rather than the Sonnet-tier default.

    The fallback is the right default — a new model costing something
    approximate beats it costing zero. What it cannot do is tell you it
    happened, and an unrecognised Opus priced as a Sonnet is wrong by 40% while
    looking exactly as confident as a correct figure.

    So anything that prints a dollar amount asks this first. A percentage
    nobody audits can absorb a silent guess; a number someone compares against
    a card statement cannot. See docs/metering.md decision 4.
    """
    if model in BASE:
        return True
    return any(model and model.startswith(key) for key in BASE)


def _base(model):
    if model in BASE:
        return BASE[model]
    # Coarse family match so future dated snapshots still price sensibly.
    for key, rate in BASE.items():
        if model and model.startswith(key):
            return rate
    return _DEFAULT


def cost_usd(model, *, input_tokens=0, output_tokens=0,
             cache_read=0, cache_write_5m=0, cache_write_1h=0):
    """Return the USD cost of one usage record's token counts."""
    base_in, base_out = _base(model)
    per_tok_in = base_in / 1_000_000
    per_tok_out = base_out / 1_000_000
    return (
        input_tokens * per_tok_in
        + output_tokens * per_tok_out
        + cache_read * per_tok_in * CACHE_READ_MULT
        + cache_write_5m * per_tok_in * CACHE_WRITE_5M_MULT
        + cache_write_1h * per_tok_in * CACHE_WRITE_1H_MULT
    )


# ---------------------------------------------------------------- OpenAI
#
# Codex models, for the usage study. Standard tier, USD per 1M tokens:
# (input, cached input, output). OpenAI prices cached input per model rather
# than as a fixed multiple, so it is a column here, not a multiplier.
#
# Checked against https://developers.openai.com/api/docs/pricing. Unlike the
# Claude table there is no fallback: a Codex model missing here is unpriced
# and reported as such, because no OpenAI tier is a safe default for another
# (luna and astra differ 100x).

OPENAI_RATES_CHECKED = "2026-10-03"

OPENAI = {
    "gpt-6-astra": (10.00, 1.00, 50.00),
    "gpt-6.1-sol": (2.00, 0.10, 10.00),
    "gpt-6-sol": (2.00, 0.20, 10.00),
    "gpt-6-luna": (0.10, 0.01, 0.50),
    "gpt-5.6-sol": (4.00, 0.40, 20.00),
    "gpt-5.6-terra": (2.00, 0.20, 12.00),
    "gpt-5.6-luna": (0.20, 0.02, 1.20),
}


def openai_rates(model):
    """(input, cached, output) per 1M for a Codex model id, or None."""
    if model in OPENAI:
        return OPENAI[model]
    # Dated snapshots ("gpt-6-luna-2026-09-01") price as their model. Longest
    # key first, so "gpt-6.1-sol" never matches as "gpt-6".
    for key in sorted(OPENAI, key=len, reverse=True):
        if model and model.startswith(key + "-"):
            return OPENAI[key]
    return None


def openai_cost_usd(model, *, input_tokens=0, cached_input=0, output_tokens=0):
    """USD for one Codex turn, or None when the model has no rates here.

    `input_tokens` is fresh input only, without the cached part.
    """
    rates = openai_rates(model)
    if rates is None:
        return None
    rate_in, rate_cached, rate_out = rates
    return (input_tokens * rate_in + cached_input * rate_cached
            + output_tokens * rate_out) / 1_000_000
