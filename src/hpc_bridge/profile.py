from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


MODES = ("interactive", "batch")


@dataclass(frozen=True)
class Profile:
    mode: Literal["interactive", "batch"] = "batch"
    nodes_per_block: int = 1
    max_idletime_s: int = 30
    account: str | None = None
    queue: str | None = None

    def __post_init__(self) -> None:
        # Literal is not enforced at runtime; reject unknown modes rather than silently
        # treating them as batch downstream.
        if self.mode not in MODES:
            raise ValueError(f"invalid profile mode {self.mode!r}: must be one of {MODES}")
