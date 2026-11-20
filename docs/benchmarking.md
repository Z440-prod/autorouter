# Verification and performance

Run `python -m unittest discover -s tests` and `python -m unittest discover -s examples/ledger`. `scripts/validate.sh` adds compilation, shell syntax and lint checks where the local tools are installed. The cross-platform CI runs the portable suite and packaging checks. Native builds have separate Linux instructions.

The isolated host fixture used a synthetic Markdown note and separate config directories. Codex 0.169.0 completed an authenticated read-only native TUI turn and logged Luna classification and routing at tool boundaries. The official Codex executable reported 0.171.0. Claude Code 2.1.329 installed, listed all adapter components, connected its MCP server and uninstalled. Gemini CLI 0.70.0 validated and linked the extension with workspace consent, connected MCP and uninstalled. OpenCode 1.19.6 connected MCP through its local config hook and removed the managed plugin. Cline 3.0.74 registered and removed the stdio endpoint. Its runtime SDK discovered tools and retrieved the expected note through that registration.

## Reproduce the demo

Create an empty project directory and an approved notes directory outside it. Put “Saturn owns the synthetic fixture” in a file named `saturn.md`. Grant one host the notes directory and run its installation command. In the native session inspect the MCP server, then ask it to query “Saturn” and cite the file. An unauthenticated client can verify MCP connectivity using its host’s MCP listing command.

## Measured latency

`python scripts/benchmark-portable.py --runs 20` starts the installed AutoRouter stdio command for each host ID, initializes MCP, and retrieves the note. On Linux x86-64 using Python 3.12 and a Xeon E5-1650 v3:

| Target | Startup median | Retrieval median |
| --- | ---: | ---: |
| Codex | 52.84 ms | 1.71 ms |
| Claude Code | 52.20 ms | 1.70 ms |
| Gemini CLI | 47.46 ms | 1.70 ms |
| OpenCode | 47.59 ms | 1.71 ms |
| Cline | 50.02 ms | 1.71 ms |

These measurements cover the shared subprocess and SQLite retrieval. Claude Code's plugin inventory estimated about 56 always-on tokens from the skill and reviewer descriptions. Four live Codex Luna classifications in the read-only test took 2.18–2.48 seconds each. End-to-end comparative token and latency budgets across host providers remain unmeasured.
