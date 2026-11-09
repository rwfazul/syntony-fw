PYTHON ?= python3
RUFF ?= ruff
COMPOSE = docker compose
.PHONY: test validate hygiene lint format format-check up down logs integration compose-check bundle
validate:
	$(PYTHON) -m runtime validate
hygiene:
	$(PYTHON) scripts/check_repository.py
lint:
	$(RUFF) check runtime scripts deployment tests
format:
	$(RUFF) format runtime scripts deployment tests
format-check:
	$(RUFF) format --check runtime scripts deployment tests
test:
	$(PYTHON) -m unittest discover -s tests -v
compose-check:
	$(COMPOSE) config --quiet
up:
	$(COMPOSE) up --build -d --wait --wait-timeout 180
down:
	$(COMPOSE) down
logs:
	$(COMPOSE) logs -f monitor_application_service composer_application_service cep coordinator executor
integration:
	$(PYTHON) scripts/run_local_integration.py
bundle:
	$(PYTHON) scripts/bundle.py --output dist/syntony-fw.tar.gz
