# OpenCode

Run `autorouter install --target opencode` from the approved project using OpenCode 1.x. The config hook registers scoped MCP. Check the connection with `opencode mcp list`. The installer preserves an existing server declaration and refuses to overwrite a different plugin file.

OpenCode 2.x can be configured manually using a local server entry under `mcp.servers`, with `type: "local"` and `command: ["autorouter", "serve", "--target", "opencode"]`. Live 2.x validation is pending. [Plugin documentation](https://opencode.ai/docs/plugins/) · [OpenCode MCP](https://opencode.ai/docs/mcp-servers/)
