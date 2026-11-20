# Model routing

The native Codex adapter asks Luna to classify the next execution stage and chooses from the current Codex catalog. Model and reasoning pins win over automatic routing. If the catalog or classifier cannot produce an authorized target, the active Codex model stays in use.

In the portable path, the host can classify its task into analysis, implementation, debugging, verification or completion and call `recommend_route`. The first operator-ordered catalog entry for that stage is returned. An empty or unmatched catalog yields host default. Apply a recommendation through the native picker if appropriate.
