# Codex

An existing AutoRouter installation launches its isolated patched native client with `autorouter`. `codex` launches the official installation. The native patch retains TUI, tools, approvals, history, session resume and model controls. `autorouter -m MODEL` and `-c model_reasoning_effort=LEVEL` pin model and effort. Use `autorouter models status` and `autorouter routing decisions` for evidence.

For portable tools with official Codex, run `autorouter install --target codex` from the granted workspace. An existing AutoRouter memory MCP registration must be reviewed before migration, because the installer declines a duplicate.

To build the optional Linux client, prepare the upstream [Codex development dependencies](https://github.com/openai/codex/tree/rust-v0.169.0). Fetch the exact upstream source and apply the reviewed patch.

```sh
git clone --branch rust-v0.169.0 --depth 1 https://github.com/openai/codex.git codex-source
git -C codex-source rev-parse HEAD
git -C codex-source apply --check "$PWD/adapters/codex/rust-v0.169.0.patch"
git -C codex-source apply "$PWD/adapters/codex/rust-v0.169.0.patch"
AUTOROUTER_SOURCE="$PWD/codex-source" AUTOROUTER_RUNTIME=/path/to/official/matching-runtime \
  scripts/build-native.sh --stage
```

The expected upstream commit is `d32063f96c2dff1dc96af839b3952abb6acfeda4`. The pinned v0.169.0 runtime manifest and sidecars must match the compiled client. Stage and smoke-test before installation. For an existing matching package, use `scripts/build-native.sh --activate PATH` and `scripts/build-native.sh --rollback`. On a clean system, copy the staged package directory contents to `~/.local/lib/autorouter` after verification. The downloaded official runtime remains under its own license and distribution.
