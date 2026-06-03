#!/usr/bin/env bash
# Non-load-bearing backstop (spec §7/§10): deny obvious inbound credential-looking
# strings in a tool call. Pure-bash (no jq dependency) — greps the raw tool input.
input=$(cat)
if printf '%s' "$input" | grep -iqE '(password|api[_-]?key|secret|token)[[:space:]]*[=:]'; then
  printf '%s' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"Possible inline credential detected — use the hpc-bridge endpoint/broker instead of passing secrets in a command."}}'
fi
exit 0
