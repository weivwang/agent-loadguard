"""Tests for loadguard. Run: python3 -m unittest discover -s tests -v"""

import importlib.machinery
import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROGRAM = os.path.join(ROOT, "bin", "loadguard")

_loader = importlib.machinery.SourceFileLoader("loadguard", PROGRAM)
_spec = importlib.util.spec_from_loader("loadguard", _loader)
lg = importlib.util.module_from_spec(_spec)
_loader.exec_module(lg)

# Allocates memory in a DETACHED grandchild (its own session), so the test also
# proves the tree follows processes that leave the command's process group.
HOG = textwrap.dedent(
    """
    import subprocess, sys, time
    child = subprocess.Popen([sys.executable, "-c", '''
    import time
    blocks = []
    while len(blocks) < 80:
        blocks.append(b"x" * (25 * 1024 * 1024))
        time.sleep(0.05)
    time.sleep(60)
    '''], start_new_session=True)
    open(sys.argv[1], "w").write(str(child.pid))
    time.sleep(60)
    """
)


def read_pid(path):
    with open(path) as handle:
        return int(handle.read().strip() or 0)


def alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    # A zombie is dead for our purposes.
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], stdout=subprocess.PIPE,
                           universal_newlines=True).stdout.strip()
    return bool(state) and not state.startswith("Z")


class Env(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = dict(os.environ, LOADGUARD_STATE_DIR=self.tmp.name, LOADGUARD_MIN_FREE_PCT="0")
        self.env.pop("LOADGUARD_ACTIVE", None)

    def tearDown(self):
        self.tmp.cleanup()

    def run_lg(self, *args, env=None, timeout=60):
        return subprocess.run([PROGRAM] + list(args), env=dict(self.env, **(env or {})),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, timeout=timeout)


class Classification(unittest.TestCase):
    HEAVY = {
        "pytest -x": "pytest",
        "cd a && uv run pytest tests/unit": "pytest",
        "python3 -m pytest -q 2>&1 | tail": "pytest",
        "FOO=1 npm run build:web": "npm run build:web",
        "npm test": "npm test",
        "pnpm typecheck": "pnpm typecheck",
        "bun test": "bun test",
        "npx vitest run": "vitest",
        "bash -c \"make test\"": "make test",
        "make": "make",
        "docker compose up --build -d": "docker compose up --build",
        "docker build -t x .": "docker build",
        "cargo test --workspace": "cargo test",
        "go test ./...": "go test",
        "xcodebuild -scheme App build": "xcodebuild",
        "time nice tsc -b": "tsc",
        "(cd web && npm run test:e2e)": "npm run test:e2e",
    }
    LIGHT = [
        "git commit -m \"npm test; fix\"",
        "grep -r pytest .",
        "npm run lint",
        "npm install",
        "yarn install",
        "make help",
        "tsc --version",
        "pytest --help",
        "loadguard run -- pytest",
        "cd x && loadguard run -- npm test",
        "loadguard run -- sh -c 'npm test && npm run build'",
        "echo 'pytest' # npm test",
        "cat <<EOF > run.sh\nnpm test\nEOF",
        "go vet ./...",
        "docker ps",
        "ls -la",
    ]

    def test_heavy_commands_are_caught(self):
        for command, label in self.HEAVY.items():
            with self.subTest(command=command):
                self.assertEqual([h[2] for h in lg.find_heavy(command)], [label])

    def test_light_commands_pass(self):
        for command in self.LIGHT:
            with self.subTest(command=command):
                self.assertEqual(lg.find_heavy(command), [])

    def test_extra_pattern(self):
        os.environ["LOADGUARD_EXTRA_PATTERN"] = r"^git push"
        try:
            self.assertTrue(lg.find_heavy("git push origin main"))
            self.assertFalse(lg.find_heavy("git status"))
        finally:
            del os.environ["LOADGUARD_EXTRA_PATTERN"]

    def test_suggestion_wraps_only_the_heavy_segment(self):
        cases = {
            "cd a && uv run pytest tests/unit": "cd a && loadguard run -- uv run pytest tests/unit",
            "FOO=1 BAR='x y' npm test": "FOO=1 BAR='x y' loadguard run -- npm test",
            "(cd web && npm test) ; echo done": "(cd web && loadguard run -- npm test) ; echo done",
            "npm test && cargo build": "loadguard run -- npm test && loadguard run -- cargo build",
        }
        for command, expected in cases.items():
            with self.subTest(command=command):
                self.assertEqual(lg.guarded_suggestion(command, lg.find_heavy(command)), expected)
                # the suggestion itself must pass the hook, or agents loop
                self.assertEqual(lg.find_heavy(expected), [])


class Hook(Env):
    def hook(self, payload, env=None):
        raw = payload if isinstance(payload, str) else json.dumps(payload)
        return subprocess.run([PROGRAM, "hook"], input=raw, env=dict(self.env, **(env or {})),
                              stdout=subprocess.PIPE, universal_newlines=True, timeout=10)

    def test_denies_heavy_bash_with_a_runnable_suggestion(self):
        result = self.hook({"tool_name": "Bash", "tool_input": {"command": "cd x && npm test"}})
        self.assertEqual(result.returncode, 0)
        decision = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "deny")
        self.assertIn("run -- npm test", decision["permissionDecisionReason"])

    def test_suggestion_names_a_runnable_program(self):
        payload = {"tool_name": "Bash", "tool_input": {"command": "npm test"}}
        bare = dict(PATH="/usr/bin:/bin")
        reason = lambda env: json.loads(self.hook(payload, env).stdout)["hookSpecificOutput"][
            "permissionDecisionReason"]
        # off PATH: the absolute path, so the agent can still run it
        self.assertIn("%s run -- npm test" % os.path.realpath(PROGRAM), reason(bare))
        # as a Claude Code plugin, bin/ is on the Bash tool's PATH
        self.assertIn("\nloadguard run -- npm test",
                      reason(dict(bare, CLAUDE_PLUGIN_ROOT=ROOT)))

    def test_allows_light_and_other_tools(self):
        for payload in ({"tool_name": "Bash", "tool_input": {"command": "git status"}},
                        {"tool_name": "Read", "tool_input": {"file_path": "pytest"}}):
            result = self.hook(payload)
            self.assertEqual((result.returncode, result.stdout), (0, ""))

    def test_fails_open_and_can_be_disabled(self):
        self.assertEqual(self.hook("not json").stdout, "")
        result = self.hook({"tool_name": "Bash", "tool_input": {"command": "pytest"}},
                           env={"LOADGUARD_HOOK": "off"})
        self.assertEqual(result.stdout, "")


class KillDecision(unittest.TestCase):
    def settings(self):
        s = lg.Settings.__new__(lg.Settings)
        s.total, s.tree_cap, s.panic_free_pct = 100 * lg.GIB, 50 * lg.GIB, 8
        return s

    def test_cap(self):
        s = self.settings()
        self.assertIsNone(lg.kill_reason(s, 50 * lg.GIB, lambda: 90, lambda: True))  # at the cap
        self.assertIn("limit", lg.kill_reason(s, 50 * lg.GIB + 1, lambda: 90, lambda: True))

    def test_panic_stops_only_a_large_and_largest_command(self):
        s = self.settings()
        self.assertIn("free memory", lg.kill_reason(s, 20 * lg.GIB, lambda: 5, lambda: True))
        self.assertIsNone(lg.kill_reason(s, 20 * lg.GIB, lambda: 8, lambda: True))  # not below
        self.assertIsNone(lg.kill_reason(s, 20 * lg.GIB, lambda: 5, lambda: False))  # someone bigger
        self.assertIsNone(lg.kill_reason(s, 9 * lg.GIB, lambda: 5, lambda: True))  # too small to help


class Run(Env):
    def test_exit_code_passes_through(self):
        self.assertEqual(self.run_lg("run", "--", "sh", "-c", "exit 3").returncode, 3)
        self.assertEqual(self.run_lg("run", "--", "sh", "-c", "kill -TERM $$").returncode, 143)

    def test_runaway_tree_is_stopped_including_detached_children(self):
        pidfile = os.path.join(self.tmp.name, "hog.pid")
        started = time.monotonic()
        result = self.run_lg("run", "--", sys.executable, "-c", HOG, pidfile,
                             env={"LOADGUARD_TREE_CAP_GB": "0.5"})
        self.assertEqual(result.returncode, lg.EXIT_KILLED, result.stderr)
        self.assertIn("over the 0.5 GB limit", result.stderr)
        self.assertLess(time.monotonic() - started, 30)
        self.assertFalse(alive(read_pid(pidfile)))
        self.assertEqual(os.listdir(self.tmp.name).count("slot-0.json"), 0)

    def test_leftover_processes_are_stopped(self):
        pidfile = os.path.join(self.tmp.name, "sleep.pid")
        result = self.run_lg("run", "--", "sh", "-c", "sleep 300 & echo $! > %s" % pidfile)
        self.assertEqual(result.returncode, 0)
        self.assertIn("left running", result.stderr)
        self.assertFalse(alive(read_pid(pidfile)))

    def test_second_command_queues_for_the_only_slot(self):
        env = dict(self.env, LOADGUARD_SLOTS="1")
        first = subprocess.Popen([PROGRAM, "run", "--", "sleep", "3"], env=env)
        try:
            time.sleep(0.8)
            started = time.monotonic()
            result = self.run_lg("run", "--", "true", env={"LOADGUARD_SLOTS": "1"})
            self.assertEqual(result.returncode, 0)
            self.assertGreater(time.monotonic() - started, 1.5)
            self.assertIn("waiting", result.stderr)
            self.assertIn("sleep 3", result.stderr)
        finally:
            first.wait(timeout=20)

    def test_queue_timeout_runs_nothing(self):
        env = dict(self.env, LOADGUARD_SLOTS="1")
        marker = os.path.join(self.tmp.name, "ran")
        first = subprocess.Popen([PROGRAM, "run", "--", "sleep", "3"], env=env)
        try:
            time.sleep(0.8)
            result = self.run_lg("run", "--max-wait", "1", "--", "touch", marker,
                                 env={"LOADGUARD_SLOTS": "1"})
            self.assertEqual(result.returncode, lg.EXIT_QUEUE_TIMEOUT)
            self.assertIn("Nothing was run", result.stderr)
            self.assertFalse(os.path.exists(marker))
        finally:
            first.wait(timeout=20)

    def test_low_memory_blocks_admission(self):
        result = self.run_lg("run", "--max-wait", "0", "--", "true",
                             env={"LOADGUARD_MIN_FREE_PCT": "101"})
        self.assertEqual(result.returncode, lg.EXIT_QUEUE_TIMEOUT)
        self.assertIn("largest:", result.stderr)

    def test_nested_run_does_not_take_a_second_slot(self):
        result = self.run_lg("run", "--", PROGRAM, "run", "--", "sh", "-c", "exit 4",
                             env={"LOADGUARD_SLOTS": "1"}, timeout=15)
        self.assertEqual(result.returncode, 4)

    def test_terminating_loadguard_stops_the_tree(self):
        pidfile = os.path.join(self.tmp.name, "sleep.pid")
        proc = subprocess.Popen([PROGRAM, "run", "--", "sh", "-c", "echo $$ > %s; sleep 300" % pidfile],
                                env=self.env)
        deadline = time.monotonic() + 10
        while not os.path.exists(pidfile) or not read_pid(pidfile):
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.1)
        time.sleep(1.5)  # let one poll register the tree
        proc.send_signal(signal.SIGTERM)
        self.assertEqual(proc.wait(timeout=15), 128 + signal.SIGTERM)
        self.assertFalse(alive(read_pid(pidfile)))

    def test_status_reports_holders(self):
        first = subprocess.Popen([PROGRAM, "run", "--", "sleep", "3"], env=self.env)
        try:
            time.sleep(1.5)
            out = self.run_lg("status").stdout
            self.assertIn("sleep 3", out)
            self.assertIn("largest apps:", out)
        finally:
            first.wait(timeout=20)


if __name__ == "__main__":
    unittest.main()
