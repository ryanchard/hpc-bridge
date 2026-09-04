from __future__ import annotations

import asyncio
import sys

from ..profile import Profile
from .base import EndpointHandle

# Tokens a MEP entry's env_setup (→ worker_init) may carry; resolved at attach from the facility's
# published metadata / this client. `{gce_version}` = the facility endpoint's own version (a worker
# pool at the CLIENT's SDK version is the skew that fails every job — globus1, 2026-08); `{python_version}`
# = this client's major.minor (the task function is dill-serialised by this interpreter).
GCE_VERSION_TOKEN = "{gce_version}"
PYTHON_VERSION_TOKEN = "{python_version}"


class MEPFacility:
    """A facility-run **multi-user** Globus Compute endpoint (MEP), consumed with ZERO SSH.

    Unlike ``SlurmFacility`` (which SSHes in to stand up a *personal* manager) the facility already
    runs the manager, owns the UEP template, and maps our Globus identity to a local account. We only
    dispatch a ``user_endpoint_config`` to its UUID over AMQP. Consequences, all load-bearing:

    - ``provision`` does no work — no SSH, no ``configure``/``start``. It hands back the catalogued
      UUID with ``reused=True`` (there was never anything to bootstrap; the connect result reports a
      zero-SSH attach).
    - There is **no login shape**. A facility MEP rejects ``LocalProvider`` / ``compute: false`` (its
      forked UEPs run in ``system.slice`` with no memory cgroup, so an unbounded LocalProvider task on
      the controller node is refused). This facility is compute-only; the server must never request
      the ``login`` shape against it. Low-latency comes from a warm block (``init_blocks: 1``), not a
      free login node.
    - We do **not** own the template, so ``config_template`` returns only the compute-shape
      ``user_endpoint_config`` defaults (partition / walltime / worker_init / …); the template slot is
      unused (``_shape_runtime`` reads element ``[1]`` only).
    - ``manager_online`` is a best-effort web check. The authoritative warmth signal for a MEP is the
      dispatch **canary** (a worker answers), so a status-API hiccup — or a foreign-endpoint read we
      aren't authorized for — degrades to ``True`` rather than falsely stranding us as 'provisioning'.
    """

    # Capabilities the server reads via getattr (the same style as `bootstrap`/`teardown`/
    # `scratch_root` on other facilities). No login shape exists on a MEP, and the server DERIVES the
    # rest from that one fact: no login shape ⇒ no scancel-over-login release channel ⇒ stop is
    # draining-only, teardown is a no-op, and every shape is billed.
    supported_shapes: tuple[str, ...] = ("compute",)

    def __init__(
        self,
        endpoint_id: str,
        name: str,
        user_opts: dict,
        *,
        scratch_root: str = "$HOME/.hpc-bridge",
        account_required: bool = False,
        max_idletime_s: int | None = None,
        client_factory=None,
    ) -> None:
        self.endpoint_id = endpoint_id
        self.name = name
        # The compute-shape user_endpoint_config defaults (from the catalog entry). worker_init is
        # passed through verbatim — a MEP runs it as the *mapped* local user, so it must be shell-
        # resolvable there (``$HOME``/``$USER``), never client-side ``{user}``/``{venv}`` templating,
        # which we can't resolve (the local account is the facility's identity-mapping secret).
        # Empty/None values are dropped: the manager json.dumps's every string, so an ``account: ""``
        # arrives as the two-char string ``""`` — truthy in the template — and sbatch rejects an
        # empty --account.
        self._user_opts = {k: v for k, v in user_opts.items() if v not in (None, "")}
        # Session-shell root: worker-side form ($HOME/…), expanded by the mapped user's shell.
        self.scratch_root = scratch_root
        self.account_required = account_required
        self.max_idletime_s = max_idletime_s  # the FACILITY's idle-release (its template), if known
        self._client_factory = client_factory or self._default_client
        # The facility's published template contract, read once at attach (`load_template`): the JSON
        # schema its manager validates `user_endpoint_config` against (Anvil's rejects unknown keys —
        # our `interface`/`compute` would fail every submit), its endpoint version, its display name.
        self.schema: dict | None = None
        self.endpoint_version: str | None = None
        self.display_name: str | None = None
        self.template_notes: list[str] = []

    @classmethod
    def from_entry(cls, entry, *, account: str | None = None, client_factory=None) -> MEPFacility:
        """Build from a catalog entry that carries `compute_mep_uuid`.

        The entry's `compute`/`defaults` split maps straight onto the MEP's schema: pinned
        invariants (`interface`, `env_setup` → `worker_init`) + per-run tunables (`defaults.*`,
        including `init_blocks`, the warm-block knob). `account` is seeded only when given — the
        startup-pin path's HPC_BRIDGE_ACCOUNT (an env the server *demanded* must not be silently
        dropped); the agentic path supplies it per-user via ensure_endpoint_up(account=…). An empty
        one is removed by the constructor's filter. `compute: True` is added by shape_config('compute')."""
        c, d = entry.compute, entry.defaults
        opts = {
            "interface": c.interface,
            # "" ⇒ no worker_init of ours: the facility template's default runs (it installs the worker at
            # THIS client's SDK version — only right when that equals the facility's endpoint version)
            "worker_init": c.env_setup or None,
            "partition": d.partition,
            "walltime": d.walltime,
            "max_workers_per_node": d.max_workers_per_node,
            "nodes_per_block": d.nodes_per_block,
            "init_blocks": d.init_blocks,
            "max_blocks": d.max_blocks,
            "account": account,
            **(d.extra or {}),  # the facility template's own knobs (qos, cores_per_node, scheduler_options…)
        }
        return cls(
            endpoint_id=entry.compute_mep_uuid,
            name=c.endpoint_name or entry.id,
            user_opts=opts,
            scratch_root=c.scratch_root,
            account_required=entry.account_required,
            client_factory=client_factory,
        )

    @staticmethod
    def _default_client():
        from globus_compute_sdk import Client

        return Client()

    async def provision(self, profile: Profile) -> EndpointHandle:
        # The facility already runs the manager; there is nothing to stand up and no SSH to make.
        # reused=True so ConnectFacilityResult reports a zero-SSH attach.
        return EndpointHandle(endpoint_id=self.endpoint_id, name=self.name, reused=True)

    async def manager_online(self, endpoint_id: str) -> bool:
        try:
            # The whole call — SDK Client construction (token-storage I/O) included — off the loop.
            status = await asyncio.to_thread(
                lambda: self._client_factory().get_endpoint_status(endpoint_id)
            )
        except Exception:  # noqa: BLE001 - the canary is the real signal; a status hiccup must not read as offline
            # Best-effort: the MEP is administered infrastructure and the dispatch canary is the real
            # liveness check — a status-API error (or a foreign-endpoint read we can't see) must not
            # condemn a live endpoint to 'provisioning'.
            return True
        await self.load_template()  # the attach is the moment to learn the facility's contract
        return status.get("status", "online") == "online"

    async def load_template(self) -> None:
        """Read the facility's published template contract once (best-effort): the user_config_schema
        its manager validates against, its endpoint version, its display name."""
        if self.schema is not None or self.endpoint_version is not None:
            return
        try:
            md = await asyncio.to_thread(
                lambda: self._client_factory().get_endpoint_metadata(self.endpoint_id)
            )
        except Exception as exc:  # noqa: BLE001 - metadata is a courtesy; the canary still decides
            self.template_notes.append(
                f"facility template not readable ({type(exc).__name__}): sending the entry's config as-is")
            return
        md = md or {}
        self.schema = md.get("user_config_schema") or None
        self.endpoint_version = str(md.get("endpoint_version") or "") or None
        self.display_name = md.get("display_name") or None

    def sanitize_uec(self, uec: dict) -> dict:
        """The final user_endpoint_config for a submit, made to fit the FACILITY's contract:
        - `{gce_version}` / `{python_version}` in worker_init resolved (the version skew fix);
        - when the published schema forbids unknown keys, everything not in it is dropped (Anvil rejects
          `interface`, `compute`, `max_workers_per_node`; Delta ignores extras) and the drop is noted."""
        out = dict(uec)
        wi = out.get("worker_init")
        if isinstance(wi, str) and (GCE_VERSION_TOKEN in wi or PYTHON_VERSION_TOKEN in wi):
            if GCE_VERSION_TOKEN in wi and not self.endpoint_version:
                # no version to pin to: the facility's own default worker_init is the safer fallback
                out.pop("worker_init", None)
                self._note("facility endpoint version unknown: using the facility's default worker_init")
            else:
                py = f"{sys.version_info.major}.{sys.version_info.minor}"
                out["worker_init"] = (wi.replace(GCE_VERSION_TOKEN, self.endpoint_version or "")
                                      .replace(PYTHON_VERSION_TOKEN, py))
        sc = self.schema or {}
        if sc.get("additionalProperties") is False:
            allowed = set((sc.get("properties") or {}).keys())
            dropped = sorted(k for k in out if k not in allowed)
            for k in dropped:
                out.pop(k)
            if dropped:
                self._note(f"keys not in the facility's template schema were dropped: {', '.join(dropped)}")
        return out

    def _note(self, msg: str) -> None:
        if msg not in self.template_notes:
            self.template_notes.append(msg)

    def config_template(self, profile: Profile) -> tuple[str, dict]:
        # Only user_endpoint_config defaults; the facility owns the real template (the '' slot is
        # unused). shape_config('compute') merges ``compute=True`` over these to form the UEC, then
        # `sanitize_uec` fits the result to the facility's published schema.
        return ("", dict(self._user_opts))
