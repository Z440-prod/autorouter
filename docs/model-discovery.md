# Model discovery

Use `autorouter models status` with the optional patched Codex client. The Codex App Server advertises provider models and supported reasoning levels. It can include cached or bundled entries, so an observed request remains the strongest confirmation of execution.

For Claude Code, Gemini CLI, OpenCode and Cline, use the native model picker or a documented host catalog command. Supply operator-authorized entries in preference order via `--catalog`. A family alias can come from the host itself. The shared service reports the catalog source as operator configured.

```json
[
  {"id": "your-configured-model-or-alias", "stages": ["analysis", "verification"]}
]
```
