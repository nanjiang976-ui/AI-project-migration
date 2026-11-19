"""Small Windows APIs needed to preserve NTFS streams and access rules (Python 3.12+)."""
import ctypes as c
from ctypes import wintypes as w
from functools import lru_cache
import hashlib
import msvcrt
import os
from pathlib import Path
import struct

kernel = c.WinDLL("kernel32", use_last_error=True)
adv = c.WinDLL("advapi32", use_last_error=True)
P = c.c_void_p
INVALID = c.c_void_p(-1).value
PROTECTED = 0x1000
OWNER_GROUP_DACL = 7


def api(dll, name, result, *types):
    fn = getattr(dll, name)
    fn.restype = result
    fn.argtypes = types
    return fn


get_sd = api(adv, "GetFileSecurityW", w.BOOL, w.LPCWSTR, w.DWORD, P, w.DWORD, c.POINTER(w.DWORD))
set_sd = api(adv, "SetFileSecurityW", w.BOOL, w.LPCWSTR, w.DWORD, P)
get_dacl = api(adv, "GetSecurityDescriptorDacl", w.BOOL, P, c.POINTER(w.BOOL), c.POINTER(P), c.POINTER(w.BOOL))
get_owner = api(adv, "GetSecurityDescriptorOwner", w.BOOL, P, c.POINTER(P), c.POINTER(w.BOOL))
get_group = api(adv, "GetSecurityDescriptorGroup", w.BOOL, P, c.POINTER(P), c.POINTER(w.BOOL))
get_sid_len = api(adv, "GetLengthSid", w.DWORD, P)
set_control = api(adv, "SetSecurityDescriptorControl", w.BOOL, P, w.WORD, w.WORD)
set_attrs = api(kernel, "SetFileAttributesW", w.BOOL, w.LPCWSTR, w.DWORD)
get_attrs = api(kernel, "GetFileAttributesW", w.DWORD, w.LPCWSTR)
mkdir = api(kernel, "CreateDirectoryW", w.BOOL, w.LPCWSTR, P)
create = api(kernel, "CreateFileW", w.HANDLE, w.LPCWSTR, w.DWORD, w.DWORD, P, w.DWORD, w.DWORD, w.HANDLE)
close = api(kernel, "CloseHandle", w.BOOL, w.HANDLE)
volume_info = api(kernel, "GetVolumeInformationW", w.BOOL, w.LPCWSTR, w.LPWSTR, w.DWORD, P, P, P, w.LPWSTR, w.DWORD)
final_name = api(kernel, "GetFinalPathNameByHandleW", w.DWORD, w.HANDLE, w.LPWSTR, w.DWORD, w.DWORD)


class SecAttrs(c.Structure):
    _fields_ = [("length", w.DWORD), ("descriptor", P), ("inherit", w.BOOL)]


class Stream(c.Structure):
    _fields_ = [("size", c.c_longlong), ("name", w.WCHAR * 296)]


find_first = api(kernel, "FindFirstStreamW", w.HANDLE, w.LPCWSTR, c.c_int, c.POINTER(Stream), w.DWORD)
find_next = api(kernel, "FindNextStreamW", w.BOOL, w.HANDLE, c.POINTER(Stream))
find_close = api(kernel, "FindClose", w.BOOL, w.HANDLE)


def fail(path):
    code = c.get_last_error()
    raise OSError(code, c.FormatError(code), str(path))


def wide(path):
    return "\\\\?\\" + str(path)


def volume(path):
    fmt = c.create_unicode_buffer(64)
    if not volume_info(path.anchor, None, 0, None, None, None, fmt, len(fmt)):
        fail(path)
    if fmt.value != "NTFS":
        raise ValueError(f"Expected a local NTFS volume: {path.anchor} ({fmt.value})")


def physical(path):
    """Map even SUBST/short-name aliases to a comparable volume-GUID path."""
    # Parents above a prospective new path need not exist yet.
    suffix = []
    while not os.path.lexists(path):
        if path == path.parent:
            raise FileNotFoundError(path)
        suffix.append(path.name)
        path = path.parent
    h = create(wide(path), 0x80, 7, None, 3, 0x02000000 | 0x00200000, None)
    if h == INVALID:
        fail(path)
    try:
        buf = c.create_unicode_buffer(32768)
        n = final_name(h, buf, len(buf), 1)
        if not n or n >= len(buf):
            fail(path)
        return Path(buf.value).joinpath(*reversed(suffix))
    finally:
        close(h)


def descriptor(path):
    n = w.DWORD()
    if get_sd(wide(path), OWNER_GROUP_DACL, None, 0, c.byref(n)) or c.get_last_error() != 122:
        fail(path)
    sd = c.create_string_buffer(n.value)
    if not get_sd(wide(path), OWNER_GROUP_DACL, sd, n, c.byref(n)):
        fail(path)
    return sd


def acl_addr(sd):
    present, defaulted, ptr = w.BOOL(), w.BOOL(), P()
    if not get_dacl(sd, c.byref(present), c.byref(ptr), c.byref(defaulted)):
        raise c.WinError(c.get_last_error())
    if not present or not ptr:
        raise ValueError("Absent/null DACL requires a separate security review")
    return ptr.value


def ace_blocks(data):
    count = struct.unpack_from("<H", data, 4)[0]
    pos = 8
    for _ in range(count):
        kind, flags, size = struct.unpack_from("<BBH", data, pos)
        if size < 4 or pos + size > len(data):
            raise ValueError("Malformed Windows access rule")
        yield pos, kind, flags, data[pos:pos + size]
        pos += size


@lru_cache(maxsize=4096)
def acl_hash(data):
    # Inheritance provenance has no effect on access. Preserve allow/deny
    # boundaries and order of non-basic rules; adjacent simple allows/denies commute.
    groups = []
    for _, kind, flags, record in ace_blocks(data):
        rec = record[:1] + bytes([flags & ~16]) + record[2:]
        if kind in (0, 1) and groups and groups[-1][0] == kind:
            groups[-1][1].append(rec)
        else:
            groups.append((kind, [rec]))
    signature = b"".join(b"".join(sorted(rows)) for _, rows in groups)
    return hashlib.sha256(signature).hexdigest()


def security(path):
    sd = descriptor(path)
    addr = acl_addr(sd)
    length = c.c_uint16.from_address(addr + 2).value
    result = [acl_hash(c.string_at(addr, length))]
    for get in (get_owner, get_group):
        ptr, defaulted = P(), w.BOOL()
        if not get(sd, c.byref(ptr), c.byref(defaulted)) or not ptr:
            fail(path)
        result.append(c.string_at(ptr.value, get_sid_len(ptr)).hex())
    return result, bool(struct.unpack_from("<H", sd.raw, 2)[0] & PROTECTED)


def template_sd(path, protect=False):
    sd = descriptor(path)
    if protect:
        addr = acl_addr(sd)
        length = c.c_uint16.from_address(addr + 2).value
        for pos, _, flags, _ in ace_blocks(c.string_at(addr, length)):
            c.c_ubyte.from_address(addr + pos + 1).value = flags & ~16
        if not set_control(sd, PROTECTED, PROTECTED):
            fail(path)
    return sd


def create_secured(path, source, *, directory, protect=False):
    sd = template_sd(source, protect)
    sa = SecAttrs(c.sizeof(SecAttrs), c.cast(sd, P), False)
    if directory:
        if not mkdir(wide(path), c.byref(sa)):
            fail(path)
        return None
    # CREATE_NEW, exclusive sharing while this descriptor writes the main stream.
    h = create(wide(path), 0x40000000, 0, c.byref(sa), 1, 0x80, None)
    if h == INVALID:
        fail(path)
    try:
        fd = msvcrt.open_osfhandle(h, os.O_WRONLY | os.O_BINARY)
    except BaseException:
        close(h)
        raise
    return os.fdopen(fd, "wb")


def restore_security(source, dest, root):
    expected, protected = security(source)
    actual, actual_protected = security(dest)
    if actual != expected or actual_protected != (root or protected):
        sd = template_sd(source, root)
        # Only set owner/group if needed; unrelated rights may be unavailable.
        mask = 4 | (1 if expected[1] != actual[1] else 0) | (2 if expected[2] != actual[2] else 0)
        if not set_sd(wide(dest), mask, sd):
            fail(dest)
        actual, actual_protected = security(dest)
        if actual != expected or actual_protected != (root or protected):
            raise PermissionError(f"Could not reproduce effective security: {dest}")


def streams(path):
    data, result = Stream(), {}
    h = find_first(wide(path), 0, c.byref(data), 0)
    if h == INVALID:
        if c.get_last_error() == 38:
            return {}
        fail(path)
    try:
        while True:
            name = data.name
            if name != "::$DATA":
                if not name.startswith(":") or not name.endswith(":$DATA") or any(x in name for x in ("\\", "/", "\0")):
                    raise ValueError(f"Unsupported stream on {path}")
                result[name] = data.size
            if not find_next(h, c.byref(data)):
                if c.get_last_error() != 38:
                    fail(path)
                return result
    finally:
        find_close(h)


def write_attrs(path, attrs):
    # Directory is implicit. NORMAL is used for a file without other settable flags.
    if not set_attrs(wide(path), (attrs & ~0x10) or 0x80):
        fail(path)


def attributes(path):
    # FileStatBasicByNameInfo used by CPython can include transient NT kernel
    # bits (e.g. 0x10000000 on newly created directories). Compare the persistent
    # Win32 attributes returned by the matching read/write API instead.
    attrs = get_attrs(wide(path))
    if attrs == 0xffffffff:
        fail(path)
    return attrs

