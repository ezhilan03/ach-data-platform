
# ACH data platform. `make` on its own prints this list.
#
# Two modes of running the same code:
#   * local  -- `make pipeline` runs the steps directly, no containers. Fast
#               loop for changing models and the detector.
#   * stack  -- `make up` runs the whole thing under Airflow with Prometheus
#               and Grafana. What the screenshots come from.

SHELL := /bin/bash
.DEFAULT_GOAL := help
UV ?= uv
RUN := $(UV) run
COMPOSE ?= docker compose

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

# --- environment ------------------------------------------------------------

.PHONY: install
install: ## Create the venv and install everything (incl. dbt)
	$(UV) sync --extra dbt --extra dev

.PHONY: lock
lock: ## Refresh uv.lock (commit the result)
	$(UV) lock

# --- quality ----------------------------------------------------------------

.PHONY: lint
lint: ## ruff check + format check
	$(RUN) ruff check .
	$(RUN) ruff format --check .

.PHONY: format
format: ## Apply ruff formatting and autofixes
	$(RUN) ruff check --fix .
	$(RUN) ruff format .

.PHONY: test
test: ## Unit tests (no warehouse needed)
	$(RUN) pytest -v

.PHONY: parse-dags
parse-dags: ## Parse the DAG files inside the built image (needs `make build`)
	docker run --rm \
		-v "$(PWD)/airflow/dags:/opt/airflow/dags:ro" \
		-v "$(PWD)/scripts:/opt/scripts:ro" \
		ach-data-platform:local python /opt/scripts/parse_dags.py /opt/airflow/dags

# --- pipeline (local, no containers) ----------------------------------------

.PHONY: pipeline
pipeline: backfill dbt detect triage eval ## Full local run: land -> model -> detect -> triage -> score

.PHONY: backfill
backfill: ## Generate and land the full synthetic history as Parquet
	$(RUN) python -m ach_platform backfill

.PHONY: dbt
dbt: ## dbt build (models + tests) against the DuckDB warehouse
	$(RUN) python -m ach_platform dbt build

.PHONY: dbt-docs
dbt-docs: ## Generate and serve the dbt docs site (lineage graph)
	$(RUN) dbt docs generate --project-dir dbt/ach_dbt --profiles-dir dbt/ach_dbt
	$(RUN) dbt docs serve --project-dir dbt/ach_dbt --profiles-dir dbt/ach_dbt

.PHONY: detect
detect: ## Run the anomaly detector over the marts
	$(RUN) python -m ach_platform detect --no-push

.PHONY: triage
triage: ## Triage detected clusters (rules + LLM)
	$(RUN) python -m ach_platform triage --no-annotate

.PHONY: triage-rules
triage-rules: ## Triage with the rules baseline only (no model needed)
	$(RUN) python -m ach_platform triage --no-llm --no-annotate

.PHONY: eval
eval: ## Score detection and triage against the labeled incidents
	$(RUN) python -m ach_platform eval

.PHONY: check
check: ## Enforce the quality floors in eval/thresholds.json
	$(RUN) python scripts/check_eval.py

.PHONY: crosscheck
crosscheck: ## Verify the dbt marts against the pandas reference implementation
	$(RUN) python scripts/crosscheck_marts.py

.PHONY: verify
verify: lint test crosscheck check ## Everything CI enforces, locally

# --- stack (containers) -----------------------------------------------------

.PHONY: build
build: ## Build the Airflow + dbt image
	$(COMPOSE) build

.PHONY: up
up: ## Start Airflow, Prometheus, Grafana (http://localhost:8080 and :3000)
	@mkdir -p airflow/logs airflow/config data/{lake,warehouse,ground_truth,runs}
	AIRFLOW_UID=$$(id -u) $(COMPOSE) up -d
	@echo
	@echo "Airflow    http://localhost:8080  (user: airflow -- run 'make airflow-password')"
	@echo "Grafana    http://localhost:3000  (admin / admin)"
	@echo "Prometheus http://localhost:9090"
	@echo
	@echo "Then unpause ach_ingest in the Airflow UI; catchup replays the history."

.PHONY: down
down: ## Stop the stack (keeps volumes)
	$(COMPOSE) down

.PHONY: nuke
nuke: ## Stop the stack and delete its volumes
	$(COMPOSE) down -v

.PHONY: logs
logs: ## Tail the scheduler logs
	$(COMPOSE) logs -f airflow-scheduler

.PHONY: airflow-password
airflow-password: ## Print the generated Airflow UI password
	@cat airflow/config/simple_auth_manager_passwords.json.generated 2>/dev/null \
		|| echo "not generated yet -- start the stack and wait for airflow-apiserver to come up"

# --- housekeeping -----------------------------------------------------------

.PHONY: clean
clean: ## Delete generated data, the warehouse and dbt artifacts
	rm -rf data/lake/* data/warehouse/* data/ground_truth/* data/runs/*
	rm -rf dbt/ach_dbt/target dbt/ach_dbt/logs dbt/ach_dbt/dbt_packages
	@echo "cleaned -- everything removed is reproducible from ACH_SEED"
