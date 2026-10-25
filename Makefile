# Shortcuts for the local stack. Add compose.gpu.yaml through COMPOSE_FILE in .env for an NVIDIA GPU.
MODEL ?= ollama/llama3.2:3b-instruct-q4_K_M
ANALYST ?= granite4.2:8b-q4_K_M
SNAPSHOT ?= demo/snapshot.sql
COMPOSE = docker compose --profile local
PSQL = $(COMPOSE) exec -T postgres psql -q -U costguard -d costguard

.PHONY: up down models warm demo test e2e gate snapshot restore logs

up:        ## Build and start app, runner, PostgreSQL, Bifrost and Ollama
	$(COMPOSE) up -d --build --wait

down:      ## Stop the stack (data volumes are kept)
	$(COMPOSE) down

models:    ## Pull the workload and analyst models and register them in Bifrost
	$(COMPOSE) exec ollama ollama pull $(MODEL:ollama/%=%)
	$(COMPOSE) exec ollama ollama pull $(ANALYST)
	$(COMPOSE) exec app python -m costguard.bootstrap_gateway ollama

warm:      ## Load the workload model into memory so the first live run starts fast
	curl -sf -m 300 localhost:11435/api/generate -d '{"model": "$(MODEL:ollama/%=%)", "prompt": "ok", "stream": false}' > /dev/null && echo "$(MODEL) loaded"

demo:      ## Run baseline and candidate on MODEL, then investigate with the analyst set on Connections
	$(COMPOSE) exec app python -m costguard demo --model $(MODEL) --keep-analyst --investigate

test:      ## Unit tests in the app image
	$(COMPOSE) run --rm --no-deps -T app python -m unittest discover

e2e:       ## Mock end-to-end workflow (starts the demo profile)
	COSTGUARD_DEMO=1 docker compose --profile demo up -d --build --wait
	docker compose --profile demo exec -T app python -m tests.e2e

gate:      ## CI cost gate on the recorded runs in examples/ci
	bash examples/ci/gate.sh

snapshot:  ## Save the database as the demo fallback
	$(COMPOSE) exec -T postgres pg_dump -U costguard -d costguard --clean --if-exists --no-owner > $(SNAPSHOT)

restore:   ## Replace the database with the saved demo fallback
	$(PSQL) -v ON_ERROR_STOP=1 < $(SNAPSHOT)

logs:
	$(COMPOSE) logs -f app runner
