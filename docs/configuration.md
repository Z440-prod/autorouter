# Configuration

AutoRouter stores project profiles below `${AUTOROUTER_HOME}/workspaces`. The default state base is `~/.local/state/autorouter`. The profile key is derived from the absolute, resolved launch directory. Each profile declares allowed hosts, a Markdown root, approved skills, a per-host advisory catalog, and per-host checkpoint write permission.

```sh
autorouter workspace --target claude-code --vault /path/to/project-notes \
  --catalog /path/to/claude-catalog.json --skill /path/to/trusted/SKILL.md --write
```

`--revoke` disables the named host for the current directory. The previous profile snapshot is kept locally. Schema 1 profiles use explicit grants. Existing Codex memory and administrative configuration continues through the legacy entry point. Grant a shared host the relevant subfolder explicitly to adopt existing notes.

An operator can opt into optional semantic retrieval by adding an `embeddings` object to the trusted profile. Set `enabled` and `provider`, with `base_url` and `model` as needed. Use `provider: local` with a loopback endpoint. A hosted provider requires HTTPS, `allow_hosted: true`, and credentials from an explicitly configured environment variable.
