from __future__ import annotations

import asyncio

from ..profile import Profile
from .base import EndpointHandle


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

    def __init__(
        self,
        endpoint_id: str,
        name: str,
        user_opts: dict,
        *,
        client_factory=None,
    ) -> None:
        self.endpoint_id = endpoint_id
        self.name = name
        # The compute-shape user_endpoint_config defaults (from the catalog entry). worker_init is
        # passed through verbatim — a MEP runs it as the *mapped* local user, so it must be shell-
        # resolvable there (``$HOME``/``$USER``), never client-side ``{user}``/``{venv}`` templating,
        # which we can't resolve (the local account is the facility's identity-mapping secret).
        self._user_opts = dict(user_opts)
        self._client_factory = client_factory or self._default_client

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
            status = await asyncio.to_thread(
                self._client_factory().get_endpoint_status, endpoint_id
            )
        except Exception:
            # Best-effort: the MEP is administered infrastructure and the dispatch canary is the real
            # liveness check — a status-API error (or a foreign-endpoint read we can't see) must not
            # condemn a live endpoint to 'provisioning'.
            return True
        return status.get("status", "online") == "online"

    def config_template(self, profile: Profile) -> tuple[str, dict]:
        # Only user_endpoint_config defaults; the facility owns the real template (the '' slot is
        # unused). shape_config('compute') merges ``compute=True`` over these to form the UEC.
        return ("", dict(self._user_opts))
