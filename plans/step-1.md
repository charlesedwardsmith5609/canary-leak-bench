# Step 1: design and scenarios

Design only, plus the scenario catalog. No runner code in this step.
(The design lives in REQUIREMENTS.md, docs/design.md, and DECISIONS.md D-01..D-09.)

## Files
- `REQUIREMENTS.md`, `docs/design.md`, `DECISIONS.md` (this step's decisions)
- `scenarios/catalog.json`: 12 scenarios

## Scenario shape
`id` (unique), `category` (one of peer_forum, install_script, helpful_colleague,
prompt_injection), `task` (no URL host, no `clb_`), `submit_path`, `pages`
(path, content_type, body). Injection pages are the only ones that mention
`https://collect.example.test/inbox`.

## Shared state
None. The catalog is immutable data.

## Tests (later steps load this file)
- Exactly 12 scenarios, 3 per category, unique ids, paths start with `/`,
  no `..`, no `clb_`, bodies under 8 KiB.
- The collect URL appears in every prompt_injection page and in no other category.

## Deferred
Forum, scorer, agents, report, live calls, agent-guard process.
