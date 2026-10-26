#!/bin/sh
# Install loadguard: the CLI, the agent skill, and the Claude Code hook.
#
#   ./install.sh              copy into place
#   ./install.sh --link       symlink instead (follow a git checkout)
#   ./install.sh --no-hook    CLI + skill only
#   ./install.sh --uninstall  remove all three
set -eu

here=$(cd "$(dirname "$0")" && pwd)
bin_dir=${LOADGUARD_BIN_DIR:-"$HOME/.local/bin"}
claude_dir=${CLAUDE_CONFIG_DIR:-"$HOME/.claude"}
codex_dir=${CODEX_HOME:-"$HOME/.codex"}
mode=copy
hook=yes
action=install
for arg in "$@"; do
  case "$arg" in
    --link) mode=link ;;
    --no-hook) hook=no ;;
    --uninstall) action=uninstall ;;
    *) echo "usage: $0 [--link] [--no-hook] [--uninstall]" >&2; exit 64 ;;
  esac
done

command -v python3 >/dev/null || { echo "loadguard needs python3 (3.9+)" >&2; exit 1; }

place() { # place SOURCE TARGET
  rm -rf "$2"
  mkdir -p "$(dirname "$2")"
  if [ "$mode" = link ]; then ln -s "$1" "$2"; else cp -R "$1" "$2"; fi
}

# Add or remove our PreToolUse entry in settings.json, keeping everything else.
edit_hook() { # edit_hook add|remove
  python3 - "$claude_dir/settings.json" "$bin_dir/loadguard" "$1" <<'PY'
import json, os, shutil, sys

path, program, op = sys.argv[1:]
settings = {}
if os.path.exists(path):
    with open(path) as handle:
        settings = json.load(handle)
    shutil.copy2(path, path + ".loadguard-backup")
entries = settings.setdefault("hooks", {}).setdefault("PreToolUse", [])
ours = lambda entry: any("loadguard" in h.get("command", "") and h.get("command", "").endswith(" hook")
                         for h in entry.get("hooks", []))
entries[:] = [e for e in entries if not ours(e)]
if op == "add":
    entries.append({"matcher": "Bash",
                    "hooks": [{"type": "command", "command": '"%s" hook' % program, "timeout": 10}]})
if not entries:
    del settings["hooks"]["PreToolUse"]
if not settings["hooks"]:
    del settings["hooks"]
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(path, "w") as handle:
    json.dump(settings, handle, indent=2)
    handle.write("\n")
PY
}

if [ "$action" = uninstall ]; then
  rm -f "$bin_dir/loadguard"
  rm -rf "$claude_dir/skills/loadguard" "$codex_dir/skills/loadguard"
  [ -f "$claude_dir/settings.json" ] && edit_hook remove
  echo "loadguard removed (settings.json backup: $claude_dir/settings.json.loadguard-backup)"
  exit 0
fi

chmod +x "$here/bin/loadguard"
place "$here/bin/loadguard" "$bin_dir/loadguard"
place "$here/skills/loadguard" "$claude_dir/skills/loadguard"
[ -d "$codex_dir" ] && place "$here/skills/loadguard" "$codex_dir/skills/loadguard"
[ "$hook" = yes ] && edit_hook add

echo "installed: $bin_dir/loadguard"
echo "skill:     $claude_dir/skills/loadguard$( [ -d "$codex_dir" ] && printf ' + %s/skills/loadguard' "$codex_dir")"
[ "$hook" = yes ] && echo "hook:      PreToolUse(Bash) in $claude_dir/settings.json — new sessions pick it up"
case ":$PATH:" in
  *":$bin_dir:"*) ;;
  *) echo "note: $bin_dir is not on PATH; add it so agents can type 'loadguard'" ;;
esac
