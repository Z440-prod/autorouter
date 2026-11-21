"""Bounded retrieval from Graphify's local, rebuildable project indexes."""

from collections import defaultdict
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

GRAPH_LIMIT = 128 * 1024**2
MANIFEST_LIMIT = 4 * 1024**2
RECEIPT_LIMIT = 16 * 1024


def projects(state):
    project = Path(__file__).resolve().parents[2]
    return {
        name: (root.resolve(), state / "graphs" / name)
        for name, root in (
            ("autorouter", project),
            ("native", project.with_name("codex-autorouter")),
        )
    }


def graph_identity(directory):
    stat = (directory / "graph.json").stat()
    manifest = (directory / "manifest.json").stat()
    if stat.st_size > GRAPH_LIMIT or manifest.st_size > MANIFEST_LIMIT:
        raise ValueError("Graph exceeds the local retrieval size limit")
    receipt = read_json(directory / ".graphify_build_state.json", RECEIPT_LIMIT)
    if not isinstance(receipt, dict) or (
        (receipt.get("graph_size"), receipt.get("graph_mtime_ns")) != (stat.st_size, stat.st_mtime_ns)
    ):
        raise ValueError("Graph differs from its published build receipt")
    return tuple((s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns) for s in (stat, manifest))


def read_json(path, limit):
    with path.open("rb") as handle:
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Graph index file exceeds the local retrieval size limit")
    return json.loads(data)


@lru_cache(maxsize=2)
def graph_index(directory, identity):
    graph = read_json(directory / "graph.json", GRAPH_LIMIT)
    manifest = read_json(directory / "manifest.json", MANIFEST_LIMIT)
    if not isinstance(graph.get("nodes"), list) or not isinstance(graph.get("links"), list):
        raise ValueError("Invalid Graphify graph")
    nodes = {node["id"]: node for node in graph["nodes"]}
    terms = defaultdict(list)
    edges = defaultdict(list)
    for identifier, node in nodes.items():
        for term in set(re.findall(r"[a-z0-9]+", node["label"].lower())):
            terms[term].append(identifier)
    for index, edge in enumerate(graph["links"]):
        edges[edge["source"]].append(index)
        edges[edge["target"]].append(index)
    return nodes, terms, edges, graph["links"], manifest


@lru_cache(maxsize=2048)
def matches_source(path, stamp, digest):
    # Graphify's ast_hash and semantic_hash are MD5 content checks, not trust signatures.
    algorithm = "md5" if len(digest) == 32 else "sha256"
    if stamp[2] > 8 * 1024**2:
        return False
    with path.open("rb") as file:
        actual = hashlib.file_digest(file, lambda: hashlib.new(algorithm, usedforsecurity=False)).hexdigest()
    return actual == digest and source_stamp(path.stat()) == stamp


def source_stamp(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)



def status(root, directory):
    try:
        with (directory / ".graphify_root").open() as marker:
            location = marker.read(4097)
        if len(location) > 4096 or location.strip() != str(root):
            return {"status": "wrong-root"}
        graph_identity(directory)
        return {"status": "available", "graph": str(directory / "graph.json"),
                "indexed_at": (directory / "manifest.json").stat().st_mtime}
    except FileNotFoundError:
        return {"status": "missing"}
    except (OSError, ValueError):
        return {"status": "unavailable"}


def refresh(root, directory, force=False):
    import fcntl
    directory.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    def update(output):
        env = dict(os.environ, GRAPHIFY_OUT=str(output), GRAPHIFY_BACKEND="none",
                   GRAPHIFY_QUERY_LOG_DISABLE="1", GRAPHIFY_NO_TIPS="1",
                   GRAPHIFY_FOLLOW_SYMLINKS="0", GRAPHIFY_MAX_WORKERS="2",
                   PYTHONUNBUFFERED="1")
        command = ["graphify", "update", str(root), "--no-cluster"]
        if force:
            command.append("--force")
        subprocess.run(command, cwd=root, env=env, check=True, timeout=900)

    with (directory.parent / (directory.name + ".lock")).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        health = status(root, directory)["status"]
        if health == "available" or not (directory / "graph.json").exists():
            update(directory)
            return
        # Graphify reads old graph.json even with --force. Recover in an empty directory.
        with tempfile.TemporaryDirectory(dir=directory.parent, prefix=directory.name + "-repair-") as stage:
            candidate = Path(stage) / "graph"
            update(candidate)
            graph_identity(candidate)
            previous = directory.with_name(f"{directory.name}.corrupt-{time.time_ns()}")
            directory.rename(previous)
            try:
                candidate.rename(directory)
            except OSError:
                previous.rename(directory)
                raise


def query(root, directory, topic):
    health = status(root, directory)
    if health["status"] != "available":
        return {**health, "relationships": []}
    try:
        identity = graph_identity(directory)
        nodes, symbols, adjacency, edges, manifest = graph_index(directory, identity)
        if identity != graph_identity(directory):
            return {"status": "updating", "relationships": []}
        terms = set(re.findall(r"[a-z0-9]+", topic.lower()))
        if not terms:
            return {**health, "relationships": []}
        matches = defaultdict(int)
        for term in terms:
            for identifier in symbols.get(term, ()):
                matches[identifier] += 1
        scores = {identifier: (nodes[identifier]["label"].lower().rstrip("()") == topic.lower(), count)
                  for identifier, count in matches.items()}
        closest = sorted(scores, key=lambda i: (scores[i], str(i)), reverse=True)[:10]
        candidates = sorted({index for i in closest for index in adjacency.get(i, ())})
        references = {}

        def manifest_entry(name):
            return manifest.get(name) or manifest.get(f"{root.name}/{name}", {})

        def source(row):
            name = row.get("source_file", "")
            if name not in references:
                path = root / name
                entry = manifest_entry(name)
                references[name] = (
                    bool(name) and not Path(name).is_absolute()
                    and ".." not in Path(name).parts and not path.is_symlink()
                    and path.resolve().is_relative_to(root)
                    and path.is_file() and path.stat().st_mtime == entry.get("mtime")
                )
                digest = entry.get("sha256") or entry.get("ast_hash") or entry.get("semantic_hash")
                if references[name] and digest:
                    references[name] = matches_source(path, source_stamp(path.stat()), digest)
            if references[name]:
                return {"path": name, "location": row.get("source_location")}

        relationships = []
        stale = False
        for index in candidates:
            edge = edges[index]
            start, end = edge["source"], edge["target"]
            if start not in nodes or end not in nodes:
                continue
            origin = source(edge)
            left, right = source(nodes[start]), source(nodes[end])
            if not (origin and left and right):
                stale |= any(
                    ref is None and bool(manifest_entry(row.get("source_file", "")))
                    for row, ref in ((edge, origin), (nodes[start], left), (nodes[end], right))
                )
                continue
            relationships.append((
                max(scores.get(start, (False, 0)), scores.get(end, (False, 0))),
                edge["relation"] not in ("contains", "defines"),
                {
                    "from": {"symbol": nodes[start]["label"][:200], **left},
                    "relation": edge["relation"],
                    "to": {"symbol": nodes[end]["label"][:200], **right},
                    "evidence": origin,
                    "confidence": edge.get("confidence", "unknown"),
                },
            ))
        relationships.sort(key=lambda edge: edge[:2], reverse=True)
        return {**health, "status": "partially-stale" if stale else "available",
                "relationships": [edge[2] for edge in relationships[:8]]}
    except (OSError, TypeError, ValueError, AttributeError, KeyError):
        return {"status": "unavailable", "relationships": []}
