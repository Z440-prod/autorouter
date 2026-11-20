"""Stage an official release with the reviewed AutoRouter patch and all gates."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
import tomllib
from urllib.request import urlopen

from .evaluation import write as write_atomic

REPO = "https://github.com/openai/codex"
API = "https://api.github.com/repos/openai/codex"
PROJECT = Path(__file__).resolve().parents[2]
GATES = ("source", "runtime", "build", "python", "rust", "pty")


def release_info():
    with urlopen(f"{API}/releases/latest", timeout=15) as response:
        release = json.load(response)
    tag = release["tag_name"]
    if (
        not re.fullmatch(r"rust-v\d+\.\d+\.\d+", tag)
        or release["html_url"] != f"{REPO}/releases/tag/{tag}"
        or release["prerelease"]
        or release["draft"]
    ):
        raise RuntimeError("Unexpected upstream release metadata")
    return release


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def registry_pins(lock):
    return {
        (entry["name"], entry["version"], entry["source"], entry.get("checksum"))
        for entry in tomllib.loads(lock.read_text())["package"]
        if "source" in entry
    }


def version(binary):
    return subprocess.check_output([str(binary), "--version"], text=True, timeout=20).strip().removeprefix("codex-cli ")


def download_runtime(release, destination):
    target = {"x86_64": "x86_64", "aarch64": "aarch64"}.get(platform.machine())
    if platform.system() != "Linux" or target is None:
        raise RuntimeError("Automatic staging supports Linux x86_64 and aarch64")
    tag = release["tag_name"]
    name = f"codex-package-{target}-unknown-linux-musl.tar.gz"
    asset = next((asset for asset in release["assets"] if asset["name"] == name), None)
    url = f"{REPO}/releases/download/{tag}/{name}"
    if (
        not asset or asset["browser_download_url"] != url
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", asset.get("digest") or "")
        or not 0 < asset["size"] <= 1_000_000_000
    ):
        raise RuntimeError("Official runtime asset or checksum unavailable")
    archive = destination / name
    with urlopen(url, timeout=60) as response, archive.open("xb") as file:
        remaining = asset["size"]
        while chunk := response.read(min(1024 * 1024, remaining + 1)):
            remaining -= len(chunk)
            if remaining < 0:
                raise RuntimeError("Runtime asset exceeded the published size")
            file.write(chunk)
    if remaining != 0 or f"sha256:{digest(archive)}" != asset["digest"]:
        raise RuntimeError("Official runtime asset did not match its checksum")
    runtime = destination / "runtime"
    runtime.mkdir()
    with tarfile.open(archive) as package:
        package.extractall(runtime, filter="data")
    matches = list(runtime.rglob("codex-package.json"))
    if len(matches) != 1 or json.loads(matches[0].read_text())["version"] != tag.removeprefix("rust-v"):
        raise RuntimeError("Runtime package manifest did not match the release")
    return matches[0].parent


def busy(binary):
    """Catch sessions launched before the launcher began taking an activation lease."""
    target = binary.resolve()
    for process in Path("/proc").glob("[0-9]*"):
        try:
            if (process / "exe").readlink() == target:
                return True
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
    return False


def replace_link(link, target):
    with tempfile.TemporaryDirectory(dir=link.parent) as scratch:
        new = Path(scratch) / "link"
        new.symlink_to(target)
        os.replace(new, link)


def activate(package, candidate, *, rollback=False):
    current = package / "codex"
    previous = package / "codex.rollback"
    with (package / ".activation.lock").open("a") as lease:
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if busy(current):
            raise RuntimeError("An AutoRouter session is active")
        if rollback:
            if not previous.is_file():
                raise RuntimeError("No previous package is recorded")
            candidate = previous.resolve()
        else:
            candidate = candidate.resolve()
            gates = json.loads((candidate.parents[1] / "autorouter-verified.json").read_text())
            if (
                gates["gates"] != list(GATES) or gates["sha256"] != digest(candidate)
                or gates.get("sidecars") != {
                    name: digest(candidate.parent / name)
                    for name in ("codex-code-mode-host", "codex-exec-server")
                }
            ):
                raise RuntimeError("Candidate has missing gates or a different executable or sidecar hash")
        active = current.resolve()
        if candidate == active:
            return str(active)
        replace_link(previous, active)
        replace_link(current, candidate)
    return str(candidate)


def command(args, binary, package, auto_marker):
    release = release_info() if args not in (["rollback"], ["activate"]) else None
    if args == ["check"]:
        print(json.dumps({
            "installed": version(binary),
            "latestStable": release["tag_name"].removeprefix("rust-v"),
            "releaseNotes": release["html_url"],
        }, indent=2))
        return
    state = Path.home() / ".local/share/autorouter-build/updates"
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    latest = state / "latest-ready.json"
    if args == ["rollback"]:
        print(activate(package, None, rollback=True))
        return
    if args == ["activate"]:
        print(activate(package, Path(json.loads(latest.read_text())["binary"])))
        return
    tag = release["tag_name"]
    if tuple(map(int, tag.removeprefix("rust-v").split("."))) <= tuple(map(int, version(binary).split("."))):
        print("AutoRouter is on the current stable version.")
        return
    patch = PROJECT / "docs/autorouter-3-native.patch"
    if not patch.is_file():
        patch = PROJECT / "adapters/codex/rust-v0.169.0.patch"
    patch_hash = digest(patch)
    work = state / f"{tag}-{patch_hash[:12]}"
    with (state / ".stage.lock").open("a") as lease:
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        ready = work / "ready.json"
        if not ready.is_file():
            work.mkdir(mode=0o700, exist_ok=True)
            snapshot = work / "autorouter.patch"
            if not snapshot.exists():
                shutil.copyfile(patch, snapshot)
            if digest(snapshot) != patch_hash:
                raise RuntimeError("The staged patch differs from the reviewed patch")
            try:
                if (work / "built.json").is_file():
                    finish_gates(release, work, snapshot)
                elif (work / "source").exists():
                    raise RuntimeError(f"An interrupted source preparation remains in {work}")
                else:
                    stage(release, work, snapshot)
            except (subprocess.SubprocessError, OSError, ValueError) as error:
                raise RuntimeError(f"Staging failed. See {work / 'stage.log'}") from error
        record = json.loads(ready.read_text())
        replace_link(latest, ready)
    print(json.dumps(record, indent=2))
    if args == ["automatic"] and auto_marker.is_file():
        print(activate(package, Path(record["binary"])))


def stage(release, work, patch):
    tag = release["tag_name"]
    fork = Path.home() / "Projects/codex-autorouter"
    source = work / "source"
    log_path = work / "stage.log"
    build_target = work.parent / "targets" / tag
    build_target.parent.mkdir(mode=0o700, exist_ok=True)
    env = dict(os.environ, AUTOROUTER_SOURCE=str(source), CARGO_TARGET_DIR=str(build_target.resolve()),
               RUST_MIN_STACK="33554432", CARGO_PROFILE_TEST_DEBUG="0")
    env.pop("AUTOROUTER_ENABLED", None)
    build_sysroot = Path.home() / ".local/share/autorouter-build/sysroot"
    if build_sysroot.is_dir():
        env.update(PKG_CONFIG_SYSROOT_DIR=str(build_sysroot),
                   PKG_CONFIG_PATH=str(build_sysroot / "usr/lib/x86_64-linux-gnu/pkgconfig"),
                   CPATH=f"{build_sysroot}/usr/include:{build_sysroot}/usr/include/x86_64-linux-gnu")
    env["PATH"] = f"{Path.home()}/.cargo/bin:{PROJECT}/.venv/bin:{env['PATH']}"
    with log_path.open("a") as log:
        def run(argv, cwd=PROJECT):
            subprocess.run(argv, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT,
                           check=True, timeout=3600)

        ref = f"refs/autorouter/staging/{tag}"
        run(["git", "fetch", "--no-write-fetch-head", "--no-tags", f"{REPO}.git", f"refs/tags/{tag}:{ref}"], fork)
        with urlopen(f"{API}/git/ref/tags/{tag}", timeout=15) as response:
            expected = json.load(response)["object"]["sha"]
        fetched = subprocess.check_output(["git", "rev-parse", ref], cwd=fork, text=True).strip()
        if not re.fullmatch(r"[0-9a-f]{40}", expected) or fetched != expected:
            raise RuntimeError("Fetched release ref differs from GitHub's published ref")
        run(["git", "worktree", "add", "--detach", str(source), fetched], fork)
        run(["git", "apply", "--3way", "--exclude=codex-rs/Cargo.lock", str(patch)], source)
        run(["git", "diff", "--cached", "--check"], source)
        rust = source / "codex-rs"
        lock = rust / "Cargo.lock"
        upstream_pins = registry_pins(lock)
        # The official release may retain workspace versions from before its tag was cut.
        run(["cargo", "update", "--workspace", "--offline"], rust)
        if registry_pins(lock) != upstream_pins:
            raise RuntimeError("Patch refresh changed official third-party dependency pins")
        env["AUTOROUTER_RUNTIME"] = str(download_runtime(release, work))
        candidate = Path(subprocess.check_output(
            [str(PROJECT / "scripts/build-native.sh"), "--stage"], env=env, text=True,
            stderr=log, timeout=3600,
        ).strip())
        write_atomic(work / "built.json", build_receipt(source, patch, candidate, Path(env["AUTOROUTER_RUNTIME"])))
    finish_gates(release, work, patch)


def build_receipt(source, patch, candidate, runtime):
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=source, text=True).strip()

    if git("diff", "--name-only", "--", ".", ":(exclude)codex-rs/Cargo.lock") or git(
        "ls-files", "--others", "--exclude-standard"
    ):
        raise RuntimeError("The isolated worktree has unreviewed edits")
    tree = git("write-tree")
    # A three-way patch can merge different surrounding lines on the new tag.
    # Replay it in a temporary index and compare the resulting tree.
    with tempfile.TemporaryDirectory() as scratch:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(scratch) / "index"))
        subprocess.run(["git", "read-tree", "HEAD"], cwd=source, env=env,
                       check=True, timeout=30)
        subprocess.run(["git", "apply", "--cached", "--3way", "--exclude=codex-rs/Cargo.lock",
                        str(patch)], cwd=source, env=env, check=True, capture_output=True, timeout=30)
        expected = subprocess.check_output(["git", "write-tree"], cwd=source, env=env, text=True).strip()
    if tree != expected:
        raise RuntimeError("The isolated source differs from a fresh replay of the reviewed patch")
    candidate = candidate.resolve(strict=True)
    return {
        "binary": str(candidate), "sha256": digest(candidate),
        "sourceHead": git("rev-parse", "HEAD"), "sourceTree": tree,
        "lockSha256": digest(source / "codex-rs/Cargo.lock"),
        "patchSha256": digest(patch), "runtime": str(runtime.resolve(strict=True)),
        "sidecars": {
            name: digest(candidate.parent / name)
            for name in ("codex-code-mode-host", "codex-exec-server")
        },
    }


def finish_gates(release, work, patch):
    source = work / "source"
    receipt = json.loads((work / "built.json").read_text())
    candidate = Path(receipt["binary"])
    runtime = Path(receipt["runtime"])
    if build_receipt(source, patch, candidate, runtime) != receipt:
        raise RuntimeError("The built candidate or its source changed before verification")
    if version(candidate) != release["tag_name"].removeprefix("rust-v"):
        raise RuntimeError("Candidate executable version differs from the release")
    build_target = work.parent / "targets" / release["tag_name"]
    env = dict(os.environ, AUTOROUTER_SOURCE=str(source), CARGO_TARGET_DIR=str(build_target.resolve()),
               AUTOROUTER_RUNTIME=str(runtime))
    env.pop("AUTOROUTER_ENABLED", None)
    with (work / "stage.log").open("a") as log:
        for argv, timeout in (
            (["bash", "scripts/validate.sh"], 600),
            ([str(PROJECT / "scripts/test-native-rust.sh")], 3 * 3600),
            (["/usr/bin/python3", "scripts/test-native-pty.py", str(candidate), "--autorouter",
              "--bare", "--log", str(work / "terminal.log")], 180),
        ):
            subprocess.run(argv, cwd=PROJECT, env=env, stdout=log, stderr=subprocess.STDOUT,
                           check=True, timeout=timeout)
    if build_receipt(source, patch, candidate, runtime) != receipt:
        raise RuntimeError("The built candidate changed during verification")
    record = {**receipt, "gates": list(GATES)}
    write_atomic(candidate.parents[1] / "autorouter-verified.json", record)
    write_atomic(work / "ready.json", record)
