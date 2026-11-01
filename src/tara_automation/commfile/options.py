"""TARA option-block rendering.

Blocks live in settings/tara_options.json (tracked; extracted verbatim from
the proven RedButte contingency comm). This module renders them into comm
syntax and applies per-run overrides.

Rendered form (matches the working comm files):

    lfopt  adj
    PAR_AdjEnabledLF \t1
    ...
    0 0
"""

from __future__ import annotations

import json
from pathlib import Path

from tara_automation.machine import REPO_ROOT

OPTIONS_FILE = REPO_ROOT / "settings" / "tara_options.json"


def load_options(path: Path | None = None) -> dict:
    return json.loads((path or OPTIONS_FILE).read_text(encoding="utf-8"))


def apply_overrides(blocks: dict, overrides: dict | None) -> dict:
    """overrides: {block_name: {option: value}}. Unknown keys raise —
    a silently ignored override is a silent wrong study."""
    if not overrides:
        return blocks
    blocks = json.loads(json.dumps(blocks))  # deep copy
    named = {b["name"]: b for b in blocks["analysis_blocks"]}
    named["dimension_block"] = blocks["dimension_block"]
    for bname, opts in overrides.items():
        if bname not in named:
            raise KeyError(f"Unknown option block '{bname}'. "
                           f"Blocks: {sorted(named)}")
        target = named[bname]["options"]
        for k, v in opts.items():
            if k not in target:
                raise KeyError(f"Unknown option '{k}' in block '{bname}'")
            target[k] = v
    return blocks


def render_block(block: dict) -> str:
    """One option block -> comm text."""
    pad = max(len(k) for k in block["options"]) + 1
    lines = [block["command"]]
    for k, v in block["options"].items():
        lines.append(f"{k:<{pad}}\t{v}")
    lines.append(block["terminator"])
    return "\n".join(lines)


def render_analysis_blocks(blocks: dict) -> str:
    return "\n".join(render_block(b) for b in blocks["analysis_blocks"])
