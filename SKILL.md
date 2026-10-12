---
name: leave-no-trace
description: Record what a coding-agent session changes on the machine outside the requested diff (junk files in the repo and /tmp, global npm/pip/pipx/cargo/brew packages, dotfiles, git config --global, agent configs/hooks/MCP servers, leftover processes and ports, docker containers, cron/launchd/systemd jobs), then show a receipt with evidence and a revert plan. Use at the start of any session that will run commands, install things or start servers, and again before saying the task is done.
license: MIT
---

# Leave No Trace

You are about to work on someone's real machine. Leave it the way you found it,
except for the change you were asked to make, and prove it.

This skill ships a small CLI, `lnt` (Python 3.8+, standard library only). It works
by looking at machine state, not at your transcript, so it gives the same answer
whichever model or agent is running it.

Run it as `python3 <this-skill-dir>/scripts/lnt ...` (or just `lnt` if it is on
PATH). Below, `lnt` means that command.

## 1. At the start of the session, before changing anything

```bash
lnt snapshot --scope 'src/**' --scope 'tests/**'
```

- `--scope` names the repo paths you are expected to create files in. Tracked
  files you edit are always treated as the requested work, so scope is only for
  new files. Leave it out if you are unsure; you will just see more findings.
- The baseline is stored outside the repo (`~/.local/state/lnt/`), so it never
  shows up in `git status`.
- If this machine has background churn (IDE, auto-updaters), use
  `lnt snapshot --learn-noise 10` once: it takes two baselines 10 s apart and
  ignores whatever changed on its own.

## 2. Do the work

Work normally. Prefer project-local installs (`npm i -D`, a venv, `cargo add`)
over global ones, and stop servers you start. The diff will show what you missed.

## 3. Before you say "done"

```bash
lnt diff
```

- Exit code `0`: nothing left behind outside the requested work. Say so.
- Exit code `1`: something was left behind. Do **not** claim the task is clean.
  Show the user the receipt:

  ```bash
  lnt receipt --format md
  ```

  Paste it into your final message (or the PR description). Every line has
  evidence: a sha256, or a PID with its start time and working directory.
- Exit code `3`: there was no baseline. Say that you could not verify the session.

## 4. Undoing things: only with the user's go-ahead

```bash
lnt revert            # dry run: prints the plan, changes nothing
lnt revert --apply    # executes it, then re-checks each item against the baseline
```

Rules for you, the agent:

- Never run `lnt revert --apply` unless the user asked you to, or explicitly
  agreed after seeing the dry-run plan. Use `--only '<finding-id-glob>'` to revert
  just the items they approved.
- Never "clean up" by hand with `rm -rf`, `pkill`, `git config --unset` or
  `docker rm` instead. `lnt` checks before every action that the item is still
  exactly what the diff saw (same hash, same PID start time), moves files to a
  trash folder instead of deleting them, and touches nothing the diff did not
  report. Doing it by hand gives up all of that.
- Some things cannot be undone automatically (a file that existed before and has
  no baseline copy, a credentials file, a container that was deleted). The plan
  marks these `manual`: tell the user plainly.

## Reading the receipt

| Group | Meaning | Default severity |
|---|---|---|
| AGENT-CONFIG | agent settings, hooks, MCP servers, skills, repo `.git/config` and `.git/hooks` changed | high |
| PERSISTENCE | crontab entry, systemd user unit or LaunchAgent added/changed | high |
| RUNNING | process, listening port or docker container still there; a listener that existed before is gone | medium/high |
| ENV | global packages, dotfiles, `git config --global`, new executables in `~/.local/bin` etc. | medium |
| LITTER | new untracked files in the repo, new entries in the temp dir | low (medium if ≥100 MB) |
| IN-SCOPE | tracked-file edits and new files inside `--scope` | info, never fails |

`lnt diff --fail-on medium` raises the bar; `--fail-on none` never fails.
False positives go in `.lntignore` (one glob per line, matched against finding
ids such as `tmp:*/my-cache-*`, `process:node`, `package:npm:corepack`).
