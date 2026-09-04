# scheduler_ops

> [!abstract] Role
> The scheduler-facing shell one-liners that ride the FREE login shape: cancel this endpoint's blocks (`_release_cmd` → `scancel`/`qdel` scoped by the `uep.<eid>` StdOut marker; `_release_blocks_over_login` retries a cold channel `HPC_BRIDGE_RELEASE_ATTEMPTS` × backoff and reports an HONEST unconfirmed result), and read the pilot job's state (`_pilot_status_cmd`, `_summarize_pilot`: starting / queued / held / rejected, with the `PROVISION_GRACE_S` window so a not-yet-visible pilot isn't cried as rejected — #32; `_augment_provisioning_notice` appends the verdict).

Split step 6 (2026-09-03). The first step that needed **injection**: these ops run commands through `run_shell(shape="login")`, which lives in [[server]]; importing it back would be a cycle, so the login-shape channel is a parameter (`run_login`) that `server._login_runner(app)` builds at call time. Tests that patch `server._run_shell` therefore still reach every op; tests that patch the release call patch `scheduler_ops._release_blocks_over_login`. The harness keeps a DELIBERATE copy of `_release_cmd` (see [[Review 2026-09-03 — code quality]] D11) pinned by a test to the same marker scoping.

## See also
[[server]] · [[Cost control]] · [[Warmth, the canary & cold-start]] · [[config]]
