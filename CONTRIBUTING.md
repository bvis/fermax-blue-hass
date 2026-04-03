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

- Python 3.12+
- A Fermax Blue account with a paired device

### Install Dependencies

```bash
pip install -e ".[dev]"
```

### Running Tests

```bash
# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ -v --cov=custom_components/fermax_blue --cov-report=term-missing

# Run a specific test file
pytest tests/test_api.py -v

# Run a specific test
pytest tests/test_api.py::TestAuthentication::test_successful_auth -v
```

### Linting

```bash
pip install ruff
ruff check custom_components/ tests/
```

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
