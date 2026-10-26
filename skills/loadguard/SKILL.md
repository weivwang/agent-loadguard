---
name: loadguard
description: Run heavy commands (test suites, builds, type checks, docker builds) through `loadguard run --` so parallel agents on one machine queue for memory instead of freezing it. Use whenever you are about to run such a command, when a command is denied with a "loadguard:" message, when loadguard stopped a command, or when the machine is slow or out of memory.
---

# loadguard

Several agents often share one computer. Each one starting a test suite or a
build at the same moment, or one test run spawning workers without limit, is
what freezes a machine. `loadguard` gives heavy commands a few machine-wide
slots and watches every process they spawn.

## Run heavy commands through it

```bash
loadguard run -- pytest -x tests/unit
cd web && loadguard run -- npm test
loadguard run -- sh -c 'make build && make test'
```

Heavy means bounded jobs that can take many GB: test runners, builds, type
checks, `docker build`. Do not wrap long-running servers (`npm run dev`) or
cheap commands (`git`, `ls`, `grep`, linters on a few files).

- It may wait for a slot first. While waiting it prints
  `loadguard: waiting (…) — <why>` to stderr every 30 s. If the command may
  take longer than your shell tool's timeout, run it in the background.
- The command's exit code is passed through unchanged.
- Nested calls are free: inside a guarded command, `loadguard run` just runs.

## When it says no

| Exit / message | Meaning | Do this |
|---|---|---|
| hook denied with `loadguard: … Run it through loadguard` | the Claude Code hook caught an unguarded heavy command | rerun the exact suggested command it printed |
| `99` · `loadguard: stopped … reached X GB, over the Y GB limit` | the command and its children grew past the per-command ceiling and were all stopped | do **not** retry unchanged — narrow it: fewer workers (`-n 2`, `--maxWorkers=2`), one package or test file |
| `99` · `… the machine fell to N% free memory …` | the machine was about to run out and this was the largest guarded command | check `loadguard status`; wait for other work, or narrow the scope |
| `75` · `gave up after waiting …` | no slot or not enough free memory within `--max-wait` | tell the user what is holding the machine (the message names it); do not loop |

`loadguard status` shows free memory, who holds each slot (command, memory,
age, directory), who is waiting, and the largest apps on the machine. When
the machine is out of memory because of an app rather than a guarded command
(for example a desktop app grown to tens of GB), say so to the user — that is
theirs to restart.

## Settings (environment variables)

| Variable | Default | Effect |
|---|---|---|
| `LOADGUARD_SLOTS` | 1 per 32 GB of RAM (min 1) | heavy commands allowed at once |
| `LOADGUARD_TREE_CAP_GB` | half of RAM | per-command ceiling, children included |
| `LOADGUARD_MIN_FREE_PCT` | 20 | a command starts only above this % of free memory |
| `LOADGUARD_PANIC_FREE_PCT` | 8 | below this, the largest guarded command is stopped |
| `LOADGUARD_EXTRA_PATTERN` | — | regex; extra commands the hook treats as heavy |
| `LOADGUARD_HOOK=off` | — | disable the hook for one session |
