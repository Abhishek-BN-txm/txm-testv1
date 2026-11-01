"""Shared agent tools and evidence gatherers.

TOOLS: read-only tools the model may call during the reasoning loop.
GATHERERS: deterministic evidence-pack builders, referenced by name from
agent definitions. Both are registries — new agents reuse or add entries.

Everything here is read-only with capped output. Write access belongs to the
runner's write-back, never to tools.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from tara_automation.machine import REPO_ROOT

RUNS_ROOT = REPO_ROOT / "runs"

WARN_LIST_MAX_LINES = 250
LOG_TAIL_LINES = 80
SEARCH_MAX_HITS = 40


# ── Tools (model-callable, read-only) ──────────────────────────────────────

def search_artifacts(pattern: str, run: int) -> str:
    """Regex grep over one run's results + logs. Capped output."""
    base = RUNS_ROOT / str(run)
    try:
        rx = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        return f"bad regex: {e}"
    hits: list[str] = []
    for sub in ("results", "logs"):
        for f in sorted((base / sub).glob("*")):
            if not f.is_file():
                continue
            try:
                for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                    if rx.search(line):
                        hits.append(f"{f.name}: {line.strip()[:300]}")
                        if len(hits) >= SEARCH_MAX_HITS:
                            return "\n".join(hits) + "\n... (capped)"
            except OSError:
                continue
    return "\n".join(hits) if hits else "(no matches)"


TOOLS: dict[str, dict] = {
    "search_artifacts": {
        "fn": search_artifacts,
        "schema": {
            "name": "search_artifacts",
            "description": (
                "Search the results CSVs and logs of a run under review with "
                "a case-insensitive regex. Use it to chase evidence for "
                "specific buses, branches, units, or warning IDs from digest "
                "outliers. Returns matching lines with file names."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string",
                                "description": "regex, e.g. '148017|Q288'"},
                    "run": {"type": "integer", "description": "run number"},
                },
                "required": ["pattern", "run"],
            },
        },
    },
}


# ── Gatherers (deterministic evidence packs) ───────────────────────────────

def _read_capped(path: Path, max_lines: int, tail: bool = False) -> str:
    if not path.exists():
        return f"(missing: {path.name})"
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    kept = lines[-max_lines:] if tail else lines[:max_lines]
    note = "" if len(lines) <= max_lines else f"\n... ({len(lines)} lines total)"
    return "\n".join(kept) + note


def gather_runs_review(params: dict) -> str:
    """Evidence pack for the review_runs agent (recipe Step 1)."""
    mode = params["mode"]
    runs = params["runs"]
    digests_dir = RUNS_ROOT / "output" / "digests"
    registry = REPO_ROOT / "docs" / "workflows" / "accepted_findings.md"

    digest = None
    if mode == "compare" and len(runs) == 2:
        digest = digests_dir / f"compare_{runs[0]}_vs_{runs[1]}.md"
    elif mode == "validate" and len(runs) == 2:
        digest = digests_dir / f"validation_{runs[0]}_vs_{runs[1]}.md"
    digest_text = (digest.read_text(encoding="utf-8")
                   if digest and digest.exists() else "(no digest on disk)")
    registry_text = (registry.read_text(encoding="utf-8")
                     if registry.exists() else "(no registry)")

    user = (
        f"Review request: mode={mode}, runs={runs}\n\n"
        f"## Evidence pack\n\n"
        f"### Digest\n{digest_text}\n\n"
        f"### Accepted findings registry\n{registry_text}\n\n"
    )
    for n in runs:
        rdir = RUNS_ROOT / str(n)
        manifest = rdir / "manifest.json"
        mtext = (json.dumps(json.loads(manifest.read_text(encoding="utf-8")), indent=1)
                 if manifest.exists() else "(missing manifest)")
        user += (
            f"### Run {n}\nmanifest:\n{mtext}\n"
            f"Warn_Sum:\n{_read_capped(rdir / 'results' / 'Warn_Sum.csv', 60)}\n\n"
            f"Warn_List (capped):\n"
            f"{_read_capped(rdir / 'results' / 'Warn_List.csv', WARN_LIST_MAX_LINES)}\n\n"
            f"tara_trace tail:\n"
            f"{_read_capped(rdir / 'logs' / 'tara_trace.log', LOG_TAIL_LINES, tail=True)}\n\n"
            f"pytara_log tail:\n"
            f"{_read_capped(rdir / 'logs' / 'pytara_log.txt', LOG_TAIL_LINES, tail=True)}\n\n"
        )
    return user


GATHERERS: dict = {
    "runs_review": gather_runs_review,
}
