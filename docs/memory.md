# Shared memory

Point each project at an approved Markdown folder in Obsidian. Grant each client from the same workspace root. The core indexes that folder in a local SQLite database with FTS5. Matching excerpts include paths relative to the folder. After Markdown edits, the next query reconciles the index. Edit canonical notes in Obsidian or an authorized editor. Checkpoints use a separate SQLite table.

The index is a derived cache. `memory_status` reports integrity and semantic retrieval status. Excerpts are untrusted evidence. Check the original note and live source before acting on them.
