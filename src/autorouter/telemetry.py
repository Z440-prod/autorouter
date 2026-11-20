"""Read bounded routing evidence from Codex's own local logs."""

from datetime import datetime
import json
import os
from pathlib import Path
import re
import sqlite3

from .integrations import codex_home

MARKERS = {
    "routing applied at tool boundary": "route",
    "execution request ": "sample",
    "execution response completed ": "completed",
    "classified independent child task": "child",
}


def fields_from(body):
    for marker, kind in MARKERS.items():
        if marker in body:
            tail = body.rsplit(marker, 1)[-1]
            fields = dict(re.findall(
                r'\b(thread|turn|model|effort|classifier|classification_ms)=["\']?([a-zA-Z0-9.()-]+)',
                tail,
            ))
            if "effort" in fields:
                fields["effort"] = fields["effort"].removeprefix("Some(").removesuffix(")").lower()
            skills = re.search(r'\bskills=(\[[^\]]{0,1024}\])', tail)
            if skills:
                try:
                    fields["skills"] = [item for item in json.loads(skills[1]) if isinstance(item, str)]
                except ValueError:
                    pass
            return {"event": kind, **fields}
    return {}


def routing_events(*, thread=None, limit=30):
    trace = os.environ.get("AUTOROUTER_TRACE_FILE")
    if trace:
        # Native PTY tests can direct the Codex tracing layer to an isolated file.
        with Path(trace).open("rb") as log:
            log.seek(max(0, os.fstat(log.fileno()).st_size - 2_000_000))
            lines = log.read(2_000_000).decode(errors="replace").splitlines()
        output = []
        for line in reversed(lines):
            if "autorouter:" not in line:
                continue
            fields = fields_from(line)
            if fields and (thread is None or fields.get("thread") == thread):
                timestamp, _, _ = line.partition(" ")
                try:
                    output.append({"time": int(datetime.fromisoformat(timestamp).timestamp()), **fields})
                except ValueError:
                    continue
                if len(output) == limit:
                    break
        return output
    paths = list(codex_home().glob("logs_*.sqlite"))
    if not paths:
        return []
    database = max(paths, key=lambda path: path.stat().st_mtime)
    query = "SELECT ts,thread_id,feedback_log_body FROM logs WHERE target='autorouter'"
    args = []
    if thread:
        query += " AND thread_id=?"
        args.append(thread)
    query += " ORDER BY ts DESC, ts_nanos DESC LIMIT ?"
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        rows = db.execute(query, [*args, limit])
        output = []
        for timestamp, thread_id, body in rows:
            if not body:
                continue
            fields = fields_from(body)
            if fields:
                output.append({"time": timestamp, "thread": thread_id, **fields})
        return output
