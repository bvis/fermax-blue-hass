#!/bin/bash
# Pre-push hook: replicates the full CI pipeline locally via Docker.
# Install: cp scripts/pre-push.sh .git/hooks/pre-push && chmod +x .git/hooks/pre-push
# Or run manually: bash scripts/pre-push.sh

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

DOCKER_RUN_312="docker run --rm -v $(pwd):/app -w /app python:3.12-slim"
DOCKER_RUN_313="docker run --rm -v $(pwd):/app -w /app python:3.13-slim"
DEPS="pytest pytest-asyncio pytest-cov httpx firebase-messaging homeassistant"

step() {
    echo -e "\n${YELLOW}▶ $1${NC}"
}

pass() {
    echo -e "${GREEN}✓ $1${NC}"
}

fail() {
    echo -e "${RED}✗ $1${NC}"
    exit 1
}

echo -e "${YELLOW}═══════════════════════════════════════════════════${NC}"
echo -e "${YELLOW}  Pre-push CI check (replicates GitHub Actions)   ${NC}"
echo -e "${YELLOW}═══════════════════════════════════════════════════${NC}"

# 1. Lint
step "Lint (ruff check)"
$DOCKER_RUN_312 sh -c "pip install -q ruff 2>/dev/null && ruff check custom_components/ tests/ scripts/" \
    && pass "Lint" || fail "Lint failed"

# 2. Format
step "Format check (ruff format)"
$DOCKER_RUN_312 sh -c "pip install -q ruff 2>/dev/null && ruff format --check custom_components/ tests/ scripts/" \
    && pass "Format" || fail "Format check failed — run 'make format'"

# 3. Type check
step "Type check (mypy)"
$DOCKER_RUN_312 sh -c "pip install -q mypy httpx firebase-messaging homeassistant 2>/dev/null && mypy custom_components/fermax_blue/ --ignore-missing-imports" \
    && pass "Type check" || fail "Type check failed"

# 4. Tests on Python 3.12
step "Tests (Python 3.12)"
$DOCKER_RUN_312 sh -c "pip install -q $DEPS 2>/dev/null && pytest tests/ -q --tb=short" \
    && pass "Tests (3.12)" || fail "Tests failed on Python 3.12"

# 5. Tests on Python 3.13
step "Tests (Python 3.13)"
$DOCKER_RUN_313 sh -c "apt-get update -qq && apt-get install -qq -y gcc > /dev/null 2>&1 && pip install -q $DEPS 2>/dev/null && pytest tests/ -q --tb=short" \
    && pass "Tests (3.13)" || fail "Tests failed on Python 3.13"

echo -e "\n${GREEN}═══════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  All checks passed — safe to push                 ${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════${NC}"
