from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any


def slotted_dataclass(*args: Any, **kwargs: Any):
    if sys.version_info >= (3, 10):
        kwargs.setdefault("slots", True)
    return dataclass(*args, **kwargs)
