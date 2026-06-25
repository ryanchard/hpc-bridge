from hpc_bridge.catalog.parsers import PARSERS, AllocationOption, parse_mybalance

# Real Anvil `mybalance` output (captured live, 2026-06).
MYBALANCE = """
Allocation     Type    SU Limit    SU Usage   SU Usage  SU Balance
Account                           (account)     (user)
=============  ====  ==========  ========== ==========  ==========
cis250223       CPU     10001.0      1014.9      214.4      8986.1
cis250223-gpu   GPU      1000.0         0.0        0.0      1000.0
"""


def test_parse_mybalance_extracts_accounts_and_balances():
    allocs = parse_mybalance(MYBALANCE)
    assert [a.account for a in allocs] == ["cis250223", "cis250223-gpu"]
    a0, a1 = allocs
    assert a0.type == "CPU" and a0.balance == 8986.1 and a0.units == "SU"
    assert a1.type == "GPU" and a1.balance == 1000.0
    assert all(isinstance(a, AllocationOption) for a in allocs)


def test_parse_mybalance_ignores_header_before_the_rule():
    # only rows AFTER the `====` rule are data — the two header lines must not parse.
    assert len(parse_mybalance(MYBALANCE)) == 2


def test_parse_mybalance_empty_and_garbage():
    assert parse_mybalance("") == []
    assert parse_mybalance("no rule here\njust text") == []  # nothing past a (missing) rule


def test_parsers_registry_keys_match_the_schema():
    # the registry key is what CatalogEntry.allocation.parser names.
    assert PARSERS["mybalance"] is parse_mybalance
