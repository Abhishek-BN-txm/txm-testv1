"""Emit a TARA INCH (INcremental CHange) file for one scenario.

INCH format, verified against the TARA2503 manual:

  * ch. 5.1  — continuous CSV: sections keyed by a ``#KEYWORD [Col,Col,...]``
               header line, data rows beneath, closed by ``#END``.
  * ch. 5.4.2 — actions are ``#ADD_*`` / ``#MODIFY_*`` / ``#DELETE_*``; a
               MODIFY record carries only the columns being changed.
  * ch. 5.7  — the equipment sections and their keywords.
  * ch. 6.2  — ``#TAP``, ``#SCALE_GEN``, ``#SCALE_LOAD``, ``#SOLVE_FDEC``,
               ``#SAVE``.

Two format rules that are easy to get wrong and are load-bearing here:

  * the section keyword and its bracketed column list must sit on ONE line
    (manual 6.1) — no wrapping, however long the transformer header gets;
  * equipment must exist before it is referenced (manual 5.6.1), which fixes
    the emission order: buses, taps, generators/loads, branches/transformers,
    then modifications, then scaling, solve and save.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from tara_automation.casechange.model import (
    ChangeSet, Scenario, bus_value, new_bus_number,
)

# --- Section keywords -------------------------------------------------------
# Ch. 6.2 tabulates the add-a-line section as ADD_LINE while the ch. 5.5
# worked example uses ADD_BRANCH, and ch. 5.7 lists #ADD_BRANCH among the
# equipment sections. ADD_BRANCH is the one shown with real data in the
# manual, so that is what we emit; if a TARA build rejects it, this constant
# is the single place to change.
SEC_ADD_BRANCH = "ADD_BRANCH"


def _fmt(v) -> str:
    """One INCH cell. Blank stays blank so MODIFY records omit nothing."""
    if v is None or v == "":
        return ""
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, float):
        if v == int(v) and abs(v) < 1e15:
            return str(int(v))
        return repr(round(v, 8)).rstrip("0").rstrip(".")
    s = str(v)
    # TARA reads unquoted CSV, so quote anything holding a separator or a
    # space: an owner name like "Gila River Power, LP" would otherwise shift
    # every later column, which is exactly the bug that inflated the GILARIVR
    # loadings in the run 18-24 violation parsing.
    if any(ch in s for ch in ",\"'") or s != s.strip() or " " in s:
        return '"' + s.replace('"', "") + '"'
    return s


def _section(keyword: str, columns: list[str], rows: list[list],
             comment: str = "") -> list[str]:
    """One ``#KEYWORD [cols]`` / data / ``#END`` block.

    Columns that are blank on every row are dropped. The manual says an ADD
    record should define all its data fields (5.5), and an omitted column
    takes TARA's default — an *empty* field is the one thing that has no
    defined meaning, so we never emit a column that is empty throughout.
    """
    if not rows:
        return []
    keep = [i for i, _ in enumerate(columns)
            if any(r[i] not in (None, "") for r in rows)]
    columns = [columns[i] for i in keep]
    rows = [[r[i] for i in keep] for r in rows]
    out = []
    if comment:
        out.append(f"// {comment}")
    out.append(f"#{keyword} [{','.join(columns)}]")
    out.extend(",".join(_fmt(v) for v in r) for r in rows)
    noun = "record" if len(rows) == 1 else "records"
    out.append(f"#END // {len(rows)} {noun}")
    out.append("")
    return out


def _mul(value, factor):
    """Scale an impedance, preserving blank (so the column can be dropped)."""
    if value in (None, ""):
        return value
    return round(float(value) * factor, 8)


def _impedance_scale(row: dict, base) -> float:
    """Factor turning the sheet's R/X into per unit on the 100 MVA system base.

    TARA ignores CZ — probe 3 (run 32) wrote the same transformer with CZ=1
    X=0.03 and CZ=2 X=0.105 and stored 0.105 and 0.3675 on tbase 350, i.e.
    both were read as system-base per unit and merely re-expressed. So the
    conversion from a nameplate percentage has to happen here.

    NAMEPLATE_PCT: 10.5% on a 350 MVA base -> 0.105 x 100/350 = 0.03.
    """
    if (row.get("ImpedanceBasis") or "SYSTEM_100MVA") != "NAMEPLATE_PCT":
        return 1.0
    try:
        mva = float(base)
    except (TypeError, ValueError):
        mva = 0.0
    if mva <= 0:
        return 1.0          # validation reports this; do not silently distort
    return 1.0 / mva


def _present(row: dict, names: list[str]) -> list[str]:
    """Column names whose cell actually carries a value (for MODIFY records)."""
    return [n for n in names if row.get(n) not in (None, "")]


class InchWriter:
    """Builds the INCH text for one scenario of a ChangeSet."""

    def __init__(self, changes: ChangeSet, scenario: Scenario,
                 base_case: Path, out_case: Path):
        self.cs = changes
        self.sc = scenario
        self.base_case = Path(base_case)
        self.out_case = Path(out_case)

    # -- helpers ----------------------------------------------------------
    def _rows(self, sheet: str) -> list[dict]:
        return self.cs.rows_for(sheet, self.sc.id)

    def _bus(self, row: dict, col: str):
        return bus_value(row, col, self.sc.id)

    # -- sections ---------------------------------------------------------
    def _header(self) -> list[str]:
        sc = self.sc
        src = self.cs.source.name if self.cs.source else "(unsaved workbook)"
        lines = [
            f"// TARA INCH — scenario {sc.id}: {sc.name}",
            f"// Generated {date.today().isoformat()} by tara-automation "
            f"from {src}",
        ]
        if sc.description:
            lines.append(f"// {sc.description}")
        lines += [
            f"// Base case : {self.base_case}",
            f"// Saves to  : {self.out_case}  ({sc.out_format} "
            f"v{sc.out_version})",
            "//",
            "// Change counts: " + ", ".join(
                f"{k}={v}" for k, v in self.cs.counts(sc.id).items() if v)
            or "// Change counts: none",
            "",
        ]
        return lines

    def _buses(self) -> list[str]:
        cols = ["BusNum", "BusName", "BusNomVolt", "BusType", "AreaNum",
                "ZoneNum", "OwnerNum", "BusVoltPU", "BusAngle"]
        rows = []
        for r in self._rows("New_Buses"):
            rows.append([new_bus_number(r, self.sc.id)]
                        + [r.get(c) for c in cols[1:]])
        return _section("ADD_BUS", cols, rows, "New buses")

    def _taps(self) -> list[str]:
        cols = ["BusNumFr", "BusNumTo", "CKT", "PercentTap", "NewBusNum",
                "NewBusName"]
        rows = []
        for r in self._rows("Line_Taps"):
            rows.append([
                self._bus(r, "BusNumFr"), self._bus(r, "BusNumTo"),
                r.get("CKT"), r.get("PercentTap"),
                new_bus_number(r, self.sc.id), r.get("NewBusName"),
            ])
        return _section("TAP", cols, rows, "Line taps (new bus per record)")

    def _generators(self) -> list[str]:
        cols = ["BusNum", "GenID", "Status", "GenMW", "GenMVR", "GenMWMax",
                "GenMWMin", "GenMVRMax", "GenMVRMin", "VoltTarg",
                "GenRegBusNum", "GenRMPCT", "MBase", "RSource", "XSource",
                "Own1"]
        rows = []
        for r in self._rows("New_Generators"):
            pmax = r.get("GenMWMax") or 0.0
            qmax = r.get("GenMVRMax")
            if qmax in (None, ""):
                qmax = round(pmax * 0.3286, 3)     # ~0.95 pf
            qmin = r.get("GenMVRMin")
            if qmin in (None, ""):
                qmin = -qmax
            rows.append([
                self._bus(r, "BusNum"), r.get("GenID"), r.get("Status"),
                r.get("GenMW"), r.get("GenMVR"), pmax, r.get("GenMWMin"),
                qmax, qmin, r.get("VoltTarg"),
                self._bus(r, "GenRegBusNum") or self._bus(r, "BusNum"),
                r.get("GenRMPCT"), r.get("MBase") or pmax, r.get("RSource"),
                r.get("XSource"), r.get("OwnerNum"),
            ])
        return _section("ADD_GEN", cols, rows, "New generators")

    def _loads(self) -> list[str]:
        cols = ["BusNum", "LoadID", "Status", "LoadP0", "LoadQ0", "LoadArea",
                "LoadZone", "LoadOwner"]
        rows = []
        for r in self._rows("New_Loads"):
            rows.append([self._bus(r, "BusNum")]
                        + [r.get(c) for c in cols[1:]])
        return _section("ADD_LOAD", cols, rows, "New loads")

    def _branches(self) -> list[str]:
        # RateD fills the fourth rating slot. Without it TARA stores 0 there
        # (verified in run 33 against a real line, which carries all four),
        # and a study selecting rating 4 would treat the new line as unrated
        # and never monitor it.
        cols = ["BusNumFr", "BusNumTo", "CKT", "Status", "LineR", "LineX",
                "Charge", "RateA", "RateB", "RateC", "RateD", "Length",
                "BranchName", "Own1"]
        rows = []
        for r in self._rows("New_Branches"):
            a = r.get("RateA")
            b = r.get("RateB") or a
            c = r.get("RateC") or b
            d = r.get("RateD") or c
            rows.append([
                self._bus(r, "BusNumFr"), self._bus(r, "BusNumTo"),
                r.get("CKT"), r.get("Status"), r.get("LineR"), r.get("LineX"),
                r.get("Charge"), a, b, c, d, r.get("Length"),
                r.get("BranchName"), r.get("OwnerNum"),
            ])
        return _section(SEC_ADD_BRANCH, cols, rows, "New branches")

    def _transformers(self) -> list[str]:
        """ADD_TRANSFORMER, two- and three-winding.

        Column set established empirically over runs 31-33, verified by
        reading the SAVED case rather than the log:

        * ``W1CtrlBus`` is the one required control column. Without it TARA
          drops the record (WarnID-1652 "Can't find regulated bus 1", always
          trailed by a bogus WarnID-1607 from TARA's own retry). ``W1AdjStatus``
          and ``W1ANG`` are accepted but ignored; a record carrying only
          ``W1AdjStatus`` still failed. 0 is a valid control bus and is the
          fixed-tap case, so that is the default.
        * A three-winding record needs a control bus on EVERY winding — it
          failed with "Wind=2" until ``W2CtrlBus``/``W3CtrlBus`` were added.
        * Every added transformer is stored as adjustment type 1 whatever we
          write, and with no target band TARA stores +/-99999. Emitting
          ``W1TargetMax``/``W1TargetMin`` gives it a sane 1.05/0.95 band.
          (Harmless on a radial GSU, which TARA auto-excludes from tap
          adjustment, but wrong on a networked transformer.)
        * ``W1RateA/B/C`` and ``W1Rate1/2/3`` are the same slots; slot 4 is
          left 0 unless ``W1Rate4`` is given, so it is filled from RateC.
        * ``SBAS1`` only sets the base the stored impedance is expressed on.
          The value itself is always system-base per unit — see
          ``_impedance_scale``.
        """
        two_cols = ["W1Bus#", "W2Bus#", "W3Bus#", "CKT", "XfmrName", "Status",
                    "R1-2", "X1-2", "SBAS1", "W1RateA", "W1RateB", "W1RateC",
                    "W1Rate4", "W1NOMV", "W2NOMV", "W1WINV", "W2WINV",
                    "W1CtrlBus", "W1TargetMax", "W1TargetMin"]
        three_cols = ["W1Bus#", "W2Bus#", "W3Bus#", "CKT", "XfmrName",
                      "Status", "R1-2", "X1-2", "R2-3", "X2-3", "R3-1",
                      "X3-1", "SBAS1", "W1RateA", "W1RateB", "W1RateC",
                      "W1Rate4", "W2RateA", "W3RateA", "W1NOMV", "W2NOMV",
                      "W3NOMV", "W1CtrlBus", "W2CtrlBus", "W3CtrlBus",
                      "W1TargetMax", "W1TargetMin"]
        two, three = [], []
        for r in self._rows("New_Transformers"):
            a = r.get("W1RateA")
            b = r.get("W1RateB") or a
            c = r.get("W1RateC") or b
            base = r.get("SBAS1") or a
            z = _impedance_scale(r, base)
            head = [self._bus(r, "W1Bus"), self._bus(r, "W2Bus"),
                    self._bus(r, "W3Bus") or 0, r.get("CKT"),
                    r.get("XfmrName"), r.get("Status")]
            ctrl = self._bus(r, "W1CtrlBus") or 0
            band = [r.get("W1TargetMax"), r.get("W1TargetMin")]
            if self._bus(r, "W3Bus"):
                three.append(head + [
                    _mul(r.get("R1_2"), z), _mul(r.get("X1_2"), z),
                    _mul(r.get("R2_3"), z), _mul(r.get("X2_3"), z),
                    _mul(r.get("R3_1"), z), _mul(r.get("X3_1"), z),
                    base, a, b, c, c,
                    r.get("W2RateA") or a, r.get("W3RateA") or a,
                    r.get("W1NOMV"), r.get("W2NOMV"), r.get("W3NOMV"),
                    ctrl, 0, 0] + band)
            else:
                two.append(head + [
                    _mul(r.get("R1_2"), z), _mul(r.get("X1_2"), z),
                    base, a, b, c, c,
                    r.get("W1NOMV"), r.get("W2NOMV"),
                    r.get("W1WINV"), r.get("W2WINV"), ctrl] + band)
        out = _section("ADD_TRANSFORMER", two_cols, two,
                       "New two-winding transformers")
        out += _section("ADD_TRANSFORMER", three_cols, three,
                        "New three-winding transformers")
        return out

    def _unit_changes(self) -> list[str]:
        """MODIFY_GEN / MODIFY_LOAD, one section per distinct column set.

        A MODIFY record carries only the fields being changed (manual 5.4.2),
        and every row under one header must share that column list — so rows
        are grouped by which optional cells they filled in.
        """
        gen_map = {"Status": "Status", "P": "GenMW", "Q": "GenMVR",
                   "Pmax": "GenMWMax", "Pmin": "GenMWMin",
                   "Qmax": "GenMVRMax", "Qmin": "GenMVRMin",
                   "VoltTarg": "VoltTarg", "GenRegBusNum": "GenRegBusNum"}
        load_map = {"Status": "Status", "P": "LoadP0", "Q": "LoadQ0"}

        out: list[str] = []
        for element, keyword, id_col, mapping in (
                ("GEN", "MODIFY_GEN", "GenID", gen_map),
                ("LOAD", "MODIFY_LOAD", "LoadID", load_map)):
            groups: dict[tuple, list[list]] = {}
            for r in self._rows("Unit_Changes"):
                if (r.get("Element") or "").upper() != element:
                    continue
                fields = _present(r, list(mapping))
                if not fields:
                    continue
                key = tuple(fields)
                values = [self._bus(r, "BusNum"), r.get("ID")]
                for f in fields:
                    v = r.get(f)
                    values.append(self._bus(r, f) if f == "GenRegBusNum" else v)
                groups.setdefault(key, []).append(values)
            for fields, rows in groups.items():
                cols = ["BusNum", id_col] + [mapping[f] for f in fields]
                out += _section(keyword, cols, rows,
                                f"Existing {element.lower()} changes")
        return out

    def _upgrades(self) -> list[str]:
        """MODIFY_BRANCH / MODIFY_TRANSFORMER, grouped by column set."""
        branch_map = {"Status": "Status", "LineR": "LineR", "LineX": "LineX",
                      "Charge": "Charge", "RateA": "RateA", "RateB": "RateB",
                      "RateC": "RateC", "RateD": "RateD"}
        xfmr_map = {"Status": "Status", "LineR": "R1-2", "LineX": "X1-2",
                    "RateA": "W1RateA", "RateB": "W1RateB",
                    "RateC": "W1RateC", "RateD": "W1Rate4"}
        out: list[str] = []
        for element, keyword, id_cols, mapping in (
                ("BRANCH", "MODIFY_BRANCH",
                 ["BusNumFr", "BusNumTo", "CKT"], branch_map),
                ("TRANSFORMER", "MODIFY_TRANSFORMER",
                 ["W1Bus#", "W2Bus#", "W3Bus#", "CKT"], xfmr_map)):
            groups: dict[tuple, list[list]] = {}
            for r in self._rows("Upgrades"):
                if (r.get("Element") or "").upper() != element:
                    continue
                fields = _present(r, list(mapping))
                if not fields:
                    continue
                ids = [self._bus(r, "BusNumFr"), self._bus(r, "BusNumTo")]
                if element == "TRANSFORMER":
                    ids.append(self._bus(r, "W3Bus") or 0)
                ids.append(r.get("CKT"))
                groups.setdefault(tuple(fields), []).append(
                    ids + [r.get(f) for f in fields])
            for fields, rows in groups.items():
                cols = id_cols + [mapping[f] for f in fields]
                out += _section(keyword, cols, rows,
                                f"{element.title()} upgrades")
        return out

    def _scaling(self, when: str) -> list[str]:
        """SCALE_GEN / SCALE_LOAD for rows whose ApplyOrder matches `when`.

        One selector per record (manual 6.2.10/6.2.11), so each row becomes
        its own single-row section — the selector column differs per row and
        cannot share a header.
        """
        out: list[str] = []
        for r in self._rows("Gen_Load_Scaling"):
            if (r.get("ApplyOrder") or "AFTER_ADDS") != when:
                continue
            kind = (r.get("Type") or "").upper()
            sel_by = (r.get("SelectBy") or "").upper()
            sel_col = "SubSysName" if sel_by == "SUBSYSTEM" else sel_by.title()
            selector = r.get("Selector")
            if sel_by != "SUBSYSTEM":
                try:
                    selector = int(float(str(selector)))
                except (TypeError, ValueError):
                    pass
            note = r.get("Comment") or ""
            if kind == "GEN":
                cols = [sel_col, "ScalingMethod", "MachineLimit",
                        "ValueOption", "Value", "NegPgen"]
                vals = [selector, r.get("ScalingMethod"), r.get("MachineLimit"),
                        r.get("ValueOption"), r.get("Value"), r.get("NegPgen")]
                out += _section("SCALE_GEN", cols, [vals], note)
            elif kind == "LOAD":
                # ValueOption, NOT PValueOption. PValueOption is the ch. 4.2.1
                # menu-format keyword and is absent from the INCH dictionary:
                # probe 1 rejected it (WarnID-1001) and then silently fell
                # back to "total Pload", setting area 14's load from 7485 MW
                # to 2 MW. The value column stays PValue. Confirmed on the
                # engine in run 31: Base Pload 7485.10 ==> Final 7735.10 MW,
                # with Qload following the P/Q ratio (635.48 ==> 656.71).
                cols = [sel_col, "ScalingMethod", "NonConform", "ValueOption",
                        "PValue"]
                vals = [selector, r.get("ScalingMethod"), r.get("NonConform"),
                        r.get("ValueOption"), r.get("Value")]
                if r.get("QValue") not in (None, ""):
                    cols += ["QValue"]
                    vals += [r.get("QValue")]
                out += _section("SCALE_LOAD", cols, [vals], note)
        return out

    def _solve(self) -> list[str]:
        if not self.sc.solve:
            return ["// Solve skipped (Scenarios.Solve = N)", ""]
        return _section(
            "SOLVE_FDEC",
            ["StartOption", "Tap", "PAR", "SWShunt", "AreaInt", "DCTap"],
            [[0, 1, 1, 1, 0, 1]],
            "Solve from current values; area interchange control off so the "
            "gen/load changes are not undone by interchange adjustment")

    def _save(self) -> list[str]:
        return _section("SAVE", ["Format", "Version", "FileName"],
                        [[self.sc.out_format, self.sc.out_version,
                          str(self.out_case)]],
                        "Write the modified case")

    # -- assembly ---------------------------------------------------------
    def render(self) -> str:
        parts: list[str] = []
        parts += self._header()
        parts += self._scaling("BEFORE_ADDS")
        parts += self._buses()
        parts += self._taps()
        parts += self._generators()
        parts += self._loads()
        parts += self._branches()
        parts += self._transformers()
        parts += self._unit_changes()
        parts += self._upgrades()
        parts += self._scaling("AFTER_ADDS")
        parts += self._solve()
        parts += self._save()
        return "\n".join(parts).rstrip() + "\n"

    def write(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.render(), encoding="utf-8")
        return path


def write_inch(changes: ChangeSet, scenario: Scenario, base_case: Path,
               out_case: Path, inch_path: Path) -> Path:
    """Render and write one scenario's INCH file."""
    return InchWriter(changes, scenario, base_case, out_case).write(inch_path)


def read_save_target(inch_file: Path) -> tuple[str, int, Path] | None:
    """(format, version, path) from an existing INCH file's #SAVE record.

    Lets the engine learn where a hand-written INCH intends to write its case
    without being told twice — the INCH file stays the single source of truth
    for its own output.
    """
    lines = Path(inch_file).read_text(encoding="utf-8",
                                      errors="replace").splitlines()
    for i, line in enumerate(lines):
        if not line.strip().upper().startswith("#SAVE"):
            continue
        cols = [c.strip() for c in
                line[line.index("[") + 1:line.index("]")].split(",")] \
            if "[" in line and "]" in line else ["Format", "Version",
                                                 "FileName"]
        for value_line in lines[i + 1:]:
            s = value_line.strip()
            if not s or s.startswith("//"):
                continue
            if s.upper().startswith("#END"):
                break
            # FileName can contain commas only if quoted; split off the first
            # two fields and keep the remainder whole.
            parts = s.split(",", len(cols) - 1)
            if len(parts) < 3:
                break
            data = dict(zip(cols, [p.strip().strip('"') for p in parts]))
            try:
                return (data["Format"].upper(), int(float(data["Version"])),
                        Path(data["FileName"]))
            except (KeyError, ValueError):
                break
    return None
