# login_gate

> [!abstract] Role
> The Globus login gate as the tools see it: `_start_login_and_wait` (arm, wait up to `HPC_BRIDGE_LOGIN_WAIT_S` in browser mode and continue in the same call; re-arm in paste mode if the browser attempt dies), and the tool bodies `_authenticate` / `_complete_login`, which also forget the sticky no-account verdicts because a new login may be a different identity.

Split step 9 (2026-09-03). The flow machinery stays in [[login]] (kept free of server/runtime imports); this module is the seam between the flow and the runtime. Nothing here is monkeypatched; `server` re-exports the three names and its tools call through the module.

## See also
[[login]] · [[login_flow_manager]] · [[server]] · [[In-terminal Globus login]]
