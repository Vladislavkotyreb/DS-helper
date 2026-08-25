# Quick integration into your repository

**English** · [Русский](QUICKSTART.ru.md)

The bot checks your markup against a Figma design system and fails CI only when
**new** drift appears. System Python 3, zero dependencies.

Everything below covers the "mockups and bot in one repository" mode: CI on
every push, nothing to keep running. The other mode — bot next to a folder on
disk — differs only in `prototypesRoot`.

## Step 1. Copy the bot

From this repository into yours:

```
bin/                                     the whole engine
.github/workflows/prototypes-check.yml   check on every push and PR
config.example.json                      configuration template
```

Optional: `.github/workflows/ds-watch.yml` (scheduled Figma polling),
`skill/` (a role for an agent with Figma MCP).

## Step 2. Configuration

```bash
cp config.example.json config.json
```

The minimum you must fill in:

- `figma.designSystemFileKey` — from the DS file URL: `figma.com/design/<THIS>/…`
- `prototypesRoot` — the folder with your markup, usually `"prototypes"`
- `prototypes[]` — one entry per mockup folder

Everything else has working defaults; every key carries a `*Note` field
explaining it. A fully configured real project lives in
`examples/config-r4s.json`.

Reports are English by default; set `"lang": "ru"` for Russian. Switching the
language later renames a few finding subjects — re-run `--accept` afterwards.

In a private repository `config.json` is committed. In a public one it is not
(Figma file keys) — pass it via the `NW_CONFIG` secret instead.

## Step 3. Linking code to the design system

The bot guesses nothing — it reads the link you keep in the code itself.
This is the one real convention; the rest is machinery.

**Tokens.** A trailing comment on a CSS variable names the Figma Variable:

```css
--color-text-primary: #04141f;   /* Color/Text/Default/Primary */
```

Values are compared through it: drift from Figma becomes a `TOKEN_VALUE_DRIFT`
finding.

**Components.** A CSS section header names the DS component:

```css
/* ---------- Button ---------- */
.btn { … }
```

State coverage is checked through it: if Figma draws `State=Disabled` and the
section has no disabled selector — that is a `STATE_GAP`.

**Existing code without comments** is linked via `componentMap` in the config
(class → component) and by value matching: a raw `#04141f` next to a token
with the same value becomes a `RAW_VALUE` finding with a ready substitution.

## Step 4. The design-system snapshot

The only step that needs an agent with Figma MCP (Claude Code with the Figma
plugin): variable values with all their modes are Enterprise-only over REST,
but the Plugin API through MCP serves them on any plan.

Ask your agent to "take a design-system snapshot for night-watch" and hand it
`skill/` from this repository — the full recipe is in
`references/figma-pull.md`. The result is `snapshots/ds-latest.json`, and it
gets committed. CI never takes snapshots itself — it only compares against
one; refresh it when the library is published.

A snapshot must honestly mark what it does not know
(`variablesComplete: false`) — the report then softens its wording instead of
raising false alarms.

## Step 5. First run and the baseline

```bash
python3 bin/nw.py --fail-on never
```

Read `reports/REPORT.md`. On existing code there will be hundreds of findings —
that is normal. Accept them as the starting point:

```bash
python3 bin/nw.py --accept
```

From now on the bot fails only on **new** drift while the debt is paid down
gradually. Commit `config.json`, `snapshots/ds-latest.json` and
`snapshots/baseline.json` — CI is ready.

## Step 6. What people will see

- **a red PR** — new drift appeared; details in the run summary
- **the run summary** (Actions run page) — the report with `file:line`
- **the artifact** `prototypes-report` — full report plus machine-readable
  `findings.json`
- **Security → Code scanning** — inline annotations; works in public repos,
  private ones need Advanced Security (the workflow step is already soft)

## Optional

**Figma schedule** — `ds-watch.yml` + a `FIGMA_TOKEN` secret: the repository
notices library publishes on its own and marks the snapshot stale.

**Webhook instead of polling** — `bin/webhook.py` +
`relay/cloudflare-worker.js`: a LIBRARY_PUBLISH event wakes CI right after a
publish.

## If the bot is noisy

- `/* nw:ignore reason */` at the end of a line — a deliberate exception,
  reason kept in the code
- `outOfScope` — foreign chrome excluded wholesale
- `tier: "legacy"` — the prototype is frozen: no demands to build out states
- `stateMapByComponent` — when "Active" means "selected" on Tab but "pressed"
  on Button

A false finding costs more than a missed one: fill the report with noise and
people stop reading it. Every mechanism above exists for that reason.
