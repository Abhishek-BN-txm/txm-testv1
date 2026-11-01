# Workflow: build_report — the reporter recipe

The recipe for anything that leaves the repo: decks, memos, summary tables for
an external audience. One rule governs everything here: **every number you
present comes from an approved results pack.**

## Step 1 — Get the pack, or stop

```bash
python scripts/study.py run --name <Name> --stage report
```

This writes `studies/<Name>/06_Reports/<Name>_results_pack.json` and fails if
any gated stage is unapproved. If it fails:

- report exactly which stages are `pending`, `stale` or `rejected`
- tell the user what to review and the `approve` command that unblocks it
- **stop.** Do not read the run manifests directly to get the numbers anyway.
  The gate is the point. A stale gate means the results changed after sign-off,
  which is more dangerous than no sign-off at all.

Never run `study.py approve` yourself — you are denied that command.

## Step 2 — Build from the pack

The pack carries the study context, the approved run numbers, the gate records
(who approved what, when, against which verdict) and the ATC rows.

- Numbers come from the pack, not from a CSV you re-parse, not from memory of
  an earlier conversation, and not from a superseded analysis note. If a figure
  you need is not in the pack, it has not been approved — say so.
- Carry the caveat with the number. A transfer limit quoted without its
  pre-existing-overload count is misleading (`atc_summary` keeps both in the
  same row for this reason). The same goes for a rating basis (Rate 1 vs
  Rate 2), the case vintage, and the contingency count.
- An unfilled commercial input renders as a visible red placeholder, never as a
  plausible-looking number. An unfinished deck must not read as finished.

## Step 3 — Cite provenance on the artefact

Every deck or memo states, somewhere a reader can find it: the study name, the
case(s), the run numbers, the rating basis, and the approval (who, when). A
number whose provenance cannot be traced back to a run does not go on a slide.

## Step 4 — Hand back

Say what you built, where it is, which pack version it came from, and which
placeholders remain. The user decides whether it goes out.

## Hard rules

- Write only under `studies/<Name>/06_Reports/`.
- Never modify run results, manifests, digests or verdicts.
- Never present a figure from a superseded method. Check the study's analysis
  notes for "supersedes"/"superseded" before reusing an older number
  (precedent: run 50's 37 MW, invalid, already drafted into a deck).
- If the user asks for a number you cannot source from an approved pack,
  explain what is missing rather than producing it.
