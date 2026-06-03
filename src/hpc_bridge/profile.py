from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Profile:
    mode: Literal["interactive", "batch"] = "batch"
    nodes_per_block: int = 1
    max_idletime_s: int = 30
    account: str | None = None
    queue: str | None = None
