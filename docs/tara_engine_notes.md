# TARA engine notes

Findings from running PowerGEM TARA 26.02 through this package, each
verified on the engine. Read before writing comm or INCH files.

- EPC read: `READ EPC <ver> "file" 0` inline. PSSE raw read: `READ PSSE
  <ver>` + filename on its OWN line + standalone `0` to exit the menu.
  NEVER `RAWD` — that is the raw *exporter* and will truncate the target
  file to 0 bytes and crash.
- Set dimension block (lfopt misc maxBuses...) BEFORE reading large cases.
- Loss totals: use `tarea`/AreaSum, not `tzone` (excludes inter-zone tie
  losses). Rate Cont 9999 = no emergency rating: use normal rating, always
  flag a warning. EPC-vs-RAW comparisons have known format artifacts —
  compare.py labels them.
- Case edits go through **INCH** files (manual ch. 4-6). Apply one with
  `READ CIMINCH "file.inch" 0 0` — after the case read, before the solve.
  That keyword is in NO manual; it came from the `_comm.txt` TARA generated
  when a case was loaded with an INCH attached. `#SAVE [Format,Version,
  FileName]` inside the INCH writes the modified case.
- INCH gotchas, all verified on the engine by reading the SAVED case
  (runs 31-34). `#ADD_TRANSFORMER`: `W1CtrlBus` is the ONLY required control
  column (0 is valid = fixed tap); `W1AdjStatus`/`W1ANG` are accepted and
  ignored; without `W1CtrlBus` the record is dropped with WarnID-1652, always
  trailed by a bogus WarnID-1607 from TARA's own retry. A three-winding record
  needs a control bus on EVERY winding. Every added transformer is stored as
  adjustment type 1 with a +/-99999 volt band unless `W1TargetMax/Min` are
  given — type 1 cannot be avoided.
- **TARA ignores `CZ`.** Transformer R/X is ALWAYS read as per unit on the
  100 MVA system base, whatever CZ says. `SBAS1` only sets the base the value
  is re-expressed on when stored. A 10.5% GSU on 350 MVA must be written as
  0.03; the workbook's `ImpedanceBasis = NAMEPLATE_PCT` does that conversion.
- Rating slot 4 stays 0 on new branches and transformers unless `RateD` /
  `W1Rate4` is given, which makes the element unrated for any study selecting
  rating 4. `RateA/B/C` == `Rate1/2/3`.
- `#SCALE_LOAD` takes `ValueOption`, never `PValueOption`: the wrong keyword
  is rejected with WarnID-1001 and then silently falls back to "total Pload",
  which cut area 14 load from 7485 MW to 2 MW.
- A TARA exit code of 0 does NOT mean the INCH applied. Every failure above
  exited 0 and wrote a full-size case. Always parse `Warn_Sum.csv` for WarnID
  1001/1607/1644/1652 — `commfile/casechange.py` does and fails the run.
- Guard against empty/small case files before running.
- Engine is TARA 26.02.1 (`C:\TARA2602_1\tara.exe`, since 2026-10-21; licence
  in HKLM\Software\PowerGEM). Always pass ABSOLUTE paths: TARA runs inside
  runs/N, and 26.02 given a file it cannot find waits for input forever (CPU
  busy, empty trace) instead of failing (runs 80-83). A run that ends at
  once with `_excel_nolicense.wake` in its folder = TARA not registered.
