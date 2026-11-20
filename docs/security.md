# Security model

The stdio service has an explicit tool registry. MCP calls can read approved project data and can write bounded SQLite checkpoints only with a workspace grant. Tool arguments cannot select arbitrary workspaces, vaults, executables, or files. The service binds to the resolved process working directory and a host-specific operator grant. Hosts run it as the local user. Keep the trusted state directory and vault outside untrusted checkout content.

The local index filters common credential markers. Curate the permitted Markdown folder and review excerpts and skill content before granting access. Host providers can receive MCP outputs through their normal model context. Semantic retrieval can send approved notes to the endpoint chosen in the trusted profile. Host approvals remain authoritative.

The public source is built from a file allowlist. Local transcripts, secrets, worktree checkpoints, vaults, package caches and vendor downloads remain private. The optional Codex patch preserves upstream attribution. [Report a vulnerability](../SECURITY.md)
