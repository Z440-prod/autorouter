# Claude Code

From the approved project run `autorouter install --target claude-code` and then `claude`. The installer uses a versioned local marketplace and a locally scoped plugin. The plugin adds MCP, the `project-context` skill, a reviewer subagent and a SessionStart hook. The hook adds a short availability hint when there is a project grant. View the registration with `claude plugin list`.

Use the native `/model` picker for model selection. Configure advisory catalog entries using models or family aliases from your account. Authenticated plugin execution and automatic handoffs are pending live validation. [Claude Code plugin docs](https://code.claude.com/docs/en/plugins) · [Claude Code model configuration](https://code.claude.com/docs/en/model-config)
