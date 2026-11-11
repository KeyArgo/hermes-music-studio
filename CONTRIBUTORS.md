# Contributors

Music Studio is built and maintained by [InovinLabs](https://github.com/inovinlabs), with the
agent work credited below. The product, its direction and every decision about what ships belong
to the maintainers; the tools listed here wrote and checked code under their review.

## People

- **The owner of InovinLabs** — product direction, the approval gate, the honesty rules (a control
  that fails must say so), and the final word on every release.

## Agent collaborators

These models worked on this codebase during development, each on named tasks, with their output
reviewed and verified before it was merged:

| Model | Worked on |
|---|---|
| Claude (Anthropic) | The public-readiness audit that produced the "not ready" verdict and the silent-failure findings, the loopback studio host, the desktop pane |
| Codex (OpenAI) | The publish-blocker audit, engine and settings plumbing, static-analysis remediation |
| DeepSeek-V4.1-Flash | Refactors, test scaffolding and bug sweeps on the DGX Spark lanes |
| Qwen 3.8 | Test scaffolding and small fixes on the DGX Spark lanes |
| GLM-5.3-Flash | Bug sweeps on the DGX Spark lanes |

Every claim in this repository was reproduced by a human or by an orchestrating agent before it
was accepted: no model's self-report is treated as evidence.

## Media

- `assets/demo/afterglow.wav` is a real render produced by this studio; its seed and settings are
  recorded in `demo-take.json` beside it.
- The demo is instrumental. No third-party samples or vocals are bundled.
