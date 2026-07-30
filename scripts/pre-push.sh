#!/bin/bash
# Pre-push hook: exact replica of GitHub Actions using the same Dockerfile.dev.
# Single source of truth: Dockerfile.dev defines all deps, ARG PYTHON_VERSION for matrix.
#
# Install: cp scripts/pre-push.sh .git/hooks/pre-push && chmod +x .git/hooks/pre-push
# Or run: make pre-push

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

PROJECT_DIR="$(git rev-parse --show-toplevel)"
DEV_IMG="fermax-blue-dev"

step() { echo -e "\n${YELLOW}▶ $1${NC}"; }
pass() { echo -e "${GREEN}✓ $1${NC}"; }
fail() { echo -e "${RED}✗ $1${NC}"; exit 1; }

echo -e "${YELLOW}══════════════════════════════════════════════${NC}"
echo -e "${YELLOW}  Pre-push CI replica (Dockerfile.dev)        ${NC}"
echo -e "${YELLOW}══════════════════════════════════════════════${NC}"

for PY in 3.13 3.14; do
    TAG="${DEV_IMG}:py${PY}"
    step "Building image Python ${PY}"
    docker build -q -t "$TAG" --build-arg PYTHON_VERSION="$PY" -f "$PROJECT_DIR/Dockerfile.dev" "$PROJECT_DIR" > /dev/null \
        && pass "Image py${PY}" || fail "Image build py${PY}"

    RUN="docker run --rm -v $PROJECT_DIR:/app -w /app $TAG"

    if [ "$PY" = "3.13" ]; then
        step "Lint (py${PY})"
        $RUN ruff check custom_components/ tests/ scripts/ \
            && pass "Lint" || fail "Lint"

        step "Format (py${PY})"
        $RUN ruff format --check custom_components/ tests/ scripts/ \
            && pass "Format" || fail "Format — run 'make format'"

        step "Type check (py${PY})"
        $RUN mypy custom_components/fermax_blue/ --ignore-missing-imports \
            && pass "Type check" || fail "Type check"
    fi

    step "Tests (py${PY})"
    $RUN pytest tests/ -q --tb=short \
        && pass "Tests py${PY}" || fail "Tests py${PY}"
done

echo -e "\n${GREEN}══════════════════════════════════════════════${NC}"
echo -e "${GREEN}  All checks passed — safe to push             ${NC}"
echo -e "${GREEN}══════════════════════════════════════════════${NC}"
