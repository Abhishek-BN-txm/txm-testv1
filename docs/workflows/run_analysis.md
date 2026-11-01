# Workflow: run_analysis — the analysis runner recipe

The recipe for executing TARA work. Your job is that the right runs execute and
that failures are visible. **You do not interpret results**: no verdicts, no
"looks reasonable", no numbers quoted as findings. That is the reviewer's job,
and keeping the two apart is why run 50's invalid 37 MW was caught.

Read `docs/workflows/study_flow.md` first for the stage contracts.

## Step 1 — Establish what is being asked

From the user or the Study Lead, you need: the study name (or the inputs to
create one), and which stages to run. If a study does not exist yet, ask for
objective, area of study, and the input files — never invent paths.

Check `python scripts/study.py status --name <Name>` before running anything.
It shows what has already run and which gates are open; a stage already done is
skipped unless `--force` is given, and re-running a gated stage **cancels the
user's sign-off**. If `--force` would do that, say so and get explicit
confirmation first.

## Step 2 — Run through the pipeline, never around it

```bash
python scripts/study.py run --name <Name> --stage <stages...> [--jobs N]
```

Never invoke `tara.exe`, a `.bat`, or a comm file by hand, and never write into
`runs/` yourself. Every engine execution goes through the run manager so it
gets a number, a manifest and a RUN_LOG line. The single-purpose
CLIs (`scripts/atc_trlim.py`, `contingency_run.py`, `task02_basecase.py`,
`case_change.py`) are the exception: they use the run manager too, and are the
right tool for a one-off outside a study.

Runs likely to exceed ~10 minutes go in the background.

## Step 3 — Report execution health, not meaning

For every run, state: run number, status, and the one-line summary the stage
printed. Then the things that make a run untrustworthy:

- a stage that raised — quote the error and stop; do not "try something else"
  with different options unless asked
- `notes` in the manifest (e.g. a sending bus with no reported rows)
- a TARA exit code of 0 is **not** proof of success; the stage checks
  (`check_outputs`) are. Say which checks passed.
- contingency counts that differ from a previous run on the same file — that
  is a real signal (run 58: option ordering changed 6596 → 6565)

Legitimate output: "runs 62–64 succeeded; 6565 contingencies each; run 63 has a
note about bus 19067." Not your output: "capability is small at ED-5."

## Step 4 — Hand off

Say which runs the reviewer should look at and where the summary landed
(`studies/<name>/05_Analysis/`). Then stop. Do not open the gate, do not build
a deck, do not summarise findings for a slide.

## Hard rules

- The reference repo (`..\TARA Automation`) and all prior runs/manifests are
  read-only. Never modify a previous run's results.
- Never overwrite an existing case file or a prior run's results.
- Guard against empty/small case files (the pipeline does; do not bypass it).
- Never run `study.py approve`. You are denied that command, and asking the
  user to approve something you have not been asked to run is out of scope.
