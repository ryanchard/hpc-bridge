# Endpoint reuse and MEP integration

> [!abstract] In one line
> The zero-SSH ladder: first **surface the reuse hpc-bridge already does silently** for endpoints it stood up, then **consume facility-run multi-user MEPs** so the facility's identity mapping replaces our SSH bootstrap outright — shrinking SSH from "every cold start" toward "never." **Phase 1 (reuse our own) ships first; Phase 2 (facility MEPs) is captured here to revisit once Phase 1 lands.**

## The reuse ladder

Three ways to reach a warm compute channel, best-first — each removes more SSH:

| Tier | Mechanism | SSH cost | Status |
|---|---|---|---|
| 1. **Facility MEP** | submit a UEP config to a facility's multi-user (identity-mapped) manager UUID | **none, ever** | Phase 2 (this note) |
| 2. **Our online endpoint** | reattach to an already-running `hpc-bridge-<facility>` by name | none after the first bootstrap | Phase 1 — *detection built, signal not surfaced* |
| 3. **SSH bootstrap** | SSH in once, start our own personal manager | one bootstrap (~2 auths) | built — the current default |

## Two senses of "MEP" — don't conflate them

[[MEP & templated endpoints]]: hpc-bridge's endpoint *is already* a Globus v4 MEP — but in **personal / single-user mode** (`configure --multi-user false`, `endpoint.py:58`), where "multi-user" names only the **manager + templated-UEP** architecture, not identity mapping. We own that manager; it serves one user.

A **facility MEP** (Phase 2) is the *other* sense: a manager the **facility** runs in **true multi-user mode with identity mapping** — one daemon serving many users, forking an identity-mapped UEP per authenticated Globus identity. **That identity mapping is precisely what replaces our SSH bootstrap:** SSH exists today only to authenticate-as-the-user and start their personal manager; a facility MEP already runs the manager and maps our Globus identity to a local account over AMQP, so no SSH is needed. Tier 1 is not "reuse our endpoint" — it's "borrow the facility's."

## Phase 1 — reuse hpc-bridge's own endpoints (do first)

**The detection already exists and works cross-session.** `bootstrap()` (`facility/remote.py:544`) asks Globus, *before any SSH*, whether we already own an online endpoint by the stable name `hpc-bridge-<facility>`:

```python
reused = await self.find_online_endpoint(self.profile.endpoint_name)   # remote.py:559 / :643
if reused is not None:
    return EndpointHandle(endpoint_id=reused, ...)                      # zero SSH, over AMQP
```

Because the name is stable and the manager persists on the cluster, a **fresh server process reconnects to a prior session's endpoint with zero SSH** — the SSH-once story is real *today* ([[Standing up the endpoint]]).

**The gap is the signal, not the logic.** The reuse fact is computed into `reused` and then dropped: `EndpointHandle` (`facility/base.py`) carries no reuse flag, so `_provision` (`server.py:463`) reads only `handle.endpoint_id`, and `_connect_facility` (`server.py:695`) / `ConnectFacilityResult` (`models.py:119`) never learn it happened. Neither the agent nor the user can tell "reattached, free" from "freshly bootstrapped."

**The work** — the [[Agentic testing - Plan B (runtime sandbox)|`endpoint_reuse`]] red scenario is the spec ([#20](https://github.com/ryanchard/hpc-bridge/issues/20)):
- Add `reused: bool` to `EndpointHandle`; set it at the two reuse branches (`bootstrap()` `:561`, `provision()`'s `running` case `:593`), false on a fresh `start`.
- Thread it up: `_provision` → `EndpointState` → `ConnectFacilityResult.reused`; the tool result signals it and the skill teaches the agent to say "reused (zero-SSH)" rather than imply a fresh bootstrap.
- Confirm the cross-session path end-to-end: restart the server, `connect_facility` the same facility, assert `reused=True` and no SSH.

> [!note] Known sub-gap (deferred, but named): stale-online reuse
> `find_online_endpoint` trusts the web service's "online" — a **stale registration** (registered-online but dead) is reused as-is and the canary can never warm it (`bootstrap()` docstring caveat). Re-bootstrap-on-stale is the natural Phase 1.5.

## Phase 2 — consume facility MEPs (revisit after Phase 1)

**The dispatch half is nearly built.** A Globus Compute run is already `Executor(endpoint_id, user_endpoint_config)` (`runner.py:74`) — literally what a facility MEP consumes — and the `HPC_BRIDGE_ENDPOINT_ID=<uuid>` BYO hatch (`endpoint.py:44`) already dispatches to a foreign UUID with **zero provisioning**. What's missing is everything around *choosing* and *configuring* that UUID as a first-class, discovered path.

**The information we'd need to gather** (the open question the user flagged — settle this before building):
1. **The MEP UUID, per facility** — a catalog field (`mep_endpoint_id`) or, better, *discovered*. A new discovery channel layered on [[Discovery channel model]] / [[Globus index discovery channel]] and the ACCESS survey ([#7](https://github.com/ryanchard/hpc-bridge/issues/7)): a facility that *publishes* a MEP is the cleanest provide-vs-discover case.
2. **The allowed `user_endpoint_config` + its schema** — the facility owns the `user_config_template.yaml.j2` and constrains it with a `user_config_schema`. We fill *its* variables (account / partition / walltime / nodes); we can't send an arbitrary template. Discover the accepted shape → propose → confirm (same discipline as raw-SSH discovery). This is the "template out a compute node" step, done against the facility's template instead of ours.
3. **Consent** — a Globus Auth consent for that endpoint is the irreducible "access" input the user provides.
4. **Identity mapping** — confirm the facility maps our Globus identity to the intended local account (the SSH replacement); surface a clear failure if it doesn't, rather than a silent wrong-user run.

**Where it touches the code:**
- `connect_facility` / `ensure_endpoint_up` gain a **MEP branch**: if the facility has a MEP UUID (+ consent), skip `bootstrap()`/SSH entirely, bind a runner to the UUID; "provision the slurm shape" becomes "submit the UEP config to the MEP."
- A catalog / [[Globus index discovery channel|`FacilityDetails`]] field for the MEP UUID (and, optionally, the discovered schema).

> [!warning] Stop/spend does NOT carry over — don't regress the honesty guarantee
> On our personal endpoint, `stop_endpoint` `scancel`s the block over the login shape ([[Cost control]], the `stop_is_honest` fix [#24](https://github.com/ryanchard/hpc-bridge/issues/24)). On a facility MEP **we don't own the manager**, so that scancel-over-login path doesn't exist — the MEP owns UEP lifecycle. Tier 1 needs its *own* honest stop (a UEP-scoped release, or honest reliance on the MEP's idle policy), and it must satisfy `stop_is_honest` too: an unconfirmed stop must never report `status="down"`.

**Feasibility to settle first:** which target facilities actually run a *targetable* multi-user MEP? NERSC runs a Globus Compute MEP; does our ACCESS target (Anvil) expose one, or is it SSH-bootstrap-only? Can we submit our own `user_endpoint_config`, or only select a named site preset? These answers size Phase 2.

## Guiding invariants (must hold across both phases)
- **Hot path stays token/AMQP — no new SSH channel** ([[Two-channel architecture]]). Reuse and MEP consumption *remove* SSH; neither adds a work channel.
- **hpc-bridge still only ever *creates* personal endpoints** — `--multi-user false` stays for anything we stand up ([[MEP & templated endpoints]]). Phase 2 *consumes* a facility MEP; it never makes hpc-bridge run one.
- **Discovery proposes; the user confirms/consents** — a discovered MEP UUID/schema is a session-local candidate, never auto-trusted ([[Discovery channel model]]).
- **Stop stays honest on every channel** ([#24](https://github.com/ryanchard/hpc-bridge/issues/24)).

## Deferred
Phase 2 implementation until Phase 1 ships; identity-mapping edge cases and stale-consent handling; MEP-side allocation/quota reporting; re-bootstrap-on-stale for Tier 2 (Phase 1.5).

## See also
[[MEP & templated endpoints]] · [[Discovery channel model]] · [[Globus index discovery channel]] · [[Standing up the endpoint]] · [[Cost control]] · [[Two-channel architecture]] · [[facility-remote]]
