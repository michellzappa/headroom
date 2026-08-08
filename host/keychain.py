"""Read and write Keychain secrets in-process, without ever showing a dialog.

`security add-generic-password -w <secret>` publishes the secret in the process
table for the life of the call — any process on the machine can `ps` it. That
matters here because the thing being written is a refreshed Claude OAuth token.

Reading is NOT fine via security(1), which is what this module used to claim.
Keychain access is gated per item by an ACL listing the *binaries* allowed to
read the secret. Shelling out makes `/usr/bin/security` the caller, and it is
not on those lists — so macOS shows a "security wants to access key ..." modal
on every single read. Two consequences bit us:

  * Foreign items (Claude Code's token, gh's token) prompt forever. Clicking
    Allow authorises one read; the next poll prompts again.
  * Even *our own* items prompt, because the item was created by the Headroom
    process and the reader is a different binary.

Reading in-process fixes both: the caller is Headroom, which is on the ACL of
anything Headroom created. And SecKeychainSetUserInteractionAllowed(False)
guarantees that a read we are *not* entitled to fails with a status code
instead of putting a modal on the user's screen. A background daemon must
never be able to spawn UI; that is the invariant this module enforces.

Uses the Security framework through ctypes (stdlib) rather than the deprecated
SecKeychain* C API. Stdlib only.
"""

from __future__ import annotations

import ctypes
import ctypes.util

_CF_PATH = ctypes.util.find_library("CoreFoundation")
_SEC_PATH = ctypes.util.find_library("Security")

ERR_SEC_SUCCESS = 0
ERR_SEC_ITEM_NOT_FOUND = -25300
ERR_SEC_AUTH_FAILED = -25293
ERR_SEC_INTERACTION_NOT_ALLOWED = -25308
ERR_SEC_USER_CANCELED = -128
_CF_STRING_ENCODING_UTF8 = 0x08000100

# Statuses that mean "this process may not have that secret". They are a
# permanent property of the item's ACL, not a transient miss, so callers must
# back off for a long time rather than retrying on their normal poll interval.
DENIED_STATUSES = frozenset({
    ERR_SEC_AUTH_FAILED,
    ERR_SEC_INTERACTION_NOT_ALLOWED,
    ERR_SEC_USER_CANCELED,
})

# Process-wide UI suppression is idempotent but only needs doing once.
_ui_suppressed = False


class KeychainError(OSError):
    """A Security.framework call returned a non-zero OSStatus."""


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
    cf.CFRelease.restype = None
    cf.CFRelease.argtypes = [ctypes.c_void_p]

    cf.CFDataGetLength.restype = ctypes.c_long
    cf.CFDataGetLength.argtypes = [ctypes.c_void_p]
    cf.CFDataGetBytePtr.restype = ctypes.POINTER(ctypes.c_char)
    cf.CFDataGetBytePtr.argtypes = [ctypes.c_void_p]
    cf.CFDictionaryGetValue.restype = ctypes.c_void_p
    cf.CFDictionaryGetValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    cf.CFStringGetLength.restype = ctypes.c_long
    cf.CFStringGetLength.argtypes = [ctypes.c_void_p]
    cf.CFStringGetMaximumSizeForEncoding.restype = ctypes.c_long
    cf.CFStringGetMaximumSizeForEncoding.argtypes = [
        ctypes.c_long, ctypes.c_uint32]
    cf.CFStringGetCString.restype = ctypes.c_bool
    cf.CFStringGetCString.argtypes = [
        ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]

    sec.SecItemAdd.restype = ctypes.c_int32
    sec.SecItemAdd.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    sec.SecItemUpdate.restype = ctypes.c_int32
    sec.SecItemUpdate.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    sec.SecItemCopyMatching.restype = ctypes.c_int32
    sec.SecItemCopyMatching.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    sec.SecKeychainSetUserInteractionAllowed.restype = ctypes.c_int32
    sec.SecKeychainSetUserInteractionAllowed.argtypes = [ctypes.c_bool]
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


def _suppress_ui(sec):
    """Forbid this process from ever drawing a Keychain dialog.

    Legacy (file-based) login-keychain items ignore kSecUseAuthenticationUI in
    some macOS versions, so the old process-wide switch is the load-bearing
    one; the query-level flag in get_generic_password() is belt and braces.
    A read we lack rights for then returns errSecInteractionNotAllowed or
    errSecAuthFailed rather than blocking on SecurityAgent.
    """
    global _ui_suppressed
    if not _ui_suppressed:
        sec.SecKeychainSetUserInteractionAllowed(False)
        _ui_suppressed = True


def get_generic_password(service, account=None):
    """Return (status, secret) for a generic password item.

    `secret` is a str on success and None otherwise. Never prompts, never
    blocks: on a denied item it returns a status in DENIED_STATUSES. Callers
    should treat those as sticky and stop polling that item.
    """
    cf, sec = _load()
    _suppress_ui(sec)
    owned = []

    def track(ref):
        owned.append(ref)
        return ref

    try:
        pairs = [
            (_const(sec, "kSecClass"),
             _const(sec, "kSecClassGenericPassword")),
            (_const(sec, "kSecAttrService"), track(_cfstr(cf, service))),
            (_const(sec, "kSecReturnData"), _const(cf, "kCFBooleanTrue")),
            (_const(sec, "kSecUseAuthenticationUI"),
             _const(sec, "kSecUseAuthenticationUIFail")),
        ]
        if account is not None:
            pairs.append(
                (_const(sec, "kSecAttrAccount"), track(_cfstr(cf, account))))

        query = _cfdict(cf, pairs)
        owned.append(query)

        out = ctypes.c_void_p()
        status = sec.SecItemCopyMatching(query, ctypes.byref(out))
        if status != ERR_SEC_SUCCESS or not out:
            return status, None

        owned.append(out.value)
        length = cf.CFDataGetLength(out)
        if length <= 0:
            return status, None
        raw = ctypes.string_at(cf.CFDataGetBytePtr(out), length)
        try:
            return status, raw.decode("utf-8")
        except UnicodeDecodeError:
            return status, None
    finally:
        for ref in owned:
            if ref:
                cf.CFRelease(ref)


def _cfstr_to_str(cf, ref):
    """Decode a CFStringRef to a Python str, or None."""
    if not ref:
        return None
    length = cf.CFStringGetLength(ref)
    if length <= 0:
        return None
    cap = cf.CFStringGetMaximumSizeForEncoding(length, _CF_STRING_ENCODING_UTF8) + 1
    buf = ctypes.create_string_buffer(int(cap))
    if not cf.CFStringGetCString(ref, buf, len(buf), _CF_STRING_ENCODING_UTF8):
        return None
    return buf.value.decode("utf-8", "replace") or None


def get_internet_password(server, account=None):
    """Return (status, account, secret) for an internet password item.

    Same no-UI guarantee as get_generic_password(). Replaces
    `security find-internet-password -g`, which additionally printed the
    secret to stderr where it could land in a log.
    """
    cf, sec = _load()
    _suppress_ui(sec)
    owned = []

    def track(ref):
        owned.append(ref)
        return ref

    def query(return_key):
        pairs = [
            (_const(sec, "kSecClass"),
             _const(sec, "kSecClassInternetPassword")),
            (_const(sec, "kSecAttrServer"), track(_cfstr(cf, server))),
            (_const(sec, return_key), _const(cf, "kCFBooleanTrue")),
            (_const(sec, "kSecUseAuthenticationUI"),
             _const(sec, "kSecUseAuthenticationUIFail")),
        ]
        if account is not None:
            pairs.append(
                (_const(sec, "kSecAttrAccount"), track(_cfstr(cf, account))))
        ref = _cfdict(cf, pairs)
        owned.append(ref)
        out = ctypes.c_void_p()
        status = sec.SecItemCopyMatching(ref, ctypes.byref(out))
        if status == ERR_SEC_SUCCESS and out:
            owned.append(out.value)
        return status, out

    try:
        # Attributes first: cheap, and tells us the account name.
        acct = account
        status, out = query("kSecReturnAttributes")
        if status == ERR_SEC_SUCCESS and out and acct is None:
            acct = _cfstr_to_str(
                cf, cf.CFDictionaryGetValue(out, _const(sec, "kSecAttrAccount")))

        status, out = query("kSecReturnData")
        if status != ERR_SEC_SUCCESS or not out:
            return status, acct, None
        length = cf.CFDataGetLength(out)
        if length <= 0:
            return status, acct, None
        raw = ctypes.string_at(cf.CFDataGetBytePtr(out), length)
        try:
            return status, acct, raw.decode("utf-8").strip() or None
        except UnicodeDecodeError:
            return status, acct, None
    finally:
        for ref in owned:
            if ref:
                cf.CFRelease(ref)


def read_secret(service, account=None):
    """Best-effort secret read: the string, or None. Never prompts, never raises.

    Drop-in for the old `security find-generic-password -w` subprocess calls.
    Callers that need to tell "denied" from "absent" should use
    get_generic_password() and check DENIED_STATUSES.
    """
    try:
        _, secret = get_generic_password(service, account)
    except (KeychainError, OSError, ValueError):
        return None
    if not secret:
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
            raise KeychainError(f"Keychain write failed (OSStatus {status})")
    finally:
        for ref in owned:
            if ref:
                cf.CFRelease(ref)
