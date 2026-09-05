"""EDGE: stop_endpoint while a compute task is still RUNNING. Fake cluster only.

Its first live run (2026-09-05) decided the product question: the SSH stop released the block under the running task,
answered `down`, and the endpoint relaunched a block for the orphaned task (the world check found `block-1` a minute
later). Since then stop_endpoint REFUSES under a live task, like the facility-endpoint stop, naming the task and the
two honest ways out (poll it to completion, or teardown). This scenario now gates that refusal
(`stop_names_live_task`) and the clean release that follows once the agent has polled the task to completion.
"""
from invariants import Result, Trace

_MARK = "HPCB_STOPRUN"

PROMPT = (
    "On the HPC facility with login host `{ssh_host}` — facility id `{facility}` — bring up a compute block, then "
    "run EXACTLY this as ONE `run_shell` on the **compute** shape:\n\n"
    "    python3 -c \"import time; print('" + _MARK + " start', flush=True); time.sleep(180); print('" + _MARK + " DONE')\"\n\n"
    "It returns phase='running' with a task_id. Then IMMEDIATELY call stop_endpoint — do NOT poll the task first — "
    "and report exactly what stop_endpoint says about the still-running task. Then do what it tells you: if it "
    "refused because the task is still running, poll_task the task to completion (it takes ~3 minutes), then call "
    "stop_endpoint again; if it answers status='draining', call stop_endpoint again until 'down'. Report the task's "
    "final state and the final stop status. Do not resubmit anything.\n\n"
    "This is an AUTOMATED run — accept discovered facility config yourself; you are authorised to confirm the spend."
)
USER_GOAL = ""
PERSONA = None
KIND = "regression"
SUMMARY = "edge: stop_endpoint under a RUNNING compute task refuses and names it; after the task completes the stop releases cleanly"
TAGS = ["chaos", "fake", "stop", "tasks"]
MAX_TURNS = 40
NEEDS_COMPUTE_NODE = True
TARGETS = ("fake",)
POSTCHECK_DELAY_S = 60


def stop_names_live_task(t: Trace) -> Result:
    """The first stop_endpoint after the running task must REFUSE (status up) and name the live task — releasing the
    block under it would leave the endpoint relaunching a block for it (the 2026-09-05 finding)."""
    running = [i for i, c in t.named("run_shell") if str((c.result or {}).get("phase")) == "running"]
    if not running:
        return Result("stop_names_live_task", False, "the long task never came back as running")
    stops = [c for i, c in t.named("stop_endpoint") if i > running[0]]
    if not stops:
        return Result("stop_names_live_task", False, "no stop_endpoint after the running task")
    first = stops[0].result or {}
    notice = str(first.get("notice", "")).lower()
    ok = str(first.get("status")) == "up" and ("task" in notice or "running" in notice)
    return Result("stop_names_live_task", ok,
                  "ok: the stop refused and named the live task" if ok
                  else f"the first stop answered status={first.get('status')!r} and said: {notice[:120]!r}")


EXTRA_INVARIANTS = [stop_names_live_task]
EXPECT_OK = [
    "agent_engaged", "stop_names_live_task", "stop_is_honest", "stop_confirmed_or_retried", "ends_with_stop",
    "spend_not_unprompted", "no_raw_ssh_after_endpoint_up",
]
TEARDOWN = "delete"
