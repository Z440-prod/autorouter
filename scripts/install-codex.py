#!/usr/bin/env python3
"""Install a locked official Codex release only when no Codex is available."""

import hashlib, io, json, os, platform, shutil, tarfile, urllib.request
from pathlib import Path

if shutil.which("codex"):
    print("Preserving existing official Codex executable")
    raise SystemExit(0)
if platform.system() != "Linux" or platform.machine() not in ("x86_64", "amd64"):
    raise SystemExit(
        "This locked distribution targets Linux x86_64; review the appropriate official release for this architecture"
    )
root = Path(__file__).resolve().parents[1]
lock = json.loads((root / "config/integrations.lock.json").read_text())
release = lock["codex_distribution"]
blob = urllib.request.urlopen(release["url"], timeout=120).read()
if hashlib.sha256(blob).hexdigest() != release["sha256"]:
    raise SystemExit("Official release checksum mismatch")
dest = Path.home() / ".local/opt" / ("codex-" + lock["codex"])
dest.mkdir(parents=True, exist_ok=True)
binary = dest / "codex"
with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as archive:
    members = [
        m
        for m in archive.getmembers()
        if m.isfile() and Path(m.name).name == "codex-x86_64-unknown-linux-musl"
    ]
    if len(members) != 1:
        raise SystemExit("Unexpected official release archive structure")
    data = archive.extractfile(members[0]).read()
    if not binary.exists():
        fd = os.open(binary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o755)
        with os.fdopen(fd, "wb") as file:
            file.write(data)
    elif hashlib.sha256(binary.read_bytes()).digest() != hashlib.sha256(data).digest():
        raise SystemExit(
            "Preserving conflicting existing Codex binary; inspect manually"
        )
link = Path.home() / ".local/bin/codex"
link.parent.mkdir(parents=True, exist_ok=True)
if not link.exists() and not link.is_symlink():
    link.symlink_to(binary)
else:
    raise SystemExit("Preserving existing Codex link; inspect PATH manually")
print(link)
