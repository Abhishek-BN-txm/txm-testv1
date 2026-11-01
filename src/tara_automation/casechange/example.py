"""A worked Eagle Eye example workbook, built on real 30HS_22-G7 case data.

Its purpose is deliberate: the four scenarios isolate the four INCH command
families, so a manual TARA load tells us which family works and which does
not, instead of one failure hiding the rest.

    S1  ADD_*     new buses, GSU, POI tie line, 300 MW plant, a new load
    S2  MODIFY_*  S1 plus an existing-unit redispatch and a line re-rating
    S3  SCALE_*   S1 plus area 14 load and generation scaling
    S4  TAP       tap DUGAS-MORGAN 500 kV and hang the plant off the tap

Every existing element referenced below was read out of
``30HS_22-G7_pre.epc`` (area 14, APS):

    14005 WESTWING 500   14011 MORGAN 500     14018 DUGAS 500
    14205 COCONINO 230   14823 CEDARMT gen '1' (Pgen 35.0, Pmax 99.2)
    14005-14011 ckt 1: R 0.000088  X 0.001962  B 0.148290  2390 MVA  8.0 mi

The new POI tie borrows the WESTWING-MORGAN per-mile impedance, scaled to
12.5 miles.
"""

from __future__ import annotations

from pathlib import Path

from tara_automation.casechange.workbook import append_rows, write_template

# WESTWING-MORGAN per-mile impedance (8.0 mi), scaled to a 12.5 mi tie.
_MI = 12.5
TIE_R = round(0.000088 / 8.0 * _MI, 7)
TIE_X = round(0.001962 / 8.0 * _MI, 7)
TIE_B = round(0.148290 / 8.0 * _MI, 6)

ADD_SCENARIOS = "S1,S2,S3"

SCENARIOS = [
    dict(ScenarioID="S1", Name="POI adds only",
         Description="New 500 kV POI bus, 34.5 kV collector bus, GSU, 12.5 mi "
                     "tie to WESTWING 500, 300 MW plant, one new load. "
                     "Exercises ADD_BUS / ADD_GEN / ADD_LOAD / ADD_BRANCH / "
                     "ADD_TRANSFORMER only.",
         OutputFormat="EPC", OutputVersion=21, Solve="Y", NewBusStart=990001,
         Enabled="Y", Comment="syntax probe: ADD family"),
    dict(ScenarioID="S2", Name="POI plus modifications",
         Description="S1 plus a redispatch of the existing CEDARMT wind unit "
                     "and a re-rating of DUGAS-MORGAN 500. Exercises "
                     "MODIFY_GEN and MODIFY_BRANCH.",
         OutputFormat="EPC", OutputVersion=21, Solve="Y", NewBusStart=990001,
         Enabled="Y", Comment="syntax probe: MODIFY family"),
    dict(ScenarioID="S3", Name="POI plus area scaling",
         Description="S1 plus area 14 load up 250 MW and area 14 generation "
                     "up 250 MW to balance it. Exercises SCALE_LOAD and "
                     "SCALE_GEN.",
         OutputFormat="EPC", OutputVersion=21, Solve="Y", NewBusStart=990001,
         Enabled="Y", Comment="syntax probe: SCALE family"),
    dict(ScenarioID="S4", Name="Line tap variant",
         Description="Tap DUGAS-MORGAN 500 kV at 30% from DUGAS to create the "
                     "POI, then GSU and plant off the tap bus. Exercises TAP.",
         OutputFormat="EPC", OutputVersion=21, Solve="Y", NewBusStart=990001,
         Enabled="Y", Comment="syntax probe: TAP"),
]

NEW_BUSES = [
    dict(Scenarios=ADD_SCENARIOS, BusName="EAGLEEYE5", BusNomVolt=500.0,
         BusType=1, AreaNum=14, ZoneNum=141, BusVoltPU=1.0, BusAngle=0.0,
         Comment="POI switchyard, 500 kV"),
    dict(Scenarios="ALL", BusName="EAGLEEYE34", BusNomVolt=34.5, BusType=2,
         AreaNum=14, ZoneNum=141, BusVoltPU=1.0, BusAngle=0.0,
         Comment="Collector bus (generator terminal)"),
]

LINE_TAPS = [
    dict(Scenarios="S4", BusNumFr=14018, BusNumTo=14011, CKT="1",
         PercentTap=30.0, NewBusName="EETAP500",
         Comment="Tap DUGAS-MORGAN 500 at 30% from DUGAS"),
]

NEW_TRANSFORMERS = [
    dict(Scenarios=ADD_SCENARIOS, W1Bus="@EAGLEEYE5", W2Bus="@EAGLEEYE34",
         W3Bus=0, CKT="1", XfmrName="EAGLEEYE GSU", Status=1,
         ImpedanceBasis="NAMEPLATE_PCT", R1_2=0.0, X1_2=10.5, SBAS1=350.0,
         W1RateA=350.0, W1RateB=350.0, W1RateC=350.0,
         W1NOMV=500.0, W2NOMV=34.5, W1WINV=1.0, W2WINV=1.0,
         W1CtrlBus=0, W1TargetMax=1.05, W1TargetMin=0.95,
         Comment="500/34.5 kV GSU, 10.5% on 350 MVA — entered as the "
                 "nameplate percentage and converted to 0.03 system-base pu"),
    dict(Scenarios="S4", W1Bus="@EETAP500", W2Bus="@EAGLEEYE34", W3Bus=0,
         CKT="1", XfmrName="EAGLEEYE GSU", Status=1,
         ImpedanceBasis="NAMEPLATE_PCT", R1_2=0.0, X1_2=10.5, SBAS1=350.0,
         W1RateA=350.0, W1RateB=350.0, W1RateC=350.0,
         W1NOMV=500.0, W2NOMV=34.5, W1WINV=1.0, W2WINV=1.0,
         W1CtrlBus=0, W1TargetMax=1.05, W1TargetMin=0.95,
         Comment="Same GSU, hung off the tap bus"),
]

NEW_BRANCHES = [
    dict(Scenarios=ADD_SCENARIOS, BusNumFr="@EAGLEEYE5", BusNumTo=14005,
         CKT="1", Status=1, LineR=TIE_R, LineX=TIE_X, Charge=TIE_B,
         RateA=2390.0, RateB=2390.0, RateC=2390.0, Length=_MI,
         BranchName="EAGLEEYE-WESTWING",
         Comment="POI tie; impedance scaled from WESTWING-MORGAN per mile"),
]

NEW_GENERATORS = [
    dict(Scenarios="ALL", BusNum="@EAGLEEYE34", GenID="1", Status=1,
         GenMW=300.0, GenMVR=0.0, GenMWMax=300.0, GenMWMin=0.0,
         VoltTarg=1.0, GenRMPCT=100.0, RSource=0.0, XSource=0.2,
         Comment="Eagle Eye 300 MW; Qmax/Qmin and MBase left blank so the "
                 "0.95 pf and Pmax defaults apply"),
]

NEW_LOADS = [
    dict(Scenarios=ADD_SCENARIOS, BusNum=14205, LoadID="Z", Status=1,
         LoadP0=25.0, LoadQ0=5.0,
         Comment="Test load at COCONINO 230; ID 'Z' to avoid colliding with "
                 "an existing load ID"),
]

UNIT_CHANGES = [
    dict(Scenarios="S2", Element="GEN", BusNum=14823, ID="1", Status=1,
         P=20.0,
         Comment="CEDARMT wind backed down from 35.0 to 20.0 MW"),
]

UPGRADES = [
    dict(Scenarios="S2", Element="BRANCH", BusNumFr=14018, BusNumTo=14011,
         W3Bus=0, CKT="1", RateA=2600.0, RateB=2800.0,
         Comment="Re-rate DUGAS-MORGAN 500 from 2390 MVA; impedance untouched"),
]

GEN_LOAD_SCALING = [
    dict(Scenarios="S3", Type="LOAD", SelectBy="AREA", Selector="14",
         ValueOption="2", Value=250.0, ScalingMethod=1, NonConform=0,
         ApplyOrder="AFTER_ADDS",
         Comment="Area 14 load up 250 MW, holding P/Q ratio"),
    dict(Scenarios="S3", Type="GEN", SelectBy="AREA", Selector="14",
         ValueOption="2", Value=250.0, ScalingMethod=1, MachineLimit=1,
         NegPgen=0, ApplyOrder="AFTER_ADDS",
         Comment="Area 14 generation up 250 MW to balance, respecting Pmax"),
]

_SHEET_ROWS = {
    "Scenarios": SCENARIOS,
    "Gen_Load_Scaling": GEN_LOAD_SCALING,
    "Unit_Changes": UNIT_CHANGES,
    "New_Buses": NEW_BUSES,
    "New_Generators": NEW_GENERATORS,
    "New_Loads": NEW_LOADS,
    "New_Branches": NEW_BRANCHES,
    "New_Transformers": NEW_TRANSFORMERS,
    "Line_Taps": LINE_TAPS,
    "Upgrades": UPGRADES,
}


def build_example_workbook(path: Path) -> Path:
    """Write the worked Eagle Eye example workbook to `path`."""
    path = Path(path)
    write_template(path)
    for sheet_name, rows in _SHEET_ROWS.items():
        if rows:
            append_rows(path, sheet_name, rows)
    return path
