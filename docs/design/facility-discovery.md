# Facility Discovery

**Status:** design note · companion to [`agent-tool-boundary.md`](./agent-tool-boundary.md) · **Applies to:** how hpc-bridge learns what a machine is.

## Why this exists

The boundary doc names *discovery* as how a fact crosses from **library-shaped** (we hardcoded it) to **agent-shaped** (probed live). This note specifies the *process*: what we discover, on which side of the connection, who interprets it, and — deliberately — that **we do not cache it yet**.

A hardcoded `anvil_profile` is a frozen snapshot of a discovery someone did by hand; the `anvil-first-target` agent-memory entry is literally that discovery written as prose. This note is how we turn that into a repeatable, regenerable process instead of a per-machine class we hand-author.

## Principle: no cache (yet)

We do **not** persist a discovered profile and read it back to skip discovery. Every provisioning re-derives from the live machine. Why:

1. **Caching now = premature de-agentification.** A cache makes "read the stored answer" (procedure) the common path and demotes discovery/judgment to a rare fallback — hollowing out the exact capability that justifies an agent over a library, and ensuring we never accumulate evidence that it works.
2. **You can't design intelligent invalidation for a pipeline you haven't watched run.** Which facts are stable (modules), volatile (queue, orphans, login node), or cross-cutting (version skew flips when *either* side updates) is the *output* of running discovery uncached for a while.
3. **Running it every time is how it gets robust** — it surfaces the drift and per-facility quirks we'd otherwise paper over.

Caching is a deliberate **later phase**, designed by observing the real pipeline. When it comes, it is keyed by volatility class, provenance/staleness-checked, and **degrades to discovery — never to a hardcoded default**, so the agent path is always reachable.

**"No cache" ≠ "no output."** Discovery still *emits* a structured report each run — **write-for-the-record** (documentation + provenance + state the agent reads *this* session), not **read-to-skip**. The same artifact becomes the cache later, when the pipeline is robust. Two jobs now (documentation + agent-state); caching is the deferred third.

Two constraints make this safe and future-proof:

- **Read-only + idempotent.** Discovery only *observes*; it is safe to run every time, and that is exactly what makes later memoization trivial (a pure function of machine state).
- **Cadence = provisioning, not per-call.** Discovery runs at provision (Stages 0–2) and when cost-relevant (Stage 4) — not on every `run_shell`. The canary's 45 s liveness TTL is a separate concern and stays.

## Three faces: remote, local, cross

Discovery is not only "probe the login node." It has three faces, and the third is where the nastiest opaque failures live:

- **Remote (on-machine):** what the login node can tell us — modules, scheduler, fabric, filesystems, venv health, orphans, allocation.
- **Local (off-machine):** what *our* side knows — SDK/dill/Python versions, platform (macOS can't provision → BYO), the local Globus identity, endpoint UUIDs the SDK already holds, which SSH key maps to which facility.
- **Cross (comparisons):** the checks that are inherently *both* — version **skew** (local SDK vs remote gce/worker), **identity match** (local Globus identity must own the remote endpoint or dispatch is unauthorized), **reachability** (local→SSH, remote→AMQP:443). A library checks none of these; they are the agent's bread and butter.

## The lifecycle: what's discoverable, who interprets

Each fact splits on the boundary: the **tool gathers** (a deterministic probe — procedure) and the **agent interprets** (resolve drift, repair, choose — decision).

| Stage | Face | Fact | How (Slurm / login node) | Gather | Agent decides | Today |
|---|---|---|---|---|---|---|
| **0 · Reach/identity** | cross | login node we landed on (round-robin); Globus identity present + matches local | `hostname`; gce whoami / token | ✓ | reuse vs re-login | ✗ (round-robin orphan bug) |
| **1 · Facility shape** | remote | Python module ≥3.11 (ideally matching SDK minor) | `module spider` / `module avail` | ✓ | which if none exact | hardcoded `anaconda/2024.02-py311` |
| | remote | high-speed fabric for `address_by_interface` | `ip -o addr`; pick IB/HSN | ✓ (rule) | ambiguous fabrics | hardcoded `ib0` |
| | remote | partitions + {max walltime, nodes, cores/node} | `sinfo`, `scontrol show partition` | ✓ | which partition for *this* job | hardcoded `debug` |
| | remote | accounts/allocations (incl. GPU) | `sacctmgr` / facility tool | ✓ | which account | hardcoded `cis250223` |
| | remote | scratch/project roots | `$SCRATCH`, `$PROJECT` | ✓ | — | hardcoded Anvil path |
| | cross | AMQP egress on 443 | connectivity test | ✓ | — | assumed (safe default) |
| **2 · Endpoint health** | remote | venv exists + `globus-compute-endpoint` on PATH | `test -d` / `which` after `env_setup` | ✓ | bootstrap if missing | ✗ (start just fails) |
| | cross | **remote gce version vs local SDK** | `gce --version` vs `globus_compute_sdk.__version__` | ✓ | rebuild vs warn vs proceed | ✗ caught only at *dispatch* (canary), late |
| | remote | existing/orphan endpoint for our name + which node | `gce list` (+ node) | ✓ | reuse vs kill | partial (`status`, node-blind) |
| **3 · Provision exec** | cross | manager online; **worker live** | `manager_online`; **canary** | ✓ | wait/retry/investigate | ✓ (canary) |
| **4 · Cost** | remote | allocation remaining | `mybalance`/`xdusage` (Anvil) | ✓ | spend/downgrade/ask | ✗ (`→ None`; gate inert) |

Two things stand out: **almost the entire profile is Stage-1 discovery we happen to have hardcoded** (so "add a facility" is currently "write a class" — the library pattern), and **the expensive failures we've hit live** — version skew, orphans, wrong login node, the cold-start gap — **are all Stage-0/2 discovery gaps**: state the machine knew that we never read at provision time.

**Not everything is reliably probeable — which is the point of *probe-then-validate*.** Two fields resist discovery: `address_by_interface` (Globus's own docs call ifname selection "educated guesses and trial and error" — `hsn0`/`ib0`/`bond0`/…), and the reproducible worker environment (`worker_init`), which depends on the *client's* exact Python/dill/SDK versions a login-node probe can't see (the cross-face version-coupling trap). So discovery never *trusts* its output blind: the Stage-3 **canary** is the validation backstop — it turns "guess the interface" into automated trial-and-error and catches skew + stale module names at provision time, not at first real task. This is the synthesis the analysis reached independently — config decomposes into a probeable layer plus a pinned/curated layer, validated by a canary: [`../analysis/04-architecture-and-roadmap.md`](../analysis/04-architecture-and-roadmap.md) §1.3, §5.

## Gathering: recipes, not sealed commands

A subtlety the boundary glosses: the *command to discover* is itself environment-dependent. A `discover_partitions()` tool that hardcodes `sinfo` is "forced into a bad command" the moment it meets PBS, a wrapped scheduler, or a module-gated binary — re-importing the brittleness discovery was meant to kill. So **the agent does the gathering, through `run_shell`**, guided by *recipes*, not a sealed probe:

- A **recipe** is canonical knowledge — *"for Slurm partitions, `sinfo -h -o '%P|%l|%D|%c'` (delimited so it parses cleanly); for PBS, `qstat -Q`; module-load first if the binary's missing; if all fail, ask."* The agent *uses* it on the happy path and *deviates* when the environment doesn't match. Recipes live where agent-facing knowledge already does: the `driving-hpc` skill.
- That's the sweet spot: **a tool's reliability in the common case** (the canonical, clean-format command) **plus the agent's adaptability in the tail** (fall back, module-load, ask) — strictly better than a rigid tool, which has only the common case and breaks in the tail.
- It's safe to hand the agent here because **discovery is read-only** — `sinfo`/`module spider` damage nothing, so exploration is low-risk. (Mutation/cost actions — provision, teardown — are where determinism earns its keep.)

**Reserve deterministic code for the invariant-fiddly-risky core**, *not* for "list the partitions": the version-skew comparison (a precise check you don't want eyeballed), `config_template` generation (the hard-won YAML), and the canary (the worker probe in the dispatch path). Everything the agent can robustly do via `run_shell` + a recipe, it should.

## Managing multiple sources: select by fidelity, layer, fall back

A fact often has more than one source — partitions from the ACCESS Operations API (a public catalog) *or* a live `sinfo`; budget from `mybalance` on the login node *or* XDMoD. The wrong move is a fixed "always try source X first": it re-couples (forces ACCESS even off-ACCESS) and breaks when a source is stale, down, or inapplicable.

Instead, each fact carries a **source map** (data) tagging every candidate with properties, and the agent selects against a heuristic:

| property | example |
|---|---|
| **applies-when** | ACCESS API → only ACCESS resources; `sinfo` → only Slurm |
| **freshness** | the catalog gives *static* shape (which partitions exist, their caps); only the live probe knows *idle-right-now* |
| **cost / auth** | public HTTP  <  an SSH round-trip  <  a token-gated service |

**The heuristic — this is the priority order:** *meet the fact's freshness requirement first; then minimize cost/auth; then fall back on failure.* Freshness is the hard constraint, cost/auth breaks ties, fallback is resilience. So there *is* a priority — but it's **derived from source properties per fact, not a fixed ranking of sources** — and the agent owns the selection, free to deviate when a source is down or a facility is quirky (same reason recipes aren't sealed commands).

**Sources layer, they don't merely fall back.** The cheap source seeds the *static* layer; the live probe overlays the *dynamic* layer. The partition gate needs idle-now → it must hit `sinfo`, but it can enrich each option with the catalog's documented caps. A "what could this machine run?" question is fine on the catalog alone. Compose by fidelity; fall back only on unavailability (catalog down → the probe covers everything; SSH down → the catalog yields a static-only, flagged-degraded answer).

So **nothing is forced as "first."** ACCESS is "first" only where it's the best fit — a cheap, no-auth, *static* fact on an ACCESS facility — and even there the live probe refines or replaces it. In the workflow: the agent consults the source map, applies the heuristic, gathers via the right channel (HTTP / `login_shell` / `xdmod-data`), records *which source + when* in the `FacilityProbe`, and falls through on failure. Source map = data; recipes + heuristic = skill knowledge; the agent selects.

### ACCESS as the worked example

Three surfaces, and the credentials they need (researched 2026-06; confirm endpoint paths against the Operations API Swagger when building):

| Surface | Gives | Credentials |
|---|---|---|
| **Operations API** (`operations-api.access-ci.org`) | infrastructure **catalog** — resource descriptions, **batch-scheduler config**, project↔resource access | **largely public** today (some CILogon); future self-service registration |
| **XDMoD Data Analytics** (`xdmod-data` Python pkg) | usage **analytics** (historical) from the XDMoD warehouse | an **API token** from XDMoD *My Profile* (needs an ACCESS Identity) |
| **`xdusage` / `mybalance`** (per-resource CLI) | **live** allocation remaining | **none new** — the RP holds the key; you run it on the login node over the SSH you already have |

The finding that shapes the integration: **ACCESS's low-credential API helps *discovery* (the catalog) more than *budget*.** There is no clean public "GET my balance" — live remaining comes from the per-resource CLI, so **budget stays a per-facility recipe** (and is *better* that way: live, not historical). The catalog, by contrast, is a genuine public **discovery seed** (resources + scheduler config in one HTTP call), behind the discovery seam, degrading to the live `sinfo` probe. So ACCESS slots in exactly as the decoupling rules predict: catalog = a discovery *source*; budget = a *recipe*; identity = CILogon behind the *broker*. Delete ACCESS and only the *seed* is lost — per-facility `sinfo`/`mybalance` still work.

## Discovery output: a `FacilityProbe` record

Whatever the gather path, the agent emits a structured **`FacilityProbe`** as the *output record* (the report-not-cache artifact) — so the question, the provision, and the provenance log all consume a clean shape. **Freeform gather, structured product.**

```python
@dataclass(frozen=True)
class FacilityProbe:
    # provenance (write-for-the-record; NOT read back to skip discovery — yet)
    host: str                 # the login node actually probed (round-robin aware)
    probed_at: str            # timestamp, passed in (no Date.now in pure code)
    # remote facility shape
    python_modules: list[str] # candidates yielding >=3.11
    interface: str | None     # chosen high-speed fabric, or None -> agent picks
    partitions: list[dict]    # name, max_walltime, max_nodes, cores_per_node
    accounts: list[str]
    scratch_root: str | None
    # health
    venv_ok: bool
    gce_version: str | None   # remote
    orphans: list[dict]       # name, uuid, state, node
    # cross-checks (local vs remote)
    sdk_version: str          # local
    version_skew: bool
    identity_ok: bool
    amqp_egress_ok: bool | None
```

The report is **surfaced to the agent** (the "see" half of the design rule) and **logged** (documentation + provenance). It is *not* read back to short-circuit the next provision.

## From probe to profile

`MachineProfile` becomes a *derivation* of a `FacilityProbe`, not a hand-authored constant:

- **Deterministic synthesis** handles the parts where a rule suffices — pick the IB/HSN fabric, the module that yields ≥3.11, `$SCRATCH`.
- The **agent** handles the ambiguous and broken cases via `run_shell` — module renamed, venv missing → bootstrap it, gce skews → rebuild or warn, orphan running → reuse or kill, no obvious fabric → choose.

The agent authoring a new facility's profile from a probe **is** the generalization capability — the concrete reason there's an agent here and not just a library. `anvil_profile` stays as a **seed/fallback and the worked example**, not the mechanism.

## Policy gates: the human↔agent decision gradient

The "agent decides" half of the boundary is not binary. A discovered option set (which partition, which account) can be *resolved* by the human, by the agent, or — eventually — automatically; and a decision can **graduate** along that gradient as we earn trust, the same way caching is earned by watching the pipeline run.

A **policy gate** is the un-automated first rung: the agent gathers the options (read-only discovery, above), presents them, and the *human* picks. The discovered facts become the choices in a question — Claude Code constructs it (`AskUserQuestion`); the descriptions are discovered metadata, so the gate is *informed*:

> **Which partition for this job?**
> • **shared** — sub-node (128c/257 GB), schedules fastest, ≤96 h • **debug** — fast, but currently full (pending: Resources) • **gpu** — if the job needs accelerators

The gradient, each rung earned by the previous one's evidence:

1. **Human picks** — agent surfaces all options, user chooses.
2. **Agent proposes, human confirms** — "I'd use `shared`; ok?"
3. **Agent decides clear cases, asks on ambiguity.**
4. **Always human-gate the irreversible/costly** — the deterministic floor (large spend, destructive ops) never goes silent.

Three disciplines: it's a **gate, not an interrogation** (ask only consequential/ambiguous choices; sensible-default the unambiguous — there's usually one fabric, `$SCRATCH` is unambiguous); **legibility bounds the human's pick too** (a bare name list is a weak question — limits + live queue state are what make it good); and it needs a **no-human fallback** (headless/autonomous mode can't prompt → fall back to the safe/cheap default).

Who owns what stays clean: discovery *gathers* the options (via `run_shell` + recipes), the **mode** ("user-driving" vs "autonomous") is the agent's policy — not baked into a tool — and `AskUserQuestion` is the surface. The same discovery path serves both modes.

## Worked example: Anvil (seed)

The `anvil-first-target` memory entry is the v0 discovery output, by hand: login `login07`, x86_64; account `cis250223` (GPU `cis250223-gpu`); module `anaconda/2024.02-py311`; fabric `ib0`; partitions `shared`/`debug`/`wholenode`/…; `SCRATCH=/anvil/scratch/$USER`, `PROJECT=/anvil/projects/x-cis250223`; gce `4.12.0`; key-only SSH, no Duo on the CLI. The agentic version regenerates exactly this as a structured `FacilityProbe`, live, and reconciles it against drift — instead of me re-typing it into memory.

## Later: intelligent caching (deferred)

Once the pipeline is robust and we've *watched* it, cache by volatility class: facility shape (Stage 1) is long-lived; health/state (Stage 2, orphans, queue) is volatile → re-probe; cross-checks invalidate when either side's version changes. Always provenance-checked, always degrading to a fresh probe — never to a hardcoded default.
