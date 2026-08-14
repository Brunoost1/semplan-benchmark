UV ?= $(shell command -v uv 2>/dev/null || printf 'python3 -m uv')
RUN ?= $(UV) run --python 3.12 --extra dev
DOCKER_COMPOSE ?= docker compose

.PHONY: lint typecheck test-unit test-contract test-property test-golden test-integration schema-check catalog-check validate-data validate-benchmark validate-release-benchmark validate-language-quality validate-free db-up db-down db-migrate db-downgrade db-load-small validate-benchmark-db e2e-free secret-scan coverage build

lint:
	$(RUN) ruff format --check .
	$(RUN) ruff check .

typecheck:
	$(RUN) mypy src scripts

test-unit:
	$(RUN) pytest tests/unit

test-contract:
	$(RUN) pytest tests/contract

test-property:
	$(RUN) pytest tests/property

test-golden:
	$(RUN) pytest tests/golden

test-integration:
	$(RUN) pytest tests/integration

schema-check:
	$(RUN) python scripts/check_schemas.py

catalog-check:
	$(RUN) python -m semplan.cli.main validate-config configs/base.yaml
	$(RUN) python -m semplan.cli.main validate-catalog catalog

validate-data:
	rm -rf artifacts/validation/f2_small_a artifacts/validation/f2_small_b
	$(RUN) python -m semplan.cli.main generate-data artifacts/validation/f2_small_a --profile small --seed 20260806 --overwrite
	$(RUN) python -m semplan.cli.main generate-data artifacts/validation/f2_small_b --profile small --seed 20260806 --overwrite
	$(RUN) python -m semplan.cli.main compare-data artifacts/validation/f2_small_a artifacts/validation/f2_small_b

validate-benchmark:
	$(RUN) python -m semplan.cli.main validate-benchmark data/benchmark/f3_smoke

validate-release-benchmark:
	$(RUN) python -m semplan.cli.main validate-benchmark data/benchmark/f7_release_scale --allow-hidden --require-approved
	$(RUN) python -m semplan.cli.main validate-release-benchmark data/benchmark/f7_release_scale
	$(RUN) python -m semplan.cli.main validate-language-quality data/benchmark/f7_release_scale

validate-language-quality:
	$(RUN) python -m semplan.cli.main validate-language-quality data/benchmark/f7_release_scale

validate-free: lint typecheck test-unit test-contract test-property test-golden schema-check catalog-check validate-data validate-benchmark secret-scan build

db-up:
	$(DOCKER_COMPOSE) up -d semplan-postgres
	@for attempt in $$(seq 1 30); do \
		if $(DOCKER_COMPOSE) exec -T semplan-postgres pg_isready -U semplan_admin -d semplan >/dev/null 2>&1; then \
			echo "postgres ready"; \
			exit 0; \
		fi; \
		sleep 1; \
	done; \
	echo "postgres did not become ready" >&2; \
	exit 1

db-migrate:
	$(RUN) alembic upgrade head

db-downgrade:
	$(RUN) alembic downgrade base

db-load-small:
	$(RUN) python -m semplan.cli.main load-data artifacts/validation/f2_small_a

validate-benchmark-db:
	$(RUN) python -m semplan.cli.main validate-benchmark data/benchmark/f3_smoke --execute-gold

e2e-free: db-up db-migrate
	rm -rf artifacts/validation/f4_small artifacts/runs/free_e2e
	$(RUN) python -m semplan.cli.main generate-data artifacts/validation/f4_small --profile small --seed 20260806 --overwrite
	$(RUN) python -m semplan.cli.main load-data artifacts/validation/f4_small
	$(RUN) python -m semplan.cli.main e2e-free --benchmark-dir data/benchmark/f3_smoke --output-dir artifacts/runs/free_e2e

db-down:
	$(DOCKER_COMPOSE) down --remove-orphans

secret-scan:
	$(RUN) python scripts/check_secrets.py

coverage:
	$(RUN) pytest --cov=semplan --cov-report=term-missing --cov-fail-under=90 tests/unit tests/contract tests/golden

build:
	$(RUN) python -m build
