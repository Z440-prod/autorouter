// OpenCode 1.x config hook. Preserve more specific operator settings.
export const AutoRouter = async () => ({
  config: async config => {
    config.mcp ??= {}
    config.mcp.autorouter ??= {
      type: "local",
      command: ["autorouter", "serve", "--target", "opencode"],
    }
  },
})
