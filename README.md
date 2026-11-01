# tara-automation

Config-driven automation around **TARA** (PowerGEM Transmission Adequacy & Reliability
Assessment) for transmission planning studies: contingency screening, deliverability /
DNR evaluations, transfer limits, case changes, and post-processing into review-ready
deliverables.

Maintained by Abhishek Thurumalla.

## Repository layout

```
tara-automation/
├── README.md                  This file
├── pyproject.toml             Package definition (src layout, editable install)
├── requirements.txt           Pinned Python dependencies
├── setup_env.bat              One-shot Windows environment setup
├── start_study_desk.bat       Starts the study desk (local web page)
├── settings/
│   ├── machine.example.json   Per-machine paths (tara.exe etc.); copy to machine.json
│   ├── tara_options.json      Tuned TARA option blocks
│   └── research.json          Optional AI research assistant: model and budgets
├── docs/
│   ├── 01_environment_setup.md
│   ├── tara_engine_notes.md   TARA syntax and behaviour verified on the engine
│   ├── reference/             Reference comm files and queue document lists
│   └── workflows/             Run analysis, review and report recipes
├── data/reference/            Data shipped with the package (bus coordinates, counties)
├── src/tara_automation/       The Python package (import tara_automation)
├── scripts/                   Command-line tools
├── tools/                     Footprint builder / study desk page
├── research/areas/APS/        APS study area: sources, digests, summary, findings,
│                              EIA statistics, large loads, interconnection queue data
├── studies/_templates/        Study templates used by the scripts
└── tests/                     Pytest suite
```

The APS research area ships complete except for three things kept off the share: the
source documents themselves (`files/`, re-download from the URLs in `sources.yaml`),
the licensed grid layer (`data/grid/`), and generated pages. Rebuild the queue ladder
pages before opening the desk's *Interconnection queue* tab:

```bat
python scripts\aps_queue.py build --pages-only
```

## Quick start (new machine)

1. Install Python 3.11+ (3.12 recommended), Git for Windows, and TARA (26.02.1 tested).
2. From a command prompt in the repo root:
   ```bat
   setup_env.bat
   ```
   Creates `.venv`, installs pinned requirements, installs `tara_automation`
   in editable mode, and runs the smoke tests.
3. Copy `settings\machine.example.json` to `settings\machine.json` and set the
   paths for this machine. `machine.json` is git-ignored.
4. Activate the environment in any new shell:
   ```bat
   .venv\Scripts\activate
   ```

See `docs/01_environment_setup.md` for details and troubleshooting, and
`docs/tara_engine_notes.md` before writing comm or INCH files.

## Optional features that need API keys

Everything that runs TARA, compares cases, screens contingencies, computes transfer
limits, builds footprints or reads the interconnection queue works **without any API
key**. A few optional features call outside services:

| Feature | Where | Key | Without the key |
|---|---|---|---|
| Research assistant: **Ingest** (reads each source, writes a digest) | study desk, Research step; `scripts/research.py ingest` | `ANTHROPIC_API_KEY` (AI) | Refuses with a message; the source list still works |
| Research assistant: **Summarize** (area summary and findings from the digests) | study desk, Research step; `scripts/research.py summarize` | `ANTHROPIC_API_KEY` (AI) | Refuses with a message |
| Research assistant: **Discover** (searches the web for public sources) | study desk, Research step; `scripts/research.py discover` | `ANTHROPIC_API_KEY` (AI) | Refuses with a message |
| **AI run review** (written verdict on runs) | `scripts/review_agent.py`; the `review` stage of `scripts/study.py run`; `--review` on `task02_basecase.py` / `contingency_run.py` | `ANTHROPIC_API_KEY` (AI) | Skipped; the computed summaries are still written |
| **EIA load and dispatch** statistics | Research step, EIA pillar (`src/tara_automation/eia.py`) | `EIA_API_KEY` (free, from eia.gov; not AI) | Skipped with a message |

The interconnection queue tools (`scripts/aps_queue.py`, the desk's *Interconnection
queue* tab, upgrade ladders, POI matching) need no key. The queue also feeds the research
summary, and writing that summary is the Summarize step above.

Set a key for your Windows user, then open a new shell:

```bat
setx ANTHROPIC_API_KEY <key>
setx EIA_API_KEY <key>
```

The AI features use the `anthropic` Python package (in `requirements.txt`). Model and
spending settings for the research assistant are in `settings/research.json`; each call's
token use and cost is logged in the study area's `log.md`.

## Working model

- Every TARA execution goes through the run manager: a numbered `runs/N/` folder with
  a `manifest.json` and a line in `runs/RUN_LOG.md`.
- Results are reviewed before any conclusion or follow-on action. Study stages that
  produce numbers stay `pending` until a person runs `scripts/study.py approve`, and
  the report stage refuses to run until every analysis stage is approved.
- Heavy inputs and outputs (cases, TARAResults, logs, workbooks) stay out of git;
  code, configs, docs, and text-based TARA input files (.con/.mon/.sub/.inch) are tracked.
