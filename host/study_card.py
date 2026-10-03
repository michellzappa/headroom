"""The stats card: the one thing about your usage that you hand to someone else.

A card is the coarse payload from usage_study plus a handle, the ISO week it
was made, and a random card id. That is all. It is what a friend imports, and
later what a public board or a study could read, so its shape is a contract:
change it additively and bump `CARD_VERSION` when it cannot be.

Two things about the design that are not obvious:

  * **A card is untrusted input, and `decode` is the only way in.** A friend's
    handle ends up on your menu bar, and possibly on the phone and the board.
    So decode is strict: unknown keys are refused, every share must be a number
    between 0 and 1 on a bin this code knows, the handle is stripped of control
    and bidi characters and cut to length, and the whole card has a size cap.
    Anything wrong raises `CardError`. Nothing is coerced.

  * **A card is not signed.** Two strangers share no secret, and the standard
    library has no public-key signatures. The `card_id` is random and lives on
    the sender's Mac so an updated card can *replace* the old one instead of
    adding a second friend. It says "same sender as before" and nothing about
    who the sender is. Treat a card like a friend's message: you know who sent
    it because of how it reached you.

The card is separate from any study identity on purpose. Reusing one id in both
places would join a named friend list to anonymous behaviour data.

Stdlib only.

    python3 host/study_card.py                       # make a card from your logs
    python3 host/study_card.py --handle nightowl
    python3 host/study_card.py --merge laptop.json   # add another Mac's shard
    python3 host/study_card.py --decode hrc1.…       # read a friend's card
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import unicodedata
import uuid
from datetime import datetime

import usage_study

# Version 2 lets a card's model shares name Codex models as well as Claude's
# four families. A card whose models are all Claude's still goes out as
# version 1, byte for byte what an older build expects, so a friend who has
# not updated can read it. Only a card that needs version 2 uses it, and an
# older build refuses that one as "a newer version", which is true.
CARD_VERSION = 2
VERSIONS = (1, 2)
PREFIX = "hrc%d." % CARD_VERSION
MAX_ENCODED = 8192          # the real card is about 1 KB; this is a ceiling
HANDLE_MAX = 24
CARD_ID_PATH = os.path.expanduser("~/.headroom/study/card_id")

_WEEK = re.compile(r"^\d{4}-W(0[1-9]|[1-4]\d|5[0-3])$")
_ID = re.compile(r"^[0-9a-f]{32}$")

# What each share map is allowed to be keyed by. Derived from usage_study so a
# new bin there cannot drift from what a card will accept.
_BINS = {
    "prompt_len_share": {str(e) for e in usage_study.PROMPT_EDGES},
    "out_per_turn_share": {str(e) for e in usage_study.OUT_EDGES},
    "session_active_min_share": {str(e) for e in usage_study.SESSION_EDGES},
    "time_of_day_share": {name for name, _, _ in usage_study.TIME_BLOCKS},
}
_MODELS = set(usage_study.FAMILIES) | {usage_study.OTHER}
# Model bins one week of a version 2 card may carry. Real use is a handful.
MAX_MODEL_BINS = 16
_DATA_KEYS = set(_BINS) | {"cache_hit_bucket_pct", "weekly_model_share"}
_CARD_KEYS = {"v", "id", "handle", "week", "data"}

_ADJ = ("amber", "brisk", "calm", "dusky", "eager", "feral", "gentle",
        "hazy", "idle", "jolly", "keen", "lucid", "mellow", "nimble", "odd",
        "plucky", "quiet", "restless", "sly", "tidy", "urban", "vivid",
        "wry", "zesty")
_NOUN = ("badger", "comet", "drifter", "egret", "fox", "gecko", "heron",
         "ibis", "jackal", "kestrel", "lynx", "marmot", "newt", "otter",
         "plover", "quokka", "raven", "stoat", "tapir", "urchin", "vole",
         "wombat", "yak", "zebra")


class CardError(ValueError):
    """The card is malformed, oversized, or carries something we do not accept."""


def sanitize_handle(raw):
    """A handle a menu bar can safely draw, or None if nothing usable is left.

    Drops control, format (zero-width and bidi override), surrogate,
    private-use and unassigned characters, folds compatibility forms, collapses
    whitespace, and cuts to HANDLE_MAX. It does not try to judge the words.
    """
    if not isinstance(raw, str):
        return None
    text = unicodedata.normalize("NFKC", raw)
    text = "".join(" " if ch in "\t\n\r" else _keep(ch) for ch in text)
    text = " ".join(text.split())[:HANDLE_MAX].strip()
    return text or None


def _keep(ch):
    """The character itself, or '' when it is invisible or a control."""
    if ch == " ":
        return ch
    return "" if unicodedata.category(ch)[0] in "CZ" else ch


def generated_handle(card_id):
    """A stable, harmless name from the card id: 'quiet-otter'."""
    n = int(card_id, 16)
    return f"{_ADJ[n % len(_ADJ)]}-{_NOUN[(n // len(_ADJ)) % len(_NOUN)]}"


def own_card_id(path=None):
    """This Mac's card id, minted once. Random, and unrelated to the hardware."""
    path = path or CARD_ID_PATH
    try:
        with open(path) as handle:
            value = handle.read().strip()
        if _ID.match(value):
            return value
    except OSError:
        pass
    value = uuid.uuid4().hex
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(value + "\n")
    return value


def build(prof, card_id, handle=None, now=None):
    """A card from a profile. `now` is a datetime, so tests need no clock."""
    now = now or datetime.now().astimezone()
    year, week, _ = now.isocalendar()
    data = {k: v for k, v in usage_study.payload(prof).items()
            if k in _DATA_KEYS}
    models = {m for row in data.get("weekly_model_share", {}).values()
              for m in row}
    return {
        "v": 1 if models <= _MODELS else CARD_VERSION,
        "id": card_id,
        "handle": sanitize_handle(handle) or generated_handle(card_id),
        "week": f"{year}-W{week:02d}",
        "data": data,
    }


def encode(card):
    raw = json.dumps(card, separators=(",", ":"), sort_keys=True)
    return ("hrc%d." % card["v"]
            + base64.urlsafe_b64encode(raw.encode()).decode().rstrip("="))


def _model_bin(key):
    """Is `key` a model a version 2 card may name? Claude's or a Codex id."""
    return key in _MODELS or bool(usage_study.CODEX_FAMILY.match(key))


def _share_map(name, value, allowed):
    if not isinstance(value, dict):
        raise CardError(f"{name} must be an object")
    for key, share in value.items():
        if key not in allowed:
            raise CardError(f"{name}: unknown bin {key!r}")
        if isinstance(share, bool) or not isinstance(share, (int, float)) \
                or not 0 <= share <= 1:
            raise CardError(f"{name}: share for {key!r} is not in 0..1")


def _check_data(data, version=1):
    if not isinstance(data, dict) or set(data) - _DATA_KEYS:
        raise CardError("data has unknown keys")
    for name, allowed in _BINS.items():
        _share_map(name, data.get(name, {}), allowed)
    cache = data.get("cache_hit_bucket_pct")
    if cache is not None and (isinstance(cache, bool)
                              or not isinstance(cache, int)
                              or cache not in range(0, 101, 10)):
        raise CardError("cache_hit_bucket_pct must be 0, 10 ... 100")
    weeks = data.get("weekly_model_share", {})
    if not isinstance(weeks, dict) or len(weeks) > usage_study.PAYLOAD_WEEKS:
        raise CardError("weekly_model_share has too many weeks")
    for week, row in weeks.items():
        if not isinstance(week, str) or not _WEEK.match(week):
            raise CardError(f"bad week {week!r}")
        if version == 1:
            _share_map("weekly_model_share", row, _MODELS)
            continue
        if isinstance(row, dict) and len(row) > MAX_MODEL_BINS:
            raise CardError("weekly_model_share has too many models")
        _share_map("weekly_model_share", row,
                   {k for k in (row if isinstance(row, dict) else {})
                    if isinstance(k, str) and _model_bin(k)})


def decode(text):
    """Text -> card dict, or CardError. The only way a foreign card gets in."""
    if not isinstance(text, str):
        raise CardError("card must be text")
    text = text.strip()
    if len(text) > MAX_ENCODED:
        raise CardError("card is too large")
    prefix = next((f"hrc{v}." for v in VERSIONS
                   if text.startswith(f"hrc{v}.")), None)
    if prefix is None:
        raise CardError("not a Headroom card, or a newer version")
    body = text[len(prefix):]
    try:
        raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
        card = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise CardError("card is not readable") from exc
    if not isinstance(card, dict) or set(card) != _CARD_KEYS:
        raise CardError("card has the wrong fields")
    if card["v"] not in VERSIONS or f"hrc{card['v']}." != prefix:
        raise CardError("unsupported card version")
    if not isinstance(card["id"], str) or not _ID.match(card["id"]):
        raise CardError("bad card id")
    if not isinstance(card["week"], str) or not _WEEK.match(card["week"]):
        raise CardError("bad week")
    handle = sanitize_handle(card["handle"])
    _check_data(card["data"], card["v"])
    return {"v": card["v"], "id": card["id"],
            "handle": handle or generated_handle(card["id"]),
            "week": card["week"], "data": card["data"]}


def describe(card):
    """A short readable view of a card. Safe for a terminal: the handle is clean."""
    d = card["data"]
    out = [f"{card['handle']}  ·  {card['week']}"]

    def line(label, share):
        if share:
            out.append(f"  {label:9s} " + "  ".join(
                f"{k} {v:.0%}" for k, v in sorted(
                    share.items(),
                    key=lambda kv: (len(kv[0]), kv[0]))))
    line("prompts", d.get("prompt_len_share"))
    line("replies", d.get("out_per_turn_share"))
    line("sessions", d.get("session_active_min_share"))
    line("day", d.get("time_of_day_share"))
    if d.get("cache_hit_bucket_pct") is not None:
        out.append(f"  cache hit  {d['cache_hit_bucket_pct']}%+")
    weeks = d.get("weekly_model_share") or {}
    if weeks:
        last = sorted(weeks)[-1]
        out.append(f"  models {last}: " + "  ".join(
            f"{k} {v:.0%}" for k, v in weeks[last].items()))
    return "\n".join(out)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--handle", help="name to show on your card")
    parser.add_argument("--root", default=None)
    parser.add_argument("--merge", metavar="SHARD", nargs="+", default=[],
                        help="add shards exported from your other Macs")
    parser.add_argument("--decode", metavar="CARD",
                        help="read a card someone sent you ('-' for stdin)")
    args = parser.parse_args(argv)

    if args.decode:
        text = sys.stdin.read() if args.decode == "-" else args.decode
        try:
            print(describe(decode(text)))
        except CardError as exc:
            print(f"refused: {exc}")
            return 1
        return 0

    import machine_identity
    now = datetime.now().astimezone()
    prof = usage_study.combined(
        machine_identity.machine_id(), now.isoformat(timespec="seconds"),
        args.merge, root=args.root)
    if prof is None:
        print("no usage found")
        return 1
    card = build(prof, own_card_id(), args.handle, now)
    print(describe(card))
    print("\nshare this card (nothing has been sent):\n")
    print(encode(card))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
