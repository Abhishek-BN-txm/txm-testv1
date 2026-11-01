# Workflow: review_runs — the agent review loop

The recipe the optional AI reviewer follows when asked to review runs. The code produces
artifacts; this workflow turns them into judgments. It is deliberately
deterministic about *what to read* and *where to write*, and reserves judgment
for *what the evidence means*.

## Invocation

- "review run N" → Mode A (single run)
- Mode B (comm-vs-pygem engine validation) was retired 2026-09-24 with the
  pygem engine; runs 2/4 remain as history only.
- "review comparison A vs B" → Mode C (case vs case)
- "review contingency comparison A vs B [vs C]" → Mode D (N-1 contingency
  comparison across contingency runs)
- "review the ATC runs N M ..." / "review the transfer limits" → Mode E
  (TrLim transfer capability)

If the user names no runs, use the highest-numbered relevant runs and confirm
the choice in the verdict.

## Step 1 — Gather (always, in this order)

1. `runs/<N>/manifest.json` for every run in scope — engine, case, format,
   options, status, summary. A run with status `failed` short-circuits to
   failure diagnosis (Step 2F).
2. The relevant digest, if the mode has one, from
   `runs/output/digests/`: `compare_<a>_vs_<b>.md` (Mode C), or `contingency_compare_<runs>_<rating>.{md,xlsx}`
   (Mode D). Regenerate via the script if missing — never hand-compute what the
   scripts already compute.
3. Warning evidence: `results/Warn_Sum.csv` + `Warn_List.csv`,
   `logs/tara_trace.log` tail.
4. `docs/workflows/accepted_findings.md` — the registry of findings the user
   has already reviewed and accepted. Do not re-flag these; cite them.

## Step 2 — Cross-reference (the judgment core)

For every anomaly in the digest, look for its cause before reporting it:

- **Outlier bus voltages** → search Warn_List for that bus number
  (oscillating switched shunts, shunts that cannot reach target, var-limit
  changes). A matched warning usually explains a voltage pocket.
- **Outlier branch flows / loadings** → check status changes in the digest
  (series-cap bypass sections, reclosed lines) and suspicious-R/X or rating
  warnings for those branches.
- **Totals deltas** → decompose: swing-bus dispatch shift (check swing summary
  in CaseSum / solve log) vs real dispatch or load change (digest gen/load
  sections).
- **Topology counts** → check the digest's Format context section first;
  cross-format runs (EPC vs RAW) legitimately differ in 3W star buses,
  bypass records, rating slots, flow signs.
- **Convergence / mismatch** → CaseSum ConvStatus + busPmismMax; iterations
  materially above the case's history (30HS2 family: 32–43) is worth a note.
- **F. Failed runs** → read the console/trace tail, identify the first real
  error (TARA warnings cascade — everything after the first failure is noise),
  check RUN_LOG for whether this failure mode was seen before, propose the fix.
- **D. Contingency comparisons (N-1 across cases)** → generate the standard
  artifacts with `python scripts/compare_contingency.py --runs <baseline>
  <case...>` (first run = baseline). This writes, to
  `runs/output/digests/`, an **xlsx workbook (the default deliverable)**
  and a markdown digest; the rating basis (Rate 1 vs Rate 2) is auto-detected
  from the run's option overrides. Attribute new/worsened overloads to the
  project (they are flagged in the digest's status column); note that a
  contingency's non-convergence set changing between cases is a stability
  signal, and that a 0-contingency or all-`NConv` result means the study files
  did not take effect (see the run's trace for WarnID-206). Never re-report the
  raw `worst_overloads` loadings without the comma-safe parser — the owner-name
  column shift inflates them (GILARIVR 500 kV artifact).

- **E. ATC / transfer capability (TrLim)** → the deliverable is
  `studies/<name>/05_Analysis/<name>_atc_summary.md` (+ `.json`), produced by
  the `atcsummary` stage from each run's `manifest.trlim_summary`. The raw
  `<SEND>_TrLim.csv` is TARA's own output and is never rewritten. Check, in
  this order:
  1. **Method.** `manifest.trlim.recipe` must be the by-bus UI recipe
     (`TrLimMode 1` + `busDCMaxTransfer`, validated by run 54). Anything using
     `scaleTrLim` + the `Binding` report is the superseded method of runs
     49–53 and its numbers are not usable — say so and stop.
  2. **The transfer actually happened.** The sending subsystem must expand into
     one path per bus; a path with no rows, or a "DFAX for 1 buses" note on a
     multi-bus subsystem, means less was injected than the label claims.
  3. **The limit against its caveats.** `first_limit_mw` is only the
     incremental headroom *once pre-existing overloads are set aside*. Where
     `worsened_initial_overloads` is non-empty, the strict incremental ATC is
     zero; report both numbers, never the limit alone.
  4. **Basis comparability.** Contingency count, rating slot and case vintage
     must match across the cases being compared (run 58: 6596 vs 6565 came from
     comm-deck option ordering, not from the case).
  5. **Direction sanity.** A binding constraint should carry a positive Dfax;
     rows flagged "Increasing Transfer relieves initial overload" are
     informational and must never be read as the limit.

## Step 3 — Classify every finding

| Class | Meaning | Example |
|---|---|---|
| REAL | Case content difference / genuine result | Fort Rock series caps in service |
| ARTIFACT | File-format or representation difference | RAW 999xxx star buses |
| ACCEPTED | Already reviewed and accepted by user (registry) | EPC-vs-RAW format artifacts |
| CODE | Our pipeline caused it | READ menu not exited |
| UNKNOWN | Cannot determine from artifacts | flag for user, say what would resolve it |

## Step 4 — Verdict

Write a verdict with exactly these parts, in this order:
1. **Bottom line** — one sentence: pass/fail/needs-attention and why.
2. **Findings table** — finding, class, evidence (file + what matched).
3. **What to check yourself** — at most 3 items, only genuinely user-facing
   judgments (engineering significance, study implications).
4. **Follow-ups** — proposed next runs, code fixes, or backlog items.

Keep it under a page. The user reads the verdict, not the raw artifacts.

## Step 5 — Write-back (only after presenting the verdict)

- Save verdict as `runs/output/review_runs/review_<scope>.md`
  (e.g. `review_compare_3_vs_10.md`) — git-tracked, part of the project
  record. Each agent writes only under its own
  `runs/output/<agent_name>/` folder.
- Mode D: the `contingency_compare_<runs>_<rating>.xlsx` workbook (and its
  markdown digest) are the standard deliverables — reference them in the
  verdict; they are produced by the script, not hand-built.
- Append one `Review:` line to `runs/RUN_LOG.md` under today's date.
- New user-accepted findings → add to `docs/workflows/accepted_findings.md`
  with date and scope.
- CODE findings → fix if small, else log them as an issue.
- Never modify results, manifests, or the reference repo.

## Step 6 — The gate (what a verdict is not)

A verdict is evidence for a decision, not the decision. When the runs belong to
a study, close by naming the gate it informs:

    python scripts/study.py approve --name <Study> --stage <contingency|atc> \
        --verdict runs/output/review_runs/<this verdict>.md

Never run that command. Approval is the user's, is recorded against their
name, and binds to the exact run numbers reviewed — re-running the stage makes
it lapse. An agent that could approve its own runs would make the gate
decorative.

## Rules inherited from the project

- Review results with the user before follow-on actions; the verdict is a
  proposal, not a trigger.
- **Do not run the engine.** The reviewer never executes TARA runs: the runner and the
  reviewer are kept apart so results are not graded by whoever produced them.
  If a run is missing, say which one and let the runner (or the user) produce
  it. Read-only post-processing scripts are yours to run.
- Everything computable stays in the scripts; if a judgment recurs, promote it
  into `compare.py` / `compare_contingency.py` and note the promotion in the changelog
  (precedent: EPC-vs-RAW normalization, 2026-07-28).
- Never re-write any results generated by TARA. - These are for read-only