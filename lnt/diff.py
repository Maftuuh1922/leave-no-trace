"""Compare two snapshots and turn the differences into findings with evidence."""
from __future__ import annotations

import os

from .config import in_scope, matches_any
from .util import human_bytes, is_within, redact

SEVERITY_ORDER = {"info": 0, "low": 1, "medium": 2, "high": 3}
TMP_GROWTH_FLAG = 100 * 1024 * 1024  # an existing temp dir growing by more than this is reported
TMP_BIG = 100 * 1024 * 1024

GROUPS = {
    "repo.untracked": "LITTER",
    "repo.ignored": "LITTER",
    "repo.tracked": "IN-SCOPE",
    "tmp": "LITTER",
    "dotfile": "ENV",
    "gitconfig": "ENV",
    "userbin": "ENV",
    "agentconfig": "AGENT-CONFIG",
    "process": "RUNNING",
    "port": "RUNNING",
    "cron": "PERSISTENCE",
    "unit": "PERSISTENCE",
}


def group_of(cat: str) -> str:
    if cat.startswith("pkg."):
        return "ENV"
    if cat.startswith("docker."):
        return "RUNNING"
    return GROUPS.get(cat, "OTHER")


def _ancestors(procs_by_pid, pid):
    seen = set()
    while pid and pid not in seen:
        seen.add(pid)
        rec = procs_by_pid.get(pid)
        if not rec:
            break
        pid = rec.get("ppid")
    return seen


def _rel(cfg, path):
    try:
        return os.path.relpath(path, cfg["repo_root"])
    except ValueError:
        return path


def _title(cat, change, key, before, after, cfg):
    rec = after or before or {}
    verb = {"created": "new", "modified": "changed", "removed": "removed"}[change]
    if cat == "repo.untracked":
        return "%s untracked file in repo: %s" % (verb, key)
    if cat == "repo.ignored":
        return "%s git-ignored path in repo: %s" % (verb, key)
    if cat == "repo.tracked":
        return "tracked file changed (requested work): %s" % key
    if cat == "tmp":
        size = human_bytes(rec.get("size"))
        if change == "modified":
            return "temp dir grew %s -> %s: %s" % (human_bytes(before.get("size")), size, key)
        return "%s temp %s (%s): %s" % (verb, rec.get("kind", "entry"), size, key)
    if cat == "dotfile":
        return "%s dotfile: %s" % (verb, key.replace(os.path.expanduser("~"), "~", 1))
    if cat == "agentconfig":
        shown = key.replace(os.path.expanduser("~"), "~", 1)
        if key.endswith("#mcp"):
            b = set((before or {}).get("servers") or [])
            a = set((after or {}).get("servers") or [])
            added = sorted(a - b)
            return "MCP/hook config changed in %s%s" % (shown[:-4], (" (added: %s)" % ", ".join(added)) if added else "")
        return "%s agent config/hook: %s" % (verb, shown)
    if cat == "gitconfig":
        if change == "removed":
            return "git config --global %s removed (was %s)" % (key, redact(", ".join(before["values"])))
        return "git config --global %s = %s" % (key, redact(", ".join(after["values"])))
    if cat.startswith("pkg."):
        mgr = cat[4:]
        if change == "modified":
            return "global %s package %s %s -> %s" % (mgr, key, before.get("version"), after.get("version"))
        return "%s global %s package: %s %s" % (verb, mgr, key, rec.get("version"))
    if cat == "userbin":
        return "%s executable in user bin: %s" % (verb, key.replace(os.path.expanduser("~"), "~", 1))
    if cat == "process":
        state = " [zombie]" if rec.get("state") == "Z" else ""
        cmd = " ".join((rec.get("cmd") or "").split())
        return "process still running%s: pid %s `%s`" % (state, rec.get("pid"), cmd[:80] + ("…" if len(cmd) > 80 else ""))
    if cat == "port":
        if change == "removed":
            return "port %s was listening before and is gone (server killed?)" % rec.get("port")
        if change == "modified":
            return "port %s now owned by a different process" % rec.get("port")
        return "new listening port %s (pid %s)" % (rec.get("port"), ",".join(str(p) for p in rec.get("pids", [])))
    if cat == "docker.container":
        return "%s docker container %s (%s, %s)" % (verb, rec.get("name"), rec.get("image"), rec.get("status"))
    if cat.startswith("docker."):
        return "%s docker %s: %s" % (verb, cat[7:], rec.get("name") or rec.get("repo") or key[:19])
    if cat == "cron":
        return "%s crontab entry: %s" % (verb, rec.get("line"))
    if cat == "unit":
        return "%s user service/agent unit: %s" % (verb, key.replace(os.path.expanduser("~"), "~", 1))
    return "%s %s: %s" % (verb, cat, key)


def _severity(cat, change, before, after):
    if cat in ("repo.tracked", "repo.ignored"):
        return "info"
    if cat == "repo.untracked":
        return "medium" if change == "removed" else "low"
    if cat == "tmp":
        rec = after or before or {}
        return "medium" if (rec.get("size") or 0) >= TMP_BIG else "low"
    if cat in ("agentconfig", "cron", "unit"):
        return "high"
    if cat == "port" and change == "removed":
        return "high"
    if cat in ("docker.image", "docker.network", "docker.volume") and change == "created":
        return "low"
    return "medium"


def _evidence(cat, rec, cfg):
    if not rec:
        return {}
    keep = ("sha256", "size", "mode", "mtime", "kind", "version", "pid", "ppid", "pgid", "start", "cwd",
            "cmd", "state", "port", "pids", "procs", "name", "image", "status", "created", "line", "servers",
            "values", "files", "secret", "target", "python", "children")
    ev = {k: rec[k] for k in keep if k in rec and rec[k] not in (None, "", [])}
    if "values" in ev:
        ev["values"] = [redact(v) for v in ev["values"]]
    return ev


def compute_diff(before, after, extra_ignores=(), all_processes=False):
    cfg = before["config"]
    scope = cfg.get("scope", [])
    ignores = list(cfg.get("ignores", [])) + list(extra_ignores)
    b_items = before["items"]
    a_items = after["items"]
    findings = []
    notes = []

    a_procs = {r["pid"]: r for r in a_items.get("process", {}).values()}
    # lnt's own process and its ancestors (shell, `timeout`, agent tool runner ...) in either snapshot
    excluded = _ancestors(a_procs, after.get("pid")) | {after.get("pid"), before.get("pid")}
    excluded |= set(after.get("self_pids") or []) | set(before.get("self_pids") or [])

    new_ports = {}
    for key, rec in a_items.get("port", {}).items():
        if key not in b_items.get("port", {}):
            for p in rec.get("pids", []):
                new_ports.setdefault(p, []).append(rec["port"])

    tmp_dirs = cfg.get("tmp_dirs", [])
    other_new = 0
    proc_candidates = {}
    new_procs = [(k, r) for k, r in a_items.get("process", {}).items()
                 if k not in b_items.get("process", {}) and r["pid"] not in excluded]
    missing_cwd = [r["pid"] for _, r in new_procs if r.get("cwd") is None]
    if missing_cwd:
        from .collectors import process_cwds
        cwds = process_cwds(missing_cwd)
        for _, r in new_procs:
            if r.get("cwd") is None:
                r["cwd"] = cwds.get(r["pid"])
    for key, rec in new_procs:
        cwd = rec.get("cwd")
        reason = None
        if rec["pid"] in new_ports:
            reason = "listening on %s" % ",".join(str(p) for p in new_ports[rec["pid"]])
        elif rec.get("state") == "Z":
            reason = "zombie"
        elif cwd and (is_within(cwd, cfg["repo_root"]) or any(is_within(cwd, t) for t in tmp_dirs)):
            reason = "cwd inside repo/tmp"
        elif cfg["repo_root"] in (rec.get("cmd") or ""):
            reason = "command references repo"
        elif all_processes:
            reason = "new process"
        if reason:
            proc_candidates[key] = dict(rec, reason=reason)
        else:
            other_new += 1
    cand_pids = {r["pid"]: k for k, r in proc_candidates.items()}
    for key, rec in list(proc_candidates.items()):
        parent_key = cand_pids.get(rec.get("ppid"))
        if parent_key and parent_key in proc_candidates and parent_key != key:
            proc_candidates[parent_key].setdefault("children", []).append(rec["pid"])
            del proc_candidates[key]
    if other_new:
        notes.append("%d other new processes outside the repo/tmp without listening ports were not reported (use --all-processes)." % other_new)

    categories = sorted(set(b_items) | set(a_items))
    for cat in categories:
        if cat == "process":
            changes = [(k, "created", None, r) for k, r in proc_candidates.items()]
        else:
            b = b_items.get(cat, {})
            a = a_items.get(cat, {})
            changes = []
            for k in a:
                if k not in b:
                    changes.append((k, "created", None, a[k]))
                elif a[k].get("fp") != b[k].get("fp"):
                    changes.append((k, "modified", b[k], a[k]))
            for k in b:
                if k not in a:
                    changes.append((k, "removed", b[k], None))
        for key, change, brec, arec in changes:
            if cat == "cron" and key == "__crontab__":
                continue
            if cat == "tmp" and change == "removed":
                continue
            if cat == "tmp" and change == "modified":
                if not (brec.get("kind") == "dir" and (arec.get("size") or 0) - (brec.get("size") or 0) >= TMP_GROWTH_FLAG):
                    continue
            if cat == "repo.ignored" and change != "created":
                continue
            fid = "%s:%s" % (cat, key)
            names = [fid]
            rec = arec or brec or {}
            if cat == "process":
                names += ["process:%s" % rec.get("comm"), "process:%s" % rec.get("cmd")]
                fid = "process:%s:%s" % (rec.get("comm"), rec.get("pid"))
            if cat.startswith("pkg."):
                names.append("package:%s:%s" % (cat[4:], key))
            relpath = None
            if cat.startswith("repo."):
                relpath = key
            elif cat == "agentconfig" and is_within(key, cfg["repo_root"]):
                relpath = _rel(cfg, key)
                names.append("agentconfig:%s" % relpath)
            scoped = cat == "repo.tracked" or (relpath is not None and in_scope(relpath, scope))
            ignored_by = matches_any(names, ignores)
            findings.append({
                "id": fid,
                "category": cat,
                "group": "IN-SCOPE" if scoped else group_of(cat),
                "change": change,
                "key": key,
                "severity": "info" if scoped else _severity(cat, change, brec, arec),
                "title": _title(cat, change, key, brec or {}, arec or {}, cfg),
                "evidence": {"before": _evidence(cat, brec, cfg), "after": _evidence(cat, arec, cfg)},
                "before": brec,
                "after": arec,
                "in_scope": scoped,
                "ignored_by": ignored_by,
            })
    order = {"AGENT-CONFIG": 0, "PERSISTENCE": 1, "RUNNING": 2, "ENV": 3, "LITTER": 4, "OTHER": 5, "IN-SCOPE": 6}
    findings.sort(key=lambda f: (order.get(f["group"], 9), -SEVERITY_ORDER[f["severity"]], f["id"]))
    return {
        "lnt_diff": 1,
        "after_crontab_fp": (a_items.get("cron", {}).get("__crontab__") or {}).get("fp"),
        "before_taken_at": before["taken_at"],
        "after_taken_at": after["taken_at"],
        "repo_root": cfg["repo_root"],
        "findings": findings,
        "notes": notes,
        "status": after.get("status", {}),
        "seconds": {"before": before.get("seconds"), "after": after.get("seconds")},
    }


def actionable(diff, fail_on="low"):
    floor = SEVERITY_ORDER.get(fail_on, 1)
    return [f for f in diff["findings"]
            if not f["in_scope"] and not f["ignored_by"] and SEVERITY_ORDER[f["severity"]] >= floor]
