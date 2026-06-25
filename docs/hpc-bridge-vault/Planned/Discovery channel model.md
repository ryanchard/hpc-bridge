# Discovery channel model

> [!warning] Planned · the conceptual frame
> The target model for *where* hpc-bridge learns what a facility is, and what the user must supply versus what Claude can discover. **Partly built** — the facility shape now comes from the [[Facility catalog|catalog]] (index/seed), not a hardcoded profile; the *fuller cascade* (login-probe + human fallback, ablation, trace) is what remains. The concrete build is [[Globus index discovery channel]]. Tracking: [#7](https://github.com/ryanchard/hpc-bridge/issues/7). Absorbs the former `docs/design/discovery-channels.md`.

## One abstraction — the human is terminal

A **discovery channel** is any provider that answers facts about a facility. The Globus index, a live login-node probe, the [[state|local client]], and a **Socratic dialogue with the user** are the *same* abstraction — they differ in two axes: **coverage** (which facts) and **cost** (auth/latency/intrusion — an anonymous index lookup ≪ an SSH round-trip ≪ interrupting the human).

Selection rule: **per-fact happiest-path** — for each fact, the *cheapest channel that correctly covers it* (correctly = meets its freshness need; a live fact like queue idle prefers the login node even though the index is "cheaper overall").

> [!note] The human is the terminal channel
> Total coverage at maximum cost — they can supply or find out anything about their own facility, and they are *always partially active* (the SSH key + login name come from them even on the happiest path). As automated channels degrade, their question set simply widens. So the floor is a **question**, never a hardcoded default — discovery degrades to discovery.

## The channels

| Channel | Covers (its strength) | Cost / auth | How it's called | Status |
|---|---|---|---|---|
| **Globus index** (ours) | the **static bootstrap shape**: `ssh_host`, `interface`, `env_setup`, `scheduler`, `auth_method`, scratch pattern, defaults, allocation command+parser | free · instant · **anonymous** | `globus_sdk.SearchClient` | live (Anvil entry) |
| **Login-node probe** | **live state**: partitions+idle, balance, `$SCRATCH`, account/QOS, interface *candidates*, gce version | one SSH bootstrap, then login-shape AMQP | `run_shell(shape="login")` | **built** ([#13](https://github.com/ryanchard/hpc-bridge/pull/13)) |
| [[state\|Local client]] | Globus identity, owned endpoint UUIDs, local SDK/dill/Python versions, platform | the Globus login already done | `globus_sdk` / `get_endpoints` | **built** ([#12](https://github.com/ryanchard/hpc-bridge/pull/12)) |
| **Human (Socratic)** | **everything** — secrets (key, login name) + the unpublished + the un-probeable | slow · effortful · always available | `AskUserQuestion` + dialogue | partial (gates built; full elicitation designed) |

> [!note] Deferred: external catalogs
> ACCESS-CI MCP / Operations API are a real future channel (hardware/scheduler/status), kept out for now (simplicity + observability) — every channel here is *ours*, *the target machine*, *local*, or *the human*. They slot in later as just another channel, ranked by happiest-path, with no restructuring.

## Provide vs. discover

Three tiers, named by *who answers*:

- **Tier 1 — irreducible user input** (the human channel, always active): the **SSH key** (a secret, never discoverable) and the **login name**. The **SSH host** lands here *only when the index is unavailable*.
- **Tier 2 — consequential choices** (gates: discovered options, the human picks): which facility, which partition, the spend confirmation — already built ([[Resource shapes & the spend floor]]).
- **Tier 3 — machine-discoverable** (no user input): everything the old `anvil_profile` hardcoded — now answered by the index (static), the login node (live), or the local client (identity). **The machine profile is ~90% a frozen discovery, not configuration; users provide credentials, not configuration.**

### The per-fact matrix

The load-bearing reference. "→ human" is the implicit terminal fallback everywhere; named only where it's the *primary* source.

| Fact | Happiest channel | Falls back to | Tier |
|---|---|---|---|
| facility selection | index (`search`/browse) | human names it | choice |
| **`ssh_host`** | **index** | **human** | T3 w/ index · T1 without |
| `ssh_key` | human (local file) | — | T1 (secret) |
| login name | human | — | T1 |
| `auth_method` | index | human / assume key-only | T3 → bootstrap |
| `scheduler` | index | login node (`which sbatch`) | T3 (static) |
| **`interface`** | **index** | login probe (`ip -o addr`) + **canary** | T3 (curated; validate) |
| **`env_setup`** | **index** | human / facility docs | T3 → bootstrap |
| `scratch_root` | login node (`echo $SCRATCH`) | index pattern | T3 (live) |
| account / allocation | login node (`sacctmgr`) | human | T3 + choice |
| partitions + **idle** | login node (`sinfo`) | index static caps | T3 (live) |
| walltime / QOS caps | login node (`sacctmgr`) | — | T3 (live) |
| **balance** | login node (named parser, e.g. `mybalance`) | human | T3 (live) |
| GPU / accelerators | login node (`sinfo`) | index `defaults` | T3 (live) |
| run shape (nodes/blocks) | defaults · user override ([#2](https://github.com/ryanchard/hpc-bridge/issues/2)) | — | choice/default |
| `amqp_port` / `endpoint_name` | safe default (443 / `hpc-bridge`) | index override | default |
| Globus identity · endpoint UUID | local (`get_endpoints`) | — | T3 (local) |
| gce version (skew) | login node (live) | — | T3 (live) |

Two facts — **`ssh_host` and `interface`** — are the reason the index earns its keep: both live in human/curated *prose* (facility guides, the Globus Compute [example configs](https://github.com/globus/globus-compute/blob/main/docs/endpoints/endpoint_examples.md)), not in any queryable catalog.

## Principles that make the cascade work

- **The schema *is* the question template.** Facts are defined once — the index's fields — and every channel answers the same set. So "the index is down" isn't ad-hoc Q&A: the agent elicits exactly `ssh_host`, `auth_method`, `env_setup`, … *because those are the declared facts*.
- **The Socratic fallback is *staged*.** The human's job in the fallback is to get us **onto the machine** (host, key, auth, account); once SSH'd, the login node becomes available and probes the rest. Losing the index loses the *cheap* discovery, not discovery itself — it re-layers to human-bootstrap → live-probe.
- **Elicit-then-validate.** The human is a *noisy* channel. The [[Warmth, the canary & cold-start|canary]] is the universal denoiser — "probe-then-validate" generalizes to "elicit-then-validate". We don't need to be *right*, we need to be *checkable*.
- **Trust: read-only plugin, curator-only writes.** The plugin never writes the index — an open-write catalog of executable config (`env_setup` bash, UUIDs) is an injection vector. New machines are curated via an ingest tool (PR review = the audit trail), and the agent only ever sees a `CatalogSummary` (no executable config / raw UUIDs). The aspirational write-back — a canary-validated stand-up *proposing* a `provenance: plugin-validated` entry — is **reserved, not built**. *(Refined from the `facility-catalog` design; supersedes the earlier "plugin write-back loop.")*
- **Keep the degraded cascade agent-driven, not deterministic.** The *happy* path (index `get_subject` → `MachineProfile` + bundled fallback) is cheap and exact → code. The *degraded cascade* (which channel next, what to ask) is judgment → the `driving-hpc` recipe. Hardcode the cascade and you've rebuilt the rigid per-machine class.
- **Ablation is load-bearing.** Per-channel disable flags (`HPC_BRIDGE_DISABLE_CHANNELS=…`) (1) keep fallbacks alive (force-exercise the rarely-fired paths) and (2) operationalize this matrix into tests (disable a fact's channel, assert the fallback delivers).

## The resolution trace (one construct, three jobs)

The resolver already chooses a channel per fact; **recording that choice** as `{value, source, validated}` per fact does three jobs at once:

- **Observability** — every profile is legible (*host ← index · interface ← login-probe (canary-validated) · account ← user*).
- **Test oracle** — assertions read off the trace under ablation (*index off ⇒ `host.source == "human"`*), making the matrix executable.
- **Write-back seed** — a fully-validated trace *is* a proposed index entry.

Nearly free: the resolver already computes the channel choice and the canary result.

## See also
[[Globus index discovery channel]] · [[Discovery today]] · [[facility-remote]] · [[Happy path]] · [[Home]]
