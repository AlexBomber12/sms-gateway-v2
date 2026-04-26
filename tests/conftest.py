from __future__ import annotations

import ast
from collections.abc import Iterator

import pytest

from sms_gateway_v2.config import get_settings

RELAXED_COVERAGE_MARKERS = frozenset({"integration", "e2e"})


@pytest.fixture(autouse=True)
def clean_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def pytest_configure(config: pytest.Config) -> None:
    if _selects_relaxed_coverage_marker(config.option.markexpr):
        config.option.cov_fail_under = 0


def pytest_sessionstart(session: pytest.Session) -> None:
    if not _selects_relaxed_coverage_marker(session.config.option.markexpr):
        return

    cov_plugin = session.config.pluginmanager.getplugin("_cov")
    if cov_plugin is not None:
        cov_plugin.options.cov_fail_under = 0


def _selects_relaxed_coverage_marker(markexpr: str) -> bool:
    normalized = markexpr.strip()
    if not normalized:
        return False

    try:
        expression = ast.parse(normalized, mode="eval")
    except SyntaxError:
        return normalized in RELAXED_COVERAGE_MARKERS

    return _references_positive_relaxed_coverage_marker(expression.body)


def _references_positive_relaxed_coverage_marker(node: ast.expr, *, negated: bool = False) -> bool:
    if isinstance(node, ast.Name):
        return not negated and node.id in RELAXED_COVERAGE_MARKERS
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return _references_positive_relaxed_coverage_marker(node.operand, negated=not negated)
    if isinstance(node, ast.BoolOp):
        return any(
            _references_positive_relaxed_coverage_marker(value, negated=negated)
            for value in node.values
        )
    return False
