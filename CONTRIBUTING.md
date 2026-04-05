# Contributing to Fermax Blue for Home Assistant

Thank you for your interest in contributing! This document outlines the process and guidelines.

## Commit Convention

This project uses [Conventional Commits](https://www.conventionalcommits.org/). All commits **must** follow this format:

```
<type>(<scope>): <description>

[optional body]

[optional footer(s)]
```

### Types

| Type | Description |
|------|-------------|
| `feat` | A new feature |
| `fix` | A bug fix |
| `docs` | Documentation changes |
| `style` | Code style changes (formatting, no logic change) |
| `refactor` | Code refactoring (no feature or fix) |
| `test` | Adding or updating tests |
| `chore` | Maintenance tasks (deps, CI, etc.) |
| `perf` | Performance improvements |

### Examples

```
feat(doorbell): add support for multiple ring tones
fix(api): handle token refresh on 401 response
docs: add troubleshooting section for VEO-XS
test(api): add tests for call log parsing
chore(ci): update Python version matrix
```

## Pull Request Process

### Requirements

All PRs must:

1. **Pass CI checks** — Tests, linting, and HACS validation must all pass
2. **Follow conventional commits** — PR title and all commits must use the convention above
3. **Include tests** — New features must include unit tests; bug fixes should include a regression test
4. **Update documentation** — If your change affects user-facing behavior, update the README
5. **One concern per PR** — Keep PRs focused on a single feature or fix

### Review Criteria

PRs will be evaluated on:

- **Correctness** — Does it work? Does it handle edge cases?
- **Security** — No credentials, tokens, or sensitive data in code or logs
- **Compatibility** — Does it work with the supported Fermax devices?
- **Code quality** — Clean, readable, follows existing patterns

### Branch Naming

Use descriptive branch names:

```
feat/doorbell-photo-notification
fix/token-refresh-loop
docs/installation-guide
```

## Development Setup

### Prerequisites

- Docker (all tools run in containers, no local Python dependencies needed)
- A Fermax Blue account with a paired device (for integration testing)

### Quick Start

```bash
# Run ALL checks (lint + format + typecheck + tests) — same as CI
make check
```

### Available Commands

All commands use Docker (`python:3.12-slim`), so your local environment stays clean:

```bash
make lint          # Ruff linting (E, W, F, I, N, UP, B, SIM, RUF, PT, etc.)
make format        # Auto-format code with ruff
make format-check  # Verify formatting without changes (CI mode)
make typecheck     # Mypy type checking
make test          # Pytest with coverage report
make check         # Run ALL of the above in sequence
make cli           # Interactive API tester (test features against real API)
```

### Testing Against the Real API

The `make cli` command launches an interactive tool to test every API feature without Home Assistant:

```bash
# Will prompt for email/password
make cli

# Or pass credentials as env vars
FERMAX_USER=your@email.com FERMAX_PASS=yourpassword make cli
```

This is useful for:
- Verifying a new API method works before integrating it with HA entities
- Debugging API response formats
- Testing door opening, DND toggle, F1, call guard, etc. in isolation
- Making raw GET/POST calls to explore undocumented endpoints

### Running a Specific Test

```bash
docker run --rm -v $(pwd):/app -w /app python:3.12-slim sh -c \
  "pip install -q pytest pytest-asyncio httpx firebase-messaging homeassistant && \
   pytest tests/test_api.py::TestAutoOn::test_auto_on_success -v"
```

### CI Pipeline

Every push and PR runs 4 jobs in GitHub Actions:
- **test** — Pytest on Python 3.12 + 3.13 with coverage
- **lint** — Ruff check + format verification
- **type-check** — Mypy strict type checking
- **validate** — HACS integration validation

## Reporting Issues

When reporting a bug, please include:

1. Your Fermax device model (e.g., VEO-XL WiFi)
2. Home Assistant version
3. Integration version
4. Relevant log entries (Settings > System > Logs, filter by `fermax_blue`)
5. Steps to reproduce

## Code of Conduct

- Be respectful and constructive
- No credentials or personal data in issues or PRs
- Test your changes before submitting
