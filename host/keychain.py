"""Read and write Keychain secrets without putting them in argv.

`security add-generic-password -w <secret>` publishes the secret in the process
table for the life of the call — any process on the machine can `ps` it. That
matters here because the thing being written is a refreshed Claude OAuth token.

Reads go through SecItemCopyMatching for the same reason the write path does:
shelling out to `/usr/bin/security` puts the shared system binary on the ACL
instead of this process, which is a broader grant and a worse prompt.

Uses the Security framework through ctypes (stdlib) rather than the deprecated
SecKeychain* C API. Stdlib only.

`synchronizable=True` is what lets this find the PATs the app now stores in
iCloud Keychain, and it is not optional for those: synchronizable items are a
separate keyspace, so a query that omits `kSecAttrSynchronizable` defaults to
`false` and returns "not found" for an item that is plainly there. The value
used is `kSecAttrSynchronizableAny`, which spans both halves — a Mac still
holding a local-only copy from before the app migrated it must keep working.

The unentitled host reaching a synchronizable item is not a given and was
checked on macOS 15 before this shipped: plain `SecItemCopyMatching` finds
them, with no `kSecUseDataProtectionKeychain` needed.

`allow_ui=False` sets `kSecUseAuthenticationUIFail` so a locked or ACL-gated
item returns `errSecInteractionNotAllowed` instead of popping SecurityAgent.
First-run probes and background polls that must not interrupt use that;
Claude's one-shot foreign-item import keeps the default (`allow_ui=True`).
"""

from __future__ import annotations

import ctypes
import ctypes.util

_CF_PATH = ctypes.util.find_library("CoreFoundation")
_SEC_PATH = ctypes.util.find_library("Security")

ERR_SEC_SUCCESS = 0
ERR_SEC_ITEM_NOT_FOUND = -25300
ERR_SEC_USER_CANCELED = -128
ERR_SEC_AUTH_FAILED = -25293
ERR_SEC_INTERACTION_NOT_ALLOWED = -25308
_CF_STRING_ENCODING_UTF8 = 0x08000100


class KeychainError(OSError):
    """A Security.framework call returned a non-zero OSStatus."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def _load():
    """Return (CoreFoundation, Security) with argtypes set, or raise."""
    if not _CF_PATH or not _SEC_PATH:
        raise KeychainError("CoreFoundation/Security not available")
    cf = ctypes.CDLL(_CF_PATH, use_errno=True)
    sec = ctypes.CDLL(_SEC_PATH, use_errno=True)

    cf.CFStringCreateWithBytes.restype = ctypes.c_void_p
    cf.CFStringCreateWithBytes.argtypes = [
        ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long,
        ctypes.c_uint32, ctypes.c_bool,
    ]
    cf.CFDataCreate.restype = ctypes.c_void_p
    cf.CFDataCreate.argtypes = [
        ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long,
    ]
    cf.CFDictionaryCreate.restype = ctypes.c_void_p
    cf.CFDictionaryCreate.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p), ctypes.c_long,
        ctypes.c_void_p, ctypes.c_void_p,
    ]
    cf.CFDictionaryGetValue.restype = ctypes.c_void_p
    cf.CFDictionaryGetValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    cf.CFDataGetLength.restype = ctypes.c_long
    cf.CFDataGetLength.argtypes = [ctypes.c_void_p]
    cf.CFDataGetBytePtr.restype = ctypes.c_void_p
    cf.CFDataGetBytePtr.argtypes = [ctypes.c_void_p]
    cf.CFStringGetCString.restype = ctypes.c_bool
    cf.CFStringGetCString.argtypes = [
        ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32,
    ]
    cf.CFGetTypeID.restype = ctypes.c_ulong
    cf.CFGetTypeID.argtypes = [ctypes.c_void_p]
    cf.CFDictionaryGetTypeID.restype = ctypes.c_ulong
    cf.CFDictionaryGetTypeID.argtypes = []
    cf.CFDataGetTypeID.restype = ctypes.c_ulong
    cf.CFDataGetTypeID.argtypes = []
    cf.CFRelease.restype = None
    cf.CFRelease.argtypes = [ctypes.c_void_p]

    sec.SecItemAdd.restype = ctypes.c_int32
    sec.SecItemAdd.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    sec.SecItemUpdate.restype = ctypes.c_int32
    sec.SecItemUpdate.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    sec.SecItemCopyMatching.restype = ctypes.c_int32
    sec.SecItemCopyMatching.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
    ]
    return cf, sec


def _const(lib, name):
    """Read an exported CFStringRef global (kSecClass, kSecAttrService, …)."""
    return ctypes.c_void_p.in_dll(lib, name).value


def _cfstr(cf, value):
    raw = value.encode("utf-8")
    ref = cf.CFStringCreateWithBytes(
        None, raw, len(raw), _CF_STRING_ENCODING_UTF8, False)
    if not ref:
        raise KeychainError("could not encode keychain attribute")
    return ref


def _cfdata(cf, raw):
    ref = cf.CFDataCreate(None, raw, len(raw))
    if not ref:
        raise KeychainError("could not encode secret")
    return ref


def _cfdict(cf, pairs):
    count = len(pairs)
    keys = (ctypes.c_void_p * count)(*[k for k, _ in pairs])
    values = (ctypes.c_void_p * count)(*[v for _, v in pairs])
    ref = cf.CFDictionaryCreate(
        None, keys, values, count,
        _const(cf, "kCFTypeDictionaryKeyCallBacks"),
        _const(cf, "kCFTypeDictionaryValueCallBacks"),
    )
    if not ref:
        raise KeychainError("could not build query")
    return ref


def _cfdata_bytes(cf, ref):
    length = int(cf.CFDataGetLength(ref))
    if length <= 0:
        return b""
    ptr = cf.CFDataGetBytePtr(ref)
    if not ptr:
        raise KeychainError("Keychain returned empty data pointer")
    return ctypes.string_at(ptr, length)


def _cfstr_to_py(cf, ref):
    if not ref:
        return None
    buf = ctypes.create_string_buffer(4096)
    if not cf.CFStringGetCString(ref, buf, len(buf), _CF_STRING_ENCODING_UTF8):
        return None
    return buf.value.decode("utf-8")


def _decode_secret(raw):
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="replace")


def _auth_ui_pairs(cf, sec, allow_ui):
    """Fail closed when interaction would be needed, or leave the OS default."""
    if allow_ui:
        return []
    return [(
        _const(sec, "kSecUseAuthenticationUI"),
        _const(sec, "kSecUseAuthenticationUIFail"),
    )]


def generic_password_exists(service, account=None, synchronizable=True):
    """Whether an item is there, without reading the secret and without UI.

    `read_token(..., allow_ui=False)` already avoids SecurityAgent, but it
    still asks for `kSecReturnData`, and the data half of an item is what
    carries the ACL. Presence checks want neither: this asks only for the
    item's attributes, so an item this process is not on the ACL for still
    answers "yes, it exists" rather than looking like a miss.

    Used by first-run detection, where "not found" and "found but locked"
    have to read the same to the probe and very differently to the user.
    """
    try:
        cf, sec = _load()
    except KeychainError:
        return False
    owned = []
    result = ctypes.c_void_p()

    def track(ref):
        owned.append(ref)
        return ref

    try:
        pairs = [
            (_const(sec, "kSecClass"),
             _const(sec, "kSecClassGenericPassword")),
            (_const(sec, "kSecAttrService"), track(_cfstr(cf, service))),
            (_const(sec, "kSecReturnAttributes"),
             _const(cf, "kCFBooleanTrue")),
            (_const(sec, "kSecMatchLimit"),
             _const(sec, "kSecMatchLimitOne")),
        ]
        if account is not None:
            pairs.append((
                _const(sec, "kSecAttrAccount"),
                track(_cfstr(cf, account)),
            ))
        if synchronizable:
            pairs.append((
                _const(sec, "kSecAttrSynchronizable"),
                _const(sec, "kSecAttrSynchronizableAny"),
            ))
        pairs.extend(_auth_ui_pairs(cf, sec, False))
        query = track(_cfdict(cf, pairs))
        status = int(sec.SecItemCopyMatching(query, ctypes.byref(result)))
        if result.value:
            owned.append(result.value)
        return status == ERR_SEC_SUCCESS
    except KeychainError:
        return False
    finally:
        for ref in owned:
            if ref:
                cf.CFRelease(ref)


def get_generic_password(
    service, account=None, synchronizable=False, allow_ui=True,
):
    """Return (OSStatus, secret_str_or_None).

    Does not raise on the usual miss / cancel / auth-failed outcomes — those
    are what callers need to distinguish. Raises KeychainError only when the
    framework itself is unavailable or a query cannot be built.

    Pass `synchronizable=True` for anything the app stores with iCloud Keychain
    sync on; see the module docstring for why a default query cannot see it.

    Pass `allow_ui=False` for probes / background paths that must not pop
    SecurityAgent — a gated item becomes a miss instead of a modal.
    """
    cf, sec = _load()
    owned = []
    result = ctypes.c_void_p()

    def track(ref):
        owned.append(ref)
        return ref

    try:
        pairs = [
            (_const(sec, "kSecClass"),
             _const(sec, "kSecClassGenericPassword")),
            (_const(sec, "kSecAttrService"), track(_cfstr(cf, service))),
            (_const(sec, "kSecReturnData"),
             _const(cf, "kCFBooleanTrue")),
            (_const(sec, "kSecMatchLimit"),
             _const(sec, "kSecMatchLimitOne")),
        ]
        if account is not None:
            pairs.append((
                _const(sec, "kSecAttrAccount"),
                track(_cfstr(cf, account)),
            ))
        if synchronizable:
            pairs.append((
                _const(sec, "kSecAttrSynchronizable"),
                _const(sec, "kSecAttrSynchronizableAny"),
            ))
        pairs.extend(_auth_ui_pairs(cf, sec, allow_ui))
        query = track(_cfdict(cf, pairs))
        status = int(sec.SecItemCopyMatching(query, ctypes.byref(result)))
        if status != ERR_SEC_SUCCESS:
            return status, None
        if not result.value:
            return ERR_SEC_ITEM_NOT_FOUND, None
        owned.append(result.value)
        raw = _cfdata_bytes(cf, result.value)
        return ERR_SEC_SUCCESS, _decode_secret(raw)
    finally:
        for ref in owned:
            if ref:
                cf.CFRelease(ref)


def get_internet_password(server, allow_ui=True):
    """Return (OSStatus, secret_str_or_None, account_str_or_None).

    Zed (and similar) store session tokens as internet passwords keyed by
    server host. Account is optional — some blobs only need the password.
    """
    cf, sec = _load()
    owned = []
    result = ctypes.c_void_p()

    def track(ref):
        owned.append(ref)
        return ref

    try:
        pairs = [
            (_const(sec, "kSecClass"),
             _const(sec, "kSecClassInternetPassword")),
            (_const(sec, "kSecAttrServer"), track(_cfstr(cf, server))),
            (_const(sec, "kSecReturnData"),
             _const(cf, "kCFBooleanTrue")),
            (_const(sec, "kSecReturnAttributes"),
             _const(cf, "kCFBooleanTrue")),
            (_const(sec, "kSecMatchLimit"),
             _const(sec, "kSecMatchLimitOne")),
        ]
        pairs.extend(_auth_ui_pairs(cf, sec, allow_ui))
        query = track(_cfdict(cf, pairs))
        status = int(sec.SecItemCopyMatching(query, ctypes.byref(result)))
        if status != ERR_SEC_SUCCESS:
            return status, None, None
        if not result.value:
            return ERR_SEC_ITEM_NOT_FOUND, None, None
        owned.append(result.value)

        secret = None
        account = None
        if cf.CFGetTypeID(result.value) == cf.CFDictionaryGetTypeID():
            data_ref = cf.CFDictionaryGetValue(
                result.value, _const(sec, "kSecValueData"))
            if data_ref:
                secret = _decode_secret(_cfdata_bytes(cf, data_ref))
            acct_ref = cf.CFDictionaryGetValue(
                result.value, _const(sec, "kSecAttrAccount"))
            if acct_ref:
                account = _cfstr_to_py(cf, acct_ref)
        elif cf.CFGetTypeID(result.value) == cf.CFDataGetTypeID():
            secret = _decode_secret(_cfdata_bytes(cf, result.value))
        if not secret:
            return ERR_SEC_ITEM_NOT_FOUND, None, account
        return ERR_SEC_SUCCESS, secret, account
    finally:
        for ref in owned:
            if ref:
                cf.CFRelease(ref)


def read_token(service, account, synchronizable=True, allow_ui=True):
    """A stored token as a stripped string, or None for every failure.

    The shape every source's `_keychain_token()` wanted: a miss, a denied
    prompt and an unavailable framework are all "no token" to a poller that
    just has to report the source as not connected and move on.
    """
    try:
        status, secret = get_generic_password(
            service, account,
            synchronizable=synchronizable, allow_ui=allow_ui)
    except KeychainError:
        return None
    if status != ERR_SEC_SUCCESS or not secret:
        return None
    return secret.strip() or None


def set_generic_password(service, account, secret):
    """Create or replace a generic password item. Raises KeychainError."""
    cf, sec = _load()
    owned = []

    def track(ref):
        owned.append(ref)
        return ref

    try:
        query = _cfdict(cf, [
            (_const(sec, "kSecClass"),
             _const(sec, "kSecClassGenericPassword")),
            (_const(sec, "kSecAttrService"), track(_cfstr(cf, service))),
            (_const(sec, "kSecAttrAccount"), track(_cfstr(cf, account))),
        ])
        owned.append(query)
        data = track(_cfdata(cf, secret.encode("utf-8")))
        changes = _cfdict(cf, [(_const(sec, "kSecValueData"), data)])
        owned.append(changes)

        status = sec.SecItemUpdate(query, changes)
        if status == ERR_SEC_ITEM_NOT_FOUND:
            attributes = _cfdict(cf, [
                (_const(sec, "kSecClass"),
                 _const(sec, "kSecClassGenericPassword")),
                (_const(sec, "kSecAttrService"), track(_cfstr(cf, service))),
                (_const(sec, "kSecAttrAccount"), track(_cfstr(cf, account))),
                (_const(sec, "kSecValueData"), data),
            ])
            owned.append(attributes)
            status = sec.SecItemAdd(attributes, None)
        if status != ERR_SEC_SUCCESS:
            raise KeychainError(
                f"Keychain write failed (OSStatus {status})", status=status)
    finally:
        for ref in owned:
            if ref:
                cf.CFRelease(ref)
