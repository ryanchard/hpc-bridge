# connect

> [!abstract] Role
> The connect flow the agent drives: the Globus login gate FIRST, the resolution ladder (explicit details this session → the registry → the local BYO cache → the SSH probe that proposes a config), the switch of shapes/state to the bound facility, the identity-blind MEP attach or the SSH bootstrap, the allocation listing over the free login shape. Also `_commit_proven_facility` (a BYO config is cached only once the login-shape canary answered) and `_drop_dead_pin` (an unreachable login-node pin is forgotten).

Split step 10 (2026-09-03) — the last. The login-shape channel is injected (`run_login`) as in [[scheduler_ops]]; `server._connect_facility` is a thin wrapper supplying `server._login_runner(app)`, so the many tests that call it and patch `server._run_shell` keep working. Tests that patch the probe patch `connect.discover_facility_details` / `connect._propose_or_ask`. Concepts: [[Happy path]], [[Discovery today]], [[Facility catalog]], [[In-terminal Globus login]].

## See also
[[server]] · [[binding]] · [[warmth]] · [[login_gate]] · [[notices]]
