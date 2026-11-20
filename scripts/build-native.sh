#!/usr/bin/env bash
set -euo pipefail
package="$HOME/.local/lib/autorouter"
case "${1:-}" in
  ""|--stage) ;;
  --activate|--rollback)
    if [[ "$1" == --activate ]]; then
      candidate="$(readlink -f -- "${2:?Pass the staged codex executable}")"
    else
      candidate="$package/bin/codex.previous"
    fi
    exec 9>"$package/.activation.lock"
    if ! flock -n 9; then
      echo "An AutoRouter session is active" >&2
      exit 1
    fi
    current="$package/bin/codex"
    if [[ "$("$candidate" --version)" != "$("$current" --version)" ]]; then
      echo "The executable and installed runtime must have matching versions" >&2
      exit 1
    fi
    install -m 755 "$candidate" "$current.next"
    if [[ "$1" == --activate ]]; then
      cp -p "$current" "$current.previous.next"
      mv -f "$current.previous.next" "$current.previous"
    fi
    mv -f "$current.next" "$current"
    "$current" --version
    exit
    ;;
  *) echo "Usage: $0 [--stage | --activate STAGED_CODEX | --rollback]" >&2; exit 2 ;;
esac
if [[ -f "$HOME/.cargo/env" ]]; then source "$HOME/.cargo/env"; fi
project=$(cd -- "$(dirname -- "$0")/.." && pwd)
export PATH="$project/.venv/bin:$PATH"
build_root="$HOME/.local/share/autorouter-build/sysroot"
if [[ -d "$build_root" ]]; then
  export PKG_CONFIG_PATH="$build_root/usr/lib/x86_64-linux-gnu/pkgconfig"
  export PKG_CONFIG_SYSROOT_DIR="$build_root"
  export CPATH="$build_root/usr/include:$build_root/usr/include/x86_64-linux-gnu${CPATH:+:$CPATH}"
fi
cd "${AUTOROUTER_SOURCE:-$HOME/Projects/codex-autorouter}/codex-rs"
CARGO_PROFILE_DEV_DEBUG=0 cargo build --locked -p codex-cli --bin codex -j "${AUTOROUTER_BUILD_JOBS:-4}"
binary="${CARGO_TARGET_DIR:-target}/debug/codex"
mkdir -p "$HOME/.local/share/autorouter-build"
package="$(mktemp -d "$HOME/.local/share/autorouter-build/candidate.XXXXXXXX")"
runtime="${AUTOROUTER_RUNTIME:-$(dirname -- "$(dirname -- "$(readlink -f -- "$(command -v codex)")")")}"
python3 - "$runtime" "$package" "$("$binary" --version)" <<'PYRUNTIME'
import json
import shutil
import sys
from pathlib import Path

source, target = map(Path, sys.argv[1:3])
version = sys.argv[3].removeprefix('codex-cli ')
manifest = source / 'codex-package.json'
if not manifest.is_file() or json.loads(manifest.read_text())['version'] != version:
    raise SystemExit('Matching official runtime package required (set AUTOROUTER_RUNTIME to its package directory)')
(target / 'bin').mkdir()
for name in ('codex-code-mode-host', 'codex-exec-server'):
    shutil.copy2(source / 'bin' / name, target / 'bin' / name)
for name in ('codex-resources', 'codex-path'):
    shutil.copytree(source / name, target / name, symlinks=True)
shutil.copy2(manifest, target / manifest.name)
PYRUNTIME
install -m 755 "$binary" "$package/bin/codex"
ln -s bin/codex "$package/codex"
printf '%s\n' "$package/codex"
