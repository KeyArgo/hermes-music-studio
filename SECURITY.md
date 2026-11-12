# Security policy

## Supported versions

| Version | Supported |
|---|---|
| 1.0.x | Yes |
| Earlier versions | No |

## Reporting a vulnerability

Do not open a public issue for a security problem. Use GitHub's private vulnerability reporting on
this repository:

<https://github.com/KeyArgo/hermes-music-studio/security/advisories/new>

Include the version, the commit if you have it, what you did, what happened, and any logs or
diagnostics you are comfortable sharing. `Compute → Report a problem → Copy diagnostics` produces the
build facts without your library content.

You should get an acknowledgement through GitHub. Please give the maintainers time to investigate and
release a fix before public disclosure.

## Scope

The product binds its HTTP server to loopback and requires a loopback `Host`; it has no multi-user
authentication. Anyone who can reach the loopback API can approve and render. Findings that require
local shell access, a modified plugin install, or control of the configured engine endpoint are in
scope only where they cross a boundary the product claims to enforce.

The studio stores project JSON and WAV files under the plugin data directory you chose. It does not
send telemetry, and it does not change passwords or credentials. Network calls go only to endpoints
you configure (ComfyUI, an optional ACE remote server, REVamp on loopback, and the Hermes provider or
Hugging Face when you press the corresponding button).

Reports about model output quality, the ACE-Step or MiniMax models themselves, ComfyUI, ffmpeg or the
Hermes host should go to those projects.
