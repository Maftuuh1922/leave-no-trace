"""Configuration: where state lives, what is in scope, what to ignore."""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import tempfile

from .util import expand, home

DEFAULT_IGNORES = [
    # Agent transcripts, caches and counters change every session by design.
    "agentconfig:*/.claude/projects/*",
    "agentconfig:*/.claude/todos/*",
    "agentconfig:*/.claude/statsig/*",
    "agentconfig:*/.claude/shell-snapshots/*",
    "tmp:*/.X11-unix",
    "tmp:*/.ICE-unix",
    "tmp:*/.font-unix",
    "tmp:*/systemd-private-*",
    "tmp:*/pytest-of-*",
    "tmp:*/com.apple.*",
    "tmp:*/node-compile-cache",
    "process:ps",
    "process:lsof",
]

SECTION_KEYS = ("scope", "extra_files", "extra_dirs", "ignore", "collectors", "backup", "tmp_dirs")


class Config:
    def __init__(self, repo_root: str, data: dict | None = None, ignores=None):
        data = data or {}
        self.repo_root = os.path.abspath(repo_root)
        self.scope = list(data.get("scope", []))
        self.extra_files = [expand(p) for p in data.get("extra_files", [])]
        self.extra_dirs = [expand(p) for p in data.get("extra_dirs", [])]
        self.collectors = dict(data.get("collectors", {}))
        self.backup = bool(data.get("backup", True))
        env_tmp = os.environ.get("LNT_TMP_DIRS")
        if env_tmp:
            tmp_dirs = [p for p in env_tmp.split(os.pathsep) if p]
        elif data.get("tmp_dirs"):
            tmp_dirs = data["tmp_dirs"]
        else:
            tmp_dirs = [tempfile.gettempdir(), "/tmp"]
        seen = []
        for p in tmp_dirs:
            rp = os.path.realpath(expand(p))
            if os.path.isdir(rp) and rp not in seen:
                seen.append(rp)
        self.tmp_dirs = seen
        self.ignores = list(DEFAULT_IGNORES) + list(data.get("ignore", [])) + list(ignores or [])

    def enabled(self, name: str) -> bool:
        return bool(self.collectors.get(name, True))

    def to_dict(self) -> dict:
        return {
            "repo_root": self.repo_root,
            "scope": self.scope,
            "extra_files": self.extra_files,
            "extra_dirs": self.extra_dirs,
            "collectors": self.collectors,
            "backup": self.backup,
            "tmp_dirs": self.tmp_dirs,
            "ignores": self.ignores,
        }


def find_repo_root(start: str) -> str:
    cur = os.path.abspath(start)
    while True:
        if os.path.exists(os.path.join(cur, ".git")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return os.path.abspath(start)
        cur = parent


def read_ignore_file(path: str):
    out = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#"):
                    out.append(line)
    except OSError:
        pass
    return out


def load_config(repo_root: str, extra_scope=None) -> Config:
    data: dict = {}
    for path in (os.path.join(user_config_dir(), "config.json"), os.path.join(repo_root, ".lnt.json")):
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
            for key in SECTION_KEYS:
                if key in loaded:
                    if isinstance(loaded[key], list):
                        data.setdefault(key, [])
                        data[key] = data[key] + loaded[key]
                    elif isinstance(loaded[key], dict):
                        data.setdefault(key, {})
                        data[key].update(loaded[key])
                    else:
                        data[key] = loaded[key]
    if extra_scope:
        data["scope"] = list(data.get("scope", [])) + list(extra_scope)
    ignores = read_ignore_file(os.path.join(user_config_dir(), "ignore")) + read_ignore_file(
        os.path.join(repo_root, ".lntignore")
    )
    return Config(repo_root, data, ignores)


def user_config_dir() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(home(), ".config")
    return os.path.join(base, "lnt")


def state_root() -> str:
    env = os.environ.get("LNT_STATE_DIR")
    if env:
        return expand(env)
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(home(), ".local", "state")
    return os.path.join(base, "lnt")


def session_dir(repo_root: str, name: str | None = None) -> str:
    key = hashlib.sha256(os.path.realpath(repo_root).encode()).hexdigest()[:12]
    base = os.path.basename(os.path.realpath(repo_root)) or "root"
    return os.path.join(state_root(), "%s-%s" % (base, key), name or "default")


def matches_any(names, patterns) -> str | None:
    for pat in patterns:
        for name in names:
            if fnmatch.fnmatchcase(name, pat):
                return pat
    return None


def in_scope(relpath: str, scope) -> bool:
    for pat in scope:
        pat = pat.rstrip("/")
        if fnmatch.fnmatchcase(relpath, pat) or fnmatch.fnmatchcase(relpath, pat + "/*"):
            return True
        if relpath == pat or relpath.startswith(pat + "/"):
            return True
    return False
