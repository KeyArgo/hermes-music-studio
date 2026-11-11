# ARGOLABS — house style

**ArgoLabs** is the studio name. A plugin from here is titled `Hermes <Product>` and its manifest
`author:` field is `ArgoLabs`. The repo while a product is under construction is
`<product>-dev` (`hermes-music-studio-dev`); the public repo drops the suffix. Display names may
differ from plugin ids, and **the id wins for compatibility** — never rename an id for aesthetics.

> History: earlier drafts of this repo used the working name "Hewn" (and "Lathe" for this product).
> Both are retired. Internal identifiers that still carry the old name — `HEWN_STUDIO_DATA_DIR`, the
> `hewn-music-studio` data directory in standalone mode — are kept on purpose: renaming them would
> orphan every existing library. They are plumbing, not branding.

The point of a house style is not decoration. In a plugin directory every card is a title and a
sentence, so a tested local-first tool looks exactly like a weekend demo. The fix is one idea:
**we only display what a user can check.**

## The covenant

A plugin may show a badge only when the check for it passes in CI on the release being installed,
and every badge links to the release's evidence page. A claim that cannot be checked is deleted,
not softened.

| Badge on the card | What the user sees | How they verify it |
|---|---|---|
| `MIT` | "MIT, read every line" | `LICENSE` present and an SPDX field in the manifest; CI fails if they disagree. Models and dependencies with other licences are named in `THIRD-PARTY.md`. |
| `OFFLINE` | "Core workflow runs with the network unplugged" | `unshare -rn make covenant-offline` — the core suite must pass inside a network namespace. Any optional cloud path is disclosed separately and is never the default. |
| `NO TELEMETRY` | "Sends nothing home" | The offline run plus a dependency scan; `NETWORK.md` lists every host the plugin can ever contact, or states "none". |
| `LOCAL DATA` | "Your files stay in one folder" | Manifest declares the data directory; CI diffs the filesystem and fails on writes outside it (temp excluded). |
| `TESTED` | "Tested on <hardware>, <month>" | `VERIFY.md` records hardware, driver, commit, date, commands and raw log. Expires after 90 days or at the next release, whichever is first. |
| `LIMITS` | "Limits are written down" | `LIMITS.md` names unsupported cases, known failures and requirements. A limit found by testing is written there before release, not after a bug report. |

Badge inflation is the failure mode to fear: one unearned badge makes every other card suspect.
If a check cannot run in CI, the badge does not exist.

## Visual system

Grounded in the Hermes app itself, so a plugin card looks like it belongs to the app the user
already has open. ArgoLabs uses the Nous visual language.

| Token | Value | Use |
|---|---|---|
| `ink` | `#0A0C11` | banner/background base |
| `surface` | `#10141B` | panels, cards |
| `rule` | `#24303F` | hairline dividers, grid |
| `text` | `#F2F2F2` | primary type |
| `muted` | `#8FA0B6` | secondary type, metadata |
| `accent` | `#0000F2` | Nous electric blue — the only accent. Never two. |

- **Type:** UI sans for names and claims (`Inter`, fall back to system). Monospace for metadata,
  ids, test counts and anything numeric. No third family.
- **Mark:** `assets/brand/stamp.svg` — the ArgoLabs seal (circle + wing). Monochrome fallback: all
  strokes `currentColor`. No gradients beyond the stamp's own wing, no swooshes.
- **Banner:** 1200×600, rendered from `assets/brand/banner.html` at 2× so text stays crisp.
  Structure: accent hairline on top, stamp and wordmark left, three short claims right,
  signature bottom-left. Claims are specific and countable ("305 tests · 3 engines"), never
  adjectives ("powerful", "seamless").
- **Cards:** the real screenshot, not a mock. If the product has no screenshot yet, the card shows
  the banner only — an invented UI is a lie about readiness.
- **Family resemblance:** fixed stamp, palette, type, banner grid and footer line. Each product
  varies only in its wordmark and its claims.

## Naming

Product second, id stable: **Hermes Music Studio**, author `ArgoLabs`. Repo
`hermes-music-studio-dev` while private, `hermes-music-studio` at release. Plugin id
`hermes-music-studio` — unchanged, because ids carry saved state and other people's installs.

## What we do not do

- No badge without a command that can fail.
- No "AI-powered", no "revolutionary", no adjectives in place of numbers.
- No fake screenshots, no mock audio, no animated showcase of a feature that is not wired.
- No second accent colour, no gradient identity, no logo that needs a paragraph to explain.
- No renaming a plugin id for aesthetics — it breaks saved state and other people's installs.
