"""cc-dash: a local, read-only observability dashboard for Claude Code sessions.

Modules
  pricing   per-model prices and the cost estimate for one API message
  parser    incremental JSONL reader and the per-transcript accumulator
  store     discovery under ~/.claude/projects, subagent linkage, guard events,
            and the JSON payloads the dashboard asks for
  server    the 127.0.0.1-only HTTP server (Host check, fixed asset map)

Standard library only. Nothing here writes to ~/.claude or to a project.
"""
