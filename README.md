# SMS Gateway v2

## Why

This project replaces the previous SMS-to-Telegram relay with a ModemManager-based
architecture built around durable delivery, explicit observability, and operational
survival during long periods of inactivity.

## Status

Sprint 5b of 8: SmsRelay orchestrator.

## Hardware Target

- Quectel EC25-EUX USB dongle.
- NAS at `192.168.50.2` running Ubuntu 24.04.
- ModemManager on the host.

## Quick Start

```bash
cp .env.example .env  # optional, defaults work without it
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python -m sms_gateway_v2
```

```bash
curl http://127.0.0.1:8091/healthz
```

## Development

```bash
ruff check .
ruff format .
mypy src
pytest
```

## Project Layout

```text
.
|-- .env.example
|-- .github/
|   `-- workflows/
|       `-- ci.yml
|-- .gitignore
|-- AGENTS.md
|-- CLAUDE.md
|-- Dockerfile
|-- LICENSE
|-- README.md
|-- deploy/
|   `-- docker-compose.yml
|-- pyproject.toml
|-- src/
|   `-- sms_gateway_v2/
|       |-- __init__.py
|       |-- __main__.py
|       |-- app.py
|       |-- config.py
|       |-- metrics/
|       |-- modem/
|       |-- queue/
|       |-- relay/
|       `-- telegram/
`-- tests/
    |-- __init__.py
    |-- e2e/
    |   |-- __init__.py
    |   `-- test_placeholder.py
    |-- integration/
    |   |-- __init__.py
    |   `-- test_placeholder.py
    |-- test_config.py
    |-- test_metrics/
    |-- test_modem/
    |-- test_queue/
    |-- test_smoke.py
    `-- test_telegram/
```

## Roadmap

1. [x] Skeleton and CI.
2. [x] ModemManager D-Bus client with mocked tests.
3. [x] Durable file queue and SQLite deduplication.
4. [x] Telegram client and Prometheus metrics.
5. [ ] Delivery worker (5a done, 5b in progress, 5c pending FastAPI lifespan).
6. [ ] Dockerfile and deploy compose.
7. [ ] Active modem healthcheck and dead-man heartbeat.
8. [ ] Production cutover from AI-Server.
