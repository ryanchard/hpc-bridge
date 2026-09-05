"""EDGE: stop_endpoint while a compute task is still RUNNING. Fake cluster only (leaves a task to die).

The SSH path drains the task's handle silently while the facility-endpoint path refuses to stop under a live task
(review 2026-09-05, coverage table). This scenario decides the product question with evidence: it records what the
stop says about the live task (`stop_names_live_task`, REPORTED) and gates only on safety — the stop must be honest,
re-stopped to `down` if `draining`, and the block must actually be gone. No hook needed: the agent itself stops early.
"""
from invariants import Result, Trace

_MARK = "HPCB_STOPRUN"

PROMPT = (
    "On the HPC facility with login host `{ssh_host}` — facility id `{facility}` — bring up a compute block, then "
    "run EXACTLY this as ONE `run_shell` on the **compute** shape:\n\n"
    "    python3 -c \"import time; print('" + _MARK + " start', flush=True); time.sleep(180); print('" + _MARK + " DONE')\"\n\n"
    "It returns phase='running' with a task_id. Then IMMEDIATELY call stop_endpoint — do NOT poll the task first — "
    "and report exactly what stop_endpoint says about the still-running task. If it answers status='draining', call "
    "stop_endpoint again until 'down'. Finally call poll_task ONCE on the task_id and report the task's final state. "
    "Do not resubmit anything.\n\n"
    "This is an AUTOMATED run — accept discovered facility config yourself; you are authorised to confirm the spend."
)
USER_GOAL = ""
PERSONA = None
KIND = "diagnostic"
SUMMARY = "edge: stop_endpoint under a RUNNING compute task — what does the stop say, and is the block really gone?"
TAGS = ["chaos", "fake", "stop", "tasks"]
MAX_TURNS = 40
NEEDS_COMPUTE_NODE = True
TARGETS = ("fake",)
POSTCHECK_DELAY_S = 60


def stop_names_live_task(t: Trace) -> Result:
    """REPORTED: did the first stop_endpoint after the running task mention that a task was still running?"""
    running = [i for i, c in t.named("run_shell") if str((c.result or {}).get("phase")) == "running"]
    if not running:
        return Result("stop_names_live_task", False, "the long task never came back as running")
    stops = [c for i, c in t.named("stop_endpoint") if i > running[0]]
    if not stops:
        return Result("stop_names_live_task", False, "no stop_endpoint after the running task")
    notice = str((stops[0].result or {}).get("notice", "")).lower()
    ok = "task" in notice or "running" in notice
    return Result("stop_names_live_task", ok,
                  "ok: the stop named the live task" if ok else f"the stop said nothing about the live task: {notice[:120]!r}")


EXTRA_INVARIANTS = [stop_names_live_task]
EXPECT_OK = [
    "agent_engaged", "stop_is_honest", "stop_confirmed_or_retried", "ends_with_stop", "spend_not_unprompted",
    "no_raw_ssh_after_endpoint_up",
]
TEARDOWN = "delete"
