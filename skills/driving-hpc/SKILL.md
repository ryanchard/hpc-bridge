---
description: How to drive HPC well through hpc-bridge — you have a persistent per-session shell (relative paths work; call reset_session for a clean slate); redirect verbose output to a file and read it back in bounded chunks (10 MB result cap); a cold first call means nodes are being allocated.
---

# Driving HPC with hpc-bridge

- You have a **persistent session shell**: `cd` and relative paths carry across turns. Call `reset_session` for a clean slate.
- The endpoint may be **cold** on first use — `ensure_endpoint_up` reporting `provisioning` means nodes are being allocated; retry shortly.
- Results are capped at ~10 MB: for verbose commands, redirect to a file and read it back in chunks.
