# Environment setup (Windows)

## Prerequisites

- Python 3.11+ (3.12 recommended) — `py --version` or `python --version`
- Git for Windows — `git --version`
- TARA (tara.exe) licensed on this machine (only needed to *run* the engine;
  code and parsing work without it)

## Setup

From a command prompt in the repo root:

```bat
setup_env.bat
```

What it does: creates `.venv`, upgrades pip, installs `requirements.txt`
(pinned versions), installs `tara_automation` editable (`pip install -e .`),
runs the pytest smoke tests. If every step prints OK and tests pass, the
environment is good.

On a new machine, get the repo with `git clone` (the repo is already initialized).

Then set machine paths:

```bat
copy settings\machine.example.json settings\machine.json
notepad settings\machine.json
```

Set `tara_exe` to this machine's tara.exe and `reference_root` to the prior-work
folder if present. `machine.json` is git-ignored — this is the only file that
differs between machines; everything else is portable via git.

## Daily use

```bat
.venv\Scripts\activate
```

## Troubleshooting

- **`py` not found** — the script falls back to `python`; ensure one of them
  is on PATH.
- **pip SSL/proxy errors on corporate network** — add
  `--trusted-host pypi.org --trusted-host files.pythonhosted.org` to the pip
  lines in `setup_env.bat`, or configure the corporate proxy in `pip.ini`.
- **Pinned-package conflicts on a different Python version** — requirements are
  pinned for 3.12; on other versions, relax pins in `requirements.txt`
  (pyproject.toml carries the loose ranges).
- **OneDrive** — keep this repo outside OneDrive (venv + git + OneDrive sync
  do not mix well). `C:\Projects` is fine.

## Recreating from scratch

Delete `.venv` and rerun `setup_env.bat`. Nothing else is machine-generated.
