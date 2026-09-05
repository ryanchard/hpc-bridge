#!/usr/bin/env bash
# `site` profile, login nodes: a fake `mybalance` in Anvil's format (the plugin's discovery looks for it on PATH and
# its parser reads exactly this shape), and per-node host names are already set by compose.
set -u
cat > /usr/local/bin/mybalance <<'MB'
#!/usr/bin/env bash
# fake Anvil-style balance tool: two allocations for every pool user (CPU on hpcb, GPU on hpcb-gpu)
cat <<TBL
Allocation     Type    SU Limit    SU Usage   SU Usage  SU Balance
Account                           (account)     (user)
=============  ====  ==========  ========== ==========  ==========
hpcb            CPU     10000.0       412.5       38.0      9587.5
hpcb-gpu        GPU       500.0        12.0        0.0       488.0
TBL
MB
chmod 755 /usr/local/bin/mybalance
echo "[site] mybalance installed"
