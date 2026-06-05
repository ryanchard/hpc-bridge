from __future__ import annotations


def estimate_spend(elapsed_s: float, nodes: int, charge_factor: float) -> float:
    """Estimated node-hours for a warm block held `elapsed_s` seconds.

    `charge_factor` is the facility's QOS multiplier (0.0 for local dev = free).
    """
    return (elapsed_s / 3600.0) * nodes * charge_factor


def cap_output(text: str, max_chars: int) -> str:
    """Bound a stdout/stderr snippet (Globus has a hard 10 MB result limit)."""
    if len(text) <= max_chars:
        return text
    dropped = len(text) - max_chars
    return text[:max_chars] + (
        f"\n…[truncated {dropped} chars; redirect verbose output to a file"
        " and read it back in bounded chunks]"
    )
