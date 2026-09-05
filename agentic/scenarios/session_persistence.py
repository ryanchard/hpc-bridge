"""The session shell's state contract, on the FREE login shape: cwd + exported env persist ACROSS
`run_shell` calls (per session_id, under `<scratch>/sessions/<id>`), and `reset_session` wipes them.

No other scenario asserts the session semantics an agent leans on for multi-step work (cd into a
workdir once, set a variable once, keep going). The prompt fixes the directory and marker names so the
grader can read them back structurally from the tool RESULTS (`run_shell` returns `stdout`): a later,
separate call must print the cd'd path AND the marker; after `reset_session`, `pwd` must be back at the
session root (not the cd'd dir). Login shape only — no billed block, ~3 min. `reset_session` defaults
to the COMPUTE shape, so the prompt pins `shape='login'` there too (an un-pinned reset would hit the
spend floor and reset nothing — which the grader would then see as "state not cleared").
"""
from invariants import Result, ToolCall, Trace

_DIR = "hpcb_sess_dir"            # the cd'd directory (the grader looks for "/hpcb_sess_dir" in pwd)
_MARK = "hpcb-mark-7f3a"          # the exported value (HB_MARK)

PROMPT = (
    "On the HPC facility with login host `{ssh_host}` — facility id `{facility}` — "
    "I want to check that the session shell keeps state between calls. Use the LOGIN shape only "
    "(`shape='login'` on every run_shell / reset_session call); do NOT provision any billed compute "
    "block and do NOT tear anything down. Steps:\n\n"
    "1. `connect_facility` (accept the discovered configuration yourself if it isn't catalogued) "
    "and wait until the login node is up.\n"
    "2. In ONE `run_shell(shape='login')`: `mkdir -p " + _DIR + " && cd " + _DIR + " && export "
    "HB_MARK=" + _MARK + "`.\n"
    "3. In a SEPARATE, later `run_shell(shape='login')` — do not cd or re-export anything in it — "
    "print the working directory and the variable: `pwd; echo $HB_MARK`.\n"
    "4. `reset_session(shape='login')`.\n"
    "5. In another `run_shell(shape='login')`, print `pwd` again.\n\n"
    "Report what each call printed. This is an AUTOMATED run — no one is available to answer "
    "questions."
)

USER_GOAL = ""      # autonomous
PERSONA = None
KIND = "regression"
SUMMARY = "session shell: cwd + env persist across login-shape run_shell calls; reset_session clears them"
TAGS = ["session", "login-shape", "cheap"]


def _login_runs(t: Trace) -> list[tuple[int, ToolCall]]:
    return [(i, c) for i, c in t.named("run_shell") if str(c.input.get("shape")) == "login"]


def _out(c: ToolCall) -> str:
    return str((c.result or {}).get("stdout", ""))


def _complete(c: ToolCall) -> bool:
    return str((c.result or {}).get("phase")) == "complete"


def session_state_persists(t: Trace) -> Result:
    """Persist, then clear — read from the run_shell RESULTS, not the agent's prose:

    - a login-shape run_shell SET the state (its command contains `HB_MARK=`) and completed;
    - a LATER login-shape run_shell that does NOT itself cd or export (`cd ` / `HB_MARK=` absent
      from its command) completed with BOTH the marker value and `/<dir>` in its stdout — the
      cwd and env were carried by the session, not re-established in the same call;
    - a `reset_session` completed after that, and the NEXT login-shape run_shell after the reset
      printed a pwd back at the session root (`…/sessions/<id>` — the server's state dir) with no
      `/<dir>` in it; the marker must not show up either (env cleared)."""
    runs = _login_runs(t)
    setters = [i for i, c in runs if "HB_MARK=" in str(c.input.get("command", "")) and _complete(c)]
    if not setters:
        return Result("session_state_persists", False,
                      "no completed login-shape run_shell ever set HB_MARK (step 2 missing)")
    verify = [
        i for i, c in runs
        if i > setters[0] and _complete(c)
        and "HB_MARK=" not in str(c.input.get("command", ""))
        and "cd " not in str(c.input.get("command", ""))
        and _MARK in _out(c) and f"/{_DIR}" in _out(c)
    ]
    if not verify:
        return Result("session_state_persists", False,
                      "no later login-shape run_shell (that didn't cd/export itself) printed BOTH the "
                      f"marker {_MARK!r} and the cd'd path /{_DIR} — state did not persist across calls")
    resets = [i for i, c in t.named("reset_session") if i > verify[0] and _complete(c)]
    if not resets:
        return Result("session_state_persists", False,
                      "no completed reset_session after the persistence check (login shape pinned?)")
    after = [(i, c) for i, c in runs if i > resets[0] and _complete(c)]
    if not after:
        return Result("session_state_persists", False, "no login-shape run_shell after reset_session")
    i, c = after[0]
    out = _out(c)
    cleared = "/sessions/" in out and f"/{_DIR}" not in out and _MARK not in out
    return Result(
        "session_state_persists", cleared,
        f"ok: state persisted (call {verify[0]}) and reset_session cleared it (pwd back at the session "
        f"root in call {i})" if cleared else
        f"after reset_session, call {i} printed {out.strip()[:120]!r} — want the session root "
        f"(…/sessions/<id>) with no /{_DIR} and no marker",
    )


EXTRA_INVARIANTS = [session_state_persists]

# Login-only: `ends_with_stop` is vacuous here (no billed block => passes on "no billed block
# provisioned"), so it is reported but not gated; a strayed billed block would still be caught by the
# universal world postcheck (no pilot left running), which always gates.
EXPECT_OK = [
    "session_state_persists",        # the point: persist across calls, cleared by reset_session
    "no_raw_ssh_after_endpoint_up",  # the session rides the endpoint, not raw SSH
    "spend_not_unprompted",
]

TEARDOWN = "delete"
