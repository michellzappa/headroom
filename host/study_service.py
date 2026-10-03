"""The usage study as a service the Mac app can ask.

`usage_study` and `study_card` are pure: they take logs or text and return
data. This module is the stateful half, and it owns four things:

  * **A cached scan.** A full pass over the session logs took 5.7 s on 1,470
    files. A request cannot wait that long, so the scan runs on a thread and
    requests get the last result. The log tree is fingerprinted with a stat
    per file (count, total size, newest mtime), and a change only triggers a
    rescan once MIN_RESCAN_S has passed. Without that floor an open session,
    whose file grows on every turn, would rescan on every poll.
  * **Your handle**, and the card built from it.
  * **Friends.** Cards you imported, each stored as the raw card text and
    decoded again on every read, so a hand-edited file cannot smuggle in
    anything `study_card.decode` would refuse. The alias is yours alone and
    never leaves this Mac.
  * **Peer shards.** The counts from your other Macs, so this Mac can show one
    person and not one machine. One file per Mac; a newer stamp replaces an
    older one. They arrive two ways: a file the person carries over, or the
    multi-Mac sync record (`icloud_sync`), which carries each Mac's shard
    beside its settings. Nothing here makes that call; the sync round hands
    shards in and takes this Mac's out.

Everything lives under ~/.headroom/study, mode 0600. Nothing here makes a
network call. Every route that reaches this is Class 1 (docs/trust.md).

Stdlib only.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from datetime import datetime

import claude_history
import machine_identity
import study_card
import usage_study

STUDY_DIR = os.path.expanduser("~/.headroom/study")
MIN_RESCAN_S = 300
MAX_FRIENDS = 200
MAX_SHARDS = 16
# A sync round asks for this Mac's shard every minute. A full scan reads the
# whole log tree, so a round only triggers one when the last is this old.
SYNC_RESCAN_S = 3600


class StudyError(ValueError):
    """The request was well formed but cannot be done (a 400 or 409)."""


def _atomic_write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(text)
    os.replace(tmp, path)


def _read_json(path, default):
    try:
        with open(path) as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return default
    return data if isinstance(data, type(default)) else default


def _shard_file(machine):
    """A file name from a machine id. Hashed, so a hostile id cannot be a path."""
    return hashlib.sha256(str(machine).encode()).hexdigest()[:24] + ".json"


def _fingerprint(root):
    """(files, total bytes, newest mtime): cheap, and enough to notice change."""
    count = size = newest = 0
    for base, _dirs, names in os.walk(root):
        for name in names:
            if not name.endswith(".jsonl"):
                continue
            try:
                st = os.stat(os.path.join(base, name))
            except OSError:
                continue
            count += 1
            size += st.st_size
            newest = max(newest, int(st.st_mtime))
    return (count, size, newest)


class StudyService:
    def __init__(self, log_root=None, state_dir=None, clock=time.time,
                 tz=None, machine=None, machine_name=None):
        self.log_root = log_root or claude_history.LOG_ROOT
        self.dir = state_dir or STUDY_DIR
        self._clock = clock
        self._tz = tz
        self._machine = machine or machine_identity.machine_id
        self._machine_name = machine_name or machine_identity.display_name
        self._lock = threading.Lock()
        self._thread = None
        self._own = None            # this Mac's profile, or None
        self._as_of = None
        self._scanned_at = 0.0
        self._seen = None           # fingerprint of the last scan
        self._error = None
        self._io = threading.Lock()  # read-modify-write on the state files

    # ---- paths ---------------------------------------------------------
    def _path(self, *parts):
        return os.path.join(self.dir, *parts)

    # ---- the scan ------------------------------------------------------
    def _scan(self, fingerprint):
        try:
            own = usage_study.profile(root=self.log_root, tz=self._tz)
            error = None
        except Exception as exc:          # a scan must never take down the host
            own, error = None, str(exc)
        with self._lock:
            self._own = own
            self._error = error
            self._seen = fingerprint
            self._scanned_at = self._clock()
            self._as_of = datetime.fromtimestamp(self._scanned_at).astimezone() \
                .isoformat(timespec="seconds")
            self._thread = None

    def _start_scan(self, fingerprint):
        """Begin a background scan unless one is running. Caller holds the lock."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._scan, args=(fingerprint,), daemon=True)
        self._thread.start()

    def refresh(self, wait=False):
        """Rescan now, whatever the fingerprint says. `wait` for tests and CLIs."""
        fp = _fingerprint(self.log_root)
        with self._lock:
            self._start_scan(fp)
            thread = self._thread
        if wait and thread is not None:
            thread.join()

    def _maybe_rescan(self, min_age=MIN_RESCAN_S):
        """Start a scan if there is none yet, or the logs changed and it is old.

        Peer shards are not part of this. They are merged from disk on every
        read, so importing one needs no rescan of the logs.
        """
        fp = _fingerprint(self.log_root)
        with self._lock:
            first = self._seen is None
            changed = fp != self._seen
            aged = self._clock() - self._scanned_at >= min_age
            if first or (changed and aged):
                self._start_scan(fp)

    # ---- shards --------------------------------------------------------
    def _load_shards(self):
        """Readable peer shards. A bad file is skipped, not fatal."""
        out = []
        folder = self._path("shards")
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            return out
        for name in names:
            shard = _read_json(os.path.join(folder, name), {})
            try:
                usage_study.from_shard(shard)
            except ValueError:
                continue
            out.append(shard)
        return out

    def _merged(self, own_shard):
        shards = self._load_shards()
        if own_shard is not None:
            shards.append(own_shard)
        return usage_study.merge(shards) if shards else None

    def import_shard(self, shard, synced=False):
        """Store the counts from another of your Macs.

        `synced` marks a shard that came in a sync record rather than a file.
        The next round brings it back anyway, so the window offers no Remove
        for it.
        """
        try:
            usage_study.from_shard(shard)
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise StudyError(f"not a usable shard: {exc}") from exc
        if shard["machine"] == self._machine():
            raise StudyError("that shard is from this Mac")
        key = _shard_file(shard["machine"])
        path = self._path("shards", key)
        clean = dict(shard)
        clean.pop("synced", None)
        if synced:
            clean["synced"] = True
        if isinstance(clean.get("name"), str):
            clean["name"] = study_card.sanitize_handle(clean["name"])
        with self._io:
            held = _read_json(path, {})
            if held and str(held.get("generated", "")) > str(shard["generated"]):
                raise StudyError("a newer shard from that Mac is already here")
            try:
                existing = os.listdir(self._path("shards"))
            except OSError:
                existing = []
            if key not in existing and len(existing) >= MAX_SHARDS:
                raise StudyError("too many Macs")
            _atomic_write(path, json.dumps(clean, separators=(",", ":"),
                                           sort_keys=True))
        return {"ok": True, "machine": shard["machine"]}

    def accept_synced(self, shards):
        """Store the shards that arrived in peer sync records.

        Best effort, one shard at a time: a bad or stale shard from one Mac
        must not cost the others. An unchanged shard is not rewritten, so an
        idle round touches no file. Returns the machine ids stored.
        """
        stored = []
        for shard in shards or []:
            if not isinstance(shard, dict):
                continue
            machine = shard.get("machine")
            if not isinstance(machine, str) or machine == self._machine():
                continue
            held = _read_json(self._path("shards", _shard_file(machine)), {})
            if held and str(held.get("generated", "")) >= str(
                    shard.get("generated", "")):
                continue
            try:
                self.import_shard(shard, synced=True)
            except StudyError:
                continue
            stored.append(machine)
        return stored

    def own_shard(self):
        """This Mac's counts for its sync record, or None. Never blocks.

        Stamped with the scan time, not the clock, so the record only changes
        when the counts do and an idle Mac writes nothing to iCloud.
        """
        self._maybe_rescan(min_age=SYNC_RESCAN_S)
        with self._lock:
            own, as_of = self._own, self._as_of
        if own is None or not as_of:
            return None
        return usage_study.to_shard(own, self._machine(), as_of,
                                    self._machine_name())

    def remove_shard(self, machine):
        try:
            with self._io:
                os.remove(self._path("shards", _shard_file(machine)))
        except OSError:
            raise StudyError("no such Mac") from None
        return {"ok": True}

    def export_shard(self):
        """This Mac's own counts, for importing on another Mac. Waits for a scan."""
        self.refresh(wait=True)
        with self._lock:
            own = self._own
        if own is None:
            raise StudyError("no usage found on this Mac")
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        return usage_study.to_shard(own, self._machine(), now,
                                    self._machine_name())

    # ---- handle and card -----------------------------------------------
    def _state(self):
        return _read_json(self._path("state.json"), {})

    def handle(self):
        card_id = study_card.own_card_id(self._path("card_id"))
        chosen = study_card.sanitize_handle(self._state().get("handle"))
        return chosen or study_card.generated_handle(card_id)

    def set_handle(self, text):
        if text is not None and not isinstance(text, str):
            raise StudyError("handle must be text")
        clean = study_card.sanitize_handle(text) if text else None
        if text and not clean:
            raise StudyError("that name has nothing visible in it")
        with self._io:
            state = self._state()
            state["handle"] = clean
            _atomic_write(self._path("state.json"),
                          json.dumps(state, sort_keys=True))
        return {"ok": True, "handle": self.handle()}

    # ---- friends -------------------------------------------------------
    def _friends_file(self):
        """Stored friends, minus any row that is not shaped like one.

        The file is on disk where anything can edit it, so a row that is not an
        object is dropped here instead of crashing every caller downstream.
        """
        raw = _read_json(self._path("friends.json"), {})
        return {k: v for k, v in raw.items()
                if isinstance(k, str) and isinstance(v, dict)}

    def _save_friends(self, friends):
        _atomic_write(self._path("friends.json"),
                      json.dumps(friends, separators=(",", ":"), sort_keys=True))

    def add_friend(self, text):
        """Import a card. The only input is the card text; decode judges it."""
        try:
            card = study_card.decode(text)
        except study_card.CardError as exc:
            raise StudyError(str(exc)) from exc
        if card["id"] == study_card.own_card_id(self._path("card_id")):
            raise StudyError("that is your own card")
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        with self._io:
            friends = self._friends_file()
            held = friends.get(card["id"])
            if held is None and len(friends) >= MAX_FRIENDS:
                raise StudyError("too many friends")
            friends[card["id"]] = {
                "card": text.strip(),
                "alias": (held or {}).get("alias"),
                "added": (held or {}).get("added", now),
                "updated": now,
            }
            self._save_friends(friends)
            row = friends[card["id"]]
        return {"ok": True, "updated": held is not None,
                "friend": self._friend_view(card["id"], row)}

    def set_alias(self, friend_id, alias):
        if not isinstance(friend_id, str):
            raise StudyError("no such friend")
        if alias is not None and not isinstance(alias, str):
            raise StudyError("alias must be text")
        clean = study_card.sanitize_handle(alias) if alias else None
        if alias and not clean:
            raise StudyError("that name has nothing visible in it")
        with self._io:
            friends = self._friends_file()
            if friend_id not in friends:
                raise StudyError("no such friend")
            friends[friend_id]["alias"] = clean
            self._save_friends(friends)
            row = friends[friend_id]
        return {"ok": True, "friend": self._friend_view(friend_id, row)}

    def remove_friend(self, friend_id):
        if not isinstance(friend_id, str):
            raise StudyError("no such friend")
        with self._io:
            friends = self._friends_file()
            if friends.pop(friend_id, None) is None:
                raise StudyError("no such friend")
            self._save_friends(friends)
        return {"ok": True}

    def _friend_view(self, friend_id, row):
        """A friend as the app shows them. Decoded again, never trusted stored."""
        try:
            card = study_card.decode(row.get("card"))
        except study_card.CardError:
            return None
        alias = study_card.sanitize_handle(row.get("alias"))
        return {"id": friend_id, "alias": alias, "handle": card["handle"],
                "name": alias or card["handle"], "week": card["week"],
                "data": card["data"], "added": row.get("added"),
                "updated": row.get("updated")}

    # ---- the snapshot --------------------------------------------------
    def snapshot(self):
        """What GET /study returns. Never blocks on a scan."""
        self._maybe_rescan()
        card_id = study_card.own_card_id(self._path("card_id"))
        with self._lock:
            own, as_of, error = self._own, self._as_of, self._error
            scanning = self._thread is not None
            seen = self._seen is not None
        views = [v for v in (self._friend_view(fid, row)
                             for fid, row in self._friends_file().items()) if v]
        views.sort(key=lambda v: v["name"].lower())
        out = {"ok": True, "status": "scanning" if scanning and not seen
               else "ready", "as_of": as_of, "error": error,
               "handle": self.handle(), "friends": views,
               "machines": self._machine_rows(own)}
        if not seen:
            return {**out, "insights": None, "card": None, "card_text": None,
                    "payload": None}
        own_shard = None
        if own is not None:
            own_shard = usage_study.to_shard(
                own, self._machine(), as_of or "", self._machine_name())
        merged = self._merged(own_shard)
        if merged is None:
            return {**out, "status": "empty", "insights": None, "card": None,
                    "card_text": None, "payload": None}
        card = study_card.build(merged, card_id, self.handle())
        return {**out, "insights": usage_study.insights(merged),
                "card": card, "card_text": study_card.encode(card),
                "payload": usage_study.payload(merged)}

    def _machine_rows(self, own):
        rows = [{"id": self._machine(), "name": self._machine_name(),
                 "this_mac": True, "has_usage": own is not None}]
        for shard in self._load_shards():
            rows.append({"id": shard["machine"],
                         "name": shard.get("name") or "Another Mac",
                         "this_mac": False, "has_usage": True,
                         "synced": shard.get("synced") is True,
                         "generated": shard.get("generated")})
        return rows


_service = None
_service_lock = threading.Lock()


def get():
    global _service
    with _service_lock:
        if _service is None:
            _service = StudyService()
        return _service


def reset_for_tests():
    global _service
    with _service_lock:
        _service = None
