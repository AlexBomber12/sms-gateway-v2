# AGENTS.md

## Mission

Build a reliable, observable SMS-to-Telegram relay that survives long periods of
inactivity. The service talks to ModemManager over D-Bus, uses a durable file
queue with exponential retry, exposes Prometheus metrics, and sends a dead-man
heartbeat. It replaces the previous AI-Server relay with a greenfield
ModemManager-based implementation.

## Workflow

- Pull requests only into `main`; no direct commits.
- Use Conventional Commits in PR titles and commit messages.
- Allowed commit prefixes: `feat`, `fix`, `chore`, `refactor`, `docs`, `test`,
  `ci`, and `perf`.
- Branch names use the same prefixes plus a short kebab-case description.
- CI must be green before merge.
- Approving reviews are not required because this is a solo repository.

## Code Style

- Ruff is the only formatter and linter.
- Mypy runs in strict mode.
- Use type hints on all public functions and methods.
- Keep line length at 100 characters.
- Do not use `print` in library code; use `structlog`.

## Testing

- Use `pytest` with `asyncio_mode = auto`.
- Maintain three test layers: unit, integration, and e2e.
- Mark integration tests with `integration`.
- Mark e2e tests with `e2e`.
- Integration and e2e tests are skipped by default and never block CI.
- Unit test coverage gate is 100% line coverage.

## Architecture

- Every D-Bus call goes through a dedicated wrapper module under
  `src/sms_gateway_v2/modem/`.
- All queue operations go through `src/sms_gateway_v2/queue/`.
- All Telegram calls go through `src/sms_gateway_v2/telegram/`.
- Keep FastAPI routes thin and move logic into service modules.
- Use Pydantic v2 for all data shapes.
- Do not use ad-hoc subprocess calls.

## Security

- Do not commit secrets.
- Load all configuration via `pydantic-settings` from `.env`.
- Containers run as an unprivileged user.
- Deduplicate messages via SQLite.
- Write an audit log entry for every Telegram delivery attempt.

## Hardware Target

- Quectel EC25-EUX USB dongle.
- Russian MTS SIM roaming on Italian networks.
- NAS host at `192.168.50.2` running Ubuntu 24.04.
- ModemManager runs on the host and is accessed via system D-Bus.
