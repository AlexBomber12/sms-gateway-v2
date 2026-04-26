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

    marker_sets = (
        frozenset({"integration"}),
        frozenset({"e2e"}),
        RELAXED_COVERAGE_MARKERS,
    )
    return any(_evaluate_marker_expression(expression.body, markers) for markers in marker_sets)


def _evaluate_marker_expression(node: ast.expr, selected_markers: frozenset[str]) -> bool:
    if isinstance(node, ast.Name):
        return node.id in selected_markers
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return not _evaluate_marker_expression(node.operand, selected_markers)
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.And):
        return all(_evaluate_marker_expression(value, selected_markers) for value in node.values)
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
        return any(_evaluate_marker_expression(value, selected_markers) for value in node.values)
    return False
