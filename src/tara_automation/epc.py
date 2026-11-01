"""Read a GE PSLF .epc case (text) into keyed element records.

The comm engine's result CSVs are enough to report on a solved case, but not
to rebuild an element in another case: they lack the transformer MVA base,
winding kV, three-winding grouping, element names and the tap data. The .epc
has all of it, so cross-case work (task 27 differences, task 28 carrying
projects) reads the .epc directly. No TARA run is needed.

Format (as written by PSLF 22 / TARA): a section starts with a header line
`<name> data  [<count>]  <column labels>`; each record is whitespace-separated
tokens, names in double quotes, a `:` between the identity fields and the
data, and a trailing `/` continuing the record on the next line. Sections whose
header starts with `#` are trailing metadata and are skipped.

Field positions below were read off real records (30HS_22-G7_pre.epc,
`scratchpad/epc_probe.py`) and are checked against TARA's own BranchData /
GenData exports by tests/test_epc.py. `pre` = tokens before the colon,
`post` = after it.

Every record keeps its full token lists, so no field is lost even when this
module does not name it.
"""

from __future__ import annotations

import hashlib
import pickle
import re
from dataclasses import dataclass, field
from pathlib import Path

SECTIONS = ("bus", "branch", "transformer", "generator", "load", "shunt", "svd")
_HEADER = re.compile(r"^([a-z][a-z ]*?) data\s+\[\s*(\d+)\]")
_TOKEN = re.compile(r'"[^"]*"|:|[^\s"]+')

# Named fields per section: name -> ("pre"|"post", index). Only what the
# difference table and the grouping need; the rest stays in the raw tokens.
FIELDS: dict[str, dict[str, tuple[str, int]]] = {
    "bus": {
        "bus": ("pre", 0), "name": ("pre", 1), "kv": ("pre", 2),
        "ty": ("post", 0), "vsched": ("post", 1), "volt": ("post", 2), "angle": ("post", 3),
        "area": ("post", 4), "zone": ("post", 5), "vmax": ("post", 6), "vmin": ("post", 7),
        "owner": ("post", 12), "lat": ("post", 14), "lon": ("post", 15),
    },
    "branch": {
        "from": ("pre", 0), "from_name": ("pre", 1), "from_kv": ("pre", 2),
        "to": ("pre", 3), "to_name": ("pre", 4), "to_kv": ("pre", 5),
        "ck": ("pre", 6), "se": ("pre", 7), "long_id": ("pre", 8),
        "st": ("post", 0), "r": ("post", 1), "x": ("post", 2), "b": ("post", 3),
        "rate1": ("post", 4), "rate2": ("post", 5), "rate3": ("post", 6), "rate4": ("post", 7),
        "length": ("post", 9), "area": ("post", 10), "zone": ("post", 11), "owner": ("post", 24),
    },
    "transformer": {
        "from": ("pre", 0), "from_name": ("pre", 1), "from_kv": ("pre", 2),
        "to": ("pre", 3), "to_name": ("pre", 4), "to_kv": ("pre", 5),
        "ck": ("pre", 6), "long_id": ("pre", 7),
        "st": ("post", 0), "ty": ("post", 1), "reg_bus": ("post", 2),
        "tert": ("post", 9), "tert_name": ("post", 10), "tert_kv": ("post", 11),
        "area": ("post", 12), "zone": ("post", 13), "tbase": ("post", 14),
        "ps_r": ("post", 15), "ps_x": ("post", 16), "pt_r": ("post", 17), "pt_x": ("post", 18),
        "ts_r": ("post", 19), "ts_x": ("post", 20),
        "vnomp": ("post", 21), "vnoms": ("post", 22), "vnomt": ("post", 23),
        "rate1": ("post", 27), "rate2": ("post", 28), "rate3": ("post", 29), "rate4": ("post", 30),
        "tmax": ("post", 32), "tmin": ("post", 33), "vtmax": ("post", 34), "vtmin": ("post", 35),
        "step": ("post", 36), "tap": ("post", 37), "tapfp": ("post", 38), "tapfs": ("post", 39),
    },
    "generator": {
        "bus": ("pre", 0), "name": ("pre", 1), "kv": ("pre", 2), "id": ("pre", 3), "long_id": ("pre", 4),
        "st": ("post", 0), "reg_bus": ("post", 1), "area": ("post", 6), "zone": ("post", 7),
        "pgen": ("post", 8), "pmax": ("post", 9), "pmin": ("post", 10),
        "qgen": ("post", 11), "qmax": ("post", 12), "qmin": ("post", 13), "mbase": ("post", 14),
    },
    "load": {
        "bus": ("pre", 0), "name": ("pre", 1), "kv": ("pre", 2), "id": ("pre", 3), "long_id": ("pre", 4),
        "st": ("post", 0), "mw": ("post", 1), "mvar": ("post", 2), "mw_i": ("post", 3),
        "mvar_i": ("post", 4), "mw_z": ("post", 5), "mvar_z": ("post", 6),
        "area": ("post", 7), "zone": ("post", 8),
    },
    "shunt": {
        "bus": ("pre", 0), "name": ("pre", 1), "kv": ("pre", 2), "id": ("pre", 3),
        "to": ("pre", 4), "ck": ("pre", 7), "se": ("pre", 8), "long_id": ("pre", 9),
        "st": ("post", 0), "area": ("post", 1), "zone": ("post", 2), "g": ("post", 3), "b": ("post", 4),
    },
    "svd": {
        "bus": ("pre", 0), "name": ("pre", 1), "kv": ("pre", 2), "id": ("pre", 3), "long_id": ("pre", 4),
        "st": ("post", 0), "ty": ("post", 1), "reg_bus": ("post", 2), "area": ("post", 5), "zone": ("post", 6),
        "g": ("post", 7), "b": ("post", 8), "vband": ("post", 11), "bmin": ("post", 12), "bmax": ("post", 13),
    },
}

_INT = {"bus", "from", "to", "tert", "reg_bus", "area", "zone", "owner", "st", "ty", "se"}
_STR = {"name", "from_name", "to_name", "tert_name", "ck", "id", "long_id"}


class EpcError(ValueError):
    pass


def _value(tok: str, name: str):
    if tok.startswith('"'):
        return tok[1:-1].strip()
    if name in _STR:
        return tok
    try:
        return int(tok) if name in _INT else float(tok)
    except ValueError:
        return tok


@dataclass
class Record:
    kind: str
    key: tuple
    pre: list[str]
    post: list[str]
    f: dict = field(default_factory=dict)      # named fields (FIELDS)

    @property
    def buses(self) -> tuple[int, ...]:
        """Buses the element connects to (its terminals)."""
        f = self.f
        if self.kind == "bus":
            return (f["bus"],)
        if self.kind == "branch":
            return (f["from"], f["to"])
        if self.kind == "transformer":
            return tuple(b for b in (f["from"], f["to"], f["tert"]) if b)
        if self.kind == "shunt" and f.get("to"):
            return (f["bus"], f["to"])
        return (f["bus"],)


def _key(kind: str, f: dict) -> tuple:
    ck = str(f.get("ck", "")).upper()
    if kind == "bus":
        return (f["bus"],)
    if kind == "branch":
        a, b = sorted((f["from"], f["to"]))
        return (a, b, ck, f["se"])
    if kind == "transformer":
        # the tertiary is part of the identity: 3-winding units can share
        # primary, secondary and circuit ID (18032-18033 "1" in 30HS_22-G7)
        a, b = sorted((f["from"], f["to"]))
        return (a, b, ck, f["tert"] or 0)
    if kind == "shunt":
        return (f["bus"], str(f["id"]).upper(), f.get("to") or 0, ck, f.get("se") or 0)
    return (f["bus"], str(f["id"]).upper())          # generator, load, svd


@dataclass
class Case:
    path: Path
    title: list[str]
    records: dict[str, dict[tuple, Record]]

    def __getitem__(self, kind: str) -> dict[tuple, Record]:
        return self.records[kind]

    def bus(self, n: int) -> Record | None:
        return self.records["bus"].get((n,))

    def adjacency(self, in_service_only: bool = True) -> dict[int, set[int]]:
        adj: dict[int, set[int]] = {}
        for kind in ("branch", "transformer"):
            for r in self.records[kind].values():
                if in_service_only and r.f["st"] != 1:
                    continue
                bs = r.buses
                for a in bs:
                    adj.setdefault(a, set()).update(x for x in bs if x != a)
        return adj


def _parse(path: Path) -> Case:
    records: dict[str, dict[tuple, Record]] = {k: {} for k in SECTIONS}
    expected: dict[str, int] = {}
    title: list[str] = []
    sec, buf, in_title = None, "", False
    with open(path, encoding="latin-1") as fh:
        for line in fh:
            if line.startswith("title"):
                in_title, sec = True, None
                continue
            if in_title:
                if line.startswith("!"):
                    in_title = False
                elif line.strip():
                    title.append(line.strip())
                continue
            m = _HEADER.match(line)
            if m:
                sec = m.group(1)
                if sec in records:
                    expected[sec] = int(m.group(2))
                buf = ""
                continue
            if sec not in records or line.startswith("#"):
                if line.startswith("#"):
                    sec = None
                continue
            buf += " " + line.rstrip("\r\n")
            if buf.rstrip().endswith("/"):
                buf = buf.rstrip()[:-1]
                continue
            rec, buf = buf, ""
            toks = _TOKEN.findall(rec)
            if not toks:
                continue
            try:
                c = toks.index(":")
            except ValueError as e:
                raise EpcError(f"{path.name}: {sec} record without ':' -> {rec[:120]!r}") from e
            pre, post = toks[:c], toks[c + 1:]
            f = {}
            for name, (side, i) in FIELDS[sec].items():
                src = pre if side == "pre" else post
                f[name] = _value(src[i], name) if i < len(src) else None
            r = Record(sec, _key(sec, f), pre, post, f)
            records[sec][r.key] = r
    missing = [s for s in SECTIONS if s not in expected]
    if missing:
        raise EpcError(f"{path.name}: no {', '.join(missing)} section; is this an .epc case?")
    for s, n in expected.items():
        # fewer than declared = two records shared a key and one was lost
        if len(records[s]) != n:
            raise EpcError(f"{path.name}: {s} section declares {n} records, read {len(records[s])} "
                           "(duplicate element keys?)")
    return Case(Path(path), title, records)


def read_case(path: str | Path, cache_dir: Path | None = None) -> Case:
    """Parse an .epc case; with `cache_dir`, reuse a pickle keyed by the file's
    path, size and modification time (a changed case is re-read)."""
    path = Path(path)
    if not path.exists():
        raise EpcError(f"case not found: {path}")
    if cache_dir is None:
        return _parse(path)
    st = path.stat()
    tag = hashlib.sha1(f"{path.resolve()}|{st.st_size}|{st.st_mtime_ns}|v2".encode()).hexdigest()[:16]
    cache = Path(cache_dir) / f"epc_{path.stem}_{tag}.pkl"
    if cache.exists():
        try:
            with open(cache, "rb") as fh:
                return pickle.load(fh)
        except Exception:                      # stale or partial cache: re-read
            pass
    case = _parse(path)
    cache.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_suffix(".tmp")
    with open(tmp, "wb") as fh:
        pickle.dump(case, fh, protocol=pickle.HIGHEST_PROTOCOL)
    tmp.replace(cache)
    return case
