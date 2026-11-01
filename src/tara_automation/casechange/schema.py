"""Column schema for the case-change workbook.

Single source of truth for three things that must never drift apart:

  1. the .xlsx template written for the user (headers, dropdowns, help text),
  2. the reader/validator that parses a filled-in workbook,
  3. the README sheet documenting every column.

Column names on the change sheets are TARA INCH keywords wherever a keyword
exists (manual ch. 5.7.1), so a filled row maps to an INCH record without a
translation table. Where a keyword is unfriendly (``W1Bus#``) the sheet uses a
safe alias and ``inch_name`` carries the real keyword.

Fields the engineer does not care about are optional and carry defaults —
blank means "use the default", never "zero".
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Bus references accept a literal bus number or "@Name", naming a row in the
# New_Buses / Line_Taps sheets whose number was auto-assigned. Resolved in
# model.py.
BUS_REF_PREFIX = "@"


@dataclass(frozen=True)
class Column:
    name: str                      # header text in the sheet
    kind: str = "text"             # text|int|float|bus|ckt|flag|choice|scenarios
    required: bool = False
    default: object = None
    choices: tuple[str, ...] = ()
    help: str = ""
    inch_name: str | None = None   # INCH keyword, when it differs from `name`
    width: int = 14

    @property
    def keyword(self) -> str:
        return self.inch_name or self.name


@dataclass(frozen=True)
class Sheet:
    name: str
    purpose: str
    columns: tuple[Column, ...]
    example: tuple = ()            # one illustrative row, written greyed-out
    notes: tuple[str, ...] = field(default_factory=tuple)

    def column(self, name: str) -> Column | None:
        low = name.strip().lower()
        return next((c for c in self.columns if c.name.lower() == low), None)


# --------------------------------------------------------------- shared ------
def _scen() -> Column:
    return Column(
        "Scenarios", kind="scenarios", required=True, default="ALL",
        help="Which scenarios this row belongs to: ALL, one ScenarioID, or a "
             "comma-separated list (S1,S3). This is what lets one workbook "
             "drive many scenarios without duplicating rows.",
        width=16)


def _comment() -> Column:
    return Column("Comment", help="Free text — carried into the INCH file as a "
                                  "comment. Say why the change exists.", width=34)


# ------------------------------------------------------------- Scenarios -----
SCENARIOS = Sheet(
    "Scenarios",
    "One row per scenario. Every other sheet tags its rows with these IDs.",
    (
        Column("ScenarioID", required=True, width=12,
               help="Short unique key used in the Scenarios column of every "
                    "other sheet, e.g. S1. Also names the generated files."),
        Column("Name", required=True, width=28,
               help="Human-readable scenario name, e.g. 'Project + White Hills'."),
        Column("Description", width=52,
               help="What this scenario represents and why it exists. Carried "
                    "into the INCH header, the run manifest and the summary "
                    "report — this is the sentence a reviewer reads first."),
        Column("BaseCase", width=34,
               help="Case this scenario modifies: a full path, or a case label "
                    "from study.json. Blank = the study's baseline case."),
        Column("OutputFormat", kind="choice", choices=("EPC", "RAW"),
               default="EPC", width=13,
               help="Format of the modified case TARA writes out."),
        Column("OutputVersion", kind="int", width=13,
               help="Blank = 21 for EPC, 33 for RAW. EPC 18/19/21, RAW 28-33."),
        Column("OutputName", width=30,
               help="File name for the modified case. Blank = "
                    "<basecase stem>_<ScenarioID>.<ext>."),
        Column("Solve", kind="flag", default="Y", width=8,
               help="Y = solve (#SOLVE_FDEC) after applying changes and before "
                    "saving. Leave Y unless you are deliberately saving an "
                    "unsolved case."),
        Column("NewBusStart", kind="int", default=990001, width=13,
               help="First bus number handed out when New_Buses.BusNum is left "
                    "blank. Pick a block that is free in your case."),
        Column("Enabled", kind="flag", default="Y", width=9,
               help="N = skip this scenario entirely. Lets you park a scenario "
                    "without deleting its rows."),
        _comment(),
    ),
    example=("S1", "Project only", "Eagle Eye 300 MW at new POI, no network "
             "upgrades", "", "EPC", 21, "", "Y", 990001, "Y",
             "delete this row and enter your own scenarios"),
    notes=(
        "Scenarios are independent: each starts from its own clean copy of the "
        "base case. Changes never accumulate from one scenario to the next.",
    ),
)

# ------------------------------------------------------ Gen_Load_Scaling -----
GEN_LOAD_SCALING = Sheet(
    "Gen_Load_Scaling",
    "Bulk generation / load changes over an area, zone, bus or subsystem.",
    (
        _scen(),
        Column("Type", kind="choice", required=True, choices=("GEN", "LOAD"),
               width=9, help="GEN -> #SCALE_GEN, LOAD -> #SCALE_LOAD."),
        Column("SelectBy", kind="choice", required=True, width=13,
               choices=("AREA", "ZONE", "BUS", "SUBSYSTEM"),
               help="What the scaling applies to. TARA allows exactly one "
                    "selector per scaling record."),
        Column("Selector", required=True, width=16,
               help="The area/zone/bus number, or the subsystem name as "
                    "defined in the study (.sub) file."),
        Column("ValueOption", kind="choice", default=1, width=13,
               choices=("1", "2", "3", "4"),
               help="How to read Value. 1 = total MW target (default), "
                    "2 = incremental MW (+/-), 3 = percent change, "
                    "4 = Pgen/Pmax ratio in per-unit (GEN, methods 5-6 only)."),
        Column("Value", kind="float", required=True, width=12,
               help="Target generation, or new P load, interpreted per "
                    "ValueOption. For LOAD this is the P value."),
        Column("QValue", kind="float", width=12,
               help="LOAD only. Blank = hold the existing P/Q ratio, which is "
                    "what you normally want."),
        Column("ScalingMethod", kind="int", default=1, width=14,
               help="GEN: 1 = pro-rata on Pgen (default), 2 = to Pmin, "
                    "3 = to Pmax, 4 = to Pmax incl. offline, 5 = equal "
                    "Pgen/Pmax, 6 = equal Pgen/Pmax incl. offline. "
                    "LOAD: 1 = constant P/Q ratio, 2 = specify Q."),
        Column("MachineLimit", kind="int", default=0, width=13,
               help="GEN only. 1 = respect Pmin/Pmax and drop units that would "
                    "violate them; 0 = ignore limits (default)."),
        Column("NegPgen", kind="int", default=0, width=10,
               help="GEN only. 1 = include units with negative Pgen."),
        Column("NonConform", kind="int", default=0, width=12,
               help="LOAD only. 1 = include non-conforming load."),
        Column("ApplyOrder", kind="choice", default="AFTER_ADDS", width=14,
               choices=("AFTER_ADDS", "BEFORE_ADDS"),
               help="AFTER_ADDS (default): scale after new units/loads are "
                    "added, so a total-MW target counts the new equipment. "
                    "BEFORE_ADDS: scale the pre-existing fleet only, then add "
                    "new equipment on top."),
        _comment(),
    ),
    example=("ALL", "LOAD", "AREA", "14", "2", 250.0, "", 1, 0, 0, 0,
             "AFTER_ADDS", "2030 load forecast uplift for area 14"),
    notes=(
        "ValueOption 1 sets an absolute total. If the selection also gains a "
        "new generator on the New_Generators sheet, that unit is inside the "
        "total unless you set ApplyOrder = BEFORE_ADDS.",
        "TARA permits one selector per record. To scale three areas, use three "
        "rows.",
    ),
)

# ----------------------------------------------------------- Unit_Changes ----
UNIT_CHANGES = Sheet(
    "Unit_Changes",
    "Point changes to an existing generator or load (dispatch, status, limits).",
    (
        _scen(),
        Column("Element", kind="choice", required=True, choices=("GEN", "LOAD"),
               width=9, help="GEN -> #MODIFY_GEN, LOAD -> #MODIFY_LOAD."),
        Column("BusNum", kind="bus", required=True, width=12,
               help="Bus the unit or load sits on."),
        Column("ID", required=True, default="1", width=8,
               help="Machine ID or load ID as it appears in the case."),
        Column("Status", kind="int", width=9,
               help="1 = in service, 0 = out. Blank = leave unchanged."),
        Column("P", kind="float", width=11,
               help="GEN: new Pgen MW. LOAD: new P load MW. Blank = unchanged."),
        Column("Q", kind="float", width=11,
               help="GEN: new Qgen MVAr. LOAD: new Q load MVAr."),
        Column("Pmax", kind="float", width=11, inch_name="GenMWMax",
               help="GEN only."),
        Column("Pmin", kind="float", width=11, inch_name="GenMWMin",
               help="GEN only."),
        Column("Qmax", kind="float", width=11, inch_name="GenMVRMax",
               help="GEN only."),
        Column("Qmin", kind="float", width=11, inch_name="GenMVRMin",
               help="GEN only."),
        Column("VoltTarg", kind="float", width=11,
               help="GEN only. Scheduled voltage, per unit."),
        Column("GenRegBusNum", kind="bus", width=13,
               help="GEN only. Remotely regulated bus."),
        _comment(),
    ),
    example=("S2", "GEN", 14205, "1", 0, "", "", "", "", "", "", "", "",
             "Retire unit ahead of project in-service"),
    notes=(
        "Only the columns you fill in are written to the INCH record, so a row "
        "that changes nothing but Status leaves dispatch untouched.",
        "P/Q map to GenMW/GenMVR for a generator and LoadP0/LoadQ0 for a load.",
    ),
)

# -------------------------------------------------------------- New_Buses ----
NEW_BUSES = Sheet(
    "New_Buses",
    "Buses to add. Referenced from other sheets as @BusName.",
    (
        _scen(),
        Column("BusNum", kind="int", width=12,
               help="Blank = auto-assigned from the scenario's NewBusStart. "
                    "Other sheets can then reference this bus as @BusName."),
        Column("BusName", required=True, width=20,
               help="Bus name, max 12 characters for EPC/RAW compatibility. "
                    "Also the key for @Name references on other sheets, so "
                    "keep it unique within a scenario."),
        Column("BusNomVolt", kind="float", required=True, width=12,
               help="Nominal kV."),
        Column("BusType", kind="int", default=1, width=10,
               help="1 = load/PQ (default), 2 = generator/PV, 3 = swing."),
        Column("AreaNum", kind="int", required=True, width=10),
        Column("ZoneNum", kind="int", width=10),
        Column("OwnerNum", kind="int", width=11),
        Column("BusVoltPU", kind="float", default=1.0, width=11,
               help="Initial voltage, per unit."),
        Column("BusAngle", kind="float", default=0.0, width=11,
               help="Initial angle, degrees."),
        _comment(),
    ),
    example=("S1", "", "EAGLEEYE500", 500.0, 2, 14, 1420, 320, 1.0, 0.0,
             "New POI collector bus"),
    notes=(
        "INCH requires equipment to exist before it is referenced. The "
        "generator writes buses, then taps, then generators and loads, then "
        "branches and transformers — you do not have to order the sheets.",
    ),
)

# --------------------------------------------------------- New_Generators ----
NEW_GENERATORS = Sheet(
    "New_Generators",
    "Generators to add.",
    (
        _scen(),
        Column("BusNum", kind="bus", required=True, width=13,
               help="Existing bus number, or @BusName from New_Buses."),
        Column("GenID", required=True, default="1", width=8),
        Column("Status", kind="int", default=1, width=9,
               help="1 = in service (default)."),
        Column("GenMW", kind="float", required=True, width=11,
               help="Dispatch, MW."),
        Column("GenMVR", kind="float", default=0.0, width=11),
        Column("GenMWMax", kind="float", required=True, width=11,
               help="Pmax, MW. Required — redispatch and scaling depend on it."),
        Column("GenMWMin", kind="float", default=0.0, width=11),
        Column("GenMVRMax", kind="float", width=11,
               help="Blank = 0.3286 x Pmax (about 0.95 power factor)."),
        Column("GenMVRMin", kind="float", width=11,
               help="Blank = -GenMVRMax."),
        Column("VoltTarg", kind="float", default=1.0, width=11,
               help="Scheduled voltage, per unit."),
        Column("GenRegBusNum", kind="bus", width=13,
               help="Blank = regulate own terminal bus."),
        Column("GenRMPCT", kind="float", default=100.0, width=11),
        Column("MBase", kind="float", width=11,
               help="Machine MVA base. Blank = GenMWMax."),
        Column("RSource", kind="float", default=0.0, width=11),
        Column("XSource", kind="float", default=0.2, width=11,
               help="Source reactance, per unit on MBase."),
        Column("OwnerNum", kind="int", width=11, inch_name="Own1"),
        _comment(),
    ),
    example=("S1", "@EAGLEEYE500", "1", 1, 300.0, 0.0, 300.0, 0.0, "", "",
             1.0, "", 100.0, "", 0.0, 0.2, 320, "Eagle Eye solar, 300 MW"),
)

# -------------------------------------------------------------- New_Loads ----
NEW_LOADS = Sheet(
    "New_Loads",
    "Loads to add.",
    (
        _scen(),
        Column("BusNum", kind="bus", required=True, width=13,
               help="Existing bus number, or @BusName from New_Buses."),
        Column("LoadID", required=True, default="1", width=8),
        Column("Status", kind="int", default=1, width=9),
        Column("LoadP0", kind="float", required=True, width=11,
               help="Constant-power P, MW."),
        Column("LoadQ0", kind="float", default=0.0, width=11,
               help="Constant-power Q, MVAr."),
        Column("LoadArea", kind="int", width=11,
               help="Blank = the bus's area."),
        Column("LoadZone", kind="int", width=11,
               help="Blank = the bus's zone."),
        Column("LoadOwner", kind="int", width=11),
        _comment(),
    ),
    example=("S3", 14310, "1", 1, 85.0, 20.0, "", "", "", "New data centre"),
)

# ----------------------------------------------------------- New_Branches ----
NEW_BRANCHES = Sheet(
    "New_Branches",
    "AC transmission lines to add.",
    (
        _scen(),
        Column("BusNumFr", kind="bus", required=True, width=13,
               help="From bus: a number, or @BusName from New_Buses."),
        Column("BusNumTo", kind="bus", required=True, width=13,
               help="To bus: a number, or @BusName from New_Buses."),
        Column("CKT", kind="ckt", required=True, default="1", width=7),
        Column("Status", kind="int", default=1, width=9),
        Column("LineR", kind="float", required=True, width=11,
               help="Series resistance, per unit on 100 MVA."),
        Column("LineX", kind="float", required=True, width=11,
               help="Series reactance, per unit on 100 MVA."),
        Column("Charge", kind="float", default=0.0, width=11,
               help="Total line charging B, per unit."),
        Column("RateA", kind="float", required=True, width=11,
               help="Normal rating, MVA."),
        Column("RateB", kind="float", width=11,
               help="Emergency rating, MVA. Blank = RateA."),
        Column("RateC", kind="float", width=11,
               help="Short-term rating, MVA. Blank = RateB."),
        Column("RateD", kind="float", width=11,
               help="Fourth rating slot, MVA. Blank = RateC. Filled so a "
                    "study selecting rating 4 does not see the line as "
                    "unrated — TARA leaves the slot at 0 otherwise."),
        Column("Length", kind="float", default=0.0, width=10, help="Miles."),
        Column("BranchName", width=22,
               help="Shows up in violation reports — worth filling in."),
        Column("OwnerNum", kind="int", width=11, inch_name="Own1"),
        _comment(),
    ),
    example=("S1", "@EAGLEEYE500", 14205, "1", 1, 0.00018, 0.00395, 0.2947,
             1732.0, "", "", "", 12.5, "EAGLEEYE-LIBERTY", 320,
             "POI radial tie, 12.5 mi 500 kV"),
    notes=(
        "Each blank rating inherits the one before it, so a single-rating "
        "line needs one number and all four slots are filled. Never leave a "
        "new line at a zero rating: TARA reads 0 as unrated and drops it from "
        "monitoring.",
    ),
)

# ------------------------------------------------------- New_Transformers ----
NEW_TRANSFORMERS = Sheet(
    "New_Transformers",
    "Two- or three-winding transformers to add.",
    (
        _scen(),
        Column("W1Bus", kind="bus", required=True, width=12, inch_name="W1Bus#",
               help="Winding 1 bus (high side by convention)."),
        Column("W2Bus", kind="bus", required=True, width=12, inch_name="W2Bus#",
               help="Winding 2 bus."),
        Column("W3Bus", kind="bus", default=0, width=12, inch_name="W3Bus#",
               help="Winding 3 bus. Blank/0 = two-winding."),
        Column("CKT", kind="ckt", required=True, default="1", width=7),
        Column("XfmrName", width=22),
        Column("Status", kind="int", default=1, width=9),
        Column("ImpedanceBasis", kind="choice", default="SYSTEM_100MVA",
               choices=("SYSTEM_100MVA", "NAMEPLATE_PCT"), width=16,
               help="How to read the R/X columns. SYSTEM_100MVA (default): "
                    "per unit on the 100 MVA system base — what TARA stores. "
                    "NAMEPLATE_PCT: percent on SBAS1, the number off the "
                    "nameplate, converted for you (10.5 on 350 MVA -> 0.03). "
                    "TARA ignores CZ, so this conversion is done here, not "
                    "by the engine."),
        Column("R1_2", kind="float", default=0.0, width=10, inch_name="R1-2",
               help="Winding 1-2 resistance, in ImpedanceBasis units."),
        Column("X1_2", kind="float", required=True, width=10, inch_name="X1-2",
               help="Winding 1-2 reactance, in ImpedanceBasis units."),
        Column("R2_3", kind="float", width=10, inch_name="R2-3",
               help="Three-winding only."),
        Column("X2_3", kind="float", width=10, inch_name="X2-3",
               help="Three-winding only."),
        Column("R3_1", kind="float", width=10, inch_name="R3-1",
               help="Three-winding only."),
        Column("X3_1", kind="float", width=10, inch_name="X3-1",
               help="Three-winding only."),
        Column("SBAS1", kind="float", width=10,
               help="Nameplate MVA. Blank = W1RateA. Sets the base the stored "
                    "impedance is expressed on, and is the conversion base "
                    "when ImpedanceBasis is NAMEPLATE_PCT."),
        Column("W1RateA", kind="float", required=True, width=11,
               help="Normal rating, MVA."),
        Column("W1RateB", kind="float", width=11, help="Blank = W1RateA."),
        Column("W1RateC", kind="float", width=11, help="Blank = W1RateB."),
        Column("W2RateA", kind="float", width=11,
               help="Three-winding only. Blank = W1RateA."),
        Column("W3RateA", kind="float", width=11,
               help="Three-winding only. Blank = W1RateA."),
        Column("W1NOMV", kind="float", width=11,
               help="Winding 1 nominal kV. Blank = the bus's nominal kV."),
        Column("W2NOMV", kind="float", width=11,
               help="Winding 2 nominal kV. Blank = the bus's nominal kV."),
        Column("W3NOMV", kind="float", width=11,
               help="Three-winding only."),
        Column("W1WINV", kind="float", default=1.0, width=11,
               help="Winding 1 turns ratio, per unit on the bus base kV."),
        Column("W2WINV", kind="float", default=1.0, width=11),
        Column("W1CtrlBus", kind="bus", default=0, width=12,
               help="Bus winding 1 regulates. 0 (default) = none, which is "
                    "the fixed-tap GSU case. This column MUST be present — "
                    "TARA drops the whole record without it (WarnID-1652) — "
                    "so the writer always emits it."),
        Column("W1TargetMax", kind="float", default=1.05, width=12,
               help="Top of the tap-control voltage band, per unit. Every "
                    "added transformer is stored as tap-controlling whatever "
                    "you do, so leaving this blank would give it a +/-99999 "
                    "band."),
        Column("W1TargetMin", kind="float", default=0.95, width=12,
               help="Bottom of the tap-control voltage band, per unit."),
        _comment(),
    ),
    example=("S1", "@EAGLEEYE500", "@EAGLEEYE34", 0, "1", "EE GSU", 1,
             "NAMEPLATE_PCT", 0.0, 10.5, "", "", "", "", 350.0,
             350.0, "", "", "", "", 500.0, 34.5, "", 1.0, 1.0,
             0, 1.05, 0.95, "Generator step-up, 10.5% on 350 MVA"),
    notes=(
        "TARA ignores CZ: whatever you write for R/X is read as per unit on "
        "the 100 MVA system base. Set ImpedanceBasis to NAMEPLATE_PCT to "
        "enter the nameplate percentage instead and have it converted here.",
        "A three-winding record needs a control bus on every winding; the "
        "writer supplies all three. Fill W3Bus and the 2-3 / 3-1 impedances "
        "to get one.",
        "Rating slot 4 is filled from W1RateC so a study selecting the fourth "
        "rating does not see the transformer as unrated.",
    ),
)

# -------------------------------------------------------------- Line_Taps ----
LINE_TAPS = Sheet(
    "Line_Taps",
    "Tap an existing line to create a new bus — the usual POI arrangement.",
    (
        _scen(),
        Column("BusNumFr", kind="bus", required=True, width=13,
               help="From bus of the line being tapped."),
        Column("BusNumTo", kind="bus", required=True, width=13),
        Column("CKT", kind="ckt", required=True, default="1", width=7),
        Column("PercentTap", kind="float", required=True, width=12,
               help="Distance from the FROM bus, percent. 30 = 30% of the "
                    "line's impedance on the from-side segment."),
        Column("NewBusNum", kind="int", width=12,
               help="Blank = auto-assigned from NewBusStart."),
        Column("NewBusName", required=True, width=20,
               help="Name for the tap bus. Referenceable as @Name elsewhere."),
        _comment(),
    ),
    example=("S3", 14205, 14310, "1", 30.0, "", "EETAP500",
             "Tap Liberty-Prescott 500 for the POI"),
    notes=(
        "TARA splits the line at PercentTap and inherits bus data from the "
        "FROM bus. The tap bus is created as a load (PQ) bus — add a generator "
        "to it on the New_Generators sheet if that is the interconnection.",
    ),
)

# --------------------------------------------------------------- Upgrades ----
UPGRADES = Sheet(
    "Upgrades",
    "Changes to existing lines and transformers: re-rating, reconductoring, "
    "impedance, in/out of service.",
    (
        _scen(),
        Column("Element", kind="choice", required=True, width=13,
               choices=("BRANCH", "TRANSFORMER"),
               help="BRANCH -> #MODIFY_BRANCH, TRANSFORMER -> "
                    "#MODIFY_TRANSFORMER."),
        Column("BusNumFr", kind="bus", required=True, width=13,
               help="From bus (BRANCH) or winding 1 bus (TRANSFORMER)."),
        Column("BusNumTo", kind="bus", required=True, width=13,
               help="To bus (BRANCH) or winding 2 bus (TRANSFORMER)."),
        Column("W3Bus", kind="bus", default=0, width=11,
               help="TRANSFORMER only: winding 3 bus, 0 for two-winding."),
        Column("CKT", kind="ckt", required=True, default="1", width=7),
        Column("Status", kind="int", width=9,
               help="1 = in, 0 = out. Blank = leave unchanged."),
        Column("LineR", kind="float", width=11,
               help="Blank = leave unchanged. For a transformer this is R1-2."),
        Column("LineX", kind="float", width=11,
               help="For a transformer this is X1-2."),
        Column("Charge", kind="float", width=11, help="BRANCH only."),
        Column("RateA", kind="float", width=11,
               help="New normal rating, MVA. Blank = leave unchanged."),
        Column("RateB", kind="float", width=11),
        Column("RateC", kind="float", width=11),
        Column("RateD", kind="float", width=11,
               help="BRANCH only: fourth rating slot."),
        _comment(),
    ),
    example=("S2", "BRANCH", 14205, 14310, 0, "1", "", "", "", "", 1732.0,
             1905.0, "", "", "Reconductor to 1732/1905 MVA"),
    notes=(
        "Only the columns you fill in are written, so re-rating a line does "
        "not disturb its impedance.",
        "A rating change here is permanent in the saved case. For a "
        "contingency-specific rating, use the exclude file instead (manual "
        "2.6.2) — that is a different mechanism.",
    ),
)


SHEETS: tuple[Sheet, ...] = (
    SCENARIOS, GEN_LOAD_SCALING, UNIT_CHANGES, NEW_BUSES, NEW_GENERATORS,
    NEW_LOADS, NEW_BRANCHES, NEW_TRANSFORMERS, LINE_TAPS, UPGRADES,
)

CHANGE_SHEETS: tuple[Sheet, ...] = SHEETS[1:]   # everything except Scenarios

README_SHEET = "README"


def sheet(name: str) -> Sheet:
    low = name.strip().lower()
    for s in SHEETS:
        if s.name.lower() == low:
            return s
    raise KeyError(f"Unknown sheet: {name}")
