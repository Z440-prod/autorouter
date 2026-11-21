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

## Adaptive Performance Engine preview

Run the same offline harness against the release baseline and the review branch on Linux:

```sh
git worktree add --detach ../autorouter-baseline 4091bdd
PYTHONPATH=../autorouter-baseline/src python3 scripts/benchmark-local.py
PYTHONPATH=src python3 scripts/benchmark-local.py
```

One comparison used 1,200 synthetic Markdown files, a 30,000-node Graphify index, and 12 synthetic handoff checkpoints. Warm metrics use 25 samples. Cold figures are single observations.

| Fixture | Baseline p50 / p95 | Preview p50 / p95 |
| --- | ---: | ---: |
| Warm memory query | 300.25 / 351.67 ms | 2.47 / 3.62 ms |
| Warm graph query | 42.13 / 47.46 ms | 0.37 / 0.44 ms |
| Handoff JSON construction | 0.070 / 0.078 ms | 0.018 / 0.050 ms |
| Pulse calculation and rendering | — | 0.022 / 0.035 ms |

The handoff fixture shrank from 27,285 bytes to 2,572 bytes. Warm CPU time per call fell from 310.00 to 2.64 ms for memory and from 41.18 to 0.38 ms for graph retrieval. Cold indexing rose from about 780 to 933 ms and the first graph query rose from about 103 to 129 ms while the lookup structures were created. The harness measures local elapsed time, process CPU, and JSON bytes. It does not measure provider tokens, cost, first useful output, or end-to-end coding-task duration.

An installed-wheel comparison ran 20 stdio initializations and one-note lookups for each of the five host IDs. Across hosts, startup medians were 45.45–48.84 ms for the baseline and 44.87–52.42 ms for the preview. Lookup medians were 1.70–1.74 ms and 1.41–1.45 ms, respectively. Reproduce with `python scripts/benchmark-portable.py --runs 20` on each installed wheel. This measures the adapter subprocess and retrieval, independent of provider inference.

The native adapter can reuse a successful Luna decision within a turn for an unchanged history or a repeated built-in discovery call with identical results. Changes to catalog, tools, permissions, session settings, or rewritten history invalidate reuse. The same executor thread and persisted checkpoint history remain available when handoffs emit a compact payload. Provider cache hits depend on the eligible rendered prefix. Reuse stable tool order and append new evidence when possible, and use provider-reported cached input tokens to measure hits. [OpenAI prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)

Paid Standard/Fast/Ultrafast comparisons, provider token and latency measurements, and comparative scheduling trials are open. The current preview uses the native tool scheduler and local speed recommendations. [Pulse and speed controls](performance.md)
