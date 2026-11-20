# Core and adapter contract

Register a stdio client with `autorouter serve --target HOST` from the configured workspace root. Implement MCP initialization and tool discovery before using `tools/call`. Derive availability from the returned catalog. Use the JSON input schemas and treat content values as data.

For an additional native adapter, add a host identifier and verified levels to `src/autorouter/assets/capabilities.json`, register the platform command in `hosts.py`, and test startup from a fresh project. Prefer the host's supported registration API. Keep provider authentication, model execution, and approvals inside the host.

The Python SDK entry points are `Workspace(root, host)`, `Workspace.dispatch(name, arguments)`, `TaskStore`, `recommend(catalog, stage)`, and `classify_and_route(task, classifier, catalog)`. A callable classifier must return a stage from `STAGES`. Bind any custom provider in trusted adapter code and subject its data access and billing to operator consent. Contract schema 1 covers the workspace and capability formats.

Call `Workspace.close()` when the adapter session ends to release database handles.
