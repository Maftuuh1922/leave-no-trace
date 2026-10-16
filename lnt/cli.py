"""lnt: snapshot the machine before an agent session, diff after, plan a revert."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

from . import __version__
from .collectors import take_snapshot
from .config import find_repo_root, load_config, session_dir
from .diff import actionable, compute_diff
from .receipt import headline, render
from .revert import apply_plan, build_plan, irreversible, verify
from .util import read_json, write_json

EXIT_CLEAN, EXIT_FINDINGS, EXIT_ERROR, EXIT_NO_BASELINE = 0, 1, 2, 3


def _paths(args):
    repo = find_repo_root(args.repo or os.getcwd())
    sess = session_dir(repo, args.session)
    return repo, sess


def cmd_snapshot(args):
    repo, sess = _paths(args)
    cfg = load_config(repo, args.scope)
    os.makedirs(sess, mode=0o700, exist_ok=True)
    for name in ("after.json", "diff.json", "plan.json"):
        try:
            os.unlink(os.path.join(sess, name))
        except OSError:
            pass
    snap = take_snapshot(cfg, backup_dir=os.path.join(sess, "backup"), label="before")
    noise = []
    if args.learn_noise:
        time.sleep(args.learn_noise)
        second = take_snapshot(cfg, backup_dir=None, label="noise")
        d = compute_diff(snap, second, all_processes=True)
        for f in d["findings"]:
            noise.append(f["id"])
            if f["category"] == "process":
                noise.append("process:%s" % f["after"].get("comm"))
        snap = take_snapshot(cfg, backup_dir=os.path.join(sess, "backup"), label="before")
    write_json(os.path.join(sess, "before.json"), snap)
    write_json(os.path.join(sess, "noise.json"), sorted(set(noise)))
    counts = {k: len(v) for k, v in snap["items"].items()}
    if args.json:
        print(json.dumps({"session": sess, "seconds": snap["seconds"], "counts": counts, "status": snap["status"],
                          "learned_noise": len(set(noise))}, indent=1))
    else:
        print("lnt: baseline saved (%s items, %.2fs) → %s" % (sum(counts.values()), snap["seconds"], sess))
        if noise:
            print("lnt: learned %d noisy item(s) that change on their own; they will be ignored." % len(set(noise)))
        for k, v in sorted(snap["status"].items()):
            if not str(v).startswith("ok"):
                print("lnt:   %s: %s" % (k, v))
    return EXIT_CLEAN


def _load_before(sess):
    path = os.path.join(sess, "before.json")
    if not os.path.isfile(path):
        return None
    return read_json(path)


def _do_diff(args, before, sess):
    if getattr(args, "scope", None):
        before["config"]["scope"] = list(before["config"].get("scope", [])) + list(args.scope)
    repo = before["config"]["repo_root"]
    cfg = load_config(repo, before["config"].get("scope"))
    cfg.tmp_dirs = before["config"]["tmp_dirs"]
    before["config"]["ignores"] = cfg.ignores
    after = take_snapshot(cfg, backup_dir=None, label="after")
    noise = []
    if os.path.isfile(os.path.join(sess, "noise.json")):
        noise = read_json(os.path.join(sess, "noise.json"))
    diff = compute_diff(before, after, extra_ignores=noise, all_processes=getattr(args, "all_processes", False))
    write_json(os.path.join(sess, "after.json"), after)
    write_json(os.path.join(sess, "diff.json"), diff)
    plan = build_plan(diff, before_snapshot=before)
    write_json(os.path.join(sess, "plan.json"), plan)
    return diff, plan, after


def _emit(text, out):
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)


def cmd_diff(args):
    repo, sess = _paths(args)
    before = _load_before(sess)
    if before is None:
        print("lnt: no baseline for this repo/session. Run `lnt snapshot` first.", file=sys.stderr)
        return EXIT_NO_BASELINE
    diff, plan, _ = _do_diff(args, before, sess)
    if args.format == "json":
        _emit(json.dumps({"headline": headline(diff, args.fail_on), "diff": diff, "plan": plan}, indent=1) + "\n", args.out)
    else:
        _emit(render(diff, args.format, plan=plan, fail_on=args.fail_on, show_all=args.all), args.out)
    if args.fail_on == "none":
        return EXIT_CLEAN
    return EXIT_FINDINGS if actionable(diff, args.fail_on) else EXIT_CLEAN


def cmd_receipt(args):
    repo, sess = _paths(args)
    path = os.path.join(sess, "diff.json")
    if not os.path.isfile(path):
        print("lnt: no diff yet. Run `lnt diff` first.", file=sys.stderr)
        return EXIT_NO_BASELINE
    diff = read_json(path)
    plan = read_json(os.path.join(sess, "plan.json")) if os.path.isfile(os.path.join(sess, "plan.json")) else None
    if args.format == "json":
        _emit(json.dumps({"headline": headline(diff, args.fail_on), "diff": diff, "plan": plan}, indent=1) + "\n", args.out)
    else:
        _emit(render(diff, args.format, plan=plan, fail_on=args.fail_on, show_all=args.all), args.out)
    return EXIT_CLEAN


def cmd_revert(args):
    repo, sess = _paths(args)
    before = _load_before(sess)
    dpath = os.path.join(sess, "diff.json")
    if before is None or not os.path.isfile(dpath):
        print("lnt: need a baseline and a diff. Run `lnt snapshot`, then `lnt diff`.", file=sys.stderr)
        return EXIT_NO_BASELINE
    diff = read_json(dpath)
    plan = build_plan(diff, include_info=args.include_info, before_snapshot=before)
    if args.only:
        import fnmatch
        plan = [a for a in plan if any(fnmatch.fnmatchcase(a["finding"], p) for p in args.only)]
    risky = irreversible(plan)
    if not args.apply:
        if args.format == "json":
            print(json.dumps(plan, indent=1))
        else:
            print("lnt revert plan (dry run; nothing was changed). Re-run with --apply to execute.")
            for a in plan:
                tag = "" if a.get("reversible") else "  ! irreversible"
                print("%3d. [%s] %s%s" % (a["n"], a["kind"], a["description"], tag))
                print("      for: %s" % a["finding"])
            if not plan:
                print("  (nothing to revert)")
            if risky:
                print("lnt: %d action(s) cannot be undone (stopping processes, uninstalling packages, removing docker"
                      " objects); --apply asks before running them, or pass --yes." % len(risky))
        return EXIT_CLEAN
    snap_uid = before.get("uid")
    if snap_uid is not None and hasattr(os, "getuid") and os.getuid() != snap_uid:
        print("lnt: refusing to revert: the baseline was taken as uid %s but this is uid %s (sudo?). "
              "Run revert as the same user." % (snap_uid, os.getuid()), file=sys.stderr)
        return EXIT_ERROR
    refuse = set()
    if risky and not args.yes:
        if sys.stdin.isatty() and args.format == "text":
            print("These actions cannot be undone:")
            for a in risky:
                print("%3d. %s\n      (%s)" % (a["n"], a["description"], a["undo"]))
            try:
                ans = input("Run them? [y]es / [s]kip them, revert the rest / [N]o, abort: ").strip().lower()
            except EOFError:
                ans = ""
            if ans in ("y", "yes"):
                pass
            elif ans in ("s", "skip"):
                refuse = {a["n"] for a in risky}
            else:
                print("lnt: aborted; nothing was changed.")
                return EXIT_FINDINGS
        else:
            refuse = {a["n"] for a in risky}
            print("lnt: %d irreversible action(s) refused without confirmation (no terminal to ask). "
                  "Re-run with --yes to allow them." % len(refuse), file=sys.stderr)
    results, trash = apply_plan(plan, sess, before, refuse=refuse)
    cfg = load_config(before["config"]["repo_root"], before["config"].get("scope"))
    cfg.tmp_dirs = before["config"]["tmp_dirs"]
    after2 = take_snapshot(cfg, backup_dir=None, label="after-revert")
    targeted = {a["finding"] for a in plan if a["kind"] not in ("manual",)} - {
        r["finding"] for r in results if r["status"] == "refused"}
    checks = verify(diff, before, after2)
    report = {"results": results, "trash": trash, "verified": {k: v for k, v in checks.items() if k in targeted}}
    write_json(os.path.join(sess, "revert-report.json"), report)
    ok_n = sum(1 for v in report["verified"].values() if v)
    if args.format == "json":
        print(json.dumps(report, indent=1))
    else:
        for r in results:
            detail = (r.get("detail") or "").replace("moved to " + trash, "moved to trash: ")
            print("%3d. %-12s %s%s" % (r["n"], r["status"], r["finding"], (" - " + detail) if detail else ""))
        print("lnt: %d/%d reverted item(s) verified back at their baseline state. Trash: %s" % (ok_n, len(report["verified"]), trash))
    manual = [a for a in plan if a["kind"] == "manual"]
    refused = [r for r in results if r["status"] == "refused"]
    if refused and args.format == "text":
        print("lnt: %d action(s) refused and left as they are." % len(refused))
    return EXIT_CLEAN if ok_n == len(report["verified"]) and not manual and not refused else EXIT_FINDINGS


def cmd_run(args):
    if not args.command:
        print("lnt run: give a command after --", file=sys.stderr)
        return EXIT_ERROR
    cmd_snapshot(argparse.Namespace(**dict(vars(args), json=False, learn_noise=0)))
    rc = subprocess.call(args.command)
    print("lnt: command exited %d" % rc)
    return cmd_diff(args)


def cmd_doctor(args):
    repo, sess = _paths(args)
    cfg = load_config(repo)
    snap = take_snapshot(cfg, backup_dir=None, label="doctor")
    print("lnt %s · python %s · %s" % (__version__, sys.version.split()[0], sys.platform))
    print("repo: %s\nstate: %s\ntmp dirs: %s" % (repo, sess, ", ".join(cfg.tmp_dirs)))
    for k, v in sorted(snap["status"].items()):
        print("  %-14s %s" % (k, v))
    print("snapshot time: %.2fs (%s)" % (snap["seconds"], ", ".join("%s %.2fs" % kv for kv in sorted(snap["timings"].items()))))
    return EXIT_CLEAN


def build_parser():
    p = argparse.ArgumentParser(prog="lnt", description="Leave No Trace: see and undo what an agent session left behind.")
    p.add_argument("--version", action="version", version="lnt " + __version__)
    sub = p.add_subparsers(dest="cmd")

    def common(sp):
        sp.add_argument("--repo", help="project directory (default: git root of cwd)")
        sp.add_argument("--session", help="session name, for several sessions per repo (default: default)")

    sp = sub.add_parser("snapshot", help="record the baseline before a session")
    common(sp)
    sp.add_argument("--scope", action="append", default=[], help="repo path/glob the session is allowed to create (repeatable)")
    sp.add_argument("--learn-noise", type=float, default=0, metavar="SECONDS",
                    help="take two baselines this many seconds apart and ignore what changes on its own")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_snapshot)

    def diff_opts(sp, default_fmt="text"):
        sp.add_argument("--format", choices=["text", "md", "json"], default=default_fmt)
        sp.add_argument("--fail-on", choices=["info", "low", "medium", "high", "none"], default="low",
                        help="lowest severity that makes the exit code non-zero (default: low)")
        sp.add_argument("--all", action="store_true", help="also list in-scope, ignored and info findings")
        sp.add_argument("--out", help="write the output to this file")

    sp = sub.add_parser("diff", help="compare now against the baseline; exit 1 if anything was left behind")
    common(sp)
    diff_opts(sp)
    sp.add_argument("--scope", action="append", default=[])
    sp.add_argument("--all-processes", action="store_true", help="report every new process, not only repo/tmp/listening ones")
    sp.set_defaults(fn=cmd_diff)

    sp = sub.add_parser("receipt", help="print the last diff as a shareable receipt")
    common(sp)
    diff_opts(sp, "md")
    sp.set_defaults(fn=cmd_receipt)

    sp = sub.add_parser("revert", help="show (default) or apply the revert plan for the last diff")
    common(sp)
    g = sp.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", help="show the plan only; changes nothing (the default)")
    g.add_argument("--apply", action="store_true", help="execute the plan, then verify against the baseline")
    sp.add_argument("-y", "--yes", action="store_true",
                    help="with --apply: also run irreversible actions (stop processes, uninstall/reinstall packages, "
                         "remove docker objects) without asking. Without it they are asked about on a terminal and "
                         "refused otherwise")
    sp.add_argument("--include-info", action="store_true", help="also revert info-level items (e.g. new git-ignored build dirs)")
    sp.add_argument("--only", action="append", help="only actions whose finding id matches this glob (repeatable)")
    sp.add_argument("--format", choices=["text", "json"], default="text")
    sp.set_defaults(fn=cmd_revert)

    sp = sub.add_parser("run", help="snapshot, run a command, then diff")
    common(sp)
    diff_opts(sp)
    sp.add_argument("--scope", action="append", default=[])
    sp.add_argument("--all-processes", action="store_true")
    sp.add_argument("command", nargs=argparse.REMAINDER)
    sp.set_defaults(fn=cmd_run)

    sp = sub.add_parser("doctor", help="show which collectors work on this machine and how long a snapshot takes")
    common(sp)
    sp.set_defaults(fn=cmd_doctor)
    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "fn", None):
        parser.print_help()
        return EXIT_ERROR
    if getattr(args, "command", None) and args.command[:1] == ["--"]:
        args.command = args.command[1:]
    try:
        return args.fn(args)
    except KeyboardInterrupt:
        return EXIT_ERROR
    except Exception as exc:
        print("lnt: error: %s" % exc, file=sys.stderr)
        if os.environ.get("LNT_DEBUG"):
            raise
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
