# context

> [!abstract] Role
> The server's runtime state as pure data: `AppCtx` (one per server, shared by every tool call), `ShapeRuntime` (per resource shape: Executor, warmth/canary, spend clock, the sticky no-account verdict), `TaskHandle` (a command still running past the sync-wait), and `DEFAULT_SHAPE`. No behaviour lives here.

Split step 1 of the [[Review 2026-09-03 — code quality|code-quality review]]'s plan (2026-09-03): a leaf module so the modules that follow (config, notices, warmth, tasks, …) can import the state without importing `server`. `server` re-exports the four names, so `from hpc_bridge.server import AppCtx` — every test and tool — keeps working. Field-level rationale comments moved with the fields.

## See also
[[server]] · [[shapes]] · [[lifecycle]] · [[runner]]
