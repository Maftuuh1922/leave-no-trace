"""Turn findings into a revert plan, and (only with --apply) carry it out.

Safety rules, enforced here and covered by tests:
  * Only findings from the diff are acted on. Nothing else is touched.
  * Before every action the current state is re-read and must still equal what the
    diff saw. If it changed since, the action is skipped, not forced.
  * Files are never deleted: created files are moved into the session trash, and a
    file being restored has its current version moved to the trash first.
  * Processes are signalled only if PID, start time and UID still match.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import signal
import tempfile
import time

from . import collectors as C
from .diff import SEVERITY_ORDER
from .util import HASH_CAP, file_fingerprint, home, sha256_text


def _cmd_str(cmd):
    return " ".join(shlex.quote(c) for c in cmd)


def _pkg_commands(mgr, name, change, before, after):
    oldv = (before or {}).get("version")
    py = (after or before or {}).get("python") or C._python()
    table = {
        "npm": (["npm", "uninstall", "-g", name], ["npm", "install", "-g", "%s@%s" % (name, oldv)]),
        "pnpm": (["pnpm", "remove", "-g", name], ["pnpm", "add", "-g", "%s@%s" % (name, oldv)]),
        "pip": ([py, "-m", "pip", "uninstall", "-y", name], [py, "-m", "pip", "install", "%s==%s" % (name, oldv)]),
        "pipx": (["pipx", "uninstall", name], ["pipx", "install", "%s==%s" % (name, oldv)]),
        "uv": (["uv", "tool", "uninstall", name], ["uv", "tool", "install", "%s==%s" % (name, oldv)]),
        "cargo": (["cargo", "uninstall", name], ["cargo", "install", name, "--version", str(oldv)]),
        "brew": (["brew", "uninstall", name], None),
        "brewcask": (["brew", "uninstall", "--cask", name], None),
        "gem": (["gem", "uninstall", "-x", name], None),
    }
    remove, install = table.get(mgr, (None, None))
    if change == "created":
        return remove
    return install


def build_plan(diff, include_info=False, before_snapshot=None):
    actions = []
    restored_paths = set()
    findings = [f for f in diff["findings"] if not f["in_scope"] and not f["ignored_by"]
                and (include_info or SEVERITY_ORDER[f["severity"]] > 0)]

    def add(kind, f, desc, **kw):
        a = {"n": len(actions) + 1, "kind": kind, "finding": f["id"], "category": f["category"],
             "change": f["change"], "key": f["key"], "description": desc}
        a.update(kw)
        actions.append(a)
        return a

    for f in findings:
        cat, ch, key, b, a = f["category"], f["change"], f["key"], f["before"] or {}, f["after"] or {}
        if cat in ("repo.untracked", "repo.ignored", "tmp", "userbin"):
            if ch == "created":
                add("trash", f, "move to trash: %s" % key, path=_abs(diff, cat, key), expect=a.get("fp"))
            else:
                add("manual", f, "no copy was taken of %s; restore it from git/backup by hand" % key)
        elif cat in ("dotfile", "agentconfig", "unit"):
            path = key
            if ch == "created" and not key.endswith("#mcp"):
                pre = []
                if cat == "unit":
                    pre = _unit_disable(path)
                add("trash", f, "move to trash: %s" % path, path=path, expect=a.get("fp"), pre=pre)
                restored_paths.add(path)
            elif b.get("backup"):
                add("restore", f, "restore %s from the baseline copy" % path.replace(home(), "~", 1),
                    path=path, backup=b["backup"], expect=a.get("fp"), mode=b.get("mode"))
                restored_paths.add(path)
            else:
                why = "it may hold credentials, so no copy was taken" if b.get("secret") else "no baseline copy (file too big or backups off)"
                add("manual", f, "cannot restore %s automatically: %s" % (path, why))
        elif cat == "gitconfig":
            pass  # handled after the loop, once we know whether a file restore covers it
        elif cat.startswith("pkg."):
            cmd = _pkg_commands(cat[4:], key, ch, b, a)
            if cmd:
                add("command", f, _cmd_str(cmd), cmd=cmd, expect_version=a.get("version"), pkg_cat=cat)
            else:
                add("manual", f, "revert %s package %s by hand (was %s)" % (cat[4:], key, b.get("version")))
        elif cat == "process":
            add("kill", f, "stop pid %s (started %s) and its new children" % (a.get("pid"), a.get("start")),
                pid=a.get("pid"), start=a.get("start"), uid=a.get("uid"), pgid=a.get("pgid"),
                children=a.get("children", []), zombie=a.get("state") == "Z")
        elif cat == "port":
            if ch == "created":
                add("covered", f, "covered by stopping the process that owns port %s" % a.get("port"), pids=a.get("pids", []))
                continue
            add("manual", f, "port %s had a listener before; restart that service by hand if it was killed" % key)
        elif cat.startswith("docker."):
            kind = cat[7:]
            if ch != "created":
                add("manual", f, "docker %s %s was removed; it cannot be recreated automatically" % (kind, key))
                continue
            cmd = {"container": ["docker", "rm", "-f", key], "volume": ["docker", "volume", "rm", key],
                   "network": ["docker", "network", "rm", key], "image": ["docker", "rmi", key]}[kind]
            add("command", f, _cmd_str(cmd), cmd=cmd)
        elif cat == "cron":
            if not any(x["kind"] == "crontab" for x in actions):
                bk = ((before_snapshot or {}).get("items", {}).get("cron", {}).get("__crontab__") or {})
                add("crontab", f, "restore the crontab to its baseline content", backup=bk.get("backup"),
                    baseline_fp=bk.get("fp"), expect=diff.get("after_crontab_fp"))

    killed = set()
    for x in actions:
        if x["kind"] == "kill":
            killed.add(x["pid"])
            killed.update(x.get("children", []))
    for x in actions:
        if x["kind"] == "covered" and x["category"] == "port" and not (set(x.get("pids", [])) & killed):
            x["kind"] = "manual"
            x["description"] = "port %s is owned by a process that existed before the session; stop it by hand if needed" % x["key"]

    gitfiles = {os.path.join(home(), ".gitconfig"), os.path.join(home(), ".config", "git", "config")}
    covered = bool(gitfiles & restored_paths)
    for f in findings:
        if f["category"] != "gitconfig":
            continue
        if covered:
            add("covered", f, "covered by restoring the global git config file")
            continue
        key, b, a = f["key"], f["before"] or {}, f["after"] or {}
        cmds = [["git", "config", "--global", "--unset-all", key]] if f["change"] != "removed" else []
        for v in b.get("values", []):
            cmds.append(["git", "config", "--global", "--add", key, v])
        add("git", f, " && ".join(_cmd_str(c) for c in cmds), cmds=cmds, expect_values=a.get("values"))
    order = {"kill": 0, "command": 1, "crontab": 2, "trash": 3, "restore": 3, "git": 4, "covered": 5, "manual": 6}
    actions.sort(key=lambda x: (order.get(x["kind"], 9), x["n"]))
    for i, a in enumerate(actions, 1):
        a["n"] = i
    return actions


def _abs(diff, cat, key):
    if cat.startswith("repo."):
        return os.path.join(diff["repo_root"], key)
    return key


def _unit_disable(path):
    name = os.path.basename(path)
    if path.endswith(".plist"):
        return [["launchctl", "bootout", "gui/%d" % os.getuid(), path]]
    if name.endswith((".service", ".timer", ".socket", ".path")):
        return [["systemctl", "--user", "disable", "--now", name]]
    return []


# ------------------------------------------------------------------ apply
def _current_fp(cat, path):
    if cat == "tmp":
        try:
            st = os.lstat(path)
        except OSError:
            return None
        if os.path.isdir(path) and not os.path.islink(path):
            return "dir"
        return "file:%d:%d" % (st.st_size, int(st.st_mtime))
    if path.endswith("#mcp"):
        subset = C._claude_json_semantic(path[:-4])
        return None if subset is None else sha256_text(C.canonical_json(subset))
    rec = file_fingerprint(path, cap=8 * 1024 * 1024 if cat.startswith("repo.") else HASH_CAP)
    if rec is None:
        return None
    if cat == "repo.ignored" and rec["kind"] == "file":
        return "%s:%s" % (rec.get("size"), rec.get("mtime"))
    return rec["fp"]


def _to_trash(path, trash_root):
    dest = os.path.join(trash_root, path.lstrip(os.sep).replace(":", "_"))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.lexists(dest):
        dest += ".%d" % int(time.time() * 1000)
    shutil.move(path, dest)
    return dest


def _run(cmd):
    from .util import run
    r = run(cmd, timeout=300)
    if r is None:
        return False, "%s not found" % cmd[0]
    return r[0] == 0, (r[2] or r[1]).strip()[-400:]


def _proc_matches(pid, start, uid):
    procs = C.list_processes()
    rec = procs.get(pid)
    if not rec:
        return None
    if start is not None and abs(rec["start"] - start) > 1:
        return False
    if uid is not None and rec["uid"] != uid:
        return False
    return rec


def _still_running(pid, start, uid):
    """True while the process has not exited. A process that exited but whose parent has not
    reaped it yet shows up as a zombie (state Z): it is no longer running, and only its parent
    can remove the table entry, so it counts as stopped."""
    rec = _proc_matches(pid, start, uid)
    return bool(rec) and rec.get("state") != "Z"


def apply_plan(actions, session, before_snapshot):
    trash_root = os.path.join(session, "trash", time.strftime("%Y%m%d-%H%M%S"))
    backup_dir = os.path.join(session, "backup")
    baseline_pgids = {r.get("pgid") for r in before_snapshot["items"].get("process", {}).values()}
    results = []
    for a in actions:
        res = {"n": a["n"], "finding": a["finding"], "kind": a["kind"]}
        try:
            res.update(_apply_one(a, trash_root, backup_dir, baseline_pgids))
        except Exception as exc:  # report and keep going; one failure must not hide the rest
            res.update(status="failed", detail=str(exc))
        results.append(res)
    return results, trash_root


def _apply_one(a, trash_root, backup_dir, baseline_pgids):
    kind = a["kind"]
    if kind in ("manual", "covered"):
        return {"status": "skipped", "detail": a["description"]}
    if kind == "trash":
        path = a["path"]
        cur = _current_fp(a["category"], path)
        if cur is None:
            return {"status": "already-gone"}
        if a.get("expect") not in (None, "dir") and cur != a["expect"]:
            return {"status": "skipped", "detail": "changed since the diff; not touching it"}
        for cmd in a.get("pre", []):
            _run(cmd)
        dest = _to_trash(path, trash_root)
        return {"status": "done", "detail": "moved to %s" % dest}
    if kind == "restore":
        path = a["path"]
        cur = _current_fp(a["category"], path)
        if a.get("expect") is not None and cur != a["expect"]:
            return {"status": "skipped", "detail": "changed since the diff; not touching it"}
        with open(os.path.join(backup_dir, a["backup"]), "rb") as fh:
            data = fh.read()
        if path.endswith("#mcp"):
            real = path[:-4]
            with open(real, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
            shutil.copy2(real, _trash_target(real, trash_root))
            subset = json.loads(data.decode())
            for k in ("mcpServers", "hooks"):
                if subset.get(k) is None:
                    doc.pop(k, None)
                else:
                    doc[k] = subset[k]
            for proj, pdata in (doc.get("projects") or {}).items():
                if isinstance(pdata, dict):
                    want = subset.get("projects", {}).get(proj, {})
                    for k in ("mcpServers", "enabledMcpjsonServers", "hooks"):
                        if k in want:
                            pdata[k] = want[k]
                        elif pdata.get(k):
                            pdata.pop(k, None)
            _atomic_write(real, json.dumps(doc, indent=2).encode())
            return {"status": "done", "detail": "MCP/hook sections restored; full previous file copied to trash"}
        if os.path.lexists(path):
            _to_trash(path, trash_root)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        _atomic_write(path, data)
        if a.get("mode"):
            os.chmod(path, int(a["mode"], 8))
        return {"status": "done"}
    if kind == "command":
        ok, out = _run(a["cmd"])
        return {"status": "done" if ok else "failed", "detail": out}
    if kind == "git":
        for cmd in a["cmds"]:
            ok, out = _run(cmd)
            if not ok and "--unset-all" not in cmd:
                return {"status": "failed", "detail": out}
        return {"status": "done"}
    if kind == "crontab":
        current = C.read_crontab()
        if current is None:
            return {"status": "failed", "detail": "crontab not available"}
        if a.get("expect") and sha256_text(current) != a["expect"]:
            return {"status": "skipped", "detail": "crontab changed since the diff; not touching it"}
        baseline = ""
        if a.get("backup"):
            with open(os.path.join(backup_dir, a["backup"]), "r", encoding="utf-8") as fh:
                baseline = fh.read()
        if not baseline.strip():
            ok, out = _run(["crontab", "-r"])
        else:
            with tempfile.NamedTemporaryFile("w", delete=False, suffix=".cron") as fh:
                fh.write(baseline)
            ok, out = _run(["crontab", fh.name])
            os.unlink(fh.name)
        return {"status": "done" if ok else "failed", "detail": out}
    if kind == "kill":
        rec = _proc_matches(a["pid"], a.get("start"), a.get("uid"))
        if rec is None:
            return {"status": "already-gone"}
        if rec is False:
            return {"status": "skipped", "detail": "PID now belongs to a different process; not signalling"}
        if rec.get("state") == "Z":
            return {"status": "skipped", "detail": "zombie: only its parent (pid %s) can reap it" % rec.get("ppid")}
        targets = [a["pid"]] + list(a.get("children", []))
        group = a.get("pgid") == a["pid"] and a["pid"] not in baseline_pgids
        _signal(a["pid"], targets, group, signal.SIGTERM)
        for _ in range(30):
            if not _still_running(a["pid"], a.get("start"), a.get("uid")):
                return {"status": "done", "detail": "SIGTERM" + (" to process group" if group else "")}
            time.sleep(0.1)
        _signal(a["pid"], targets, group, signal.SIGKILL)
        time.sleep(0.2)
        return {"status": "done", "detail": "SIGKILL after SIGTERM timed out"}
    return {"status": "skipped", "detail": "unknown action"}


def _signal(pid, targets, group, sig):
    try:
        if group:
            os.killpg(pid, sig)
            return
    except OSError:
        pass
    for t in targets:
        try:
            os.kill(t, sig)
        except OSError:
            pass


def _trash_target(path, trash_root):
    dest = os.path.join(trash_root, path.lstrip(os.sep))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    return dest


def _atomic_write(path, data: bytes):
    tmp = path + ".lnt-tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


def verify(diff, before, after2):
    """For each finding, is the item back to its baseline state?"""
    out = {}
    b_items, a_items = before["items"], after2["items"]
    for f in diff["findings"]:
        cat, key = f["category"], f["key"]
        if cat == "process":
            pid, start = f["after"]["pid"], f["after"]["start"]
            was_zombie = f["after"].get("state") == "Z"
            # A process we stopped may linger as a zombie until its own parent reaps it; it has
            # exited, so it is back at baseline. A finding that was already a zombie is not fixed
            # until the entry is actually gone.
            alive = any(r["pid"] == pid and abs(r["start"] - start) <= 1 and (was_zombie or r.get("state") != "Z")
                        for r in a_items.get("process", {}).values())
            out[f["id"]] = not alive
            continue
        b = b_items.get(cat, {}).get(key)
        a = a_items.get(cat, {}).get(key)
        if b is None:
            out[f["id"]] = a is None
        elif a is None:
            out[f["id"]] = False
        else:
            out[f["id"]] = a.get("fp") == b.get("fp")
    return out
