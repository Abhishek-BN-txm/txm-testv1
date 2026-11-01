"""Low-level TARA comm-file syntax builders.

Knows ONLY syntax — no paths policy, no run management. Verified against
TARA2503 manual usage in prior working scripts (READ EPC / solve fdec /
lfreview %save %csv, warn %save %csv).
"""

from __future__ import annotations

from pathlib import Path


def trace(log_file: Path | str) -> str:
    """Send TARA trace output to a log file."""
    return f'%trace "{log_file}"'


def include(comm_file: Path | str) -> str:
    return f'%include "{comm_file}"'


def read_case(case_file: Path | str, fmt: str = "EPC", version: int = 21) -> str:
    """READ a powerflow case.

    fmt 'EPC' (GE, versions 18-21) -> READ EPC <ver> "file" 0
    fmt 'RAW' (PSSE, versions 24-36) -> READ PSSE <ver> "file" 0
    (NOT 'RAWD' — that is TARA's *create/export* RAW function and crashes
    with no case loaded; PSSE-read keyword verified from the prior working
    wrapper, tara_wrapper/analyses/base.py.)
    """
    fmt = fmt.upper()
    if fmt == "EPC":
        # Inline form proven in runs 1/3
        return f'READ EPC {version} "{case_file}" 0'
    if fmt == "RAW":
        # PSSE reader: filename on its own line (inline args are consumed by
        # the file-mask prompt and abort the read with 0 records). The
        # trailing standalone '0' exits the READ submenu back to L1 so the
        # following solve/lfreview commands resolve (verified against run 9
        # trace: read succeeded but 'solve' was not found at the READ L2
        # prompt without it).
        return f'READ PSSE {version}\n "{case_file}"\n0'
    raise ValueError(f"Unsupported case format: {fmt}")


def read_inch(inch_file: Path | str) -> str:
    """Apply an INCH (incremental change) file to the loaded case.

    The keyword is CIMINCH — TARA menu L2 function 12, "Read CIM incremental
    change file format". It appears nowhere in the TARA2503 manual, which
    documents applying INCH only through the GUI and documents READ PROJFILES
    for *project* INCH; this form was read out of the `_comm.txt` TARA itself
    generated when the case was loaded with an INCH file attached.

    Must come AFTER the case read and BEFORE the solve, matching the order
    TARA generates:  READ EPC ... / READ CIMINCH ... / solve fdec.
    """
    return f'READ CIMINCH "{inch_file}" 0 0'


def solve_fdec() -> str:
    """AC solve with fast decoupled loadflow."""
    return "solve fdec 0 0"


def warn_save(out_csv: Path | str, table: str = "sum") -> str:
    """Input-warning report. table: 'sum', 'list', or 'term'."""
    return f'warn %save "{out_csv}"  %csv {table} 0 0'


def sarea_all() -> str:
    """Select all areas (UI brackets its report exports with this)."""
    return "lfreview sarea -1 0 0"


def report_save(prefix: str, out_csv: Path | str, spec: str) -> str:
    """One report export line, UI style.

    prefix: 'lfreview', 'lfreview 2term', or 'warn'
    spec:   table name plus trailing args, e.g. 'bus 0 0' or 'tarea 1 0 0'
    """
    return f'{prefix} %save "{out_csv}" %csv {spec}'


def stop_exit() -> str:
    return "stop\n%exit"


def master_comm(comm_files: list[Path | str]) -> str:
    """Master comm that %includes each comm file then exits."""
    lines = [include(c) for c in comm_files]
    lines.append(stop_exit())
    return "\n".join(lines) + "\n"


def bat_launcher(tara_exe: Path | str, master_comm_file: Path | str) -> str:
    """Windows .bat that runs tara.exe against the master comm file."""
    return f'"{tara_exe}"  %%run "{master_comm_file}"\n'
