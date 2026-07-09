from hpc_bridge.models import ShellOutcome, EndpointStatus


def test_endpoint_status_defaults_and_roundtrip():
    s = EndpointStatus(status="up", block_state="warm", endpoint_id="eid")
    assert s.session_spend == 0.0
    assert EndpointStatus.model_validate(s.model_dump()) == s


def test_shell_outcome_cold_path_fields():
    o = ShellOutcome(phase="cold_start", block_state="cold", est_wait_s=120)
    assert o.exit_code is None
    assert o.est_wait_s == 120


def test_facility_details_accepts_pbs_scheduler():
    from hpc_bridge.models import FacilityDetails
    d = FacilityDetails(
        ssh_host="polaris", interface="hsn0",
        env_setup="source {venv}/bin/activate",
        scratch_root="/home/{user}/.hpc-bridge", partition="debug",
        scheduler="pbs",
    )
    assert d.scheduler == "pbs"
