from lnt.collectors import _claude_json_semantic, parse_git_config_z
from lnt.config import in_scope, matches_any
from lnt.diff import actionable, compute_diff


def snap(items, repo="/r", scope=(), ignores=(), pid=1):
    return {"taken_at": 1, "pid": pid, "items": items, "status": {}, "seconds": 0.1,
            "config": {"repo_root": repo, "scope": list(scope), "ignores": list(ignores), "tmp_dirs": ["/t"]}}


def test_created_modified_removed():
    before = snap({"dotfile": {"/h/.bashrc": {"fp": "a"}, "/h/.zshrc": {"fp": "z"}}, "pkg.npm": {}})
    after = snap({"dotfile": {"/h/.bashrc": {"fp": "b"}, "/h/.profile": {"fp": "p"}},
                  "pkg.npm": {"left-pad": {"fp": "1.0.0", "version": "1.0.0"}}}, pid=2)
    d = compute_diff(before, after)
    got = {(f["id"], f["change"]) for f in d["findings"]}
    assert ("dotfile:/h/.bashrc", "modified") in got
    assert ("dotfile:/h/.profile", "created") in got
    assert ("dotfile:/h/.zshrc", "removed") in got
    assert ("pkg.npm:left-pad", "created") in got
    assert len(actionable(d)) == 4


def test_scope_and_ignore():
    before = snap({"repo.untracked": {}}, scope=["src/**"], ignores=["repo.untracked:*.log"])
    after = snap({"repo.untracked": {"src/new.py": {"fp": "x"}, "debug.log": {"fp": "y"}, "tmpclaude-1-cwd": {"fp": "z"}}})
    d = compute_diff(before, after)
    by = {f["key"]: f for f in d["findings"]}
    assert by["src/new.py"]["in_scope"]
    assert by["debug.log"]["ignored_by"] == "repo.untracked:*.log"
    assert [f["key"] for f in actionable(d)] == ["tmpclaude-1-cwd"]


def test_tracked_changes_are_never_actionable():
    d = compute_diff(snap({"repo.tracked": {}}), snap({"repo.tracked": {"a.py": {"fp": "1"}}}))
    assert d["findings"][0]["group"] == "IN-SCOPE"
    assert actionable(d, "info") == []


def test_fail_on_threshold():
    d = compute_diff(snap({"tmp": {}}), snap({"tmp": {"/t/x": {"fp": "file:1:1", "kind": "file", "size": 1}}}))
    assert len(actionable(d, "low")) == 1
    assert actionable(d, "medium") == []


def test_tmp_growth_only_when_large():
    small = compute_diff(snap({"tmp": {"/t/d": {"fp": "dir", "kind": "dir", "size": 10}}}),
                         snap({"tmp": {"/t/d": {"fp": "dir", "kind": "dir", "size": 20}}}))
    assert small["findings"] == []
    big = compute_diff(snap({"tmp": {"/t/d": {"fp": "dir", "kind": "dir", "size": 10}}}),
                       snap({"tmp": {"/t/d": {"fp": "x", "kind": "dir", "size": 500 * 1024 * 1024}}}))
    assert big["findings"][0]["change"] == "modified"


def test_process_filtering_and_children():
    procs_after = {
        "10:5": {"pid": 10, "ppid": 1, "pgid": 10, "uid": 1, "start": 5, "comm": "node", "cmd": "node dev", "cwd": "/r", "state": "S"},
        "11:5": {"pid": 11, "ppid": 10, "pgid": 10, "uid": 1, "start": 5, "comm": "esbuild", "cmd": "esbuild", "cwd": "/r", "state": "S"},
        "12:5": {"pid": 12, "ppid": 1, "pgid": 12, "uid": 1, "start": 5, "comm": "chrome", "cmd": "chrome", "cwd": "/h", "state": "S"},
        "13:5": {"pid": 13, "ppid": 1, "pgid": 13, "uid": 1, "start": 5, "comm": "lnt", "cmd": "lnt diff", "cwd": "/r", "state": "R"},
    }
    d = compute_diff(snap({"process": {}, "port": {}}), snap({"process": procs_after, "port": {}}, pid=13))
    procs = [f for f in d["findings"] if f["category"] == "process"]
    assert len(procs) == 1
    assert procs[0]["after"]["pid"] == 10 and procs[0]["after"]["children"] == [11]
    assert any("other new processes" in n for n in d["notes"])


def test_removed_listener_is_high():
    before = snap({"port": {"tcp:8000": {"port": 8000, "pids": [5], "fp": "5:1"}}})
    d = compute_diff(before, snap({"port": {}}))
    assert d["findings"][0]["severity"] == "high"


def test_git_config_parse():
    vals = parse_git_config_z("user.email\na@b\0remote.origin.fetch\nx\0remote.origin.fetch\ny\0")
    assert vals == {"user.email": ["a@b"], "remote.origin.fetch": ["x", "y"]}


def test_claude_json_semantic_ignores_counters(tmp_path):
    p = tmp_path / "c.json"
    p.write_text('{"numStartups": 1, "mcpServers": {"a": {}}}')
    a = _claude_json_semantic(str(p))
    p.write_text('{"numStartups": 2, "mcpServers": {"a": {}}}')
    assert _claude_json_semantic(str(p)) == a
    p.write_text('{"numStartups": 2, "mcpServers": {"a": {}, "evil": {"command": "sh"}}}')
    assert _claude_json_semantic(str(p)) != a


def test_scope_helpers():
    assert in_scope("src/a/b.py", ["src/**"])
    assert in_scope("docs", ["docs/"])
    assert not in_scope("tmpclaude-x", ["src/**"])
    assert matches_any(["process:node"], ["process:no*"]) == "process:no*"


def test_wrapper_ancestors_recorded_in_snapshot_are_not_leftovers():
    # `timeout 90 lnt diff` run from the repo: the collector drops lnt's own record, so the
    # diff cannot walk ppid from lnt to the wrapper. The snapshot carries the chain instead.
    procs_after = {
        "20:5": {"pid": 20, "ppid": 1, "pgid": 20, "uid": 1, "start": 5, "comm": "timeout", "cmd": "timeout 90 lnt diff", "cwd": "/r", "state": "S"},
        "21:5": {"pid": 21, "ppid": 20, "pgid": 20, "uid": 1, "start": 5, "comm": "sh", "cmd": "sh -c lnt diff /r", "cwd": "/r", "state": "S"},
        "30:5": {"pid": 30, "ppid": 1, "pgid": 30, "uid": 1, "start": 5, "comm": "node", "cmd": "node dev", "cwd": "/r", "state": "S"},
    }
    after = snap({"process": procs_after, "port": {}}, pid=22)
    after["self_pids"] = [22, 21, 20, 1]
    d = compute_diff(snap({"process": {}, "port": {}}), after)
    pids = [f["after"]["pid"] for f in d["findings"] if f["category"] == "process"]
    assert pids == [30]


def test_ancestor_pids_walks_ppid_chain():
    from lnt.collectors import ancestor_pids
    procs = {5: {"ppid": 4}, 4: {"ppid": 3}, 3: {"ppid": 1}, 1: {"ppid": 0}, 9: {"ppid": 1}}
    assert ancestor_pids(procs, 5) == [5, 4, 3, 1]


def test_verify_counts_a_stopped_but_unreaped_process_as_gone():
    from lnt.revert import verify
    rec = {"pid": 40, "ppid": 2, "pgid": 40, "uid": 1, "start": 5, "comm": "py", "cmd": "py", "cwd": "/r", "state": "S"}
    finding = {"id": "process:py:40", "category": "process", "key": "40:5", "after": rec}
    zombie_rec = dict(rec, state="Z")
    before = snap({"process": {}})
    # killed, but its parent has not reaped it yet: it has exited, so it is back at baseline
    assert verify({"findings": [finding]}, before, snap({"process": {"40:5": zombie_rec}})) == {"process:py:40": True}
    assert verify({"findings": [finding]}, before, snap({"process": {"40:5": rec}})) == {"process:py:40": False}
    # a finding that was already a zombie is only fixed once the entry is gone
    zf = dict(finding, after=zombie_rec)
    assert verify({"findings": [zf]}, before, snap({"process": {"40:5": zombie_rec}})) == {"process:py:40": False}
