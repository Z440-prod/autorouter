# Architecture

Native host adapters register a stdio MCP process for the workspace. That process validates its launch directory and host against a local operator profile. The same Python service handles bounded knowledge queries, skills and checkpoints for all adapters. SQLite transactions persist task snapshots and event receipts. Markdown is the canonical knowledge source.

The native Rust Codex client retains its routing and execution loop. Its launcher opens the isolated executable with its native approvals and tools. The portable package can start independently with any supported host. [Core interface](universal-core.md) · [Host adapters](host-adapters.md)
