# CLAUDE.md

`AGENTS.md` is authoritative for repository mission, workflow, style, testing,
architecture, security, and hardware rules.

Claude-specific notes:

- Prefer editing existing modules over creating parallel ones.
- When uncertain about ModemManager D-Bus interfaces, consult `mmcli` output and
  write a small skipped integration test under `tests/integration/` rather than
  guessing.
- Prefer long-term sustainable solutions over short-term fixes.
