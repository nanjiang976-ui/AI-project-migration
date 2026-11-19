"""Darwin APFS identity, ACLs, extended attributes and per-object metadata copy."""
import ctypes as ct
import errno
import hashlib
import os
from pathlib import Path
import plistlib
import re
import stat
import subprocess
import sys
from uuid import UUID

if sys.platform != "darwin":
    raise ImportError("This module requires native macOS")

lib = ct.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)


def bind(name, result, *args):
    fn = getattr(lib, name)
    fn.argtypes, fn.restype = args, result
    return fn


P = ct.c_void_p
C = ct.c_char_p
N = ct.c_size_t
acl_get = bind("acl_get_file", P, C, ct.c_int)
acl_text = bind("acl_to_text", P, P, ct.POINTER(ct.c_ssize_t))
acl_free = bind("acl_free", ct.c_int, P)
list_xattr = bind("listxattr", ct.c_ssize_t, C, P, N, ct.c_int)
get_xattr = bind("getxattr", ct.c_ssize_t, C, C, P, N, ct.c_uint32, ct.c_int)
fcopyfile = bind("fcopyfile", ct.c_int, ct.c_int, ct.c_int, P, ct.c_uint32)
fchflags = bind("fchflags", ct.c_int, ct.c_int, ct.c_uint32)


def fail(path):
    err = ct.get_errno()
    raise OSError(err, os.strerror(err), str(path))


def physical_chain(path):
    for p in (*reversed(path.parents), path):
        if os.path.lexists(p) and (p.is_symlink() or not p.is_dir()):
            raise ValueError(f"Expected physical directory: {p}")


def volume_info(path):
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Expected an absolute local path: {path}")
    if len(path.parts) >= 3 and path.parts[1] == "Volumes":
        mount = Path("/", "Volumes", path.parts[2])
        if not mount.is_mount() or mount.is_symlink():
            raise ValueError(f"APFS volume is not mounted at its expected path: {mount}")
    existing = path
    while not os.path.lexists(existing):
        existing = existing.parent
    # The path may be a firmlink under /Users on a modern macOS system. Ask the
    # kernel which device actually contains it, rather than matching mount prefixes.
    output = subprocess.check_output(["/bin/df", "-P", str(existing)], env={**os.environ, "LC_ALL": "C"})
    device = output.splitlines()[-1].split()[0].decode("ascii")
    if not re.fullmatch(r"/dev/disk[0-9]+(?:s[0-9]+)*", device):
        raise ValueError(f"Expected a locally mounted disk: {existing}")
    info = plistlib.loads(subprocess.check_output(["/usr/sbin/diskutil", "info", "-plist", device]))
    mountpoint = info.get("MountPoint")
    if (info.get("Mounted") is False or not mountpoint or not os.path.isabs(mountpoint)
            or Path(mountpoint).stat().st_dev != existing.stat().st_dev
            or str(info.get("FilesystemType", "")).lower() != "apfs"):
        raise ValueError(f"Expected a mounted APFS volume: {existing}")
    if not info.get("GlobalPermissionsEnabled", False):
        raise ValueError(f"Enable ownership on the volume before migrating: {existing}")
    if info.get("ReadOnlyVolume") or info.get("WritableVolume") is False:
        raise ValueError(f"Volume must be writable: {existing}")
    return str(UUID(info["VolumeUUID"])), existing.stat().st_dev


def volume(path):
    return volume_info(path)[0]


def checked_root(arg):
    path = Path(arg)
    if not path.is_absolute() or ".." in path.parts or path == Path("/") or str(path).startswith("//"):
        raise ValueError(f"Choose an absolute local project directory: {arg}")
    physical_chain(path)
    volume(path)
    if path.is_mount() or path == Path("/Volumes"):
        raise ValueError(f"Cannot migrate or use a mount root: {path}")
    return path


def object_id(path):
    path = Path(path)
    physical_chain(path)
    uuid, dev = volume_info(path)
    st = path.lstat()
    if not stat.S_ISDIR(st.st_mode) or st.st_dev != dev or not st.st_ino:
        raise ValueError(f"Directory identity changed: {path}")
    return [uuid, st.st_ino]


def acl(path):
    # Apple ACL_TYPE_EXTENDED. On an existing ordinary file ENOENT means no ACL.
    ct.set_errno(0)
    obj = acl_get(os.fsencode(path), 0x100)
    if not obj:
        if ct.get_errno() == errno.ENOENT and os.path.lexists(path):
            return ""
        fail(path)
    try:
        size = ct.c_ssize_t()
        text = acl_text(obj, ct.byref(size))
        if not text:
            fail(path)
        try:
            raw = ct.string_at(text, size.value).strip()
            return hashlib.sha256(raw).hexdigest() if raw not in (b"", b"!#acl 1") else ""
        finally:
            acl_free(text)
    finally:
        acl_free(obj)


def create_private(path):
    path = Path(path)
    physical_chain(path.parent)
    path.mkdir(mode=0o700)
    if acl(path):
        subprocess.run(["/bin/chmod", "-N", str(path)], check=True)
    require_private(path)


def require_private(path):
    s = path.lstat()
    if (not stat.S_ISDIR(s.st_mode) or s.st_uid != os.geteuid()
            or stat.S_IMODE(s.st_mode) != 0o700 or acl(path)):
        raise ValueError(f"Use an owner-only physical audit directory: {path}")


def xattrs(path, with_hash):
    rawpath = os.fsencode(path)
    size = list_xattr(rawpath, None, 0, 1)
    if size < 0:
        fail(path)
    if not size:
        return {}
    names = ct.create_string_buffer(size)
    got = list_xattr(rawpath, names, size, 1)
    if got != size:
        raise ValueError(f"Extended attribute list changed: {path}")
    result = {}
    for name in sorted(filter(None, names.raw.split(b"\0"))):
        n = get_xattr(rawpath, name, None, 0, 0, 1)
        if n < 0:
            fail(path)
        item = {"size": n}
        if with_hash:
            if name == b"com.apple.ResourceFork":
                with open(rawpath + b"/..namedfork/rsrc", "rb") as stream:
                    item["sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
            else:
                buf = ct.create_string_buffer(max(n, 1))
                if get_xattr(rawpath, name, buf, n, 0, 1) != n:
                    raise ValueError(f"Extended attribute changed: {path}")
                item["sha256"] = hashlib.sha256(buf.raw[:n]).hexdigest()
        result[os.fsdecode(name)] = item
    return result


def transfer(src, dst, row, *, directory=False):
    """Use fds so fcopyfile cannot silently follow a replaced final symlink."""
    flags = os.O_RDONLY | os.O_NOFOLLOW | (os.O_DIRECTORY if directory else 0)
    infd = os.open(src, flags)
    try:
        if os.fstat(infd).st_ino != row["identity"][1]:
            raise ValueError(f"Source was replaced before copying: {src}")
        outflags = flags if directory else os.O_WRONLY | os.O_NOFOLLOW | os.O_CREAT | os.O_EXCL
        outfd = os.open(dst, outflags, 0o600)
        try:
            # COPYFILE_ACL | STAT | XATTR, plus DATA for regular files.
            if fcopyfile(infd, outfd, None, 7 if directory else 15) != 0:
                fail(dst)
            st = os.fstat(outfd)
            # Correct any metadata the native copy could not reproduce implicitly.
            # Some native copies only restore microsecond mtime. Pin all writes to the fd.
            adjust = ((st.st_uid, st.st_gid) != (row["uid"], row["gid"])
                      or stat.S_IMODE(st.st_mode) != row["mode"] or st.st_mtime_ns != row["mtime_ns"])
            if adjust and st.st_flags and fchflags(outfd, 0):
                fail(dst)
            try:
                if (st.st_uid, st.st_gid) != (row["uid"], row["gid"]):
                    os.fchown(outfd, row["uid"], row["gid"])
                if stat.S_IMODE(os.fstat(outfd).st_mode) != row["mode"]:
                    os.fchmod(outfd, row["mode"])
                if st.st_mtime_ns != row["mtime_ns"]:
                    os.utime(outfd, ns=(st.st_atime_ns, row["mtime_ns"]))
            finally:
                if os.fstat(outfd).st_flags != row["flags"] and fchflags(outfd, row["flags"]):
                    fail(dst)
        finally:
            os.close(outfd)
    finally:
        os.close(infd)
