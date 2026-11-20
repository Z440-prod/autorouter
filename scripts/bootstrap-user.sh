#!/usr/bin/env bash
set -euo pipefail
project=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
mkdir -p "$HOME/.local/bin" "$HOME/.config/autorouter" "$HOME/.local/state/autorouter"
export PATH="$HOME/.local/bin:$PATH"
python3 "$project/scripts/install-codex.py"
python3 -m venv "$project/.venv"
"$project/.venv/bin/pip" install -e "$project"
for app in autorouter autorouter-admin autorouter-memory-mcp; do
  target="$HOME/.local/bin/$app"
  if [[ ! -e "$target" && ! -L "$target" ]]; then
    ln -s "$project/.venv/bin/$app" "$target"
  else
    echo "Preserving existing $target"
  fi
done
"$project/.venv/bin/autorouter-admin" config
python3 - <<'PYBOOT'
from pathlib import Path
import json,os
cfg=Path(os.environ.get('AUTOROUTER_CONFIG',Path.home()/'.config/autorouter/config.json'))
vault=Path(json.loads(cfg.read_text())['vault'])
for name in ['Projects','Architecture','Decisions','Knowledge','Debugging','Research','Tasks','Archived']:(vault/name).mkdir(parents=True,exist_ok=True)
PYBOOT
python3 "$project/scripts/install-integrations.py"
if ! codex mcp get autorouter-memory >/dev/null 2>&1; then
  codex mcp add autorouter-memory -- "$project/.venv/bin/autorouter-memory-mcp"
fi
"$project/.venv/bin/autorouter-admin" memory reindex
"$project/.venv/bin/autorouter-admin" doctor
mkdir -p "$HOME/.config/systemd/user"
unit="$HOME/.config/systemd/user/autorouter-memory.service"
if [[ ! -e "$unit" ]]; then
  cat > "$unit" <<UNIT
[Unit]
Description=AutoRouter Obsidian incremental memory index
[Service]
Type=simple
ExecStart=$project/.venv/bin/autorouter-admin memory watch
Restart=on-failure
RestartSec=10
UMask=0077
[Install]
WantedBy=default.target
UNIT
fi
# Migrate only this project's old service entry point.
python3 - "$unit" "$project" <<'PYUNIT'
from pathlib import Path
import sys
unit=Path(sys.argv[1])
old=f"ExecStart={sys.argv[2]}/.venv/bin/autorouter memory watch"
unit.write_text(unit.read_text().replace(old, old.replace('/autorouter ', '/autorouter-admin ')))
PYUNIT
systemctl --user daemon-reload
systemctl --user enable --now autorouter-memory.service
for unit in autorouter-graph.service autorouter-graph.timer; do
  if [[ ! -e "$HOME/.config/systemd/user/$unit" ]]; then
    install -m 644 "$project/config/systemd/$unit" "$HOME/.config/systemd/user/$unit"
  fi
done
systemctl --user daemon-reload
systemctl --user enable --now autorouter-graph.timer

mkdir -p "$HOME/.local/share/bash-completion/completions"
if [[ ! -e "$HOME/.local/share/bash-completion/completions/autorouter" ]]; then
 cp "$project/config/completion.bash" "$HOME/.local/share/bash-completion/completions/autorouter"
fi
