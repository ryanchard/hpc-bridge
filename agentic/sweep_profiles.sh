#!/usr/bin/env bash
# The PROFILE SWEEP: every fake-cluster profile, brought up clean (--reset-cluster), with the scenarios that belong to
# it — the regression baseline across cluster shapes. Sequential (one cluster at a time); each profile's cells run
# through run_suite with the usual node gate. Results land in agentic/runs/ (bundles) and a per-sweep summary file.
#
#   agentic/sweep_profiles.sh [--models claude-opus-5] [--profiles default,site,...]
#
# The agent model defaults to run_suite's DEFAULT_MODEL (claude-opus-5); the human-sim is Haiku. Never Fable unless
# asked. `mep` is skipped unless HPCB_MEP_EMAIL is set in agentic/.env (its managers register a contact address).
# globus1-only scenarios (catalogued MEP ids, Aurora, the 25-min saturation sleepers, the 30-min long job, the
# 11-min fail2ban cooldown of no_ssh_access — covered by f2b_stranger) are not part of the fake sweep.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODELS="claude-opus-5"; PROFILES="default,site,totp,pbs,lmod,f2b,polaris,internal,mep"
while [ $# -gt 0 ]; do case "$1" in --models) MODELS="$2"; shift 2;; --profiles) PROFILES="$2"; shift 2;; *) echo "unknown arg $1"; exit 2;; esac; done
[ -f "$HERE/agentic/.env" ] && while IFS= read -r line || [ -n "$line" ]; do case "$line" in ''|\#*) continue ;; esac; k="${line%%=*}"; [ -z "${!k+x}" ] && export "$line"; done < "$HERE/agentic/.env"
STAMP="$(date +%Y%m%d-%H%M)"
SUMMARY="$HERE/agentic/runs/sweep-$STAMP.md"; mkdir -p "$HERE/agentic/runs"
echo "# profile sweep $STAMP — target fake, models $MODELS" > "$SUMMARY"

cells() {  # cells <profile> -> the scenario list (and concurrency) for that profile
  case "$1" in
    default)  echo "happy_path,gated_provision,long_task_via_handle,endpoint_reuse,endpoint_reuse_chain,facility_cache,session_persistence,byo_teardown_clean,spend_refusal,unknown_host_key,zero_config_list,orphaned_task,draining_restop,stop_while_running 2" ;;
    site)     echo "rich_gate,partition_choice,gpu_rule,submit_policy_rejected,login_pin_teardown,slurm_worker_died,gated_provision,happy_path 3" ;;
    totp)     echo "otp_preauth 1" ;;
    pbs)      echo "happy_path,gated_provision 2" ;;
    lmod)     echo "lmod_bootstrap 1" ;;
    f2b)      echo "f2b_stranger,f2b_banned 1" ;;
    polaris)  echo "polaris_filesystems 1" ;;
    internal) echo "internal_hostnames 1" ;;
    mep)      echo "fake_mep_compute,fake_mep_no_account 1" ;;
    *) echo "" ;;
  esac
}

IFS=, read -ra PROFS <<< "$PROFILES"
for prof in "${PROFS[@]}"; do
  read -r scen conc <<< "$(cells "$prof")"
  [ -n "$scen" ] || { echo "sweep: unknown profile $prof"; continue; }
  if [ "$prof" = mep ] && [ -z "${HPCB_MEP_EMAIL:-}" ]; then
    echo "## $prof — SKIPPED (HPCB_MEP_EMAIL unset in agentic/.env)" | tee -a "$SUMMARY"; continue
  fi
  echo "## $prof — $(date +%H:%M) — $scen" | tee -a "$SUMMARY"
  python3 "$HERE/agentic/run_suite.py" --target fake --profile "$prof" --reset-cluster --models "$MODELS" \
      --scenarios "$scen" --concurrency "$conc" 2>&1 | tee "$HERE/agentic/runs/sweep-$STAMP-$prof.log" \
      | grep -E "done |skip |SUITE|passed" | sed 's/^/    /' | tee -a "$SUMMARY"
  if [ "$prof" = default ]; then
    # the server-side spend floor needs the skill WITHHELD (the agent follows the literal order) — run_smoke.sh honours HPCB_NO_SKILL
    echo "    spend_gate_enforced (no-skill ablation):" | tee -a "$SUMMARY"
    HPCB_TARGET=fake HPCB_FAKE_PROFILE=default HPCB_SKIP_BUILD=1 HPCB_NO_SKILL=1 HPCB_MODEL="$MODELS" \
      "$HERE/agentic/run_smoke.sh" spend_gate_enforced 2>&1 | tee "$HERE/agentic/runs/sweep-$STAMP-default-spend_gate.log" \
      | grep -E "RESULT:" | tail -1 | sed 's/^/    spend_gate_enforced — /' | tee -a "$SUMMARY"
  fi
done
echo "## done — $(date +%H:%M)" | tee -a "$SUMMARY"
echo "$SUMMARY"
