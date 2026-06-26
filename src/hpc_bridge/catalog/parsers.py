# src/hpc_bridge/catalog/parsers.py
"""Deterministic, plugin-side parsers for allocation-listing commands.

`CatalogEntry.allocation.parser` names one of these. The command's stdout (from a
ShellFunction on the login shape) is parsed here, in code — never handed to the model.
A new machine with a new output format adds one function + a registry entry.
"""
from __future__ import annotations

from typing import Callable

from ..models import AllocationOption

__all__ = ["AllocationOption", "PARSERS", "parse_mybalance"]


def parse_mybalance(stdout: str) -> list[AllocationOption]:
    """Purdue/Anvil `mybalance`: whitespace columns under a `===` rule.

        Allocation     Type    SU Limit    SU Usage   SU Usage  SU Balance
        Account                           (account)     (user)
        =============  ====  ==========  ========== ==========  ==========
        cis250223       CPU     10001.0      1014.9      214.4      8986.1

    Account = first column, Type = second, balance = last (SU Balance) column.
    """
    out: list[AllocationOption] = []
    past_rule = False
    for line in stdout.splitlines():
        s = line.strip()
        if not s:
            continue
        if set(s) <= set("= "):  # the `====` rule that ends the (multi-line) header
            past_rule = True
            continue
        if not past_rule:
            continue
        parts = s.split()
        if len(parts) < 3:
            continue
        try:
            balance = float(parts[-1])
        except ValueError:
            continue
        out.append(AllocationOption(account=parts[0], type=parts[1], balance=balance, units="SU"))
    return out


# parser name (CatalogEntry.allocation.parser) -> function. sbank/iris are reserved until
# ALCF/NERSC machines are added (generalize at the 4th machine — deterministically).
PARSERS: dict[str, Callable[[str], list[AllocationOption]]] = {
    "mybalance": parse_mybalance,
}
