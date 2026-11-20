#!/usr/bin/env bash
# Run full workspace gates using Codex's official Cargo V8 artifacts.
set -euo pipefail
source "$HOME/.cargo/env"
export PATH="$HOME/Projects/AutoRouter/.venv/bin:$PATH"
export CARGO_PROFILE_DEV_DEBUG=0 CARGO_PROFILE_TEST_DEBUG=0 RUST_MIN_STACK=33554432 RUST_TEST_THREADS=3
export INSTA_UPDATE=no
build_root="$HOME/.local/share/autorouter-build"
source_root="${AUTOROUTER_SOURCE:-$HOME/Projects/codex-autorouter}"
if [[ -d "$build_root/sysroot" ]]; then
  export PKG_CONFIG_SYSROOT_DIR="$build_root/sysroot"
  export PKG_CONFIG_PATH="$PKG_CONFIG_SYSROOT_DIR/usr/lib/x86_64-linux-gnu/pkgconfig"
  export CPATH="$PKG_CONFIG_SYSROOT_DIR/usr/include:$PKG_CONFIG_SYSROOT_DIR/usr/include/x86_64-linux-gnu"
fi
cd "$source_root/codex-rs"
target="$(rustc -vV | sed -n 's/^host: //p')"
version="$(python3 ../.github/scripts/rusty_v8_bazel.py resolved-v8-crate-version)"
[[ "$target" =~ ^(x86_64|aarch64)-unknown-linux-(gnu|musl)$ ]]
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]
name="ptrcomp_sandbox_release_${target}"
root="$build_root/v8/$version/$target"
mkdir -p "$root"
exec 8>"$root/.download.lock"
flock 8
base="https://github.com/openai/codex/releases/download/rusty-v8-v$version"
manifest="rusty_v8_$name.sha256"
archive="librusty_v8_$name.a.gz"
binding="src_binding_$name.rs"
expected="$(awk -v name="$manifest" '$2 == name {print $1}' "../third_party/v8/rusty_v8_${version//./_}_release_manifests.sha256")"
[[ "$expected" =~ ^[0-9a-f]{64}$ ]]
download() {
  curl --fail --silent --show-error --location --retry 2 --max-time 300 "$base/$1" -o "$root/$1.next"
  mv -f "$root/$1.next" "$root/$1"
}
[[ -f "$root/$manifest" ]] || download "$manifest"
printf '%s  %s\n' "$expected" "$root/$manifest" | sha256sum -c -
for file in "$archive" "$binding"; do
  entry="$(awk -v name="$file" '$2 == name {print $1}' "$root/$manifest")"
  [[ "$entry" =~ ^[0-9a-f]{64}$ ]]
  [[ -f "$root/$file" ]] || download "$file"
  printf '%s  %s\n' "$entry" "$root/$file" | sha256sum -c -
done
flock -u 8
exec 8>&-
export RUSTY_V8_ARCHIVE="$root/$archive"
export RUSTY_V8_SRC_BINDING_PATH="$root/$binding"
if [[ $# -eq 0 ]]; then
  set -- --workspace --all-targets
fi
# Core-only Cargo tests also exercise the native sidecars used by the deployment.
runtime="${AUTOROUTER_RUNTIME:-$HOME/.local/lib/autorouter}"
if [[ -f "$runtime/codex-package.json" ]]; then
  python3 - "$runtime" <<'PYRUNTIME'
import json, sys, tomllib
from pathlib import Path
runtime = Path(sys.argv[1])
version = tomllib.loads(Path("Cargo.toml").read_text())["workspace"]["package"]["version"]
if json.loads((runtime / "codex-package.json").read_text())["version"] != version:
    raise SystemExit("Cargo test sidecars must match the source version")
PYRUNTIME
  if [[ " $* " == *" -p codex-core "* || " $* " == *" --package codex-core "* ]]; then
    export CARGO_BIN_EXE_codex_code_mode_host="$runtime/bin/codex-code-mode-host"
    export CARGO_BIN_EXE_codex_exec_server="$runtime/bin/codex-exec-server"
  fi
  if [[ -x "$runtime/codex-resources/bwrap" ]]; then
    export PATH="$runtime/codex-resources:$PATH"
  fi
fi
if [[ " $* " == *" --workspace "* && " $* " != *" --exclude codex-voice-host "* ]]; then
  manifest_hash="$(sha256sum ../third_party/voice/sources.json | cut -d ' ' -f 1)"
  voice_env="$build_root/voice-environments/$manifest_hash/$target.env"
  if [[ -f "$voice_env" ]]; then
    while IFS='=' read -r key value; do
      case "$key" in
        PKG_CONFIG_PATH_*) value="$value${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}" ;;
        PKG_CONFIG_*|SYSTEM_DEPS_*|CODEX_TEST_VOICE_RUNTIME|CARGO_TARGET_*_RUSTFLAGS) ;;
        *) echo "Unexpected SDK setting: $key" >&2; exit 1 ;;
      esac
      export "$key=$value"
    done < "$voice_env"
    if [[ -n "${PKG_CONFIG_SYSROOT_DIR:-}" ]]; then
      # A development library earlier in the linker search can shadow the pinned SDK.
      for library in "$CODEX_TEST_VOICE_RUNTIME"/lib/*.so; do
        sysroot_library="$PKG_CONFIG_SYSROOT_DIR/usr/lib/${target/-unknown-/-}/${library##*/}"
        if [[ -e "$sysroot_library" ]] && ! cmp -s "$sysroot_library" "$library"; then
          echo "$sysroot_library shadows the pinned voice SDK library $library" >&2
          exit 1
        fi
      done
    fi
    export LD_LIBRARY_PATH="$CODEX_TEST_VOICE_RUNTIME/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
  elif ! pkg-config --exists 'gstreamer-1.0 >= 1.28'; then
    echo "The complete native workspace needs the pinned GStreamer 1.28 SDK for $target." >&2
    echo "Expected a prepared Codex voice environment at $voice_env" >&2
    exit 1
  fi
fi
if [[ " $* " == *" --workspace "* && -f v8-poc/Cargo.toml ]]; then
  set -- "$@" --features codex-v8-poc/sandbox
fi
exec cargo test --locked --no-fail-fast -j "${AUTOROUTER_TEST_JOBS:-3}" "$@"
