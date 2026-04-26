# SMS Gateway v2

## Why

This project replaces the previous SMS-to-Telegram relay with a ModemManager-based
architecture built around durable delivery, explicit observability, and operational
survival during long periods of inactivity.

## Status

Sprint 1 of N: skeleton only, no application logic yet.

## Hardware Target

- Quectel EC25-EUX USB dongle.
- NAS at `192.168.50.2` running Ubuntu 24.04.
- ModemManager on the host.

## Quick Start

```bash
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
|       `-- config.py
`-- tests/
    |-- __init__.py
    |-- e2e/
    |   |-- __init__.py
    |   `-- test_placeholder.py
    |-- integration/
    |   |-- __init__.py
    |   `-- test_placeholder.py
    |-- test_config.py
    `-- test_smoke.py
```

## Roadmap

1. [x] Skeleton and CI.
2. [ ] ModemManager D-Bus client with mocked tests.
3. [ ] Durable file queue and SQLite deduplication.
4. [ ] Telegram delivery with exponential backoff and Prometheus metrics.
5. [ ] Dockerfile and deploy compose.
6. [ ] Active modem healthcheck and dead-man heartbeat.
7. [ ] Production cutover from AI-Server.
