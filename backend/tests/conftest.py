import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def isolate_refunds():
    from data.refunds import REFUND_REQUEST

    original = REFUND_REQUEST.copy()
    REFUND_REQUEST.clear()
    yield
    REFUND_REQUEST.clear()
    REFUND_REQUEST.update(original)
