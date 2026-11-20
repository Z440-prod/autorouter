# Troubleshooting

- If only diagnostics appears, start the host from the exact authorized workspace root and check the host grant. Use a fresh session after configuration changes.
- If the executable is missing, run `autorouter hosts` and check the host's PATH and local config.
- If FTS5 is missing, install a Python build with FTS5 enabled.
- If Gemini cancels linking, review the trusted-folder and extension consent prompts in an interactive terminal.
- If OpenCode is on 2.x, use the documented manual MCP fallback. The automatic plugin installer targets 1.x.
- If a native Codex package is missing or mismatched, use the optional build instructions and matching upstream runtime.
