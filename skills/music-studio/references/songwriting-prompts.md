# Caption and lyric craft

The model gets a tag line and a lyric sheet. Everything else the user says has
to be folded into those two fields. These banks are prompts for your own
drafting, not a fixed grammar the engine requires.

## Caption vocabulary

Build the caption in this order and stop when you run out of useful detail —
80–200 characters is a good target, 512 is the hard ceiling.

| Slot | Examples |
|---|---|
| genre / sub-genre | synthwave, dream pop, boom bap, country soul, garage rock, ambient, lo-fi hip hop, chamber folk, drum and bass |
| mood | wistful, defiant, euphoric, nocturnal, tender, menacing, bittersweet, playful |
| lead instrument | shimmering arpeggios, fingerpicked acoustic guitar, Rhodes piano, distorted power chords, muted trumpet, 808 bass |
| rhythm section | gated drums, brushed kit, trap hi-hats, marching snare, half-time groove, four-on-the-floor |
| vocal | breathy female vocal, raspy male tenor, layered choir harmonies, deadpan spoken vocal, double-tracked lead |
| tempo | 72 BPM, 100 BPM, 128 BPM (or slow / mid-tempo / driving) |
| production | 1980s analog, tape saturation, wide reverb, dry close-mic, wall-of-sound, radio-polished |

Rules of thumb:

- Front-load the three descriptors that would ruin the take if missing.
- Use nouns, not sentences. `gated drums` renders; `drums that sound like the
  80s` mostly does not.
- One vocal description, not three. Conflicting vocal tags produce mush.
- Don't spend caption space on the story; that is the lyrics' job.

## Lyric patterns

- Section tags on their own line: `[verse]`, `[chorus]`, `[bridge]`, `[outro]`.
- The hook is the first line of the chorus and the user's exact words if they
  gave you any.
- Concrete images beat abstractions: "coffee going cold on the dashboard" not
  "I feel lonely".
- One idea per line; end lines on a strong syllable where you can.
- Length heuristic: roughly one sung line per 3–4 seconds. 30 s ≈ 8 lines,
  45 s ≈ 12, 60 s ≈ 16. Past ~60 s repeat sections instead of adding lines;
  overlong lyrics get truncated or rushed.
- `vocal_language` supports `en es fr de it pt ja ko zh`, plus `auto` to let
  the model decide. Set it in the draft rather than promising a language the
  draft does not ask for.

## Worked example — 30 s synthwave

User: "something dreamy about driving home at 2am, kind of in love."

```json
{"title": "Night Drive",
 "duration_seconds": 30,
 "caption": "dreamy synthwave, 1980s analog, wistful, shimmering arpeggios, gated drums, breathy female vocal, 100 BPM, wide reverb",
 "lyrics": "[verse]\nStreetlights smear across the glass\nRadio low, the city hums\nYour hand asleep on the dash\nCounting exits one by one\n\n[chorus]\nTake the long way home tonight\nI don't want the morning yet\nEvery red light, every sign\nSays stay a little longer"}
```

Eight lines for 30 seconds: verse sets the scene, chorus carries the hook the
user actually asked for.

## Worked example — 45 s fingerpicked folk

User: "a quiet song about my grandfather's workshop, sad but warm."

```json
{"title": "The Workshop",
 "duration_seconds": 45,
 "caption": "chamber folk, warm, tender, fingerpicked acoustic guitar, upright bass, brushed kit, close-mic male vocal, 72 BPM, dry natural room",
 "lyrics": "[verse]\nSawdust in the corners still\nA coffee cup he never filled\nA tape measure curled like a sleeping thing\nHis name in pencil on the sill\n\n[chorus]\nThe door still opens with his weight\nAnd the light still finds the bench\nI keep the tools he wouldn't lend\nAnd I keep the door unlocked\n\n[outro]\nWarm, and a little out of tune"}
```

Twelve lines, closer to the 45-second budget, and the outro is deliberately
short so the ending has room to breathe.

## Worked example — 60 s boom bap

User: "confident comeback track, scratchy vinyl, my line is 'still here'."

```json
{"title": "Still Here",
 "duration_seconds": 60,
 "caption": "boom bap, confident, nostalgic, dusty vinyl crackle, chopped soul sample, 808 bass, tight snare, deadpan male vocal, 88 BPM, tape saturation",
 "lyrics": "[verse]\nThey wrote me off in a group chat, quiet\nI was in the basement cutting samples, trying\nCame back with a notebook full of bad ideas\nAnd one good hook that outlived the years\nEvery closed door taught me how to build one\nEvery long night taught me how to hold one\nNow the room gets quiet when the beat drops\nAnd the doubt in my head finally speaks up\n\n[chorus]\nStill here, still here\nTell them I'm still here\n\n[verse]\nSame crew, same couch, same cheap headphones\nDifferent year on the calendar, same throne\nStill here, still here\n\n[outro]\nStill here"}
```

Sixteen lines for 60 seconds. Note that the repeated hook is expensive — if the
take sounds rushed, cut verse lines before cutting the chorus.

## Revising instead of rewriting

When the user asks for a change:

- "more aggressive" → one or two caption tags, not a full rewrite.
- "the chorus is too wordy" → drop lines from the chorus only.
- "make it longer" → raise duration if ≤60, then add a section; both changes
  revoke approval, so say so.
- "try a different vibe" → new caption, keep the lyrics, new seed at generate.
