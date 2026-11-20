# Installation

Install Python 3.11 or newer with SQLite FTS5, then install AutoRouter from the repository or source archive.

```sh
pipx install .
autorouter hosts
cd path/to/project
autorouter workspace --target HOST --vault /path/to/approved-notes
autorouter install --target HOST
```

Use a project-specific notes folder inside an Obsidian vault. The launch directory must match the directory used for the grant. Review the host's installation and workspace consent prompts. Each integration requires only its own host. Codex users can opt into the separate native build. [Host guides](host-adapters.md)

Upgrade by installing the new source in your existing package environment and re-running the relevant host installation. For a linked Gemini extension, unlink the old extension and install the new one. To roll back, reinstall the earlier package version and register its adapter. To uninstall, run `autorouter uninstall --target HOST` in the project, then `autorouter workspace --target HOST --revoke`. Use `pipx uninstall autorouter-engine` once the last integration is removed. Notes and checkpoints stay on disk.

CLI changes to the Cline and Codex MCP configs are backed up under the AutoRouter state directory. Claude Code and Gemini CLI manage their plugin registries and trust state. OpenCode uses a dedicated project plugin file.

Build a source-derived wheel with `python -m pip wheel --no-deps --wheel-dir dist .`. Use the same Python, setuptools, and `SOURCE_DATE_EPOCH` when comparing build digests. Release archives come from the tagged Git tree.
