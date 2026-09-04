# The Globus login

hpc-bridge needs exactly one credential: a Globus login on the machine where Claude Code runs. It
covers everything, from submitting work to a facility-run endpoint to standing up and reusing your own,
and it is stored once.

## What happens

The first time a facility needs it, the agent starts the login and a browser window opens on Globus.
Sign in, approve the consent, and the page tells you that you can return to the terminal. The agent
waits up to about 90 seconds for that and then continues on its own. If your browser already has a
Globus session and has approved hpc-bridge before, the whole thing takes a few seconds.

If you are slower, the agent shows you the link and asks you to tell it when you are done. The link
is single-use and valid for ten minutes: once the page says you can return, do not open it again, it
will fail harmlessly. If it lapses, the agent issues a fresh one.

## Why you may be sent to your institution

Globus is a federation broker. When a facility requires a recent login with a particular identity,
Globus redirects you to that identity's provider, your university, ACCESS, and back. hpc-bridge asks
for nothing extra; those redirects are the facility's policy, enforced by Globus.

## What you are consenting to

The minimum the Globus Compute software itself asks for: permission to submit compute tasks under
your identity, plus your Globus identity information. hpc-bridge does not ask for file transfer,
search, or any other Globus service.

## Paste mode

If there is no browser on the machine running Claude Code, an SSH session to a server for instance,
the agent shows a link instead. Open it anywhere, sign in, and Globus shows a one-time code. Paste
that code back into the conversation; the agent completes the login with it. The code is not a
password and expires in minutes.

## What the agent never does

It never asks for a Globus password, an SSH password, or a multi-factor code, and it never types a
login link into a shell. If an agent ever asks you for a password, do not give it one and report it.

## Where the login lives, and logging out

Tokens are stored in Globus Compute's standard token store, `~/.globus_compute/storage.db`, and
refreshed automatically, so later sessions never prompt. To switch Globus identity, log out at
app.globus.org in your browser and ask the agent to force a fresh Globus login. Deleting the token
store logs you out entirely.

If you quit Claude Code in the middle of a login, the listener waiting for your browser goes with it.
A login that had already completed is unaffected.
