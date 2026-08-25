# DS-helper

A bot that watches your Figma design system and checks it against what your
markup actually does. Figma is the source of truth; the code in your
repository is what gets verified. System Python 3, zero dependencies, CI out
of the box.

**→ [Quick integration guide](docs/QUICKSTART.md)** — copy, configure, accept
a baseline, get a check on every PR. Русская версия: [docs/QUICKSTART.ru.md](docs/QUICKSTART.ru.md).

**→ [examples/config-r4s.json](examples/config-r4s.json)** — a real project's
live configuration: componentMap, per-component state overrides, foreign
chrome excluded from the audit.

Inspired by Meta's Night Watch Figma Librarian, turned inside out: there the
code was the truth and the bot edited Figma; here the design system is the
truth and the bot checks the code. What survived is the discipline: checkpoint
before any edit, never publish the library, keep human exceptions instead of
overwriting them.

## What it does

- takes a DS snapshot from Figma: variables with all their modes, components
  with full variant matrices
- scans your markup and reports drift down to `file:line`
- tells value drift apart from "taken from another mode or accent"
- finds defects in the DS itself: matrix holes, duplicate names, typos,
  orphaned collections
- reviews DS changes between runs and builds a change-log card
- fails CI only on **new** findings — existing debt lives in an accepted
  baseline

## How it links code to Figma

Nothing is guessed — the link lives in the code:

```css
--color-text-primary: #04141f;   /* Color/Text/Default/Primary */

/* ---------- Button ---------- */
.btn { … }
```

The trailing comment names the Figma Variable; the section header names the
DS component. Code without comments is linked via `componentMap` and by value
matching. States are read from selectors (`:hover`,
`[aria-disabled="true"]`, …) — the mapping is `stateMap` in the config.

## Running

```bash
python3 bin/nw.py            # scan, compare, report
python3 bin/nw.py --accept   # accept current findings as the baseline
python3 bin/nw.py --fix      # mechanical fixes, checkpoint first
python3 bin/nw.py --promote  # current DS snapshot becomes the review base
```

Reports are English by default; `"lang": "ru"` in config.json switches
everything — reports and CLI — to Russian.

## Finding categories

| Code | Meaning |
|---|---|
| `TOKEN_VALUE_DRIFT` | token value drifted from the Figma Variable |
| `ACCENT_MISMATCH` | value is right, but belongs to another DS accent |
| `FOREIGN_VARIABLE` | token named after a foreign kit's variable |
| `TOKEN_UNKNOWN` | token references a name the DS does not have |
| `ORPHAN_TOKEN` | declared and never used |
| `RAW_VALUE` | hardcoded value where a token exists |
| `DEPRECATED_USE` | a DEPRECATED component is in use |
| `STATE_GAP` | the DS draws a state the CSS never covers |
| `MISSING_COMPONENT` | watchlisted DS component absent from the code |
| `DS_DEFECT` | defect in the design system itself |
| `NOT_CHECKED` | nothing to compare against — snapshot lacks the data |

## Honesty rules

The snapshot must mark what it does not know (`variablesComplete`,
`missingKnown`) — wording degrades to "verify manually" instead of false
alarms. A crash is never confused with findings: findings exit 1, a crash
exits 4 and its stderr is shown. A false finding costs more than a missed
one — `nw:ignore`, `outOfScope`, `tier: legacy` and per-component state
overrides all exist to keep the report worth reading.

## Boundaries

Never publishes the Figma library, never edits mockup files, makes no visual
judgements. CSS edits happen only on an explicit `--fix`, only after a
checkpoint was written, and never in `sources` marked as production — those
get pull requests.

## Layout

```
bin/            the engine (scan, diff, review, watch, webhook)
docs/           quickstart in two languages, Russian README
examples/       a real project's configuration
skill/          an agent role for taking DS snapshots via Figma MCP
relay/          Figma → GitHub webhook relay (Cloudflare Worker)
.github/        prototypes-check (push/PR) and ds-watch (schedule)
```
