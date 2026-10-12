### Footprint Gauntlet results

Platform: Linux 6.12.110+ (aarch64), python 3.12.15

| Metric | Result |
|---|---|
| Planted changes caught | **21 / 21** |
| False positives, planted session | 0 |
| False positives, idle session (in-scope work only) | 0 |
| Restored to exact baseline by `revert --apply` | **21 / 21** |
| `lnt snapshot` wall time | 0.55 s |
| `lnt diff` wall time | 0.62 s |
| `lnt diff` exit code (planted / idle) | 1 / 0 |
| `lnt revert --apply` exit code | 0 |

| # | Planted change | Modeled on | Caught | Exact restore |
|---|---|---|---|---|
| 1 | tmpclaude-*-cwd file in the repo | [claude-code#17600](https://github.com/anthropics/claude-code/issues/17600), [claude-code#17636](https://github.com/anthropics/claude-code/issues/17636), [claude-code#17568](https://github.com/anthropics/claude-code/issues/17568) | yes | yes |
| 2 | empty read-only dotfile in the working dir | [claude-code#17087](https://github.com/anthropics/claude-code/issues/17087) | yes | yes |
| 3 | /tmp/claude-*-cwd tracking file never cleaned | [claude-code#8856](https://github.com/anthropics/claude-code/issues/8856) | yes | yes |
| 4 | task .output files piling up in /tmp/claude-{uid} | [claude-code#26911](https://github.com/anthropics/claude-code/issues/26911) | yes | yes |
| 5 | empty sandbox stub file left in the repo | [claude-code#78419](https://github.com/anthropics/claude-code/issues/78419) | yes | yes |
| 6 | git config --global user.email overwritten | [claude-code#37201](https://github.com/anthropics/claude-code/issues/37201) | yes | yes |
| 7 | ~/.config/git/ignore created | [claude-code#10230](https://github.com/anthropics/claude-code/issues/10230) | yes | yes |
| 8 | global git credential helper replaced (gh auth setup-git) | [claude-code#50232](https://github.com/anthropics/claude-code/issues/50232) | yes | yes |
| 9 | core.hooksPath written into the repo's .git/config | [claude-code#60957](https://github.com/anthropics/claude-code/issues/60957) | yes | yes |
| 10 | project .codex/config.toml modified by the agent | [codex#15680](https://github.com/openai/codex/issues/15680) | yes | yes |
| 11 | global npm package installed | (global installs; see report) | yes | yes |
| 12 | pip install into the interpreter's own site-packages | [claude-code#48992](https://github.com/anthropics/claude-code/issues/48992) | yes | yes |
| 13 | ~/.bashrc PATH line appended | (global env; see report) | yes | yes |
| 14 | MCP server injected into ~/.claude.json | [workos.com](https://workos.com/blog/npm-worm-coding-agent-config-credentials) | yes | yes |
| 15 | SessionStart hook added to ~/.claude/settings.json | [thehackernews.com](https://thehackernews.com/2026/09/malicious-git-configs-can-make-claude.html) | yes | yes |
| 16 | ~/.cursor/mcp.json created | [workos.com](https://workos.com/blog/npm-worm-coding-agent-config-credentials) | yes | yes |
| 17 | dev server left listening after the session | [claude-code#50544](https://github.com/anthropics/claude-code/issues/50544), [claude-code#22612](https://github.com/anthropics/claude-code/issues/22612) | yes | yes |
| 18 | orphaned background process still running | [claude-code#19433](https://github.com/anthropics/claude-code/issues/19433), [claude-code#17391](https://github.com/anthropics/claude-code/issues/17391), [claude-code#1935](https://github.com/anthropics/claude-code/issues/1935) | yes | yes |
| 19 | crontab entry added (persistence) | [labs.cloudsecurityalliance.org](https://labs.cloudsecurityalliance.org/wp-content/uploads/2026/09/CSA_research_note_ai_coding_agent_git_config_rce_20260904-csa-styled.pdf) | yes | yes |
| 20 | user-level service unit / LaunchAgent created | (persistence; see report) | yes | yes |
| 21 | docker container left behind | (running state; see report) | yes | yes |
