"""Collectors: each one reads one slice of machine state into {category: {key: record}}.

Every record carries an ``fp`` (fingerprint) that the diff compares, plus evidence
fields (hash, size, mode, PID, cwd, start time ...) that end up in the receipt.
Collectors never write anywhere except the snapshot's own backup directory.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time

from .config import Config
from .util import (
    IS_LINUX,
    IS_MAC,
    canonical_json,
    file_fingerprint,
    home,
    redact,
    run,
    sha256_bytes,
    sha256_text,
    tree_size,
)

BACKUP_MAX = 512 * 1024  # only small config files are copied for exact restore

DOTFILES = [
    ".bashrc", ".bash_profile", ".bash_login", ".bash_logout", ".profile",
    ".zshrc", ".zprofile", ".zshenv", ".zlogin", ".config/fish/config.fish",
    ".gitconfig", ".config/git/config", ".config/git/ignore", ".config/git/attributes", ".gitignore_global",
    ".npmrc", ".yarnrc", ".yarnrc.yml", ".config/pip/pip.conf", ".pip/pip.conf", ".condarc",
    ".cargo/config.toml", ".cargo/env", ".ssh/config", ".ssh/authorized_keys",
    ".tmux.conf", ".vimrc", ".inputrc", ".editorconfig", ".config/gh/config.yml",
]
# Hash-only: these routinely hold credentials, so no copy is ever taken.
SECRET_DOTFILES = [
    ".netrc", ".pypirc", ".docker/config.json", ".kube/config", ".aws/config", ".aws/credentials",
    ".config/gh/hosts.yml", ".git-credentials",
]

AGENT_FILES = [
    ".claude/settings.json", ".claude/settings.local.json", ".claude/CLAUDE.md",
    ".codex/config.toml", ".codex/AGENTS.md", ".codex/hooks.json",
    ".gemini/settings.json", ".gemini/GEMINI.md",
    ".cursor/mcp.json", ".cursor/hooks.json",
    ".config/opencode/opencode.json", ".config/opencode/opencode.jsonc", ".config/opencode/AGENTS.md",
    ".aider.conf.yml", ".continue/config.json", ".continue/config.yaml",
    ".codeium/windsurf/mcp_config.json",
    ".config/Claude/claude_desktop_config.json",
    "Library/Application Support/Claude/claude_desktop_config.json",
]
AGENT_DIRS = [
    ".claude/hooks", ".claude/skills", ".claude/agents", ".claude/commands",
    ".codex/skills", ".agents/skills", ".gemini/extensions", ".gemini/commands",
    ".cursor/rules", ".config/opencode/agent", ".config/opencode/command", ".config/opencode/plugin",
]
REPO_AGENT_FILES = [
    ".mcp.json", ".claude/settings.json", ".claude/settings.local.json", ".cursor/mcp.json",
    ".vscode/mcp.json", ".gemini/settings.json", ".codex/config.toml", ".git/config",
]
REPO_AGENT_DIRS = [".git/hooks", ".claude/hooks", ".claude/commands", ".claude/agents"]
USER_BIN_DIRS = [".local/bin", "bin", ".bun/bin", ".deno/bin", "go/bin"]
DIR_WALK_MAX = 2000


class Ctx:
    def __init__(self, cfg: Config, backup_dir=None):
        self.cfg = cfg
        self.backup_dir = backup_dir
        self.status = {}

    def backup(self, path: str, rec: dict, data: bytes | None = None) -> None:
        if not self.backup_dir or not self.cfg.backup:
            return
        try:
            if data is None:
                if rec.get("kind") != "file" or rec.get("size", 0) > BACKUP_MAX:
                    return
                with open(path, "rb") as fh:
                    data = fh.read()
            name = sha256_text(path)[:20]
            os.makedirs(self.backup_dir, mode=0o700, exist_ok=True)
            dest = os.path.join(self.backup_dir, name)
            with open(dest, "wb") as fh:
                fh.write(data)
            os.chmod(dest, 0o600)
            rec["backup"] = name
        except OSError:
            pass


def _walk_files(root: str, limit: int = DIR_WALK_MAX, skip_dirs=()):
    count = 0
    for cur, dirs, names in os.walk(root, onerror=lambda e: None):
        dirs[:] = [d for d in dirs if os.path.join(cur, d) not in skip_dirs]
        for d in list(dirs):
            p = os.path.join(cur, d)
            if os.path.islink(p):
                yield p
        for n in names:
            count += 1
            yield os.path.join(cur, n)
            if count >= limit:
                return


# ---------------------------------------------------------------- repo
def collect_repo(ctx: Ctx):
    cfg = ctx.cfg
    root = cfg.repo_root
    out = {"repo.untracked": {}, "repo.ignored": {}, "repo.tracked": {}}
    if not os.path.isdir(os.path.join(root, ".git")):
        ctx.status["repo"] = "not a git repo: bounded walk"
        for p in _walk_files(root, 5000):
            rel = os.path.relpath(p, root)
            rec = file_fingerprint(p, cap=8 * 1024 * 1024)
            if rec:
                out["repo.untracked"][rel] = rec
        return out
    r = run(["git", "-C", root, "ls-files", "--others", "--exclude-standard", "-z"], timeout=60)
    if r is None:
        ctx.status["repo"] = "git missing"
        return out
    for rel in filter(None, r[1].split("\0")):
        rec = file_fingerprint(os.path.join(root, rel), cap=8 * 1024 * 1024)
        if rec:
            out["repo.untracked"][rel] = rec
    r = run(["git", "-C", root, "ls-files", "--others", "--ignored", "--exclude-standard", "--directory", "-z"], timeout=60)
    if r:
        for rel in filter(None, r[1].split("\0")):
            p = os.path.join(root, rel.rstrip("/"))
            rec = file_fingerprint(p, cap=8 * 1024 * 1024)
            if rec:
                if rec["kind"] == "file":
                    rec["fp"] = "%s:%s" % (rec.get("size"), rec.get("mtime"))
                out["repo.ignored"][rel.rstrip("/")] = rec
    modified = set()
    for args in (["ls-files", "-m", "-z"], ["diff", "--cached", "--name-only", "-z"]):
        r = run(["git", "-C", root] + args, timeout=60)
        if r and r[0] == 0:
            modified.update(filter(None, r[1].split("\0")))
    for rel in sorted(modified):
        rec = file_fingerprint(os.path.join(root, rel), cap=8 * 1024 * 1024) or {"kind": "deleted", "fp": "deleted"}
        out["repo.tracked"][rel] = rec
    ctx.status["repo"] = "ok"
    return out


# ---------------------------------------------------------------- tmp
def collect_tmp(ctx: Ctx):
    out = {}
    uid = os.getuid() if hasattr(os, "getuid") else None
    state = os.path.realpath(os.environ.get("LNT_STATE_DIR", "/nonexistent"))
    for tdir in ctx.cfg.tmp_dirs:
        try:
            names = os.listdir(tdir)
        except OSError:
            continue
        for n in names:
            p = os.path.join(tdir, n)
            try:
                st = os.lstat(p)
            except OSError:
                continue
            if uid is not None and st.st_uid != uid:
                continue
            if os.path.realpath(p) == state:
                continue
            rec = {"mtime": int(st.st_mtime), "uid": st.st_uid}
            if os.path.isdir(p) and not os.path.islink(p):
                size, files, trunc = tree_size(p)
                rec.update(kind="dir", size=size, files=files, truncated=trunc)
                rec["fp"] = "dir"
            else:
                rec.update(kind="file", size=st.st_size)
                rec["fp"] = "file:%d:%d" % (st.st_size, int(st.st_mtime))
            out[p] = rec
    ctx.status["tmp"] = "ok (%s)" % ", ".join(ctx.cfg.tmp_dirs)
    return {"tmp": out}


# ---------------------------------------------------------------- dotfiles
def collect_dotfiles(ctx: Ctx):
    out = {}
    h = home()
    for rel in DOTFILES + SECRET_DOTFILES + ctx.cfg.extra_files:
        p = rel if os.path.isabs(rel) else os.path.join(h, rel)
        rec = file_fingerprint(p)
        if not rec:
            continue
        if rel in SECRET_DOTFILES:
            rec["secret"] = True
        else:
            ctx.backup(p, rec)
        out[p] = rec
    ctx.status["dotfiles"] = "ok"
    return {"dotfile": out}


# ---------------------------------------------------------------- agent configs
def _claude_json_semantic(path: str):
    """~/.claude.json changes on every run (counters, caches). Only MCP + hook sections matter."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    subset = {"mcpServers": data.get("mcpServers"), "hooks": data.get("hooks"), "projects": {}}
    for proj, pdata in (data.get("projects") or {}).items():
        if isinstance(pdata, dict):
            keep = {k: pdata.get(k) for k in ("mcpServers", "enabledMcpjsonServers", "hooks") if pdata.get(k)}
            if keep:
                subset["projects"][proj] = keep
    return subset


def collect_agentconfig(ctx: Ctx):
    out = {}
    h = home()
    paths = [os.path.join(h, rel) for rel in AGENT_FILES]
    paths += [os.path.join(ctx.cfg.repo_root, rel) for rel in REPO_AGENT_FILES]
    dirs = [os.path.join(h, rel) for rel in AGENT_DIRS] + ctx.cfg.extra_dirs
    dirs += [os.path.join(ctx.cfg.repo_root, rel) for rel in REPO_AGENT_DIRS]
    for d in dirs:
        if os.path.isdir(d):
            paths.extend(_walk_files(d))
    for p in paths:
        rec = file_fingerprint(p)
        if rec:
            ctx.backup(p, rec)
            out[p] = rec
    cj = os.path.join(h, ".claude.json")
    if os.path.isfile(cj):
        subset = _claude_json_semantic(cj)
        if subset is not None:
            text = canonical_json(subset)
            rec = {"kind": "json-subset", "fp": sha256_text(text), "servers": sorted((subset.get("mcpServers") or {}).keys())}
            ctx.backup(cj + "#mcp", rec, data=text.encode())
            out[cj + "#mcp"] = rec
    ctx.status["agentconfig"] = "ok"
    return {"agentconfig": out}


# ---------------------------------------------------------------- git config
def parse_git_config_z(text: str):
    values = {}
    for entry in filter(None, text.split("\0")):
        key, _, val = entry.partition("\n")
        values.setdefault(key, []).append(val)
    return values


def collect_gitconfig(ctx: Ctx):
    r = run(["git", "config", "--global", "--list", "-z"], timeout=10)
    if r is None:
        ctx.status["gitconfig"] = "git missing"
        return {}
    out = {}
    for key, vals in parse_git_config_z(r[1]).items():
        out[key] = {"values": vals, "fp": sha256_text("\0".join(vals))}
    ctx.status["gitconfig"] = "ok"
    return {"gitconfig": out}


# ---------------------------------------------------------------- packages
def _python():
    return os.environ.get("LNT_PYTHON") or shutil.which("python3") or sys.executable


def collect_packages(ctx: Ctx):
    out = {}
    st = {}

    def add(cat, name, version, extra=None):
        rec = {"version": version, "fp": str(version)}
        if extra:
            rec.update(extra)
        out.setdefault(cat, {})[name] = rec

    if ctx.cfg.enabled("npm"):
        r = run(["npm", "ls", "-g", "--depth=0", "--json"], timeout=60)
        if r is None:
            st["npm"] = "missing"
        else:
            try:
                deps = json.loads(r[1] or "{}").get("dependencies", {}) or {}
                out.setdefault("pkg.npm", {})
                for name, info in deps.items():
                    add("pkg.npm", name, (info or {}).get("version", "?"))
                st["npm"] = "ok"
            except ValueError:
                st["npm"] = "unparseable"
    if ctx.cfg.enabled("pnpm") and shutil.which("pnpm"):
        r = run(["pnpm", "ls", "-g", "--json"], timeout=60)
        try:
            data = json.loads(r[1] or "[]") if r else []
            out.setdefault("pkg.pnpm", {})
            for block in data if isinstance(data, list) else [data]:
                for name, info in (block.get("dependencies") or {}).items():
                    add("pkg.pnpm", name, (info or {}).get("version", "?"))
            st["pnpm"] = "ok"
        except ValueError:
            st["pnpm"] = "unparseable"
    if ctx.cfg.enabled("pip"):
        py = _python()
        r = run([py, "-m", "pip", "list", "--format=json", "--disable-pip-version-check"], timeout=60)
        if r is None or r[0] != 0:
            st["pip"] = "missing"
        else:
            try:
                out.setdefault("pkg.pip", {})
                for item in json.loads(r[1] or "[]"):
                    add("pkg.pip", item["name"].lower(), item.get("version", "?"), {"python": py})
                st["pip"] = "ok (%s)" % py
            except ValueError:
                st["pip"] = "unparseable"
    if ctx.cfg.enabled("pipx") and shutil.which("pipx"):
        r = run(["pipx", "list", "--json"], timeout=60)
        try:
            venvs = json.loads(r[1] or "{}").get("venvs", {}) if r else {}
            out.setdefault("pkg.pipx", {})
            for name, info in venvs.items():
                ver = (((info or {}).get("metadata") or {}).get("main_package") or {}).get("package_version", "?")
                add("pkg.pipx", name, ver)
            st["pipx"] = "ok"
        except ValueError:
            st["pipx"] = "unparseable"
    if ctx.cfg.enabled("uv") and shutil.which("uv"):
        r = run(["uv", "tool", "list"], timeout=30)
        out.setdefault("pkg.uv", {})
        for line in (r[1] if r else "").splitlines():
            m = re.match(r"^(\S+) v(\S+)", line)
            if m:
                add("pkg.uv", m.group(1), m.group(2))
        st["uv"] = "ok"
    if ctx.cfg.enabled("cargo") and shutil.which("cargo"):
        r = run(["cargo", "install", "--list"], timeout=30)
        out.setdefault("pkg.cargo", {})
        for line in (r[1] if r else "").splitlines():
            m = re.match(r"^(\S+) v([^\s:]+)", line)
            if m:
                add("pkg.cargo", m.group(1), m.group(2))
        st["cargo"] = "ok"
    if ctx.cfg.enabled("brew") and shutil.which("brew"):
        env = dict(os.environ, HOMEBREW_NO_AUTO_UPDATE="1", HOMEBREW_NO_ANALYTICS="1")
        for flag, cat in (("--formula", "pkg.brew"), ("--cask", "pkg.brewcask")):
            r = run(["brew", "list", "--versions", flag], timeout=90, env=env)
            out.setdefault(cat, {})
            for line in (r[1] if r else "").splitlines():
                parts = line.split()
                if parts:
                    add(cat, parts[0], " ".join(parts[1:]) or "?")
        st["brew"] = "ok"
    if ctx.cfg.collectors.get("gem", False) and shutil.which("gem"):
        r = run(["gem", "list", "--local"], timeout=60)
        out.setdefault("pkg.gem", {})
        for line in (r[1] if r else "").splitlines():
            m = re.match(r"^(\S+) \((.*)\)", line)
            if m:
                add("pkg.gem", m.group(1), m.group(2))
        st["gem"] = "ok"
    bins = {}
    for rel in USER_BIN_DIRS:
        d = os.path.join(home(), rel)
        if os.path.isdir(d):
            for n in os.listdir(d)[:DIR_WALK_MAX]:
                p = os.path.join(d, n)
                rec = file_fingerprint(p)
                if rec:
                    bins[p] = rec
    out["userbin"] = bins
    ctx.status.update({"pkg." + k: v for k, v in st.items()})
    return out


# ---------------------------------------------------------------- processes & ports
def _linux_boot_time():
    try:
        with open("/proc/stat") as fh:
            for line in fh:
                if line.startswith("btime"):
                    return int(line.split()[1])
    except OSError:
        pass
    return 0


def list_processes():
    """Return {pid: record} for processes owned by the current user."""
    procs = {}
    uid = os.getuid() if hasattr(os, "getuid") else None
    if IS_LINUX and os.path.isdir("/proc"):
        btime = _linux_boot_time()
        tick = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
        for name in os.listdir("/proc"):
            if not name.isdigit():
                continue
            pid = int(name)
            base = "/proc/" + name
            try:
                st = os.stat(base)
                if uid is not None and st.st_uid != uid:
                    continue
                with open(base + "/stat", "rb") as fh:
                    raw = fh.read().decode("utf-8", "replace")
                comm = raw[raw.find("(") + 1: raw.rfind(")")]
                fields = raw[raw.rfind(")") + 2:].split()
                with open(base + "/cmdline", "rb") as fh:
                    cmd = fh.read().replace(b"\0", b" ").decode("utf-8", "replace").strip()
            except (OSError, IndexError):
                continue
            try:
                cwd = os.readlink(base + "/cwd")
            except OSError:
                cwd = None
            procs[pid] = {
                "pid": pid, "ppid": int(fields[1]), "pgid": int(fields[2]), "uid": st.st_uid,
                "state": fields[0], "start": int(btime + int(fields[19]) / tick), "comm": comm,
                "cmd": redact(cmd[:300]) or "[%s]" % comm, "cwd": cwd,
            }
        return procs
    r = run(["ps", "-axo", "pid=,ppid=,pgid=,uid=,stat=,lstart=,command="], timeout=20)
    if not r:
        return procs
    for line in r[1].splitlines():
        parts = line.split(None, 10)
        if len(parts) < 10:
            continue
        try:
            pid, ppid, pgid, puid = (int(x) for x in parts[:4])
            start = int(time.mktime(time.strptime(" ".join(parts[5:10]), "%a %b %d %H:%M:%S %Y")))
        except ValueError:
            continue
        if uid is not None and puid != uid:
            continue
        cmd = parts[10] if len(parts) > 10 else ""
        if ppid == os.getpid():
            continue  # the ps we just spawned
        procs[pid] = {
            "pid": pid, "ppid": ppid, "pgid": pgid, "uid": puid, "state": parts[4][:1], "start": start,
            "comm": os.path.basename(cmd.split()[0]) if cmd else "?", "cmd": redact(cmd[:300]), "cwd": None,
        }
    return procs


def process_cwds(pids):
    """Working directories for several PIDs; one lsof call on macOS instead of one per PID."""
    out = {}
    pids = [int(p) for p in pids]
    if not pids:
        return out
    if IS_LINUX:
        for pid in pids:
            try:
                out[pid] = os.readlink("/proc/%d/cwd" % pid)
            except OSError:
                pass
        return out
    r = run(["lsof", "-a", "-d", "cwd", "-p", ",".join(str(p) for p in pids), "-Fpn"], timeout=20)
    cur = None
    for line in (r[1] if r else "").splitlines():
        if line.startswith("p"):
            cur = int(line[1:])
        elif line.startswith("n") and cur is not None:
            out[cur] = line[1:]
    return out


def list_listening():
    """Return {port: set(pids)} for TCP listening sockets of the current user."""
    ports = {}
    if IS_LINUX and os.path.exists("/proc/net/tcp"):
        inode_port = {}
        for f in ("/proc/net/tcp", "/proc/net/tcp6"):
            try:
                with open(f) as fh:
                    next(fh)
                    for line in fh:
                        cols = line.split()
                        if len(cols) > 9 and cols[3] == "0A":
                            inode_port[cols[9]] = int(cols[1].rsplit(":", 1)[1], 16)
            except (OSError, StopIteration):
                pass
        uid = os.getuid()
        for name in os.listdir("/proc"):
            if not name.isdigit():
                continue
            fd_dir = "/proc/%s/fd" % name
            try:
                if os.stat("/proc/" + name).st_uid != uid:
                    continue
                fds = os.listdir(fd_dir)
            except OSError:
                continue
            for fd in fds:
                try:
                    link = os.readlink(os.path.join(fd_dir, fd))
                except OSError:
                    continue
                if link.startswith("socket:["):
                    port = inode_port.get(link[8:-1])
                    if port is not None:
                        ports.setdefault(port, set()).add(int(name))
        return ports
    r = run(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "-a", "-u", str(os.getuid()), "-Fpn"], timeout=20)
    if r:
        pid = None
        for line in r[1].splitlines():
            if line.startswith("p"):
                pid = int(line[1:])
            elif line.startswith("n") and pid is not None:
                try:
                    ports.setdefault(int(line.rsplit(":", 1)[1]), set()).add(pid)
                except ValueError:
                    pass
    return ports


def collect_processes(ctx: Ctx):
    procs = list_processes()
    if not procs:
        ctx.status["processes"] = "unavailable"
        return {}
    me = os.getpid()
    out = {}
    for pid, rec in procs.items():
        if pid == me:
            continue
        rec = dict(rec, fp="proc")
        out["%d:%d" % (pid, rec["start"])] = rec
    ports = {}
    for port, pids in list_listening().items():
        pl = sorted(pids)
        evid = [{"pid": p, "start": procs.get(p, {}).get("start"), "cmd": procs.get(p, {}).get("cmd")} for p in pl]
        ports["tcp:%d" % port] = {"port": port, "pids": pl, "procs": evid, "fp": ",".join("%s:%s" % (e["pid"], e["start"]) for e in evid)}
    ctx.status["processes"] = "ok (%d procs, %d listening ports)" % (len(out), len(ports))
    return {"process": out, "port": ports}


# ---------------------------------------------------------------- docker
def _json_lines(text):
    for line in (text or "").splitlines():
        line = line.strip()
        if line:
            try:
                yield json.loads(line)
            except ValueError:
                continue


def collect_docker(ctx: Ctx):
    r = run(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=8)
    if r is None:
        ctx.status["docker"] = "missing"
        return {}
    if r[0] != 0:
        ctx.status["docker"] = "daemon unreachable"
        return {}
    out = {"docker.container": {}, "docker.volume": {}, "docker.network": {}, "docker.image": {}}
    r = run(["docker", "ps", "-a", "--no-trunc", "--format", "{{json .}}"], timeout=20)
    for c in _json_lines(r[1] if r else ""):
        out["docker.container"][c.get("ID", "?")] = {
            "name": c.get("Names"), "image": c.get("Image"), "status": c.get("Status"),
            "created": c.get("CreatedAt"), "ports": c.get("Ports"), "fp": "container",
        }
    r = run(["docker", "volume", "ls", "--format", "{{json .}}"], timeout=20)
    for v in _json_lines(r[1] if r else ""):
        out["docker.volume"][v.get("Name", "?")] = {"driver": v.get("Driver"), "fp": "volume"}
    r = run(["docker", "network", "ls", "--no-trunc", "--format", "{{json .}}"], timeout=20)
    for n in _json_lines(r[1] if r else ""):
        out["docker.network"][n.get("ID", "?")] = {"name": n.get("Name"), "fp": "network"}
    r = run(["docker", "images", "--no-trunc", "--format", "{{json .}}"], timeout=20)
    for i in _json_lines(r[1] if r else ""):
        key = i.get("ID", "?")
        out["docker.image"][key] = {"repo": i.get("Repository"), "tag": i.get("Tag"), "size": i.get("Size"), "fp": "image"}
    ctx.status["docker"] = "ok"
    return out


# ---------------------------------------------------------------- cron / launchd / systemd
def read_crontab():
    r = run(["crontab", "-l"], timeout=10)
    if r is None:
        return None
    if r[0] != 0:
        return ""
    return r[1]


def collect_scheduled(ctx: Ctx):
    out = {"cron": {}, "unit": {}}
    text = read_crontab()
    if text is None:
        ctx.status["cron"] = "missing"
    else:
        for line in text.splitlines():
            s = line.strip()
            if s and not s.startswith("#"):
                out["cron"][sha256_text(s)[:16]] = {"line": redact(s), "fp": "line"}
        rec = {"kind": "crontab", "fp": sha256_text(text), "lines": len(out["cron"])}
        ctx.backup("crontab", rec, data=text.encode())
        out["cron"]["__crontab__"] = rec
        ctx.status["cron"] = "ok"
    unit_dirs = []
    if IS_MAC:
        unit_dirs.append(os.path.join(home(), "Library", "LaunchAgents"))
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(home(), ".config")
        unit_dirs.append(os.path.join(base, "systemd", "user"))
    for d in unit_dirs:
        if os.path.isdir(d):
            for p in _walk_files(d, 500):
                rec = file_fingerprint(p)
                if rec:
                    ctx.backup(p, rec)
                    out["unit"][p] = rec
    ctx.status["units"] = "ok (%s)" % ", ".join(unit_dirs)
    return out


COLLECTORS = [
    ("repo", collect_repo),
    ("dotfiles", collect_dotfiles),
    ("agentconfig", collect_agentconfig),
    ("gitconfig", collect_gitconfig),
    ("packages", collect_packages),
    ("processes", collect_processes),
    ("docker", collect_docker),
    ("scheduled", collect_scheduled),
    # last, so caches the other collectors' tools drop in TMPDIR (e.g. npm's) are already in the baseline
    ("tmp", collect_tmp),
]


def take_snapshot(cfg: Config, backup_dir=None, label="before"):
    t0 = time.time()
    ctx = Ctx(cfg, backup_dir)
    items = {}
    timings = {}
    for name, fn in COLLECTORS:
        if not cfg.enabled(name):
            ctx.status[name] = "disabled"
            continue
        t = time.time()
        try:
            items.update(fn(ctx))
        except Exception as exc:  # a broken collector must never sink the snapshot
            ctx.status[name] = "error: %s" % exc
        timings[name] = round(time.time() - t, 3)
    return {
        "lnt_snapshot": 1,
        "label": label,
        "taken_at": int(time.time()),
        "platform": sys.platform,
        "pid": os.getpid(),
        "config": cfg.to_dict(),
        "status": ctx.status,
        "timings": timings,
        "seconds": round(time.time() - t0, 3),
        "items": items,
    }


def snapshot_digest(snap) -> str:
    return sha256_bytes(canonical_json(snap["items"]).encode())
