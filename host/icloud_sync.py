"""Multi-Mac: one file per machine in a folder that syncs.

Headroom's headline numbers are already shared without any of this — quota is
account-scoped, so two Macs polling the same login read the same percentages
off the provider. What does not travel is everything *around* those numbers:
which sources you enabled, the order you pinned them in, the colours you gave
them, and the fact that the other Mac is sitting there with three servers up
and an agent waiting on you.

## Why a folder and not CloudKit

The data lives in this Python host, not in the Swift app. CloudKit would mean
mirroring it into the app, out to iCloud, back down, and into the host again —
a sync daemon written twice — plus a Developer ID provisioning profile in a
signing pipeline that does not currently have one. `NSUbiquitousKeyValueStore`
is documented as App Store distribution only, and Headroom ships notarized off
GitHub. A folder both machines can see costs none of that and stays stdlib.

## Why it never conflicts

**A machine writes only its own file and only ever reads the others.** No file
has two writers, so iCloud has nothing to make a conflict copy of. There is no
shared document to reconcile, no merge on the write path, and no ordering
problem: each Mac derives its own view at read time and is allowed to reach a
different one. That is also the answer to a Mac that has been asleep for a
week — it is not "behind", it just knows less, and says so with a timestamp.

Settings still need a winner when two Macs disagree, and get one per setting
rather than per file (see shared_prefs.py). Each key carries the wall-clock
time it last changed, and the newest stamp wins. Clock skew between two Macs
signed into the same iCloud account is seconds; the settings in question are
changed by hand, minutes or months apart. Ties keep the local value, so a
round that adopts nothing writes nothing.

## What is deliberately not synced

Credentials, of any kind, ever — `shared_prefs` reaches config.json through a
whitelist in app_config, so the host token cannot be written here even by
accident. Also not synced: anything describing one machine's disk or moment —
`dev_root`, `codex_binary`, local servers, git commits, the Claude token log,
attention events. Those are *reported* per machine and shown with an owner,
never merged. A merged list of local servers would be a fiction.

One exception is added up, not merged: `study`, this Mac's usage-study shard
(counts from its Claude Code session logs, see study_service.py). Usage on two
Macs is disjoint sessions, so the sum is the honest total. It rides in the
record and is handed back to the caller; it never reaches `/usage`, because
`_peer_view` copies a whitelist.

Stdlib only.
"""

from __future__ import annotations

import json
import os
import threading
import time

import app_config
import machine_identity
import shared_prefs

STATE_PATH = os.path.expanduser("~/.headroom/icloud_state.json")
DEFAULT_DIR = os.path.expanduser(
    "~/Library/Mobile Documents/com~apple~CloudDocs/Headroom")
MACHINES_SUBDIR = "machines"

# How often a round runs. Settings are changed by hand and peers are read to
# be *looked at*, so this is about how fast a second Mac feels current, not
# about data loss. A minute keeps iCloud from seeing constant churn.
TICK_S = 60
# Past this, a peer is drawn as stale rather than live.
STALE_S = 10 * 60
# Past this, a peer is not shown at all — a Mac you stopped using should fall
# off the list on its own rather than needing a file deleted by hand.
FORGET_S = 30 * 24 * 3600
# A beacon is a few KB. Anything this size is not one of ours.
MAX_FILE_BYTES = 512 * 1024

# What `probe()` found, in words a person can act on. `denied` is the one that
# matters: it is what a folder inside iCloud Drive gives a host that has not
# been granted Full Disk Access, and it is invisible from the publish side —
# writing the beacon keeps working, so every Mac looks like it is syncing.
TROUBLE_DETAIL = {
    "denied": (
        "macOS is blocking the host from reading this folder. Folders inside "
        "iCloud Drive need Full Disk Access. Choose a folder outside iCloud "
        "Drive, or grant the host Full Disk Access."
    ),
    "unreadable": "The host could not read this folder.",
}

_lock = threading.Lock()
_peers = []          # last successful read, for the /usage document
_last_write = {"payload": None, "at": 0.0}


# ----------------------------------------------------------------- paths


def mode():
    """Which transport carries this Mac's record.

    CloudKit by default, because the obvious-looking alternative does not work:
    a folder in iCloud Drive lives under `~/Library/Mobile Documents`, which is
    TCC-protected, and a LaunchAgent host can create files there but is refused
    `listdir`. Every Mac publishes, none can enumerate, and all report no peers.

    `icloud_dir` opts into the folder anyway, which is the right answer for a
    directory that is *not* TCC-protected — Dropbox, Syncthing, a mounted
    share. Setting it to a path inside iCloud Drive is the one combination that
    will disappoint, and `probe()` says so rather than letting it look fine.
    """
    if not app_config.icloud_sync_enabled():
        return "off"
    return "folder" if app_config.icloud_dir() else "cloudkit"


def root_dir():
    """The sync folder, or None unless this Mac is in folder mode."""
    if mode() != "folder":
        return None
    return app_config.icloud_dir() or DEFAULT_DIR


def machines_dir():
    root = root_dir()
    return os.path.join(root, MACHINES_SUBDIR) if root else None


def _own_path():
    folder = machines_dir()
    if not folder:
        return None
    return os.path.join(folder, f"{machine_identity.machine_id()}.json")


# ------------------------------------------------------------ local state


def _load_state():
    """{'mirror': {key: value}, 'stamps': {key: epoch}} as last reconciled."""
    try:
        with open(STATE_PATH) as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    mirror = data.get("mirror")
    stamps = data.get("stamps")
    return {
        "mirror": mirror if isinstance(mirror, dict) else {},
        "stamps": stamps if isinstance(stamps, dict) else {},
    }


def _save_state(state):
    folder = os.path.dirname(STATE_PATH)
    os.makedirs(folder, exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as handle:
        json.dump(state, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(tmp, STATE_PATH)


def _stamp(stamps, key):
    try:
        return float(stamps.get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0


# ------------------------------------------------------------------ peers


def _read_peer(path):
    """Parse one peer file, or None if it is not readable as one.

    iCloud may leave a `.<name>.icloud` placeholder where a file's contents
    have been evicted, and a file mid-download reads as truncated JSON. Both
    mean "that machine is not visible right now", which is the same as absent
    and never an error worth surfacing.
    """
    try:
        if os.path.getsize(path) > MAX_FILE_BYTES:
            return None
        with open(path) as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("id"), str):
        return None
    return data


def probe(folder=None):
    """Why the peer folder cannot be read, or None when it can.

    Worth its own function because the failure this exists to catch is not one
    a caller can infer. `~/Library/Mobile Documents` is TCC-protected: a host
    without Full Disk Access may create and write files there quite happily and
    still be refused `listdir`. Every Mac then publishes into a folder none of
    them can enumerate, each reports zero peers, and "sync is on and finding
    nobody" looks exactly like "the other Mac has not synced yet".
    """
    folder = folder or machines_dir()
    if not folder:
        return "off"
    if not os.path.isdir(folder):
        # Nothing has published here yet, including us. Not an error.
        return None
    try:
        os.listdir(folder)
    except PermissionError:
        return "denied"
    except OSError:
        return "unreadable"
    return None


def _read_peers(now):
    """Every machine file in the folder. Filtering and ages are `_dated`'s job."""
    folder = machines_dir()
    if not folder or not os.path.isdir(folder):
        return []
    out = []
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return []
    for name in names:
        if not name.endswith(".json") or name.startswith("."):
            continue
        data = _read_peer(os.path.join(folder, name))
        if data is not None:
            out.append(data)
    return out


# ------------------------------------------------------------------ merge


def _reconcile(local, state, peers, now):
    """Stamp local edits, pick winners from peers. Returns (updates, stamps).

    Order matters and is the whole correctness argument: local edits are
    detected *before* anything is applied, so a value this Mac normalizes on
    the way in — a pinned order naming a provider that only exists on the
    other Mac — is not mistaken for a fresh local edit on the next round and
    bounced back. That is what stops two Macs trading the same setting
    forever.
    """
    mirror = state["mirror"]
    stamps = dict(state["stamps"])

    if not mirror:
        # First round on this Mac, and last-writer-wins has nothing to work
        # with: both machines' settings are equally "unstamped", so a plain
        # merge would leave each one sitting on its own defaults. Which is
        # exactly backwards from what a second Mac is for.
        #
        # So the first round is a bootstrap rather than a merge, and which one
        # it is depends on what is already in the folder. Joining a folder
        # that has machines in it means adopting them wholesale — that is the
        # whole point of opening Headroom on a new Mac. Being the first
        # machine there means this config is the one being published, so it is
        # stamped now and will win over the next Mac's untouched defaults.
        joining = any(
            isinstance(peer.get("stamps"), dict) and peer["stamps"]
            for peer in peers
        )
        for key in local:
            stamps.setdefault(key, 0.0 if joining else now)
    else:
        for key, value in local.items():
            if key not in mirror:
                # A key this build did not have last round: a new source, or a
                # login just added. Nobody has chosen it here yet.
                stamps.setdefault(key, 0.0)
            elif mirror[key] != value:
                stamps[key] = now

    updates = {}
    winners = {}
    for peer in peers:
        peer_prefs = peer.get("prefs")
        peer_stamps = peer.get("stamps")
        if not isinstance(peer_prefs, dict) or not isinstance(peer_stamps, dict):
            continue
        for key, value in peer_prefs.items():
            at = _stamp(peer_stamps, key)
            # Strictly newer, so a tie leaves the local value alone and a
            # round that changes nothing stays a no-op.
            if at <= _stamp(stamps, key) or at <= winners.get(key, 0.0):
                continue
            if key in local and local[key] == value:
                # Same answer, newer stamp. Take the stamp so the two Macs
                # stop re-offering it, but there is nothing to write.
                stamps[key] = at
                continue
            winners[key] = at
            updates[key] = value
    return updates, winners, stamps


# ------------------------------------------------------------------ write


def _write_own(record, now):
    """Publish this machine's file, skipping the write when nothing changed.

    A sync-on-close folder would otherwise see an identical file rewritten
    every minute, which is a minute of upload traffic for nothing. `updated` is
    excluded from the comparison precisely so an idle Mac goes quiet — a peer's
    age is then real rather than a heartbeat, and staleness in the UI means
    what it says.
    """
    path = _own_path()
    if not path:
        return False
    payload = dict(record)
    body = json.dumps(
        {k: v for k, v in payload.items() if k != "updated"}, sort_keys=True)
    if body == _last_write["payload"]:
        return False
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as handle:
            json.dump(payload, handle, sort_keys=True)
        os.replace(tmp, path)
    except OSError:
        # iCloud Drive turned off, disk full, folder gone. Sitting the round
        # out is right: the host's own job does not depend on this.
        return False
    _last_write.update(payload=body, at=now)
    return True


# ------------------------------------------------------------------- round


def _own_record(beacon, prefs, stamps, now, study=None):
    """This machine's published record. The same shape down either transport.

    A CloudKit record and a file on disk carry identical bytes on purpose: the
    merge cannot tell which one it came from, and neither can a peer.
    """
    payload = dict(beacon or {})
    payload.update(machine_identity.describe())
    payload["prefs"] = prefs
    payload["stamps"] = stamps
    if study is not None:
        payload["study"] = study
    payload["updated"] = now
    return payload


def sync(peers, beacon=None, now=None, study=None):
    """One merge round against `peers`. Transport-neutral.

    Both transports land here. The folder reads its peers off disk; CloudKit
    hands over what the Mac app fetched from the private database. Everything
    that decides *what* travels and *who wins* lives on this side of that line,
    so choosing a transport never means re-proving the merge.

    Returns the record this machine should publish, plus what it adopted.
    `study` is this Mac's usage shard to publish; `studies` in the result are
    the peers' shards, for the caller to hand to study_service.
    """
    now = time.time() if now is None else now
    peers = _dated(peers or [], now)
    state = _load_state()
    local = shared_prefs.read()
    updates, winners, stamps = _reconcile(local, state, peers, now)

    adopted = []
    if updates:
        adopted = shared_prefs.apply(updates)
        for key in adopted:
            stamps[key] = winners.get(key, now)
        # Re-read rather than assuming: `apply` normalizes, and the mirror has
        # to hold what is really stored or the next round reads the difference
        # as a local edit.
        local = shared_prefs.read()

    reconciled = {"mirror": local, "stamps": stamps}
    if reconciled != state:
        _save_state(reconciled)

    with _lock:
        _peers[:] = peers
    return {
        "adopted": adopted,
        "peers": [_peer_view(peer, now) for peer in peers],
        "record": _own_record(beacon, local, stamps, now, study),
        "studies": [peer["study"] for peer in peers
                    if isinstance(peer.get("study"), dict)],
    }


def _dated(peers, now):
    """Drop peers that are junk or long gone, and stamp each with its age."""
    out = []
    own = machine_identity.machine_id()
    for peer in peers:
        if not isinstance(peer, dict) or not isinstance(peer.get("id"), str):
            continue
        if peer["id"] == own:
            continue
        try:
            updated = float(peer.get("updated") or 0.0)
        except (TypeError, ValueError):
            updated = 0.0
        if updated <= 0 or now - updated > FORGET_S:
            continue
        peer = dict(peer)
        peer["_age_s"] = max(0.0, now - updated)
        out.append(peer)
    out.sort(key=lambda row: row["_age_s"])
    return out


def cloud_round(records, beacon=None, now=None, study=None):
    """CloudKit round: peers in, this machine's record out.

    The Mac app owns the CloudKit half because only an entitled process can
    reach it — `~/Library/Mobile Documents` is TCC-protected and a LaunchAgent
    daemon cannot get a grant for it, which is the whole reason this transport
    exists. So the app fetches changed records, posts them here, and saves back
    whatever this returns. It carries bytes and holds no opinion about them.
    """
    result = sync(records, beacon=beacon, now=now, study=study)
    result["ok"] = True
    return result


def tick(beacon=None, now=None, study=None):
    """Folder round, driven by the host's own loop.

    A no-op in CloudKit mode: there the app drives the schedule, because it is
    the half that can hear a push.
    """
    now = time.time() if now is None else now
    if root_dir() is None or mode() != "folder":
        if not app_config.icloud_sync_enabled():
            with _lock:
                _peers.clear()
        return {"enabled": False, "peers": 0, "adopted": []}

    # A callable, resolved only past the mode check: building the shard can
    # start a scan of the session logs, which a Mac with sync off never needs.
    if callable(study):
        study = study()
    result = sync(_read_peers(now), beacon=beacon, now=now, study=study)
    record = result["record"]
    wrote = _write_own(record, now)
    return {
        "enabled": True,
        "peers": len(result["peers"]),
        "adopted": result["adopted"],
        "wrote": wrote,
        "studies": result["studies"],
    }


# --------------------------------------------------------------- payload


def _peer_view(peer, now):
    """One machine as the /usage document carries it."""
    age = int(peer.get("_age_s") or 0)
    out = {
        "id": peer.get("id"),
        "name": peer.get("name") or "Mac",
        "self": False,
        "age_s": age,
        "stale": age > STALE_S,
    }
    for key in ("host_version", "providers", "servers", "attention_open",
                "attention_top", "agent", "board"):
        if peer.get(key) is not None:
            out[key] = peer[key]
    return out


def configuration(now=None):
    """What Settings shows and edits: the switch, the folder, who is out there.

    `available` is the honest half. iCloud Drive being switched off in System
    Settings is not an error this can fix or should hide — the toggle still
    works, it just will not find anyone, and saying so beats an empty list that
    looks like a bug.
    """
    now = time.time() if now is None else now
    how = mode()
    enabled = how != "off"
    folder = app_config.icloud_dir() or ""
    with _lock:
        peers = list(_peers)
    trouble = probe() if how == "folder" else None
    return {
        "ok": True,
        "enabled": enabled,
        "mode": how,
        "directory": folder,
        "available": not folder or os.path.isdir(os.path.dirname(folder)),
        "machine": machine_identity.describe(),
        "peers": [_peer_view(peer, now) for peer in peers],
        "trouble": trouble,
        "trouble_detail": TROUBLE_DETAIL.get(trouble),
    }


def machines_payload(beacon=None, now=None):
    """`machines[]` for /usage: this Mac first, then peers by how fresh.

    Present even when sync is off — a one-Mac install still gets its own row,
    so the Swift side has one shape to decode and the UI can say "just this
    Mac" instead of nothing at all.
    """
    now = time.time() if now is None else now
    own = dict(beacon or {})
    own.update(machine_identity.describe())
    own.update({"self": True, "age_s": 0, "stale": False})
    with _lock:
        peers = list(_peers)
    return [own] + [_peer_view(peer, now) for peer in peers]
