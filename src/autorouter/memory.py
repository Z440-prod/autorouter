"""Canonical Markdown with a rebuildable, credential-filtered FTS5 index."""

import hashlib, json, re, sqlite3, time
from pathlib import Path

SECRET = re.compile(
    r"-----BEGIN .*PRIVATE KEY-----|\bsk-[A-Za-z0-9_-]{16,}|\bgh[pousr]_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{30,}|^\s*(?:api[_-]?key|access[_-]?token|password|secret)\s*[:=]",
    re.M | re.I,
)


class Memory:
    def __init__(self, root, db, embeddings=None):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(db)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA secure_delete=ON")
        self.embedding = None
        self.embedding_error = None
        if embeddings and embeddings.get("enabled"):
            from .embeddings import Embeddings

            try:
                self.embedding = Embeddings(embeddings)
            except ValueError as e:
                self.embedding_error = str(e)
        self.db.executescript(
            """CREATE TABLE IF NOT EXISTS vectors(path TEXT PRIMARY KEY, hash TEXT, provider TEXT, vector TEXT); CREATE TABLE IF NOT EXISTS notes(path TEXT PRIMARY KEY,hash TEXT,body TEXT,metadata TEXT); CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(path,body); CREATE TABLE IF NOT EXISTS links(source TEXT,target TEXT,PRIMARY KEY(source,target));"""
        )

    def reindex(self):
        seen = set()
        updated = 0
        t = time.perf_counter()
        with self.db:
            # A second indexer must see the committed snapshot before it computes deletions.
            self.db.execute("BEGIN IMMEDIATE")
            for f in self.root.rglob("*.md"):
                rel = f.relative_to(self.root)
                if f.stem.lower() in {
                    "credentials",
                    "secrets",
                    "passwords",
                    "id_rsa",
                    "id_ed25519",
                }:
                    continue
                if f.is_symlink() or any(
                    x.startswith(".")
                    or x.lower() in {"cache", "node_modules", "credentials", "secrets"}
                    for x in rel.parts
                ):
                    continue
                if (
                    not f.resolve().is_relative_to(self.root)
                    or f.stat().st_size > 2_000_000
                ):
                    continue
                body = f.read_text(errors="replace")
                if SECRET.search(body):
                    continue
                path = str(rel)
                seen.add(path)
                h = hashlib.sha256(body.encode()).hexdigest()
                old = self.db.execute(
                    "SELECT hash FROM notes WHERE path=?", (path,)
                ).fetchone()
                if old and old[0] == h:
                    continue
                meta = {
                    "tags": re.findall(r"(?<!\w)#([\w/-]+)", body),
                    "frontmatter": body.split("---", 2)[1]
                    if body.startswith("---\n") and body.count("---") >= 2
                    else "",
                }
                self.db.execute(
                    "INSERT OR REPLACE INTO notes VALUES(?,?,?,?)",
                    (path, h, body, json.dumps(meta)),
                )
                self.db.execute("DELETE FROM search WHERE path=?", (path,))
                self.db.execute("INSERT INTO search VALUES(?,?)", (path, body))
                self.db.execute("DELETE FROM links WHERE source=?", (path,))
                self.db.executemany(
                    "INSERT OR IGNORE INTO links VALUES(?,?)",
                    [
                        (path, x.split("|")[0])
                        for x in re.findall(r"\[\[([^]]+)\]\]", body)
                    ],
                )
                updated += 1
            for row in self.db.execute("SELECT path FROM notes").fetchall():
                if row[0] not in seen:
                    for table, col in [
                        ("notes", "path"),
                        ("search", "path"),
                        ("links", "source"),
                    ]:
                        self.db.execute(f"DELETE FROM {table} WHERE {col}=?", (row[0],))
        with self.db:
            self.db.execute(
                "DELETE FROM vectors WHERE path NOT IN (SELECT path FROM notes)"
            )
        if self.embedding:
            try:
                for row in self.db.execute(
                    "SELECT path,hash,body FROM notes"
                ).fetchall():
                    old = self.db.execute(
                        "SELECT hash,provider FROM vectors WHERE path=?", (row["path"],)
                    ).fetchone()
                    if (
                        not old
                        or old[0] != row["hash"]
                        or old[1] != self.embedding.identity
                    ):
                        vector = self.embedding.embed(row["body"])
                        with self.db:
                            self.db.execute(
                                "INSERT OR REPLACE INTO vectors VALUES(?,?,?,?)",
                                (
                                    row["path"],
                                    row["hash"],
                                    self.embedding.identity,
                                    json.dumps(vector),
                                ),
                            )
                with self.db:
                    self.db.execute(
                        "DELETE FROM vectors WHERE path NOT IN (SELECT path FROM notes)"
                    )
                self.embedding_error = None
            except Exception as e:
                self.embedding_error = (
                    type(e).__name__
                    + ": embedding request failed; lexical retrieval remains available"
                )
        return {
            "notes": len(seen),
            "updated": updated,
            "elapsed_ms": round((time.perf_counter() - t) * 1000, 3),
        }

    def search(self, query, limit=6, *, folder=""):
        if folder:
            relative = Path(folder)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("Folder must be relative to the vault")
            folder = "" if relative == Path(".") else relative.as_posix() + "/"

        tokens = re.findall(r"[\w./-]+", query)[:32]
        if not tokens:
            return []
        q = " OR ".join('"' + x.replace('"', '""') + '"' for x in tokens)
        rows = [
            dict(x)
            for x in self.db.execute(
                """SELECT path,snippet(search,1,'','','…',40) AS excerpt,bm25(search,8,1) AS rank
                FROM search WHERE search MATCH ? AND substr(path,1,length(?)) = ?
                ORDER BY rank LIMIT ?""",
                (q, folder, folder, limit),
            )
        ]
        if self.embedding:
            try:
                from .embeddings import cosine

                vector = self.embedding.embed(query)
                candidates = []
                for row in self.db.execute(
                    """SELECT n.path,n.body,v.vector FROM vectors v
                    JOIN notes n ON n.path=v.path AND n.hash=v.hash
                    WHERE v.provider=? AND substr(n.path,1,length(?)) = ?""",
                    (self.embedding.identity, folder, folder),
                ):
                    candidates.append((cosine(vector, json.loads(row["vector"])), row))
                scores = {row["path"]: 1 / (60 + i) for i, row in enumerate(rows)}
                pool = {row["path"]: row for row in rows}
                for i, (_, row) in enumerate(
                    sorted(candidates, key=lambda x: -x[0])[:limit]
                ):
                    scores[row["path"]] = scores.get(row["path"], 0) + 1 / (60 + i)
                    pool.setdefault(
                        row["path"],
                        {"path": row["path"], "excerpt": row["body"][:500], "rank": 0},
                    )
                rows = [
                    pool[path]
                    for path in sorted(scores, key=lambda x: -scores[x])[:limit]
                ]
            except Exception:
                self.embedding_error = (
                    "Embedding retrieval failed; lexical retrieval remains available"
                )
        # Exact identifiers and paths rank ahead of broad lexical matches.
        rows.sort(
            key=lambda x: (
                not any(t.lower() in x["path"].lower() for t in tokens),
                x["rank"],
            )
        )
        return rows

    def status(self):
        return {
            "vault": str(self.root),
            "notes": self.db.execute("SELECT count(*) FROM notes").fetchone()[0],
            "integrity": self.db.execute("PRAGMA quick_check").fetchone()[0],
            "semantic": self.embedding_error
            or (
                self.embedding.identity
                if self.embedding
                else "disabled; no embedding provider authorized"
            ),
        }
