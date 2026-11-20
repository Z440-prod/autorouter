#!/usr/bin/env bash
set -euo pipefail
# Review the apt transaction before approving. Never upgrades the OS or installs Rust.
packages=(git curl ca-certificates python3 python3-venv pipx sqlite3 ripgrep procps)
missing=()
for package in "${packages[@]}"; do
  dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -q 'install ok installed' || missing+=("$package")
done
if ((${#missing[@]})); then
  sudo apt-get update
  sudo apt-get install "${missing[@]}"
else
  echo 'Required system packages already installed.'
fi
