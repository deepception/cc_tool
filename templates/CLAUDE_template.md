# Project Name

## Project Overview

<!-- Replace with your project description. Keep it to 2-3 sentences. -->

Brief description of what this project does, its tech stack, and primary purpose.

### Codebase Map

<!-- One line per top-level folder so Claude can navigate without exploring blind. -->

| Path | What lives here |
|------|-----------------|
| `module_a/` | Description |
| `module_b/` | Description |

<!--
For LARGE codebases: keep THIS root file lean (big picture + pointers + gotchas only).
Put local conventions in a CLAUDE.md inside the relevant subdirectory — Claude loads
every CLAUDE.md on the path from the file it's editing up to the repo root. A bloated
root file is loaded into every session regardless of relevance.
-->

### Key Documentation

<!-- Link important docs that Claude should read before making changes. -->

- @path/to/important-doc.md — description

---

## Commands

### Package Management

<!-- Adjust to your package manager: uv, npm, pnpm, pip, etc. -->

```bash
# Install dependencies
cd <module> && uv sync

# Add a dependency
cd <module> && uv add <package>

# Run a command in the virtual environment
cd <module> && uv run <command>
```

### Code Quality

<!-- Adjust to your linter/formatter. -->

```bash
# Format code
uv run ruff format .

# Lint and auto-fix
uv run ruff check . --fix

# Type checking
uv run mypy ./**/*.py

# Run all checks
uv run ruff format --check . && uv run ruff check . && uv run mypy ./**/*.py
```

### Testing

```bash
# Run tests for a module
cd <module> && uv run pytest tests/ -v

# Run a single test file
cd <module> && uv run pytest tests/test_specific.py -v

# Run with coverage
cd <module> && uv run pytest tests/ --cov=<module_name> --cov-report=term-missing
```

Prefer running single test files over the full suite for faster feedback.

---

## Code Style

<!-- Adjust to your project's conventions. -->

- Line length: 120 characters
- Formatter: `ruff format`
- Linter: `ruff check`
- Type checker: `mypy` (target: Python 3.12)
- Always run formatter and linter before committing
- Use type hints on all new function signatures
- Union types: `X | Y` syntax (not `Union[X, Y]`)
- Prefer dataclasses for config and internal data; Pydantic for external interfaces

### Naming Conventions

- Functions/variables: `snake_case`
- Classes: `PascalCase`
- Constants: `UPPER_SNAKE_CASE`
- Private members: `_leading_underscore`

---

## Architecture Rules

<!-- Keep these general. Add project-specific rules as needed. -->

- Each module is an independent package with its own dependency manifest
- Shared utilities belong in a common module; do NOT duplicate across packages
- Configuration uses environment variables; NEVER hardcode secrets
- Logging uses structured logging; do NOT use `print()` for production output

---

## Workflow

### Planning

1. Enter plan mode for any task with 3+ steps or architectural decisions
2. Read relevant source files BEFORE proposing changes
3. Write a plan with checkable items when appropriate
4. Track progress — mark items complete as you go

### Implementation

1. Make the smallest change that solves the problem
2. Run formatter and linter after editing code
3. Run relevant tests after changes — prefer single test files over full suite
4. Verify the change works before marking complete
5. Never commit files containing secrets (.env, credentials, API keys)

### Git Conventions

<!-- Adjust branch strategy to your project. -->

- Commit messages: imperative mood, concise ("Add batch validation" not "Added batch validation")
- One logical change per commit
- Always run code quality checks before committing

---

## Domain Context

<!-- Replace with YOUR project's domain terminology. Helps Claude use correct terminology in code and conversations. -->

| Term | Meaning |
|------|---------|
| Example Term | What it means in your domain |

---
