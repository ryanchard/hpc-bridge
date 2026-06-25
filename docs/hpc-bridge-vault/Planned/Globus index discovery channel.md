# Globus index discovery channel

> [!warning] Planned · transient
> Designed work, **not current behaviour** — expect this note to churn and be deleted as it lands. **Status: ready to implement (slice 1).** Tracking: [#7](https://github.com/ryanchard/hpc-bridge/issues/7). Supersedes `docs/design/discovery-channels.md` (being absorbed here per the vault's single-home plan).

## Goal

Replace the hardcoded `anvil_profile` ([[facility-remote]]) with a **resolver** that builds a `MachineProfile` from an entry in a self-owned **Globus Search index** — so adding a facility is *data*, not *code*. The index is the *happiest* discovery channel; everything else (login-node probe, the human) is fallback. Contrast with what exists now: [[Discovery today]].

## Design

The conceptual frame — the channel model, the provide-vs-discover matrix, the principles, and the resolution trace — lives in **[[Discovery channel model]]**. This note is the concrete *first build* of that model. The slice-specific part:

**The index.** A *public* Globus Search index (`6ff95fb8-1113-42be-a811-3d1cb5a67bd5`), queried **anonymously** via `globus_sdk.SearchClient` (already a transitive dep — **no CLI subprocess, no new auth**). `get_subject("purdue:anvil")` resolves a facility; `search(q=…)` browses. An entry carries `ssh_host`, `auth_method`, `compute.{scheduler, interface, env_setup, scratch_root}`, per-run `defaults.{…}`, an allocation `command` + named `parser`, and `provenance`.

## Plan

**Resolve flow:** `facility id → SearchClient.get_subject → content → MachineProfile`, substituting `{user}`/`{venv}` at build time. A query failure **degrades** to a bundled seed entry (today's `anvil_profile`), then to the human — never a hard stop.

**Three seams, built test-first:**
1. a **`SearchClient` injection point** — fake it in unit tests;
2. per-channel **ablation flags** (`HPC_BRIDGE_DISABLE_CHANNELS=…`) — disable a channel, assert the fallback still delivers;
3. a **resolution trace** (`{value, source, validated}` per fact) — observability **and** the test oracle **and** the write-back seed.

**Test layers:** hermetic unit (fake `SearchClient` → `MachineProfile` + trace; degrade-to-seed; ablation; named-parser fixtures; schema-driven "what to ask"); the index is testable with **no auth** (a recorded fixture + a live "still matches the fixture" check); the [[Warmth, the canary & cold-start|canary]] is the production validator; the agent cascade via scripted ablation scenarios.

**Slices:**
1. **The resolver + bundled fallback** — replace `make_facility`'s hardcode. *(next)*
2. The **resolution trace** + **ablation flags**.
3. The **Socratic fallback** as an explicit path (human → login-probe → canary).
4. The **write-back loop** — a canary-validated stand-up proposes a `provenance: plugin-validated` entry.

## Status

- **Done:** the index exists and is queryable; the discovery *workflow* (establish → discover → gate → provision → wait) is live ([[Discovery today]]); endpoint reuse over the web ([[facility-remote]]).
- **Next (ready):** slice 1 — the resolver.
- **Deferred (simplicity/observability):** ACCESS MCP / Operations API as additional channels; the symbol-sync snippet tooling for the vault.

## See also
[[Discovery today]] · [[facility-remote]] · [[Two-channel architecture]] · [[Happy path]] · [[Home]]
