import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """An isolated HOME / TMPDIR / git repo. Slow collectors are off unless a test turns them on."""
    home = tmp_path / "home"
    tmp = tmp_path / "tmp"
    repo = tmp_path / "repo"
    for d in (home, tmp, repo):
        d.mkdir()
    for k in list(os.environ):
        if k.startswith(("GIT_", "XDG_", "LNT_")):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TMPDIR", str(tmp))
    monkeypatch.setenv("LNT_TMP_DIRS", str(tmp))
    monkeypatch.setenv("LNT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    env = dict(os.environ)
    subprocess.run(["git", "init", "-q", str(repo)], check=True, env=env)
    (repo / "README.md").write_text("# t\n")
    (repo / ".lnt.json").write_text(json.dumps({"collectors": {"packages": False, "docker": False}}))
    (repo / ".gitignore").write_text(".lnt.json\nbuild/\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, env=env)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "i"],
                   check=True, env=env)
    (home / ".gitconfig").write_text("[user]\n\tname = Dev\n\temail = dev@example.com\n")
    return {"home": home, "tmp": tmp, "repo": repo, "root": tmp_path}


def lnt(sandbox, *args):
    return subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "lnt")] + list(args),
                          cwd=str(sandbox["repo"]), capture_output=True, text=True, env=dict(os.environ))
