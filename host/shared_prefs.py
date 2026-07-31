"""The settings that are the same person on every Mac, as one flat keyspace.

Multi-Mac sync needs to answer "which of these two values is newer" for each
individual setting. The stores it reads from are not shaped for that:
`sources.json` is three nested maps, `config.json` is a mixed bag of synced
preferences and machine-local paths. So this module projects both into one
dict of dotted keys with scalar values, and applies a dict of the same shape
back. Last-writer-wins then works per setting rather than per file — pinning
Codex above Claude on the laptop does not drag the laptop's accent colours
along with it.

    sources.enabled.<id>   bool
    sources.dismissed.<id> bool (True = Library; off-but-not-dismissed = paused)
    sources.accent.<id>    "#RRGGBB" or None (None = use the registry colour)
    sources.order          [id, ...]
    config.<key>           whatever config.json holds, or None when unset

Every key is always present with an explicit None for "unset", because a
missing key and a cleared one have to be told apart: clearing an accent on one
Mac must travel, and it can only travel as a value.

Nothing here decides *when* to sync or *where* — see icloud_sync.py. Stdlib
only.
"""

from __future__ import annotations

import app_config
import sources_config

ENABLED_PREFIX = "sources.enabled."
DISMISSED_PREFIX = "sources.dismissed."
ACCENT_PREFIX = "sources.accent."
ORDER_KEY = "sources.order"
CONFIG_PREFIX = "config."


def _source_ids():
    """Every source id this Mac knows, registry rows plus extra accounts."""
    return sorted(sources_config.enabled_map().keys())


def read():
    """This Mac's shared settings as {key: value}."""
    enabled = sources_config.enabled_map()
    dismissed = sources_config.dismissed_map()
    accents = sources_config.accent_overrides()
    out = {}
    for sid in _source_ids():
        out[ENABLED_PREFIX + sid] = bool(enabled.get(sid, False))
        out[DISMISSED_PREFIX + sid] = bool(
            dismissed.get(sid, not enabled.get(sid, False)))
        out[ACCENT_PREFIX + sid] = accents.get(sid)
    out[ORDER_KEY] = list(sources_config.order_ids())
    stored = app_config.shared_config()
    for key in app_config.SHARED_CONFIG_KEYS:
        out[CONFIG_PREFIX + key] = stored.get(key)
    return out


def _split(updates):
    enabled, dismissed, accents, config = {}, {}, {}, {}
    order = None
    for key, value in (updates or {}).items():
        if key == ORDER_KEY:
            if isinstance(value, list):
                order = [str(item) for item in value]
        elif key.startswith(ENABLED_PREFIX):
            if isinstance(value, bool):
                enabled[key[len(ENABLED_PREFIX):]] = value
        elif key.startswith(DISMISSED_PREFIX):
            if isinstance(value, bool):
                dismissed[key[len(DISMISSED_PREFIX):]] = value
        elif key.startswith(ACCENT_PREFIX):
            if value is None or isinstance(value, str):
                accents[key[len(ACCENT_PREFIX):]] = value
        elif key.startswith(CONFIG_PREFIX):
            config[key[len(CONFIG_PREFIX):]] = value
    return enabled, dismissed, accents, order, config


def apply(updates):
    """Write incoming settings. Returns the keys that actually landed.

    Ids this Mac does not have are dropped rather than stored: an extra login
    is a credential folder on someone's disk, so `claude:work` existing on the
    desktop says nothing about whether it can exist here. `set_enabled` and
    `set_accents` already filter to known ids — this reports what survived so
    the caller does not record a stamp for a setting it did not keep.
    """
    enabled, dismissed, accents, order, config = _split(updates)
    known = set(_source_ids())
    applied = []

    # Dismissed lands before enabled for the same reason the HTTP handler
    # orders them this way: enable-implies-tracked must win when a peer
    # switched a source on.
    if dismissed:
        keep = {k: v for k, v in dismissed.items() if k in known}
        if keep:
            sources_config.set_dismissed(keep)
            applied += [DISMISSED_PREFIX + k for k in keep]

    if enabled:
        keep = {k: v for k, v in enabled.items() if k in known}
        if keep:
            sources_config.set_enabled(keep)
            applied += [ENABLED_PREFIX + k for k in keep]

    if accents:
        keep = {}
        for sid, value in accents.items():
            if sid not in known:
                continue
            # A colour that fails validation on the way in would raise and
            # take the whole round with it. Drop the one key instead.
            if value is not None and sources_config.normalize_accent(value) is None:
                continue
            keep[sid] = value
        if keep:
            sources_config.set_accents(keep)
            applied += [ACCENT_PREFIX + k for k in keep]

    if order is not None:
        sources_config.set_order(order)
        applied.append(ORDER_KEY)

    if config:
        landed = app_config.set_shared_config(config)
        applied += [CONFIG_PREFIX + k for k in landed]

    return applied
