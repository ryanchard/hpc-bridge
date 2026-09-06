"""Hermetic: the ALCF inference wrapper's config parsing, list-endpoints parsing, and secret hygiene.
No network, no token — urlopen is stubbed and the vendored auth helper is never called."""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import alcf_inference as a  # noqa: E402

BASE = "https://inference-api.alcf.anl.gov/resource_server/sophia/vllm/v1"


def test_base_url_requires_config(monkeypatch):
    monkeypatch.delenv(a.BASE_URL_ENV, raising=False)
    with pytest.raises(RuntimeError, match=a.BASE_URL_ENV):
        a.base_url()
    monkeypatch.setenv(a.BASE_URL_ENV, BASE + "/")  # trailing slash tolerated
    assert a.base_url() == BASE


def test_endpoints_url_derivation(monkeypatch):
    monkeypatch.setenv(a.BASE_URL_ENV, BASE)
    url, cluster, framework = a._endpoints_url_and_path()
    assert url == "https://inference-api.alcf.anl.gov/resource_server/list-endpoints"
    assert cluster == "sophia" and framework == "vllm"


def test_list_models_parses_list_endpoints(monkeypatch):
    monkeypatch.setenv(a.BASE_URL_ENV, BASE)
    payload = {"clusters": {"sophia": {"frameworks": {"vllm": {"models": ["meta-llama/Llama-3.3-70B-Instruct", "openai/gpt-oss-120b"]},
                                                      "triton": {"models": ["amsc-d3"]}}},
                            "metis": {"frameworks": {"vllm": {"models": ["other/model"]}}}}}

    def fake_urlopen(req, timeout=0):  # the Bearer is set but never checked here
        assert req.get_header("Authorization", "").startswith("Bearer ")
        return io.BytesIO(json.dumps(payload).encode())

    monkeypatch.setattr(a.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(a, "access_token", lambda: "fake-token-not-real")
    got = a.list_models()
    assert got == ["meta-llama/Llama-3.3-70B-Instruct", "openai/gpt-oss-120b"]  # sophia/vllm only, not metis or triton


def test_token_env_is_never_recorded_by_provenance(monkeypatch):
    # the whole point: the ALCF bearer must not land in a provenance bundle
    import provenance as p

    assert a.TOKEN_ENV in p._REDACT
    assert not a.TOKEN_ENV.startswith(("HPCB_", "HPC_BRIDGE_"))  # so _safe_env's prefix capture never grabs it
    monkeypatch.setenv(a.TOKEN_ENV, "super-secret-bearer-value")
    monkeypatch.setenv("HPCB_ALCF_MODEL", "meta-llama/Llama-3.3-70B-Instruct")
    env = p._safe_env()
    assert "super-secret-bearer-value" not in json.dumps(env)      # value never present
    assert env.get("HPCB_ALCF_MODEL") == "meta-llama/Llama-3.3-70B-Instruct"  # non-secret config IS recorded
