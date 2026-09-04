# hpc-bridge — user guide

hpc-bridge lets Claude Code work on a supercomputer for you. You ask in plain language; the agent
finds the facility, logs you in to Globus from the terminal, brings up a compute block (asking before
it spends anything), runs your commands on a node, and releases the block when you are done.

| Page | Read it when |
|---|---|
| [Install](install.md) | you are setting it up: the two commands, prerequisites, updating |
| [Quickstart](quickstart.md) | your first session, step by step, and what to expect |
| [Facilities](facilities.md) | choosing a machine: what each facility needs from you |
| [The Globus login](login.md) | the one credential hpc-bridge needs, and why your browser opens |
| [Costs and stopping](costs-and-stopping.md) | how spend is gated, what "stop" means, long-running work |
| [Troubleshooting](troubleshooting.md) | what a refusal or a stuck state means and what to do |

**Three ways a facility is reached**, decided for you from the registry: a facility-run endpoint (zero SSH;
you need an account there with your Globus identity mapped), an SSH bootstrap (an account plus key-based SSH
to the login node), or your own un-catalogued cluster (the same, plus a minute to confirm what the agent
discovers). [Facilities](facilities.md) has the table and the registered machines.

**What it is not, yet:** it moves command output, not files (use Globus Transfer or `scp` for results);
Slurm and PBS are the supported schedulers; it is interactive, though you can submit batch jobs from the login
node through it.

The developer documentation (design, internals, the tool reference) lives in the
[vault](../hpc-bridge-vault/Home.md).
