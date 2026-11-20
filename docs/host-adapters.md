# Native host adapters

`autorouter hosts` lists capabilities with one of six levels. Native means the host supplies the capability. Adapter means an AutoRouter extension or patched client supplies it. MCP describes a tool implementation. Partial means the feature is advisory or limited. Unsupported and untested signal unavailable integration paths and missing verification respectively. The README records live verification separately.

- [Codex](hosts/codex.md)
- [Claude Code](hosts/claude-code.md)
- [Gemini CLI](hosts/gemini-cli.md)
- [OpenCode](hosts/opencode.md)
- [Cline](hosts/cline.md)

The MCP tool set is decided at request time from the installed host declaration and a workspace grant. Tool execution happens in the host with its own approval policies.
