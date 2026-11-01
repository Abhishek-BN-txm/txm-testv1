# Accepted findings registry

## 2026-07-30 — Rating convention (contingency analysis, any run)

- **Rate Cont = 9999 means no emergency rating defined.** Ruling: judge the
  loading against the normal rating for calculations, but always flag such
  facilities with a warning in summaries and verdicts (implemented in
  `violations.py` — `no_emergency_rating`). Never treat the 9999 as a real
  rating; never silently drop the flag.

Findings the user has reviewed and accepted. The review workflow cites these
instead of re-flagging them. One entry per finding; newest first.

## 2026-07-28 — comm-vs-pygem engine differences (validation scope)

- **Switched-shunt voltage pockets** — max |dV| up to ~0.09 pu at discrete
  switched-shunt clusters (e.g. ROGUE 115, 40893) because tara.exe defaults
  and pyTARA ACLF defaults adjust shunts differently. Accepted as within
  tolerance for engine equivalence; totals unaffected.
- **Swing dispatch shift** — up to ~10 MW on the swing unit (G_COULE_22)
  between independent solves; shifts bus angles globally by a fraction of a
  degree. Accepted.

## 2026-07-28 — lfreview table semantics (any comm run)

- **tzone TotLosses excludes inter-zone tie losses** (~300 MW on WECC-scale
  cases). Use tarea/AreaSum for loss totals. Accepted as a TARA reporting
  convention, not an error.

## 2026-07-28 — EPC vs RAW format artifacts (cross-format comparisons)

- 3W-transformer star buses (RAW auto-numbers 99xxxx; EPC named tap buses),
  series-cap bypass records (ckt BP, EPC-only), rating slots B–D mapping,
  metered-end flow signs. Auto-labeled by `compare.py`; EPC-side tap-bus
  records cannot be auto-classified and appear under "only in A".
