---
name: music-studio
description: "Interview a song idea, draft lyrics and style, then render."
version: 1.0.0
author: Music Studio
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [Music, Songwriting, ACE-Step, Local-Generation, Human-Approval]
    related_skills: [songwriting-and-ai-music]
---

# Music Studio: interview → draft → approve → render

The `music_studio` tool stores a song draft and renders it on the engine the
draft pins: ACE-Step 1.5 on the local GPU (default) or MiniMax Music 3 through
a ComfyUI server. Exactly two text fields reach the model: `caption` (the
style prompt, ≤512 characters) and `lyrics` (≤4096 characters), plus
`duration_seconds`, `seed` and the musical metadata (`instrumental`,
`vocal_language`, `bpm`, `keyscale`). The `idea` field is stored for the record
but is **never sent to the engine**. Your job is therefore to turn a one-line
idea into a caption and lyrics the model can actually use, with the user's
input, and then stop at the approval gate.

## When to use

- The user wants a song, a clip, a jingle, a loop, or background music made
  ("make me a 30-second synthwave track about night driving").
- The user wants to revise the lyrics, style, duration, engine, or title of an
  existing Music Studio project.
- The user asks what the studio is doing, or why a take failed.

Don't use it for audio editing, mixing, stem separation, or sample libraries —
none of that exists in 1.0.0.

## The pipeline: from a sentence in chat to a file they can play

Two clients drive the same studio, and both are safe to use from any chat:

| Client | Who uses it | Shape |
|---|---|---|
| `music_studio` tool | any Hermes session with this plugin enabled | JSON actions: `status`, `doctor`, `create`, `update`, `change`, `keep`, `snippet`, `snippet_splice`, `check_take`, `checkpoint`, `checkpoints`, `restore`, `approve`, `render`, `queue`, `takes`, `export`, `wait` |
`change` takes plain words (`{project_id, instruction}`) and rewrites the draft: it is deterministic, it
refuses what the engines cannot do (mute an instrument, subtract vocals) with a sentence, and it journals
the request on the project. `keep` records the friendly take without revoking approval.

**In a chat, `/song` is the short path:** `/song` lists songs, `/song go <id>` shows the draft and
queues nothing, `/song go <id> now` approves that version and queues one take, `/song takes <id>`,
`/song play <id>`, `/song export <id>`. The first `go` is the gate; the second is the commitment.

| `scripts/ms.py` | shells, cron jobs, agents that are not Hermes | the same actions over the loopback host: `python3 scripts/ms.py render --project <id>` |

The loop that works, end to end (each step answers JSON):

```text
status                                    # projects, engine states, what is running
create  {draft:{title, caption, lyrics, duration_seconds, instrumental, engine}}
   -> read back project_id and updated_at  # updated_at is the version token
approve {project_id, updated_at}          # only after the person said yes in chat
render  {project_id, timeout_s:240}       # queues the worker and waits; reports the take state
queue   {project_id}                      # queues and returns at once — use it to keep the chat free
takes   {project_id}                       # ids, states, seeds, errors
export  {project_id}                       # writes a normal .wav, returns its path
```

- `render` returns the take's final state, not a job id, so a chat can answer with a file. It waits
  up to `timeout_s` (max 240); a long ACE take may still be `running`, and the right move is `wait`,
  never a second `render` (one render at a time per profile).
- `export` picks the newest finished take when no `take_id` is given and refuses when nothing has
  rendered. The library keeps its own copy; export is a one-way copy into a normal folder.
- `ms.py` finds the host from `studio-host.json` (or `--base`). Exit codes: `0` done, `1` the studio
  said no (the JSON carries the sentence), `2` no host answered.
- A failed call is always `{"success": false, "error": "..."}` — quote that sentence to the person
  instead of paraphrasing it, and never retry a render on an engine error until `doctor` is clean.

## Non-negotiables

1. **Interview before drafting.** Never create a project straight from a bare
   idea and render it. One round of questions minimum (see step 1).
2. **Propose before generating.** Show the caption and lyrics in chat and get a
   yes or edits. A draft can be approved from the chat (`approve`) *only* after
   the person has said yes to the text you just showed; the dashboard works too
   and is where they can see it next to the audio.
3. **Never claim approval.** `approve` needs the `updated_at` token of the draft
   the person actually read, and `render` on an unapproved draft returns
   `draft requires approval`. That is the product's trust story: the human, not
   the agent, decides when GPU time is spent. Calling `approve` on your own
   initiative, without a yes in the conversation, is the one thing that breaks it.
4. **Hard limits:** duration 10–180 seconds on the default ACE engine and
   10–360 seconds on MiniMax Music 3 — a draft pinned to MiniMax may exceed
   180; `caption` non-blank and ≤512 characters; `lyrics` non-blank and ≤4096
   characters unless `instrumental` is true. `engine` is `"ace"` (the default,
   ACE-Step locally) or `"minimax_m3_comfy"`; `vocal_language` is one of
   `en es fr de it pt ja ko zh auto` (default `en`). Repaint and extend exist
   only on ACE — MiniMax refuses them with a 409, so do not offer those edits
   for a MiniMax take.
5. **One render at a time per profile.** A second `generate` returns
   `GPU busy`.
6. **Edits revoke approval.** `update` unsets `approved` whenever a value
   actually changes. Verify with `status` before rendering again.
7. **A MiniMax take carries a licence obligation.** Its `take.metadata` holds
   `licence`, `model_licence`, `licence_url` and `licence_notice`; report the
   notice and link to the user when you report the take. Do not strip or
   paraphrase it away.

## Procedure

### 1. Interview in one message

Ask all of it at once, with defaults, so "you pick" is an acceptable answer.
Do not drip questions one per turn.

> 1. **Genre and mood** — e.g. "dreamy synthwave, wistful", "dirty garage
>    rock, defiant". Named references are fine; translate them into
>    descriptors for the caption.
> 2. **What it's about** — one sentence, plus the hook line or phrase you want
>    repeated.
> 3. **Voice** — lead vocal type (e.g. breathy female alto, raspy male tenor),
>    whether a choir/harmony layer is wanted, and the language. Instrumental is
>    an option (`instrumental: true`, no lyrics required).
> 4. **Shape and length** — default: one verse + one chorus, 30 seconds.
>    Anything from 10 to 180 seconds on the default ACE engine; up to 360
>    seconds if you pin the draft to MiniMax. Longer takes render on MiniMax
>    only if that engine is configured.
> 5. **Engine** — default ACE-Step (local GPU, can repaint/extend); MiniMax
>    Music 3 (ComfyUI, better vocals on the measured take, but no repaint,
>    no extend, and its output carries a licence notice). If the user has no
>    preference, use ACE.
> 6. **Anything to avoid** — words, clichés, instruments, tempo changes.
> 7. **Title** — optional; I'll derive one from the hook.

Defaults if the user declines to choose: 30 seconds, verse + chorus, mid-tempo,
one clear lead vocal, English, ACE-Step. State the defaults you chose.

### 2. Compile the caption

A caption is a comma-separated tag line, not prose. Order matters — put the
three most defining descriptors first.

```
genre, sub-genre, mood, lead instrument, rhythm section, vocal type,
tempo/BPM, production era or character
```

Good: `dreamy synthwave, 1980s analog, wistful, shimmering arpeggios, gated
drums, breathy female vocal, 100 BPM, wide reverb`

Bad: `a song about driving at night that sounds nostalgic and cool` (prose, no
instrumentation, wastes the budget), and `female vocalist` alone (nothing to
render).

Keep it under 512 characters — aim for 80–200. Include the mood words the user
gave you; drop the story, which belongs in the lyrics.

### 3. Compile the lyrics

- Use section tags on their own lines: `[verse]`, `[chorus]`, `[bridge]`,
  `[outro]`.
- One image per line. Short lines sing better than long ones.
- The user's hook phrase must appear verbatim in the chorus, first line.
- Match length to duration. A useful heuristic, not a rule: about one sung
  line per 3–4 seconds. That gives roughly 8 lines for 30 seconds and 16 for
  60. Past ~60 seconds repeat sections instead of writing dozens of new lines;
  a 30-second take with 40 lines will be cut off or rushed.
- Write the `idea` into the lyrics deliberately — it is not sent to the model,
  so anything the user said about the song only exists in the draft if you put
  it in the caption or lyrics.

Shape for the default 30 seconds:

```
[verse]
four lines of setup and image

[chorus]
four lines, hook first
```

### 4. Propose in chat

Present, in this order: title, duration, the caption, the lyrics block, and one
sentence on what you chose and why. Then ask for edits or approval.

Do not call `generate` yet, and do not call `create` until the user agrees to
the text (creating early is fine only when the user asked you to save a draft).

### 5. Create the draft

```json
{"action": "create",
 "draft": {"title": "Night Drive", "idea": "driving home at 2am, half in love",
           "caption": "dreamy synthwave, 1980s analog, wistful, shimmering arpeggios, gated drums, breathy female vocal, 100 BPM, wide reverb",
           "lyrics": "[verse]\nStreetlights smear across the glass\n...\n[chorus]\n...",
           "engine": "ace", "duration_seconds": 30}}
```

`engine` pins where this draft renders: `"ace"` (default, ACE-Step locally,
10–180 s, repaint/extend available) or `"minimax_m3_comfy"` (ComfyUI,
10–360 s, no repaint or extend, output carries a licence notice). The engine
stays pinned through approval, so a take cannot switch backend behind the
user's back; change it with `update` (which revokes approval) if they want a
different one. The chat tool has no separate engine argument for `generate` —
the draft's `engine` is the choice.

The response is `{"success": true, "project": {...}}` with a `project_id`
(canonical UUID), `approved: false`, and an empty `takes` list. Quote the id
when you refer to the project; never invent one.

### 6. Revise

```json
{"action": "update", "project_id": "<uuid>",
 "draft": {"lyrics": "[verse]\n..." }}
```

Send only the fields that change. Say plainly that the edit revoked approval
and that the dashboard will show the draft as unapproved again.

### 7. Wait for approval, then generate

Read state with `{"action": "status"}` — it returns `engine_available` and
every project with its `approved` flag and takes. Only call `generate` when
`approved` is `true`.

```json
{"action": "generate", "project_id": "<uuid>", "seed": 18347291}
```

`seed` must be an integer in `0 … 4294967295` and is **required** — omitting it
returns `invalid seed`. Draw a fresh random seed per take, and tell the user
the seed: it is their replay handle for that take on this machine.

The call returns immediately with a `queued` take. The render continues in a
detached worker even if the chat ends. A 30-second take typically takes 1–3
minutes on a local GPU. Poll `status` no more often than every 10–15 seconds;
tell the user they can leave. While the take is `running`, `status` carries a
coarse `progress` percentage and an `eta_seconds` estimate — report both as
estimates, never as engine step counts.

### 8. Report the take

Take `state` is `queued` → `running` → `succeeded` or `failed`. When `status`
shows `succeeded`, the take is already settled: `qc` (measurements) and, when
the render needed a tail trim or peak cap, `playable_edit_id` plus `edits[]`
were committed with the success, so there is nothing to wait for. Report the
take number, seed, duration and, for a MiniMax take, the licence notice from
`take.metadata`. Send the user to the Music Studio tab, which streams the take.
If the *desktop* panel refuses the audio as over 8 MiB (about 47.5 seconds at
44.1 kHz/16-bit stereo), say that it is a panel limit, not a failed render, and
point them at the dashboard tab or the `/download` route.

## Reading failures

| What you see | What it means | What to do |
|---|---|---|
| `draft requires approval` | The draft is unapproved (new, or edited). | Ask the user to approve it in the Music Studio tab, then re-read `status`. Do not retry blindly. |
| `GPU busy` | Another take in this profile is `queued` or `running`. | Poll `status`; one render at a time. If the take has been stuck for minutes, check whether its worker is still alive (see next row). |
| `GPU busy: another render owns the worker lease` | A newly spawned worker found another render holding the lease and aborted its own take. | Nothing is stuck. Wait for the listed live take to finish, then retry. |
| take `failed`, error `worker died` | The detached worker exited without finishing. The take was already reconciled and the GPU lease released. | Report it and offer to retry with a new seed. If it repeats, the ACE runtime is the suspect — point the user at the README troubleshooting section. |
| take `failed`, error contains `ACE worker failed` or a Python traceback | The ACE runtime itself failed; the tail of its log is in the error. | Show the last lines. Common causes: missing checkpoints, no CUDA device, another GPU-hungry process. |
| take `failed`, error `ACE worker timed out` | The render exceeded 900 seconds. | Retry once; a shorter duration helps. |
| `worker could not start` | The plugin could not spawn its worker (bad interpreter/PYTHONPATH). | Ask the user to restart the gateway. |
| `engine_available: false` in `status` | No engine can render: the ACE runtime/checkpoints/source are missing and no MiniMax ComfyUI is reachable. | Read `engines[]` for each engine's own `state`/detail. Explain the ACE paths and `ACE_RUNTIME_DIR` / `ACE_SOURCE_DIR`, or the ComfyUI `COMFYUI_URL`; drafting still works, rendering does not. |
| `… does not support repaint` / `… does not support extend` (409) | The take's engine is MiniMax Music 3, whose ComfyUI graph has no repaint/continuation nodes. | Do not retry. Offer a fresh generate on MiniMax, or a new ACE draft if range edits matter. |
| `duration must be between 10 and 180 seconds` | The draft is pinned to ACE (or names no engine) and asked for more than ACE's measured ceiling. | Shorten it, or pin `engine: "minimax_m3_comfy"` if it is configured (10–360 s). |
| `duration must be between 10 and 360 seconds` | The draft is pinned to MiniMax and asked for more than 360 s. | Shorten it; that is the model's own cap. |
| `project not found` / `invalid UUID` | Wrong or stale project id. | Re-read `status` and use the id from there. |

## Anti-patterns

- Rendering "a few options to compare" without being asked — one at a time,
  and each one costs minutes of GPU time.
- Rewriting a whole draft when the user asked for one line.
- Padding lyrics to 4096 characters for a 15-second clip.
- Calling `create` again instead of `update` when the user asks for a tweak;
  it orphans the approved draft and its takes.
- Saying "I've approved it" or "rendering now" before approval actually
  exists in `status`.
- Putting the user's story sentence in the caption and nothing in the lyrics.

## References

- `references/tool-reference.md` — exact fields, limits, error strings, take
  lifecycle.
- `references/songwriting-prompts.md` — caption vocabulary, lyric patterns, and
  three worked examples at 30, 45 and 60 seconds.
