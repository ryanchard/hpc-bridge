from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


MODES = ("interactive", "batch")


@dataclass(frozen=True)
class Profile:
    mode: Literal["interactive", "batch"] = "batch"
    nodes_per_block: int = 1
    max_idletime_s: int = 600  # idle grace (s) before the block + UEP auto-release

    def __post_init__(self) -> None:
        # Literal is not enforced at runtime; reject unknown modes rather than silently
        # treating them as batch downstream.
        if self.mode not in MODES:
            raise ValueError(f"invalid profile mode {self.mode!r}: must be one of {MODES}")
        if self.max_idletime_s < 1:
            raise ValueError(f"max_idletime_s must be >= 1s, got {self.max_idletime_s}")
