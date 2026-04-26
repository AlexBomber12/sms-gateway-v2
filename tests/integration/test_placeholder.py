import pytest


@pytest.mark.integration
def test_integration_marker_selects_correctly() -> None:
    assert True
