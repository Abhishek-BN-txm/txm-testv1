"""Definitions for the 'key loadflow data' the comm engine extracts.

TARA writes its native lfreview CSVs (COMM_REPORTS below). The pyPowerGEM
engine and its PYGEM_TABLES schema were retired on 2026-09-24.
"""

# Full base-case report set for the comm engine, mirroring the TARA UI's
# "all data" export (user-provided _comm.txt, 2026-07-27).
# Each entry: (output filename stem, command prefix, table spec incl. args).
COMM_REPORTS: list[tuple[str, str, str]] = [
    ("Warn_Sum",                   "warn",           "sum 0 0"),
    ("Warn_List",                  "warn",           "list 0 0"),
    ("CaseSum",                    "lfreview",       "sum 0 0"),
    ("AreaSum",                    "lfreview",       "tarea 1 0 0"),
    ("ZoneSum",                    "lfreview",       "tzone 1 0 0"),
    ("BusData",                    "lfreview",       "bus 0 0"),
    ("BusMism",                    "lfreview",       "mism 0 0"),
    ("GenData",                    "lfreview",       "gener 0 0"),
    ("PlantData",                  "lfreview",       "plant 0 0"),
    ("LoadData",                   "lfreview",       "load 0 0"),
    ("BranchData",                 "lfreview",       "branch 0 0"),
    ("BranchDbData",               "lfreview",       "dbran 0 0"),
    ("TransfmrData",               "lfreview",       "tbran 0 0"),
    ("TieData",                    "lfreview",       "tie 0 0"),
    ("TieDbData",                  "lfreview",       "dtie 0 0"),
    ("ShuntData",                  "lfreview",       "shunt 0 0"),
    ("BaseShuntAdj",               "lfreview",       "AdjShuntBase 0 0"),
    ("PARsData",                   "lfreview",       "par 0 0"),
    ("ImpedCorr",                  "lfreview",       "ImpedCorr 0 0"),
    ("MultiSectLn",                "lfreview",       "MSECLN 0 0"),
    ("DCLineBusFlows",             "lfreview 2term", "termflow 0 0 0"),
    ("DC2TFlows",                  "lfreview 2term", "dc2res 0 0 0"),
    ("DC2TData",                   "lfreview 2term", "dc_2T 0 0 0"),
    ("FACTS",                      "lfreview",       "facts 0 0"),
    ("VSCData",                    "lfreview",       "VSC 0 0"),
    ("SubstationBreakerData",      "lfreview",       "Breaker 0 0"),
    ("SubstationNodeTerminalData", "lfreview",       "NodeTerminal 0 0"),
    ("InterAreaTran",              "lfreview",       "iartrn 0 0"),
    ("Subsys",                     "lfreview",       "subsys 0 0"),
    ("OwnerList",                  "lfreview",       "owner 0 0"),
    ("Dimensions",                 "lfreview",       "dimensions 0 0"),
]

# Filename stems only (used for output checks)
COMM_REPORT_NAMES: list[str] = [name for name, _, _ in COMM_REPORTS]
