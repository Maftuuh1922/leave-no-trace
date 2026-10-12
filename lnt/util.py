"""Small stdlib helpers shared by the collectors, diff and revert code."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time

IS_LINUX = sys.platform.startswith("linux")
IS_MAC = sys.platform == "darwin"
IS_WINDOWS = os.name == "nt"

HASH_CAP = 64 * 1024 * 1024  # files bigger than this are fingerprinted by size+mtime


def home() -> str:
    return os.path.expanduser("~")


def expand(path: str) -> str:
    return os.path.abspath(os.path.expanduser(path))


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def which(name: str):
    override = os.environ.get("LNT_BIN_" + name.upper().replace("-", "_"))
    if override:
        return override if os.path.exists(override) else None
    return shutil.which(name)


def run(cmd, timeout: float = 20, input_text=None, env=None):
    """Run a command. Returns (returncode, stdout, stderr) or None if the binary is missing."""
    exe = which(cmd[0])
    if not exe:
        return None
    try:
        proc = subprocess.run(
            [exe] + list(cmd[1:]),
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return (124, "", str(exc))
    return (proc.returncode, proc.stdout, proc.stderr)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8", "surrogateescape"))


def file_fingerprint(path: str, cap: int = HASH_CAP):
    """Return a dict describing a path, or None if it does not exist."""
    try:
        st = os.lstat(path)
    except OSError:
        return None
    rec = {"mode": oct(st.st_mode & 0o7777), "mtime": int(st.st_mtime), "uid": getattr(st, "st_uid", None)}
    if os.path.islink(path):
        rec["kind"] = "symlink"
        try:
            target = os.readlink(path)
        except OSError:
            target = "?"
        rec["target"] = target
        rec["fp"] = "link:" + sha256_text(target)
        return rec
    if os.path.isdir(path):
        rec["kind"] = "dir"
        rec["fp"] = "dir"
        return rec
    rec["kind"] = "file"
    rec["size"] = st.st_size
    if st.st_size > cap:
        rec["fp"] = "big:%d:%d" % (st.st_size, int(st.st_mtime))
        return rec
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        rec["fp"] = "unreadable:%d" % st.st_size
        return rec
    rec["sha256"] = h.hexdigest()
    rec["fp"] = rec["sha256"]
    return rec


def tree_size(path: str, max_entries: int = 20000):
    """Bounded recursive size of a directory. Returns (bytes, files, truncated)."""
    total = 0
    files = 0
    for root, dirs, names in os.walk(path, onerror=lambda e: None):
        for n in names:
            files += 1
            try:
                total += os.lstat(os.path.join(root, n)).st_size
            except OSError:
                pass
            if files >= max_entries:
                return total, files, True
    return total, files, False


def human_bytes(n) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024
    return "%d B" % n


_SECRET_RE = re.compile(
    r"(gh[pousr]_[A-Za-z0-9]{8,}|github_pat_[A-Za-z0-9_]{8,}|sk-[A-Za-z0-9_-]{8,}|xox[abp]-[A-Za-z0-9-]{8,}|AKIA[0-9A-Z]{12,}|//[^/@\s:]+:[^/@\s]+@)"
)


def redact(value) -> str:
    if value is None:
        return ""
    text = str(value)
    return _SECRET_RE.sub("[redacted]", text)


def write_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, sort_keys=True)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def read_json(path: str):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def is_within(path: str, parent: str) -> bool:
    try:
        path = os.path.realpath(path)
        parent = os.path.realpath(parent)
        return os.path.commonpath([path, parent]) == parent
    except ValueError:
        return False
