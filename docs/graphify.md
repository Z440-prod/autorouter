# Graphify

Graphify indexes code relationships, while Obsidian stores project decisions. Install Graphify separately following its upstream instructions. Run its local extraction with `graphify update /path/to/project --no-cluster`. Set `GRAPHIFY_OUT` to a project-specific output directory and `GRAPHIFY_BACKEND=none` for local extraction. Bind the resulting directory using `autorouter workspace --target HOST --graph /path/to/output`.

AutoRouter validates the Graphify root and build receipt. Results include source coordinates for current files, with stale sources excluded. Review live code before relying on inferred edges.
