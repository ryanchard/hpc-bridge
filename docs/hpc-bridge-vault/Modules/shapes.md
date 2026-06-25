# shapes.py

> [!abstract] Role
> Maps a resource **shape** name to the `user_endpoint_config` base — the template vars that render the [[MEP & templated endpoints|UEP template]].

## What it does

- **`SHAPES = ("login", "slurm")`** (`shapes.py:13`).
- **`shape_config(shape, **overrides)`** (`:16`) → the base dict: `login` → `{provider_type: LocalProvider, max_workers_per_node: 1, is_slurm: False}`; `slurm` → `{provider_type: SlurmProvider, is_slurm: True}`. Caller overrides (non-`None`) merge in.

> [!warning] `is_slurm` is a boolean discriminator, on purpose
> The endpoint manager runs `user_opts` through `_sanitize_user_json`, which `json.dumps`'s every *string* (so `"SlurmProvider"` → `'"SlurmProvider"'`). A template that compared the *string* `provider_type` would silently fail and drop the provider block ([#5](https://github.com/ryanchard/hpc-bridge/issues/5)). Bools pass through untouched, so the template branches on `is_slurm`. See [[Resource shapes & the spend floor]].

## See also
[[Resource shapes & the spend floor]] · [[MEP & templated endpoints]] · [[server]]
