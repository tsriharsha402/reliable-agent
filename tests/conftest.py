import datetime as dt
from pathlib import Path

import pytest

from reliable_agent.helpdesk import HandbookIndex, Helpdesk

ROOT = Path(__file__).resolve().parent.parent
TODAY = dt.date(2026, 10, 1)  # a Thursday


@pytest.fixture(scope="session")
def handbook() -> HandbookIndex:
    return HandbookIndex(ROOT / "data" / "handbook")


@pytest.fixture
def helpdesk(handbook) -> Helpdesk:
    return Helpdesk(today=TODAY, current_user="E001", handbook=handbook)
