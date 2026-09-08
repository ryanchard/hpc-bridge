"""ALCF inference service as a model backend for the agentic tier — Globus-authed, OpenAI-compatible.

Lets an open-source operator harness (hermes-agent, Pi, …) drive hpc-bridge using ALCF-hosted models
(Llama-3.3-70B, QwQ-32B, …) instead of a Claude subscription — which Anthropic's April-2026 policy forbids in
third-party tools. Auth rides Globus (the vendored `inference_auth_token.py`): a ONE-TIME interactive
`authenticate` caches a refresh token under ~/.globus/app/ (never in this repo); the 24-hour access token is minted
on demand and passed to the harness as a Bearer at run time — never written to the repo, a bundle, or an image.

SECRET HYGIENE (the whole point):
- The durable secret (Globus refresh token) lives in ~/.globus/app/… — outside the repo, like ~/.globus_compute.
- The access token is fetched fresh via `access_token()` at launch and passed as `ALCF_INFERENCE_TOKEN` — a name that
  does NOT start with HPCB_/HPC_BRIDGE_, so provenance._safe_env never captures it, and it is on _REDACT besides.
- Only NON-secret config is stored (in gitignored agentic/.env): `HPCB_ALCF_BASE_URL` and `HPCB_ALCF_MODEL` — these
  ARE recorded in the bundle (useful provenance: which endpoint/model drove the run).

CLI (stdlib only — no `openai` dependency):
    uv run --extra integration python agentic/harness/alcf_token.py authenticate   # ONE-TIME, interactive (Globus browser)
    uv run python agentic/harness/alcf_inference.py probe                 # list models (connectivity check)
    uv run python agentic/harness/alcf_inference.py expires               # seconds until the access token expires
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

# The env var a driver hands the operator harness as the OpenAI-compatible Bearer / api_key. Deliberately NOT
# HPCB_/HPC_BRIDGE_-prefixed so provenance._safe_env does not record it; also add it to provenance._REDACT.
TOKEN_ENV = "ALCF_INFERENCE_TOKEN"
BASE_URL_ENV = "HPCB_ALCF_BASE_URL"   # e.g. https://data-portal-dev.cels.anl.gov/resource_server/sophia/vllm/v1
MODEL_ENV = "HPCB_ALCF_MODEL"         # e.g. meta-llama/Llama-3.3-70B-Instruct


def access_token() -> str:
    """A valid ALCF access token, minted/refreshed via the vendored Globus helper. Triggers the interactive
    login ONLY if no cached token exists (or the refresh token lapsed after 6 months) — in the harness we run
    the one-time `authenticate` first, so this is non-interactive at run time."""
    import globus_sdk.gare  # noqa: F401 - binds the lazy attr the vendored script reads (globus-sdk 4.9)
    import inference_auth_token  # vendored; flat on the harness PYTHONPATH

    return inference_auth_token.get_access_token()


def base_url() -> str:
    url = os.environ.get(BASE_URL_ENV, "").strip().rstrip("/")
    if not url:
        raise RuntimeError(
            f"{BASE_URL_ENV} is unset — put the ALCF OpenAI-compatible base URL in agentic/.env "
            "(e.g. https://<gateway>/resource_server/<cluster>/vllm/v1). Non-secret config; never the token."
        )
    return url


def model() -> str:
    return os.environ.get(MODEL_ENV, "").strip()


def _endpoints_url_and_path() -> tuple[str, str, str]:
    """Derive the ALCF FIRST list-endpoints URL, cluster, and framework from base_url. base_url is
    <gateway>/resource_server/<cluster>/<framework>/v1; models are listed at <gateway>/resource_server/list-endpoints
    (the OpenAI /v1/models path 404s on this gateway)."""
    url = base_url()
    marker = "/resource_server/"
    if marker not in url:
        raise RuntimeError(f"{BASE_URL_ENV} does not look like an ALCF gateway URL (no {marker!r}): {url}")
    gateway, tail = url.split(marker, 1)
    parts = tail.split("/")  # <cluster>/<framework>/v1
    cluster = parts[0] if parts else ""
    framework = parts[1] if len(parts) > 1 else "vllm"
    return f"{gateway}{marker}list-endpoints", cluster, framework


def list_models(token: str | None = None) -> list[str]:
    """The models currently offered for this cluster+framework, via ALCF FIRST's list-endpoints API (the
    connectivity check). Returns model ids (non-secret). NB a listed model may still be COLD — the first request
    can return 503 'online but not ready' until it auto-scales up; drivers should retry with backoff."""
    tok = token or access_token()
    endpoints_url, cluster, framework = _endpoints_url_and_path()
    req = urllib.request.Request(endpoints_url, headers={"Authorization": f"Bearer {tok}"})
    with urllib.request.urlopen(req, timeout=30) as r:  # fixed https gateway from our own config
        data = json.load(r)
    fw = (((data.get("clusters") or {}).get(cluster) or {}).get("frameworks") or {}).get(framework) or {}
    return list(fw.get("models") or [])


def _main(argv: list[str]) -> int:
    action = argv[0] if argv else "probe"
    if action == "probe":
        try:
            ids = list_models()
        except urllib.error.HTTPError as e:
            where = _endpoints_url_and_path()[0]
            print(f"HTTP {e.code} from {where} — token expired (re-run "
                  f"`inference_auth_token.py authenticate`), or check your entitlement/base URL.", file=sys.stderr)
            return 1
        want = model()
        print(f"base_url: {base_url()}")
        print(f"default model ({MODEL_ENV}): {want or '<unset>'}"
              + ("" if not want else f"  [{'present' if want in ids else 'NOT in the live list'}]"))
        print(f"{len(ids)} model(s) offered on this cluster/framework:")
        for m in ids:
            print(f"  {m}")
        return 0
    if action == "expires":
        import globus_sdk.gare  # noqa: F401 - binds the lazy attr the vendored script reads (globus-sdk 4.9)
        import inference_auth_token
        print(f"{inference_auth_token.get_time_until_token_expiration('seconds')} s until the access token expires")
        return 0
    print(f"usage: alcf_inference.py [probe|expires]  (got {action!r})", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
