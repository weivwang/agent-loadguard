# agent-loadguard

**Stop parallel coding agents from freezing your computer.**

Running several agents at once (Claude Code, Codex, Cursor…) on one laptop
works until two of them start a test suite at the same moment, or one test run
spawns workers until the machine is out of memory. Then everything stalls and
macOS asks you to force-quit apps it has already paused.

`loadguard` is a small command plus an agent skill and a Claude Code hook:

- **Queue.** Heavy commands — test suites, builds, type checks, `docker build` —
  share a few machine-wide slots (default: one per 32 GB of RAM). A command
  starts only when a slot is free *and* enough memory is available; otherwise
  it waits and says what it is waiting for.
- **Ceiling.** While it runs, loadguard sums the memory of every process the
  command spawned — including ones that detached into their own session — and
  stops the whole tree once it passes a limit (default: half of RAM), or when the
  machine is nearly out and this is the largest guarded command.
- **Cleanup.** Processes a command leaves running after it exits (orphaned test
  workers, browsers) are stopped too.
- **Hook.** In Claude Code, a `PreToolUse` hook catches an unguarded heavy
  command and tells the agent the exact guarded command to run instead. The
  agent reruns it; nothing is rewritten behind its back.

On macOS memory is the process's *physical footprint* (what Activity Monitor
shows, compressed pages included), not RSS — RSS shrinks exactly when a
runaway process starts being compressed.

Single file, Python 3.9+ standard library, no daemon.

## Install

Needs macOS or Linux and Python 3.9+ (macOS's `python3` is enough). Pick **one**:

**Claude Code plugin** — type this in a Claude Code session (2.1.275+):

```
/plugin install loadguard --marketplace weivwang/agent-loadguard
```

The hook, the skill and the `loadguard` command come with the plugin; updates
come through `/plugin`. On older versions:
`/plugin marketplace add weivwang/agent-loadguard`, then `/plugin install loadguard@agent-loadguard`.

**One line, any agent** — also installs the skill for Codex and puts
`loadguard` on your own shell's PATH:

```bash
curl -fsSL https://raw.githubusercontent.com/weivwang/agent-loadguard/main/install.sh | sh
```

Run the same line again to update. Add `| sh -s -- --no-hook` to skip the
Claude Code hook, `| sh -s -- --uninstall` to remove everything (a backup of
`settings.json` is kept). From a clone, `./install.sh` does the same.
New Claude Code sessions pick the hook up.

For agents without hooks (Codex, Cursor, …), the skill tells them when and how
to use `loadguard run`; you can also add one line to your `AGENTS.md`:

> Run test suites, builds and type checks as `loadguard run -- <command>`.

## Use

```bash
loadguard run -- pytest -x tests/unit
cd web && loadguard run -- npm test
loadguard run -- sh -c 'make build && make test'

loadguard status          # free memory, slot holders, waiters, largest apps
loadguard check 'cd web && npm test'   # what the hook would say
```

```
$ loadguard status
memory: 61% available of 64.0 GB · new runs start at ≥20% · per-command limit 32.0 GB
slots (2):
  [0] pid 41230  9.8 GB now, peak 11.2 GB  3m12s  pytest -n 8  (/Users/me/api)
  [1] free
waiting: 0
largest apps: ChatGPT 21.4 GB · Google Chrome 6.3 GB · Claude 4.1 GB
```

| Exit code | Meaning |
|---|---|
| the command's own | it ran (loadguard passes the code through) |
| `99` | loadguard stopped it: over the limit, or the machine was running out |
| `75` | it waited `--max-wait` seconds (default 1800) without starting; nothing ran |

Every refusal is a sentence an agent can act on, e.g.

```
loadguard: stopped `pytest -n 16` — its processes reached 33.1 GB, over the 32.0 GB limit
(LOADGUARD_TREE_CAP_GB). Do not retry it unchanged: run a narrower scope (fewer workers,
one package or test file) or wait for other work (`loadguard status`).
```

## Settings

| Variable | Default | Effect |
|---|---|---|
| `LOADGUARD_SLOTS` | 1 per 32 GB of RAM (min 1) | heavy commands allowed at once |
| `LOADGUARD_TREE_CAP_GB` | half of RAM | per-command ceiling, children included |
| `LOADGUARD_MIN_FREE_PCT` | 20 | a command starts only above this % of free memory |
| `LOADGUARD_PANIC_FREE_PCT` | 8 | below this, the largest guarded command (if ≥10% of RAM) is stopped |
| `LOADGUARD_EXTRA_PATTERN` | — | regex; more commands the hook treats as heavy (e.g. `^git push` when a pre-push hook runs tests) |
| `LOADGUARD_HOOK=off` | — | disable the hook |
| `LOADGUARD_STATE_DIR` | `~/.local/state/loadguard` | slot locks and status files |

## What it does not do

- It cannot fix an app that leaks memory on its own (a desktop app grown to
  70 GB). `loadguard status` names it so you know what to restart.
- It does not limit commands that were not started through it. The hook covers
  Claude Code; other agents follow the skill.
- Nested `loadguard run` inside a guarded command runs directly, so scripts that
  call it never deadlock on their own slot.

CI runs the full suite, including the real memory-hog test, on macOS and Linux
with Python 3.9 and 3.12.

## How it works

- Slots are `flock`ed files; a crashed holder's lock is released by the OS.
- Every second the guard lists processes (`ps`), follows parent links and the
  command's process group from the root, and keeps every process it has ever
  seen in the tree, so a child that calls `setsid()` is still counted and
  stopped. Pid reuse is guarded by the process name.
- Stopping is `SIGTERM` to the group and every tracked process, then `SIGKILL`
  after 5 s.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

The suite runs a real memory hog (in a detached grandchild) under a 0.5 GB cap,
checks queueing, timeouts, orphan cleanup, signal handling, and the hook's
classification on both heavy commands and look-alikes (`grep pytest`,
`git commit -m "npm test"`, heredocs).

---

## 中文简介

多个编程 Agent 同时在一台电脑上跑时，经常有两个同时开测试，或者一次测试不受限地起 worker，把内存吃光、整机卡死。`loadguard` 让测试、构建、类型检查这类“重活”排队领全机共享的名额，内存不够就先等；运行时把它开出的整棵进程树（包括脱离出去的子进程）的内存加起来，超过上限或整机快耗尽时连根停掉，并告诉 Agent 原因和下一步怎么做。Claude Code 里有 hook 自动拦截没走 loadguard 的重命令，其他 Agent 靠 skill 说明使用。安装（二选一）：在 Claude Code 里输入 `/plugin install loadguard --marketplace weivwang/agent-loadguard`；或在终端运行 `curl -fsSL https://raw.githubusercontent.com/weivwang/agent-loadguard/main/install.sh | sh`（同时给 Codex 装 skill，重跑即升级）。

## License

MIT
