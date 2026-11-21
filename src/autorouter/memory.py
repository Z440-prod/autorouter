"""Canonical Markdown with a rebuildable, credential-filtered FTS5 index."""

import hashlib, json, os, re, sqlite3, time
from pathlib import Path

SECRET = re.compile(
    r"-----BEGIN .*PRIVATE KEY-----|\bsk-[A-Za-z0-9_-]{16,}|\bgh[pousr]_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{30,}|^\s*(?:api[_-]?key|access[_-]?token|password|secret)\s*[:=]",
    re.M | re.I,
)


SNAPSHOT_SECONDS = 2
EXCLUDED = {"cache", "node_modules", "credentials", "secrets"}
BLOCKED_NAMES = EXCLUDED | {"passwords", "id_rsa", "id_ed25519"}


def version(stat):
    return json.dumps((stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))


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
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS note_versions(path TEXT PRIMARY KEY, version TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS note_snapshot(id INTEGER PRIMARY KEY, scope TEXT NOT NULL, updated REAL NOT NULL);
        """)
        self.scope = str(self.root) + ":" + hashlib.sha256(SECRET.pattern.encode()).hexdigest()

    def _current(self, max_age):
        row = self.db.execute("SELECT scope,updated FROM note_snapshot WHERE id=1").fetchone()
        return bool(row and row[0] == self.scope and 0 <= time.time() - row[1] < max_age)

    def ensure_current(self):
        """Readers normally use the committed snapshot produced by the existing watcher."""
        if self._current(SNAPSHOT_SECONDS):
            return
        available = self.db.execute("SELECT 1 FROM note_snapshot WHERE scope=?", (self.scope,)).fetchone()
        if available:
            self.db.execute("PRAGMA busy_timeout=0")
        try:
            self.reindex(max_age=SNAPSHOT_SECONDS)
        except sqlite3.OperationalError as exc:
            if not available or exc.sqlite_errorcode != sqlite3.SQLITE_BUSY:
                raise
        finally:
            if available:
                self.db.execute("PRAGMA busy_timeout=5000")

    def reindex(self, *, max_age=0):
        seen = set()
        updated = 0
        t = time.perf_counter()
        with self.db:
            # A second indexer must see the committed snapshot before it computes deletions.
            self.db.execute("BEGIN IMMEDIATE")
            if max_age and self._current(max_age):
                return {"notes": None, "updated": 0, "elapsed_ms": 0}
            previous = {row[0]: (row[1], row[2]) for row in self.db.execute(
                "SELECT n.path,n.hash,v.version FROM notes n LEFT JOIN note_versions v USING(path)"
            )}
            same_scope = self.db.execute("SELECT 1 FROM note_snapshot WHERE scope=?", (self.scope,)).fetchone()
            for directory, dirs, files in os.walk(self.root, followlinks=False):
                dirs[:] = [name for name in dirs if not name.startswith(".") and name.lower() not in EXCLUDED
                           and not (Path(directory) / name).is_symlink()]
                for name in files:
                    f = Path(directory) / name
                    if f.suffix != ".md" or name.startswith(".") or f.stem.lower() in BLOCKED_NAMES:
                        continue
                    path = f.relative_to(self.root).as_posix()
                    old_hash, old_version = previous.get(path, (None, None))
                    try:
                        if f.is_symlink() or not f.is_file():
                            continue
                        before = f.stat()
                        if before.st_size > 2_000_000:
                            continue
                        stamp = version(before)
                        if same_scope and stamp == old_version:
                            seen.add(path)
                            continue
                        if not f.resolve(strict=True).is_relative_to(self.root):
                            continue
                        body = f.read_text(errors="replace")
                        if f.is_symlink() or version(f.stat()) != stamp or not f.resolve(strict=True).is_relative_to(self.root):
                            continue
                    except OSError:
                        continue
                    if SECRET.search(body):
                        continue
                    seen.add(path)
                    h = hashlib.sha256(body.encode()).hexdigest()
                    self.db.execute("INSERT OR REPLACE INTO note_versions VALUES(?,?)", (path, stamp))
                    if h == old_hash:
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
            for path in previous.keys() - seen:
                for table, column in (("notes", "path"), ("search", "path"), ("links", "source"),
                                      ("note_versions", "path"), ("vectors", "path")):
                    self.db.execute(f"DELETE FROM {table} WHERE {column}=?", (path,))
            self.db.execute("INSERT OR REPLACE INTO note_snapshot VALUES(1,?,?)", (self.scope, time.time()))
        if self.embedding:
            try:
                for row in self.db.execute(
                    "SELECT path,hash,body FROM notes WHERE EXISTS "
                    "(SELECT 1 FROM note_snapshot WHERE scope=?)", (self.scope,),
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
                                "INSERT OR REPLACE INTO vectors SELECT ?,?,?,? WHERE EXISTS "
                                "(SELECT 1 FROM note_snapshot WHERE scope=?) AND EXISTS "
                                "(SELECT 1 FROM notes WHERE path=? AND hash=?)",
                                (
                                    row["path"],
                                    row["hash"],
                                    self.embedding.identity,
                                    json.dumps(vector),
                                    self.scope,
                                    row["path"],
                                    row["hash"],
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
        # Read snippets and their file receipts from the same committed generation.
        self.db.execute("BEGIN")
        try:
            return self._search(query, limit, folder)
        finally:
            self.db.rollback()

    def _search(self, query, limit, folder):
        if folder:
            relative = Path(folder)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("Folder must be relative to the vault")
            folder = "" if relative == Path(".") else relative.as_posix() + "/"

        if not self.db.execute("SELECT 1 FROM note_snapshot WHERE scope=?", (self.scope,)).fetchone():
            return []
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
        def fresh(row):
            f = self.root / row["path"]
            stamp = self.db.execute("SELECT version FROM note_versions WHERE path=?", (row["path"],)).fetchone()
            try:
                return (stamp is not None and not f.is_symlink() and f.resolve(strict=True).is_relative_to(self.root)
                        and stamp[0] == version(f.stat()))
            except OSError:
                return False
        return [row for row in rows if fresh(row)]

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
