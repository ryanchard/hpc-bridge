from hpc_bridge.models import ShellOutcome, EndpointStatus


def test_endpoint_status_defaults_and_roundtrip():
    s = EndpointStatus(status="up", block_state="warm", endpoint_id="eid")
    assert s.session_spend == 0.0
    assert EndpointStatus.model_validate(s.model_dump()) == s


def test_shell_outcome_cold_path_fields():
    o = ShellOutcome(phase="cold_start", block_state="cold", task_handle="t1", est_wait_s=120)
    assert o.exit_code is None
    assert o.task_handle == "t1"
