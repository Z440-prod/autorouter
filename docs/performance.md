# AutoRouter Pulse and Adaptive Speed

## Pulse

```sh
autorouter progress
autorouter progress --task TASK_ID --detail
autorouter progress --task TASK_ID --json
```

The CLI reads local SQLite checkpoints. The workspace MCP tool is called `progress` and takes a `task` ID. It requires the same workspace and host authorization as `checkpoint_get`. A native Codex session also displays a Pulse footer from its existing plan and routing events. These paths consume zero additional provider tokens.

The native footer follows the existing plan, where available, and shows the current routing stage, model, and configured service tier. The CLI and MCP estimate progress from completed and pending checkpoint operations. Incomplete scope displays as indeterminate. Detailed CLI output includes milestones and a current-stage ETA range when at least five successful local samples match the task type, stage, model, effort and speed. Other hosts can use the CLI or workspace MCP where their adapter grants checkpoint access.

[Example terminal output](assets/pulse-demo.txt)

Use `autorouter routing usage SESSION_ID TURN_ID` on a completed native turn to inspect elapsed time, reported token usage, successful Luna classification latency, and observed reuse counts. The report uses bounded local traces and rollout records.

## Adaptive Speed

The speed command evaluates a local forecast and prints a Standard, Fast, or Ultrafast recommendation. It requires an explicit forecast and leaves speed selection in the host's native controls. Model and reasoning effort are independent of speed. Automatic recommendations consider interactivity, predicted model and tool time, provider cooldown, supported tiers, a premium upper bound and residency approval.

```sh
autorouter speed --forecast forecast.json
```

A synthetic forecast, with a common cost unit for premium and budget and seconds for durations:

```json
{
  "preference": "balanced",
  "interactive": true,
  "residency_allowed": true,
  "supported": ["fast"],
  "remaining_budget": 1,
  "premium_upper_bound": {"fast": 0.5},
  "model_seconds": {"standard": 20, "fast": 11},
  "tool_seconds": 3
}
```

Preferences are `economy`, `balanced`, and `speed-first`. Set `minimum_gain` to override the minimum fraction of total predicted time saved, `previous` for hysteresis, or `manual` to pass through an explicit choice. Ultrafast requires `allow_ultrafast: true`, an eligible forecast, and a supported tier. Missing eligibility or cost inputs select Standard in automatic mode.

| Host | Native speed control |
| --- | --- |
| Codex | Native speed selector or service-tier command, subject to the model catalog, account, and client. The provider uses `fast` or `priority` for supported Fast requests and `ultrafast` for supported Ultrafast requests. |
| Claude Code | Native `/fast` for eligible accounts and models. |
| Gemini CLI, OpenCode, Cline | AutoRouter has no speed-tier control for these adapters. Their native provider and model settings remain available. |

[OpenAI Fast](https://developers.openai.com/api/docs/guides/fast-mode) · [OpenAI Ultrafast](https://developers.openai.com/api/docs/guides/ultrafast-mode) · [Claude Code Fast](https://code.claude.com/docs/en/fast-mode)
