import json
import os
import subprocess
import sys
import time

from conftest import lnt


def test_clean_session_exits_zero(sandbox):
    assert lnt(sandbox, "snapshot").returncode == 0
    (sandbox["repo"] / "README.md").write_text("# t\nedited\n")  # requested work
    r = lnt(sandbox, "diff")
    assert r.returncode == 0, r.stdout
    assert "Clean" in r.stdout


def test_no_baseline_exit_code(sandbox):
    assert lnt(sandbox, "diff").returncode == 3


def test_litter_detected_and_receipt(sandbox):
    lnt(sandbox, "snapshot", "--scope", "src/**")
    (sandbox["repo"] / "tmpclaude-9-cwd").write_text("x")
    (sandbox["repo"] / "src").mkdir()
    (sandbox["repo"] / "src" / "ok.py").write_text("x")
    subprocess.run(["git", "config", "--global", "user.email", "agent@x"], check=True)
    r = lnt(sandbox, "diff", "--format", "json")
    assert r.returncode == 1
    data = json.loads(r.stdout)
    ids = {f["id"] for f in data["diff"]["findings"] if not f["in_scope"]}
    assert "repo.untracked:tmpclaude-9-cwd" in ids
    assert "gitconfig:user.email" in ids
    assert not any(f["key"] == "src/ok.py" and not f["in_scope"] for f in data["diff"]["findings"])
    md = lnt(sandbox, "receipt", "--format", "md").stdout
    assert "Leave No Trace receipt" in md and "tmpclaude-9-cwd" in md and "| # |" in md
    assert "agent@x" in md  # git values are shown (redacted only when they look like secrets)


def test_dry_run_changes_nothing(sandbox):
    lnt(sandbox, "snapshot")
    junk = sandbox["repo"] / "junk.txt"
    junk.write_text("x")
    lnt(sandbox, "diff")
    r = lnt(sandbox, "revert")
    assert r.returncode == 0 and "dry run" in r.stdout
    assert junk.exists()


def test_apply_moves_to_trash_and_restores_exactly(sandbox):
    gc = sandbox["home"] / ".gitconfig"
    original = gc.read_bytes()
    lnt(sandbox, "snapshot")
    junk = sandbox["repo"] / "junk.txt"
    junk.write_text("x")
    subprocess.run(["git", "config", "--global", "core.hooksPath", "/tmp/evil"], check=True)
    lnt(sandbox, "diff")
    r = lnt(sandbox, "revert", "--apply")
    assert r.returncode == 0, r.stdout + r.stderr
    assert not junk.exists()
    assert gc.read_bytes() == original
    trash = list((sandbox["root"] / "state").rglob("junk.txt"))
    assert trash, "created files must go to the trash, not be deleted"
    assert lnt(sandbox, "diff").returncode == 0


def test_revert_skips_items_changed_after_diff(sandbox):
    lnt(sandbox, "snapshot")
    f = sandbox["repo"] / "notes.txt"
    f.write_text("v1")
    lnt(sandbox, "diff")
    f.write_text("v2 - the user kept working on it")
    lnt(sandbox, "revert", "--apply")
    assert f.read_text().startswith("v2"), "a file changed after the diff must not be touched"


def test_revert_never_touches_files_outside_the_diff(sandbox):
    lnt(sandbox, "snapshot")
    (sandbox["repo"] / "junk.txt").write_text("x")
    lnt(sandbox, "diff")
    later = sandbox["repo"] / "created-after-diff.txt"
    later.write_text("mine")
    lnt(sandbox, "revert", "--apply")
    assert later.exists()


def test_process_is_stopped_only_if_identity_matches(sandbox):
    lnt(sandbox, "snapshot")
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], cwd=str(sandbox["repo"]),
                         start_new_session=True)
    try:
        time.sleep(0.5)
        d = json.loads(lnt(sandbox, "diff", "--format", "json").stdout)
        assert any(f["category"] == "process" and f["after"]["pid"] == p.pid for f in d["diff"]["findings"])
        plan_path = next((sandbox["root"] / "state").rglob("diff.json"))
        diff = json.loads(plan_path.read_text())
        for f in diff["findings"]:
            if f["category"] == "process":
                f["after"]["start"] -= 1000  # pretend the PID was reused by another process
        plan_path.write_text(json.dumps(diff))
        out = lnt(sandbox, "revert", "--apply", "--yes").stdout
        assert "different process" in out
        assert p.poll() is None, "must not signal a process whose start time does not match"
        lnt(sandbox, "diff")
        lnt(sandbox, "revert", "--apply", "--yes")
        p.wait(timeout=5)
    finally:
        if p.poll() is None:
            p.kill()


def test_agent_config_is_high_severity(sandbox):
    lnt(sandbox, "snapshot")
    d = sandbox["home"] / ".claude"
    d.mkdir()
    (d / "settings.json").write_text('{"hooks": {"Stop": []}}')
    data = json.loads(lnt(sandbox, "diff", "--format", "json").stdout)
    f = [x for x in data["diff"]["findings"] if x["category"] == "agentconfig"][0]
    assert f["severity"] == "high" and f["group"] == "AGENT-CONFIG"
    assert "sha256" in f["evidence"]["after"]


def test_lntignore(sandbox):
    (sandbox["repo"] / ".lntignore").write_text("repo.untracked:*.log\n")
    subprocess.run(["git", "-C", str(sandbox["repo"]), "add", ".lntignore"], check=True)
    subprocess.run(["git", "-C", str(sandbox["repo"]), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "i"], check=True)
    lnt(sandbox, "snapshot")
    (sandbox["repo"] / "debug.log").write_text("x")
    assert lnt(sandbox, "diff").returncode == 0


def test_run_wrapper(sandbox):
    r = lnt(sandbox, "run", "--", sys.executable, "-c", "open('left.txt','w').write('x')")
    assert r.returncode == 1 and "left.txt" in r.stdout


def test_doctor(sandbox):
    r = lnt(sandbox, "doctor")
    assert r.returncode == 0 and "snapshot time" in r.stdout


def test_secret_files_are_hash_only(sandbox):
    (sandbox["home"] / ".netrc").write_text("machine x login a password SECRET\n")
    lnt(sandbox, "snapshot")
    (sandbox["home"] / ".netrc").write_text("machine x login a password CHANGED\n")
    r = lnt(sandbox, "diff", "--format", "json")
    assert "SECRET" not in r.stdout and "CHANGED" not in r.stdout
    backups = list((sandbox["root"] / "state").rglob("backup/*"))
    assert not any(b"SECRET" in b.read_bytes() for b in backups if b.is_file())
    plan = lnt(sandbox, "revert").stdout
    assert "credentials" in plan


WRAPPER = ("import os, subprocess, sys\n"
           "sys.stderr.write('WRAPPER_PID=%d\\n' % os.getpid())\n"
           "sys.exit(subprocess.call(sys.argv[1:]))\n")


def test_wrapper_processes_are_not_reported(sandbox):
    """`timeout 90 lnt diff` from inside the repo: the wrapper chain is lnt's ancestry, not a leftover."""
    from conftest import ROOT
    lnt(sandbox, "snapshot")
    lnt_cmd = [sys.executable, os.path.join(ROOT, "scripts", "lnt"), "diff", "--format", "json"]
    # two wrapper levels, both started after the baseline with their cwd in the repo
    cmd = [sys.executable, "-c", WRAPPER, sys.executable, "-c", WRAPPER] + lnt_cmd
    r = subprocess.run(cmd, cwd=str(sandbox["repo"]), capture_output=True, text=True, env=dict(os.environ), timeout=60)
    wrappers = {int(line.split("=")[1]) for line in r.stderr.splitlines() if line.startswith("WRAPPER_PID=")}
    assert len(wrappers) == 2, r.stderr
    data = json.loads(r.stdout)
    reported = {f["after"]["pid"] for f in data["diff"]["findings"] if f["category"] == "process"}
    assert not (reported & wrappers), [f["title"] for f in data["diff"]["findings"] if f["category"] == "process"]
    assert r.returncode == 0, [f["title"] for f in data["diff"]["findings"]]


def test_revert_exit_zero_when_stopped_process_is_not_reaped_yet(sandbox):
    """A process lnt stops stays a zombie until its own parent reaps it; that is not a failed revert."""
    lnt(sandbox, "snapshot")
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], cwd=str(sandbox["repo"]),
                         start_new_session=True)
    try:
        time.sleep(0.5)
        assert lnt(sandbox, "diff").returncode == 1
        # this test process is the parent and is blocked in subprocess.run, so the child stays unreaped
        r = lnt(sandbox, "revert", "--apply", "--yes", "--format", "json")
        assert r.returncode == 0, r.stdout + r.stderr
        report = json.loads(r.stdout)
        assert all(report["verified"].values()), report["verified"]
        assert p.wait(timeout=5) is not None
    finally:
        if p.poll() is None:
            p.kill()


# ---------------------------------------------------------------- rollback safety
def _sleeper(sandbox):
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], cwd=str(sandbox["repo"]),
                         start_new_session=True)
    time.sleep(0.5)
    return p


def test_explicit_dry_run_flag_changes_nothing(sandbox):
    gc = sandbox["home"] / ".gitconfig"
    lnt(sandbox, "snapshot")
    junk = sandbox["repo"] / "junk.txt"
    junk.write_text("x")
    subprocess.run(["git", "config", "--global", "user.email", "agent@x"], check=True)
    changed = gc.read_bytes()
    p = _sleeper(sandbox)
    try:
        assert lnt(sandbox, "diff").returncode == 1
        state = sandbox["root"] / "state"
        before = sorted(str(x) for x in state.rglob("*"))
        for args in (("--dry-run",), ("--dry-run", "--yes"), ("--dry-run", "--format", "json")):
            r = lnt(sandbox, "revert", *args)
            assert r.returncode == 0, r.stderr
        assert junk.exists() and gc.read_bytes() == changed and p.poll() is None
        assert sorted(str(x) for x in state.rglob("*")) == before, "dry run must not write trash or reports"
        assert "irreversible" in lnt(sandbox, "revert", "--dry-run").stdout
        r = lnt(sandbox, "revert", "--dry-run", "--apply")
        assert r.returncode == 2, "--dry-run and --apply are mutually exclusive"
    finally:
        p.kill()


def test_plan_labels_what_can_be_undone(sandbox):
    lnt(sandbox, "snapshot")
    (sandbox["repo"] / "junk.txt").write_text("x")
    p = _sleeper(sandbox)
    try:
        lnt(sandbox, "diff")
        plan = json.loads(lnt(sandbox, "revert", "--format", "json").stdout)
        by_kind = {a["kind"]: a for a in plan}
        assert by_kind["trash"]["reversible"] is True
        assert by_kind["kill"]["reversible"] is False and by_kind["kill"]["undo"]
    finally:
        p.kill()


def test_irreversible_actions_refused_without_confirmation(sandbox):
    lnt(sandbox, "snapshot")
    junk = sandbox["repo"] / "junk.txt"
    junk.write_text("x")
    p = _sleeper(sandbox)
    try:
        lnt(sandbox, "diff")
        r = lnt(sandbox, "revert", "--apply", "--format", "json")  # stdin is not a terminal here
        assert r.returncode == 1, r.stdout
        report = json.loads(r.stdout)
        status = {x["kind"]: x["status"] for x in report["results"]}
        assert status["kill"] == "refused" and status["trash"] == "done"
        assert p.poll() is None, "a refused kill must not signal the process"
        assert not junk.exists(), "reversible actions still run"
        assert "--yes" in r.stderr
        lnt(sandbox, "diff")
        r = lnt(sandbox, "revert", "--apply", "--yes")
        assert r.returncode == 0, r.stdout + r.stderr
        p.wait(timeout=5)
    finally:
        if p.poll() is None:
            p.kill()


def test_file_actions_outside_watched_roots_are_refused(sandbox):
    outside = sandbox["root"] / "outside.txt"
    outside.write_text("keep me")
    lnt(sandbox, "snapshot")
    (sandbox["home"] / ".bashrc").write_text("export PATH=/x:$PATH\n")
    assert ".bashrc" in lnt(sandbox, "diff").stdout
    dpath = next((sandbox["root"] / "state").rglob("diff.json"))
    diff = json.loads(dpath.read_text())
    for f in diff["findings"]:
        if f["category"] == "dotfile":
            f["key"] = str(outside)  # a tampered or corrupt diff pointing elsewhere
            f["id"] = "dotfile:" + str(outside)
    dpath.write_text(json.dumps(diff))
    r = lnt(sandbox, "revert", "--apply", "--format", "json")
    assert outside.read_text() == "keep me"
    assert any(x["status"] == "refused" for x in json.loads(r.stdout)["results"]), r.stdout


def test_revert_refuses_a_different_user(sandbox):
    lnt(sandbox, "snapshot")
    junk = sandbox["repo"] / "junk.txt"
    junk.write_text("x")
    lnt(sandbox, "diff")
    bpath = next((sandbox["root"] / "state").rglob("before.json"))
    snap = json.loads(bpath.read_text())
    snap["uid"] = os.getuid() + 1
    bpath.write_text(json.dumps(snap))
    r = lnt(sandbox, "revert", "--apply", "--yes")
    assert r.returncode == 2 and "same user" in r.stderr
    assert junk.exists()


def _lnt_tty(sandbox, answer, *args):
    """Run lnt with a pseudo-terminal on stdin/stdout and type `answer` at the prompt."""
    import pty
    import select
    from conftest import ROOT
    master, slave = pty.openpty()
    p = subprocess.Popen([sys.executable, os.path.join(ROOT, "scripts", "lnt")] + list(args),
                         cwd=str(sandbox["repo"]), stdin=slave, stdout=slave, stderr=slave, close_fds=True)
    os.close(slave)
    out, sent, deadline = b"", False, time.time() + 60
    while time.time() < deadline:
        r, _, _ = select.select([master], [], [], 0.2)
        if r:
            try:
                chunk = os.read(master, 4096)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
            if not sent and b"abort:" in out:
                os.write(master, answer.encode() + b"\n")
                sent = True
        elif p.poll() is not None:
            break
    p.wait(timeout=10)
    os.close(master)
    return p.returncode, out.decode(errors="replace")


def test_terminal_prompt_no_aborts_and_skip_keeps_irreversible(sandbox):
    lnt(sandbox, "snapshot")
    junk = sandbox["repo"] / "junk.txt"
    junk.write_text("x")
    p = _sleeper(sandbox)
    try:
        lnt(sandbox, "diff")
        rc, out = _lnt_tty(sandbox, "n", "revert", "--apply")
        assert "cannot be undone" in out and "aborted" in out, out
        assert rc == 1 and junk.exists() and p.poll() is None
        rc, out = _lnt_tty(sandbox, "s", "revert", "--apply")
        assert rc == 1 and "refused" in out, out
        assert not junk.exists() and p.poll() is None
    finally:
        p.kill()
