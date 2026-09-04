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

## Facilities that ask for a one-time code at SSH login

Some facilities (SDSC Expanse, TACC) require a code from your authenticator at every SSH login, even with a
key installed. When hpc-bridge meets that prompt it asks you for the *current* code and opens the shared
connection itself; the code is single-use and expires in seconds, which is why it may pass through the
chat, exactly like the Globus one-time code above. A **password** is different and never goes through the
chat: if a facility asks for one, hpc-bridge refuses and gives you an `ssh` command to run in your own
terminal instead, after which the session continues over that connection.

## Where the login lives, and logging out

Tokens are stored in Globus Compute's standard token store, `~/.globus_compute/storage.db`, and
refreshed automatically, so later sessions never prompt. When the agent tells you the login landed it
names the identity it landed as; if that is not you, say so before anything else happens: the login
link is single-use and whoever completes it first becomes the identity, so keep it to yourself.

On an SSH-bootstrap facility a trimmed copy of this token store is also placed in your home directory
on the login node (mode 600, readable only by you), because the endpoint running there registers with
Globus as you. Asking the agent for a *teardown* removes that copy along with the endpoint; deleting
the local store alone does not. To switch Globus identity, log out at
app.globus.org in your browser and ask the agent to force a fresh Globus login. Deleting the local token
store logs this machine out; any copy on a login node stays until you tear that endpoint down.

If you quit Claude Code in the middle of a login, the listener waiting for your browser goes with it.
A login that had already completed is unaffected.
