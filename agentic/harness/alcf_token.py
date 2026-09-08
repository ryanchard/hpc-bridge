"""Mint / manage the ALCF inference token — a thin wrapper over the VENDORED `inference_auth_token.py` (ALCF's own
script, MIT, kept unmodified; excluded from ruff).

Why a wrapper: the vendored script reaches `globus_sdk.gare.GlobusAuthorizationParameters` at import time as a lazy
ATTRIBUTE of the top-level package. globus-sdk 4.9.0 (the lockfile's version) no longer exports `gare` lazily —
`import globus_sdk.gare` works, `globus_sdk.gare` does not — so the script died with "module globus_sdk has no
attribute gare" and every hermes cell failed at the token mint (2026-09-08). Importing the submodule first binds the
attribute; then the vendored script runs exactly as upstream wrote it. Same CLI:

    uv run --extra integration python agentic/harness/alcf_token.py authenticate      # once (interactive Globus login)
    uv run --extra integration python agentic/harness/alcf_token.py get_access_token  # prints the bearer
"""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

import globus_sdk.gare  # noqa: F401 - binds `globus_sdk.gare` for the vendored script (see the docstring)

if __name__ == "__main__":
    sys.argv[0] = "inference_auth_token.py"
    runpy.run_path(str(Path(__file__).with_name("inference_auth_token.py")), run_name="__main__")
