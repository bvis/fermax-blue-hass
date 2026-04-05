DOCKER_IMG = python:3.12-slim
DOCKER_RUN = docker run --rm -v $(PWD):/app -w /app $(DOCKER_IMG)
DOCKER_DEPS = pip install -q ruff mypy pytest pytest-asyncio pytest-cov httpx firebase-messaging homeassistant 2>/dev/null

.PHONY: lint format format-check typecheck test check cli

lint:
	$(DOCKER_RUN) sh -c "pip install -q ruff 2>/dev/null && ruff check custom_components/ tests/"

format:
	$(DOCKER_RUN) sh -c "pip install -q ruff 2>/dev/null && ruff format custom_components/ tests/"

format-check:
	$(DOCKER_RUN) sh -c "pip install -q ruff 2>/dev/null && ruff format --check custom_components/ tests/"

typecheck:
	$(DOCKER_RUN) sh -c "$(DOCKER_DEPS) && mypy custom_components/fermax_blue/ --ignore-missing-imports"

test:
	$(DOCKER_RUN) sh -c "$(DOCKER_DEPS) && pytest tests/ -v --cov=custom_components/fermax_blue --cov-report=term-missing --tb=short"

check: lint format-check typecheck test
	@echo "All checks passed"

cli:
	docker run --rm -it -v $(PWD):/app -w /app -e FERMAX_USER -e FERMAX_PASS python:3.12-slim sh -c "pip install -q httpx 2>/dev/null && python scripts/cli.py"
