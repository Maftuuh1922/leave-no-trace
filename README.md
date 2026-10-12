<p align="center"><img src="assets/banner.png" alt="Leave No Trace: Your AI agent made a mess. See every trace, and undo it." width="100%"></p>

# Leave No Trace (`lnt`)

**Your AI agent made a mess. See every trace, and undo it.**

[![CI](https://github.com/Maftuuh1922/leave-no-trace/actions/workflows/ci.yml/badge.svg)](https://github.com/Maftuuh1922/leave-no-trace/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python stdlib only](https://img.shields.io/badge/python-3.8%2B%20%C2%B7%20stdlib%20only-blue.svg)](pyproject.toml)
[![Agent Skill](https://img.shields.io/badge/Agent%20Skill-Claude%20Code%20%C2%B7%20Codex%20%C2%B7%20Gemini%20CLI%20%C2%B7%20Cursor%20%C2%B7%20opencode-8A2BE2.svg)](SKILL.md)

`lnt` snapshots your machine before a coding-agent session, diffs it afterwards,
and prints a receipt of everything the session left **outside the diff you asked
for**: junk files in the repo and `/tmp`, global packages, dotfiles,
`git config --global`, agent configs/hooks/MCP servers, processes and ports still
running, docker containers, cron/launchd/systemd jobs. Every line has evidence
(sha256, or PID + start time + cwd), and `lnt revert --apply` puts it back.

## 30-second install

```bash
# Claude Code (other agents: see the table under "Install" below)
git clone https://github.com/Maftuuh1922/leave-no-trace ~/.claude/skills/leave-no-trace

# or one copy for Codex, Gemini CLI, Cursor and opencode at once
git clone https://github.com/Maftuuh1922/leave-no-trace ~/.agents/skills/leave-no-trace

# or just the CLI
pip install git+https://github.com/Maftuuh1922/leave-no-trace
```

Python 3.8+, no dependencies. The skill tells the agent to run `lnt snapshot` at
the start and `lnt diff` before it says "done". By hand:

```bash
lnt snapshot          # before the session
lnt diff              # after: exit 1 = something was left behind
lnt revert --apply    # put it back (without --apply it is a dry run)
```

## Demo (real output)

A session that dropped a `tmpclaude-*-cwd` file, added a `SessionStart` hook,
changed `git config --global user.email`, appended to `~/.bashrc` and left task
output in `/tmp` (paths shortened, nothing else edited):

```
$ lnt diff
━━ Leave No Trace receipt ━━
repo: ~/myapp   session: 2026-10-12 09:12:32 → 2026-10-12 09:12:33
This session left 6 things outside the requested work: 1 agent config/hook change, 3 global environment changes, 2 leftover files.

 1. [AGENT-CONFIG] HIGH new agent config/hook: ~/.claude/settings.json
      evidence: sha256 c893fd453e70, 82 B, mode 0o644
      revert:   move to trash: ~/.claude/settings.json
 2. [ENV] MEDIUM changed dotfile: ~/.bashrc
      evidence: sha256 1cee391fa7→b361691bfa, 52 B, mode 0o644
      revert:   restore ~/.bashrc from the baseline copy
 3. [ENV] MEDIUM changed dotfile: ~/.gitconfig
      evidence: sha256 5a0cd4fe27→f5e3ccaaf9, 34 B, mode 0o644
      revert:   restore ~/.gitconfig from the baseline copy
 4. [ENV] MEDIUM git config --global user.email = agent@example.com
      revert:   covered by restoring the global git config file
 5. [LITTER] LOW new untracked file in repo: tmpclaude-3f2a-cwd
      evidence: sha256 e3b0c44298fc, 0 B, mode 0o644
      revert:   move to trash: tmpclaude-3f2a-cwd
 6. [LITTER] LOW new temp dir (97.7 KB): /tmp/claude-1000
      evidence: 97.7 KB
      revert:   move to trash: /tmp/claude-1000

0 in-scope change(s), 0 ignored finding(s).

$ lnt revert --apply
  1. done         agentconfig:~/.claude/settings.json - moved to trash
  2. done         dotfile:~/.bashrc 
  3. done         dotfile:~/.gitconfig 
  4. done         repo.untracked:tmpclaude-3f2a-cwd - moved to trash
  5. done         tmp:/tmp/claude-1000 - moved to trash
  6. skipped      gitconfig:user.email - covered by restoring the global git config file
lnt: 6/6 reverted item(s) verified back at their baseline state. Trash: ~/.local/state/lnt/…/trash/

$ lnt diff
━━ Leave No Trace receipt ━━
repo: ~/myapp   session: 2026-10-12 09:12:32 → 2026-10-12 09:12:34
Clean: nothing changed outside the requested work.

0 in-scope change(s), 0 ignored finding(s).
```

Afterwards `git config --global user.email` and `~/.bashrc` are back to their
baseline values; the removed files sit in a trash folder, never deleted.

## Proof: this happens in public bug trackers

Each of these was opened and read on 2026-10-12. Dates are when the issue was filed (UTC).

| Filed | Issue | What it says |
|---|---|---|
| 2025-06-11 | [claude-code#1935](https://github.com/anthropics/claude-code/issues/1935) | "Claude Code does not properly clean up MCP server processes when exiting, leading to orphaned processes" |
| 2025-10-03 | [claude-code#8856](https://github.com/anthropics/claude-code/issues/8856) | `/tmp/claude-*-cwd` files "are never cleaned up … 174 files accumulated in one day of usage" |
| 2025-10-24 | [claude-code#10230](https://github.com/anthropics/claude-code/issues/10230) | "automatically creates `~/.config/git/ignore` … without asking for user permission" |
| 2026-01-09 | [claude-code#17087](https://github.com/anthropics/claude-code/issues/17087) | "creates empty (0 byte), read-only (-r--r--r--) dotfiles in the current working directory" |
| 2026-01-12 | [claude-code#17600](https://github.com/anthropics/claude-code/issues/17600) | "creates `tmpclaude-*-cwd` files in the current working directory … never cleaned up" |
| 2026-02-19 | [claude-code#26911](https://github.com/anthropics/claude-code/issues/26911) | "A single research-heavy session generated **537 GB** of `.output` files" |
| 2026-03-21 | [claude-code#37201](https://github.com/anthropics/claude-code/issues/37201) | "the AI automatically ran **git config --global user.email**" with an address the user never gave |
| 2026-03-24 | [codex#15680](https://github.com/openai/codex/issues/15680) | "The agent is able to not only modify but create a project level config.toml file, without asking" |
| 2026-04-18 | [claude-code#50232](https://github.com/anthropics/claude-code/issues/50232) | "Claude ran gh auth setup-git without being asked, which overwrote my … git credential configuration" |
| 2026-04-18 | [claude-code#50544](https://github.com/anthropics/claude-code/issues/50544) | Preview MCP "spawns unbounded zombie dev server processes — 7,400+ node zombies" |
| 2026-05-20 | [claude-code#60957](https://github.com/anthropics/claude-code/issues/60957) | worktree creation "writes `core.hooksPath` to the parent repository's `.git/config`" |
| 2026-07-17 | [claude-code#78419](https://github.com/anthropics/claude-code/issues/78419) | sandbox stubs make "`git add .` fail outright" and show "~19 phantom untracked files" |

More, also cited in the benchmark below: [#17636](https://github.com/anthropics/claude-code/issues/17636),
[#17568](https://github.com/anthropics/claude-code/issues/17568),
[#48992](https://github.com/anthropics/claude-code/issues/48992),
[#22612](https://github.com/anthropics/claude-code/issues/22612),
[#19433](https://github.com/anthropics/claude-code/issues/19433),
[#17391](https://github.com/anthropics/claude-code/issues/17391), and supply-chain
write-ups on persistence through agent config
([The Hacker News](https://thehackernews.com/2026/09/malicious-git-configs-can-make-claude.html),
[WorkOS](https://workos.com/blog/npm-worm-coding-agent-config-credentials),
[CSA research note](https://labs.cloudsecurityalliance.org/wp-content/uploads/2026/09/CSA_research_note_ai_coding_agent_git_config_rce_20260904-csa-styled.pdf)).

### Why a snapshot and not a guard

Command guards (for example `destructive_command_guard`, `cc-safety-net`) block
known-bad command patterns *before* they run. `lnt` is the other half: it records
what actually changed, including changes made by the agent harness itself (several
issues above are the harness, not the model), which no guard can block. Because it
reads **machine state** and not the transcript, it works the same with Claude Code,
Codex, Gemini CLI, Cursor, opencode, Neovarch Agent, any model, or a human at a
terminal. It ships as an [Agent Skill](https://agentskills.io) (`SKILL.md`) plus
the stdlib-only CLI. Use both.

## Install

Requires Python 3.8+ and nothing else. Clone it once into the skills folder your
agent reads; the CLI runs from the checkout as `scripts/lnt`.

| Agent | Command |
|---|---|
| Claude Code | `git clone https://github.com/Maftuuh1922/leave-no-trace ~/.claude/skills/leave-no-trace` |
| Codex CLI | `git clone https://github.com/Maftuuh1922/leave-no-trace ~/.agents/skills/leave-no-trace` ([docs](https://developers.openai.com/codex/skills)) |
| Gemini CLI | `git clone https://github.com/Maftuuh1922/leave-no-trace ~/.gemini/skills/leave-no-trace` (or `~/.agents/skills/`) |
| Cursor | `git clone https://github.com/Maftuuh1922/leave-no-trace ~/.cursor/skills/leave-no-trace` (or `~/.agents/skills/`) ([docs](https://cursor.com/docs/skills)) |
| OpenCode | `git clone https://github.com/Maftuuh1922/leave-no-trace ~/.config/opencode/skills/leave-no-trace` (it also reads `~/.claude/skills` and `~/.agents/skills`) ([docs](https://opencode.ai/docs/skills/)) |
| Neovarch Agent / any other agent | clone anywhere and add `SKILL.md` to the agent's skills or instructions; the CLI is plain Python |
| Just the CLI | `pip install git+https://github.com/Maftuuh1922/leave-no-trace` then use `lnt` |

One copy in `~/.agents/skills/leave-no-trace` is picked up by Codex, Gemini CLI,
Cursor and OpenCode at once.

## Use

```bash
lnt snapshot --scope 'src/**'   # before the session (stored in ~/.local/state/lnt, not in the repo)
# ... the agent works ...
lnt diff                        # exit 0 = clean, 1 = something left behind, 3 = no baseline
lnt receipt --format md         # paste into the PR or the agent's final message
lnt revert                      # dry run: the plan, nothing changes
lnt revert --apply              # do it, then verify each item against the baseline
lnt run -- <command>            # snapshot, run a command, diff
lnt doctor                      # which collectors work here, and how long a snapshot takes
```

Because `lnt diff` exits non-zero when something was left behind, it can gate
"done": in an agent Stop hook, a pre-push hook, or CI.

### What is collected

| Area | How | Revert |
|---|---|---|
| Repo litter | `git ls-files --others` (+ ignored dirs at directory level), sha256 per file | move to trash |
| Temp dir | entries you own in `$TMPDIR` and `/tmp`, with bounded size | move to trash |
| Dotfiles | `.bashrc`, `.zshrc`, `.profile`, `.gitconfig`, `~/.config/git/*`, `.npmrc`, `.ssh/config`, ... sha256 + baseline copy (≤512 KB) | restore copy |
| Credential files | `.netrc`, `.aws/credentials`, `.docker/config.json`, `.kube/config`, `gh/hosts.yml`, ... | **hash only, never copied**; manual |
| Agent config | `~/.claude/settings*.json`, hooks/skills/agents/commands, the MCP/hook sections of `~/.claude.json`, `~/.codex/config.toml`, `~/.gemini/settings.json`, `~/.cursor/mcp.json`, OpenCode, Continue, Windsurf, Claude Desktop; repo `.mcp.json`, `.claude/`, `.codex/config.toml`, `.git/config`, `.git/hooks` | restore copy / trash |
| `git config --global` | `git config --global --list -z` | restored with the file, else `git config --unset-all/--add` |
| Global packages | npm, pnpm, pip (`$LNT_PYTHON` or `python3`), pipx, uv tools, cargo, brew formulae and casks, gem (opt-in), executables in `~/.local/bin`, `~/bin`, `~/go/bin`, ... | uninstall / reinstall old version |
| Processes | your processes, from `/proc` (Linux) or `ps` + `lsof` (macOS); reported if new and listening, a zombie, or with cwd in the repo/temp dir | SIGTERM (group if it leads one), then SIGKILL, only if PID **and** start time **and** UID still match |
| Ports | TCP listeners from `/proc/net/tcp*` or `lsof`; a listener that **disappeared** is reported too | stop owner / manual |
| Docker | containers, volumes, networks, images | `docker rm -f` / `volume rm` / ... |
| Scheduled jobs | `crontab -l`, `~/.config/systemd/user`, `~/Library/LaunchAgents` | restore crontab / disable + trash unit |

### Safety rules (tested in `tests/test_cli.py`)

- `revert` is a dry run unless you pass `--apply`.
- It only acts on items in the diff. A file created after the diff is never touched.
- Before each action it re-reads the item; if it changed since the diff (a different
  hash, a PID that now belongs to another process), the action is skipped.
- Files are moved to `~/.local/state/lnt/<repo>/<session>/trash/`, never deleted.
  A file that gets restored has its current version moved to the trash first.
- Receipts show hashes, never file contents; values that look like tokens are redacted.

### Cutting false positives

- `--scope 'src/**'` for paths the session is expected to create.
- `.lntignore` in the repo or `~/.config/lnt/ignore`: one glob per line, matched
  against finding ids (`tmp:*/my-cache-*`, `process:node`, `package:npm:corepack`).
  See `.lntignore.example`.
- `lnt snapshot --learn-noise 10`: two baselines 10 s apart; whatever changes on its
  own is ignored for this session.
- Agent transcripts and caches (`~/.claude/projects`, `todos`, `statsig`, ...) and
  the counters in `~/.claude.json` are ignored by default; only its MCP/hook
  sections are compared.
- `.lnt.json` in the repo (see `.lnt.json.example`) adds scope, extra files/dirs
  (for agents not listed above) and turns collectors on or off.

## Benchmark: Footprint Gauntlet

`bench/gauntlet.py` builds a throwaway sandbox (its own `HOME`, `TMPDIR`, git repo,
npm prefix and Python venv), takes a baseline, plants 21 changes modeled on the
issues above, runs `lnt diff`, then `lnt revert --apply`, and checks every item
with its **own** probes (file hashes, `git config --get`, `npm ls -g`, `pip show`,
`kill -0`, crontab text), not with `lnt`'s verdict. An idle session that only does
in-scope work (edit a tracked file, add a file under `src/`, write an agent
transcript, bump `~/.claude.json` counters) measures false positives.

```bash
python3 bench/gauntlet.py --json results.json --md results.md   # --strict fails unless 100% / 0 FP
```

### Results (Linux 6.12, aarch64, Python 3.12, local run on 2026-10-12)

Raw output of this run: [`bench/results/gauntlet-linux.json`](bench/results/gauntlet-linux.json)
and [`.md`](bench/results/gauntlet-linux.md). Reproduce with
`python3 bench/gauntlet.py --strict --json results.json`.

| Metric | Result |
|---|---|
| Planted changes caught | **21 / 21** |
| False positives, planted session | 0 |
| False positives, idle session (in-scope work only) | 0 |
| Restored to the exact baseline by `revert --apply` (harness probes) | **21 / 21** |
| `lnt snapshot` wall time | 0.48 s |
| `lnt diff` wall time | 0.51 s |
| `lnt revert --apply` wall time (most of it is `npm uninstall -g`) | 7.5 s |
| `lnt diff` exit code, planted / idle | 1 / 0 |

| # | Planted change | Modeled on | Caught | Exact restore |
|---|---|---|---|---|
| 1 | `tmpclaude-*-cwd` file in the repo | [#17600](https://github.com/anthropics/claude-code/issues/17600), [#17636](https://github.com/anthropics/claude-code/issues/17636), [#17568](https://github.com/anthropics/claude-code/issues/17568) | yes | yes |
| 2 | empty read-only dotfile in the working dir | [#17087](https://github.com/anthropics/claude-code/issues/17087) | yes | yes |
| 3 | `/tmp/claude-*-cwd` tracking file | [#8856](https://github.com/anthropics/claude-code/issues/8856) | yes | yes |
| 4 | task `.output` files in `/tmp/claude-{uid}` (8 MB here) | [#26911](https://github.com/anthropics/claude-code/issues/26911) | yes | yes |
| 5 | empty sandbox stub file in the repo | [#78419](https://github.com/anthropics/claude-code/issues/78419) | yes | yes |
| 6 | `git config --global user.email` overwritten | [#37201](https://github.com/anthropics/claude-code/issues/37201) | yes | yes |
| 7 | `~/.config/git/ignore` created | [#10230](https://github.com/anthropics/claude-code/issues/10230) | yes | yes |
| 8 | global git credential helper replaced | [#50232](https://github.com/anthropics/claude-code/issues/50232) | yes | yes |
| 9 | `core.hooksPath` written into the repo's `.git/config` | [#60957](https://github.com/anthropics/claude-code/issues/60957) | yes | yes |
| 10 | project `.codex/config.toml` written by the agent | [codex#15680](https://github.com/openai/codex/issues/15680) | yes | yes |
| 11 | global npm package installed | – | yes | yes |
| 12 | `pip install` into the interpreter's own site-packages | [#48992](https://github.com/anthropics/claude-code/issues/48992) | yes | yes |
| 13 | `~/.bashrc` PATH line appended | – | yes | yes |
| 14 | MCP server injected into `~/.claude.json` (counters also bumped, not reported) | [WorkOS](https://workos.com/blog/npm-worm-coding-agent-config-credentials) | yes | yes |
| 15 | SessionStart hook added to `~/.claude/settings.json` | [THN](https://thehackernews.com/2026/09/malicious-git-configs-can-make-claude.html) | yes | yes |
| 16 | `~/.cursor/mcp.json` created | [WorkOS](https://workos.com/blog/npm-worm-coding-agent-config-credentials) | yes | yes |
| 17 | dev server left listening | [#50544](https://github.com/anthropics/claude-code/issues/50544), [#22612](https://github.com/anthropics/claude-code/issues/22612) | yes | yes |
| 18 | orphaned background process | [#19433](https://github.com/anthropics/claude-code/issues/19433), [#17391](https://github.com/anthropics/claude-code/issues/17391), [#1935](https://github.com/anthropics/claude-code/issues/1935) | yes | yes |
| 19 | crontab entry (persistence) | [CSA](https://labs.cloudsecurityalliance.org/wp-content/uploads/2026/09/CSA_research_note_ai_coding_agent_git_config_rce_20260904-csa-styled.pdf) | yes | yes |
| 20 | systemd user unit (Linux) / LaunchAgent (macOS) | – | yes | yes |
| 21 | docker container left behind | – | yes | yes |

CI runs the same gauntlet with `--strict` on `ubuntu-latest` and `macos-latest`
(Python 3.9 and 3.12) on every push; the numbers per run are in the job summary
and the uploaded `gauntlet-*.json` artifacts.

**Read these numbers honestly.** The gauntlet is written by the same people as
the tool, so 21/21 says the collectors and revert paths work for these 21 shapes,
not that every real session will be caught. `crontab` and `docker` are replaced by
small shims on `PATH` so the benchmark never touches your real crontab or
containers; the real `crontab`/`docker` code paths are the same commands but are
not exercised by CI. A field study with real agents on real tasks is still to do.

## Limitations

- **Snapshot-based.** Something created and removed again during the session is
  invisible, and anything the agent changed that was already different at baseline
  time is attributed to the baseline. Take the snapshot right before the session.
- **Not a sandbox.** It records and reverts; it does not prevent. Pair it with a
  command guard or a container if you need prevention.
- **Not every file is watched.** Only the repo, temp dirs, the listed dotfiles and
  agent paths, user bin dirs and unit/LaunchAgent dirs. A write to, say,
  `~/Documents` is not seen unless you add it with `extra_files`/`extra_dirs`.
- **Processes:** only your own user's. New processes outside the repo/temp dir that
  do not listen on a port are counted but not listed (use `--all-processes`).
  A process started by you in another terminal during the session with cwd in the
  repo will be reported; ignore it with `process:<name>`.
- **Revert limits:** files that existed before and were changed can only be
  restored if a baseline copy exists (dotfiles/agent configs ≤512 KB; repo files are
  hashed, not copied: use git). Credential files are never copied, so changes to
  them are manual. Removed containers, killed servers and deleted untracked files
  cannot be brought back. Package downgrades reinstall the old version from the
  registry and need network. `brew` upgrades are manual.
- **Privacy:** baseline copies of small config files are kept in
  `~/.local/state/lnt` with mode 0600. Turn them off with `"backup": false`.
- **Platforms:** Linux and macOS. Windows is best-effort: file, git and package
  collectors work; processes, ports and scheduled jobs are reported as unavailable.
- **Noise on busy machines:** IDEs and updaters touch some of these paths. Use
  `--learn-noise` and `.lntignore`.

## Development

```bash
python3 -m pip install pytest
python3 -m pytest -q
python3 bench/gauntlet.py --strict
```

## Bahasa Indonesia (ringkas)

**Leave No Trace** mencatat apa saja yang ditinggalkan sesi agen coding di
laptopmu *di luar diff yang diminta*: file sampah di repo dan `/tmp`, paket global
(npm/pip/pipx/cargo/brew), dotfile, `git config --global`, config/hook/MCP agen,
proses dan port yang masih jalan, container docker, serta cron/launchd/systemd.
Setiap temuan punya bukti (sha256, atau PID + waktu mulai + cwd).

Cara pakai: jalankan `lnt snapshot` sebelum sesi, `lnt diff` sebelum agen bilang
"selesai" (exit code 1 berarti ada yang tertinggal), dan `lnt receipt --format md`
untuk struk yang bisa ditempel di PR. `lnt revert` hanya menampilkan rencana;
perubahan baru dijalankan dengan `--apply`. File tidak pernah dihapus (dipindah ke
folder trash), dan proses hanya dihentikan kalau PID, waktu mulai, dan UID-nya masih
cocok.

Instal: clone repo ini ke folder skill agenmu (lihat tabel *Install* di atas),
misalnya `~/.claude/skills/leave-no-trace` atau `~/.agents/skills/leave-no-trace`.
Hanya butuh Python 3.8+, tanpa dependensi lain. Batasannya ada di bagian
*Limitations*: ini alat pencatat dan pemulih, bukan sandbox.

## License

MIT. See [LICENSE](LICENSE).
