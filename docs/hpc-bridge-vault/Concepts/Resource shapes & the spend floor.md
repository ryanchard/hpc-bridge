# Resource shapes & the spend floor

> [!abstract] In one line
> One templatable endpoint serves two **shapes** — `login` (a free `LocalProvider` on the login node) and `slurm` (a billed `SlurmProvider` block) — and a billed block **will not start** until spend is explicitly confirmed.

## Shapes

A *shape* is a named bag of template vars (`user_endpoint_config`) that renders the [[MEP & templated endpoints|UEP template]]. `shape_config()` ([[shapes]]) defines them:

| Shape | Provider | Cost | Used for |
|---|---|---|---|
| `login` | `LocalProvider` (login node) | free, no allocation | discovery, light work, the no-SSH probe |
| `slurm` | `SlurmProvider` (compute block) | billed, idle-released | real compute |

Each shape has its own [[server|`ShapeRuntime`]] — its own Executor, canary, and spend clock — so they warm and bill independently. `run_shell(command, shape=...)` / `ensure_endpoint_up(shape=...)` pick the target.

> [!warning] `is_slurm` is a boolean, not a string
> `shape_config` sets `is_slurm: True/False`; the template branches on that bool. It must *not* compare a string like `provider_type == "SlurmProvider"`, because the manager's `_sanitize_user_json` JSON-quotes every string and the comparison silently fails — dropping the provider block ([#5](https://github.com/ryanchard/hpc-bridge/issues/5)). See [[MEP & templated endpoints]].

## The spend floor

A billed `slurm` shape returns `needs_confirmation` and **starts nothing** until `ensure_endpoint_up(confirm_spend=True)` — a deterministic gate enforced in `_provision` ([[server]], `server.py:297`). It covers `run_shell` too (its canary would otherwise kick a block). The `login` shape is free and exempt. The chosen `partition` is threaded in per task via `_apply_partition` (`server.py:332`) and persists for the session.

This is the front-end half of [[Cost control]] — the idle-release net catches the *back* end.

## See also
[[shapes]] · [[MEP & templated endpoints]] · [[Cost control]] · [[server]] · [[cost]]
