"""Study desk: the Footprint Builder, served locally, able to create studies.

    python scripts/study_desk.py              # opens http://127.0.0.1:8770
    python scripts/study_desk.py --port 8771 --no-browser

A web page cannot create folders or see full file paths, so this small local
server does both for the Footprint Builder:

    GET  /                     the Footprint Builder page
    GET  /api/health           {"app": "tara-study-desk", ...}
    GET  /api/browse?path=&kind=case|sub|con|mon
                               folders and matching files, inside the browse roots
    GET  /api/sub-names?path=  subsystems a .sub defines (clash check)
    GET  /api/networks         solved cases (basecase runs) to compile against
    POST /api/run-study        run a study's pipeline stages in the background
    GET  /api/job?id=          progress of that run (stage, log)
    GET  /api/study-info?name= a study's cases, case information, N-1, gates
    POST /api/studies          create a study from a study request
    POST /api/case-diff        two cases -> TARA solves each (basecase, once) ->
                               differences from the case reports, grouped into projects
    POST /api/case-sld         diagram data: buses of two solved runs, every element
                               on them in both cases (clickable single-line diagram)
    GET  /api/research         study areas with source counts; API-key status
    GET  /api/research/area?id= one area: sources, digests, summary, findings, log; "queue" and
                               "large_loads" (data/large_loads + large_loads_geo.json) when present
    GET  /api/research/queue-geo?id=  map data: queue POIs located (bus, line tap, name, county)
                               and line upgrades of the latest cluster reports
    GET  /api/research/queue-file?id=&f=pages/dsr_ladder.html[&dl=1]
                               a file of the area's queue data (ladder pages, export /
                               deliver tables, library); read-only, served folders only
    POST /api/research/<act>   create | url | status | upload | open | run (agent job:
                               ingest / summarize / discover through the Anthropic API)
    POST /api/open-study       open studies/<name> in the file explorer
    POST /api/resolve          bus lists: compile a footprint against a run or a
                               case (TARA solves the case first if needed)

Safety:
- listens on 127.0.0.1 only;
- answers only when the Host header is itself, and accepts a POST only when
  the Origin is itself and the body is JSON, so another website open in the
  same browser cannot browse your folders or create studies;
- browses only inside the roots in settings/machine.json "browse_roots"
  (default: the folder that holds this repo);
- writes only under studies/, through the same code as
  `study.py create --request`, which never overwrites an existing study.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from tara_automation.machine import MACHINE_FILE, REPO_ROOT

PAGE = REPO_ROOT / "tools" / "footprint_builder.html"
STUDIES = REPO_ROOT / "studies"
RUNS = REPO_ROOT / "runs"
KINDS = {"case": {".epc", ".raw", ".sav"}, "sub": {".sub"}, "con": {".con"},
         "mon": {".mon"}}
MAX_BODY = 2_000_000
MAX_UPLOAD = 60_000_000          # research uploads: base64 JSON, files up to ~44 MB


def browse_roots() -> list[Path]:
    roots = None
    try:
        raw = json.loads(MACHINE_FILE.read_text(encoding="utf-8"))
        roots = raw.get("browse_roots")
    except (OSError, ValueError):
        pass
    paths = [Path(r) for r in roots] if roots else [REPO_ROOT.parent]
    return [p.resolve() for p in paths if p.exists()]


def page_version() -> str:
    """Fingerprint of the Footprint Builder file as it is on disk now."""
    import hashlib
    return hashlib.sha1(PAGE.read_bytes()).hexdigest()[:12]


def inside_roots(p: Path, roots: list[Path]) -> bool:
    p = p.resolve()
    return any(p == r or r in p.parents for r in roots)


class Handler(BaseHTTPRequestHandler):
    server_version = "TaraStudyDesk/1"
    port = 8770

    # ------------------------------------------------------------ helpers --
    def _own(self) -> set[str]:
        return {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}

    def _host_ok(self) -> bool:
        return self.headers.get("Host", "") in self._own()

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj).encode("utf-8"), "application/json")

    def log_message(self, fmt, *args):  # quieter than the default
        sys.stderr.write(f"  {self.command} {self.path.split('?')[0]} -> {args[1]}\n")

    # --------------------------------------------------------------- GET ----
    def do_GET(self):
        if not self._host_ok():
            return self._json(HTTPStatus.FORBIDDEN, {"error": "bad host"})
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        if url.path in ("/", "/index.html", "/tools/footprint_builder.html"):
            text = PAGE.read_text(encoding="utf-8")
            # stamp the version so an open tab can tell it is out of date
            html = "<!doctype html>\n" + text.replace("__PAGE_VERSION__", page_version())
            return self._send(HTTPStatus.OK, html.encode("utf-8"), "text/html; charset=utf-8")
        if url.path == "/api/health":
            studies = sorted(p.name for p in STUDIES.iterdir()
                             if p.is_dir() and (p / "study.json").exists()) \
                if STUDIES.exists() else []
            return self._json(HTTPStatus.OK, {
                "app": "tara-study-desk", "repo": str(REPO_ROOT),
                "page_version": page_version(),
                "studies": studies, "roots": [str(r) for r in browse_roots()]})
        if url.path == "/api/browse":
            return self._browse(q)
        if url.path == "/api/sub-names":
            return self._sub_names(q)
        if url.path == "/api/job":
            job = JOBS.get(q.get("id", ""))
            if not job:
                return self._json(HTTPStatus.NOT_FOUND, {"error": "no such job"})
            return self._json(HTTPStatus.OK, {k: v for k, v in job.items() if k != "_log"}
                              | {"log": job["_log"][-80:]})
        if url.path == "/api/study-info":
            return _study_info(self, q.get("name", ""))
        if url.path == "/api/research":
            return _research_list(self)
        if url.path == "/api/research/area":
            return _research_area(self, q.get("id", ""))
        if url.path == "/api/research/queue-geo":
            return _queue_geo(self, q.get("id", ""))
        if url.path == "/api/research/queue-file":
            return _queue_file(self, q.get("id", ""), q.get("f", ""), q.get("dl") == "1")
        if url.path == "/api/networks":
            from tara_automation.footprint_preview import basecase_runs
            return self._json(HTTPStatus.OK, {"runs": basecase_runs(RUNS)})
        return self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def _browse(self, q: dict) -> None:
        roots = browse_roots()
        kind = q.get("kind", "case")
        exts = KINDS.get(kind)
        if exts is None:
            return self._json(HTTPStatus.BAD_REQUEST, {"error": f"unknown kind {kind}"})
        if not q.get("path"):
            return self._json(HTTPStatus.OK, {"path": None, "parent": None,
                                              "dirs": [str(r) for r in roots], "files": []})
        p = Path(q["path"])
        if not p.is_dir() or not inside_roots(p, roots):
            return self._json(HTTPStatus.FORBIDDEN,
                              {"error": "outside the browse roots (settings/machine.json "
                                        "browse_roots)"})
        dirs, files = [], []
        try:
            for e in sorted(p.iterdir(), key=lambda x: x.name.lower()):
                if e.name.startswith((".", "__")):
                    continue
                if e.is_dir():
                    dirs.append(e.name)
                elif e.suffix.lower() in exts:
                    st = e.stat()
                    files.append({"name": e.name, "size": st.st_size, "mtime": st.st_mtime})
        except PermissionError:
            return self._json(HTTPStatus.FORBIDDEN, {"error": "permission denied"})
        parent = str(p.parent) if p.resolve() not in roots else None
        return self._json(HTTPStatus.OK, {"path": str(p.resolve()), "parent": parent,
                                          "dirs": dirs, "files": files})

    def _sub_names(self, q: dict) -> None:
        from tara_automation.footprint import subsystem_definitions
        p = Path(q.get("path", ""))
        if not p.is_file() or not inside_roots(p, browse_roots()):
            return self._json(HTTPStatus.FORBIDDEN, {"error": "not a readable .sub"})
        names = subsystem_definitions(p.read_text(encoding="utf-8", errors="replace"))
        return self._json(HTTPStatus.OK, {"path": str(p), "names": names})

    # -------------------------------------------------------------- POST ----
    def do_POST(self):
        if not self._host_ok():
            return self._json(HTTPStatus.FORBIDDEN, {"error": "bad host"})
        origin = self.headers.get("Origin", "")
        if origin not in {f"http://{h}" for h in self._own()}:
            return self._json(HTTPStatus.FORBIDDEN, {"error": "requests must come from "
                                                              "the study desk page"})
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            return self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "send JSON"})
        route = urlparse(self.path).path
        if route not in ("/api/studies", "/api/resolve", "/api/open-study", "/api/run-study",
                         "/api/case-diff", "/api/case-sld") and not route.startswith("/api/research/"):
            return self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
        n = int(self.headers.get("Content-Length") or 0)
        if not 0 < n <= (MAX_UPLOAD if route == "/api/research/upload" else MAX_BODY):
            return self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "body size"})
        try:
            req = json.loads(self.rfile.read(n))
        except json.JSONDecodeError:
            return self._json(HTTPStatus.BAD_REQUEST, {"error": "not valid JSON"})
        if route == "/api/resolve":
            return self._resolve(req)
        if route == "/api/open-study":
            return self._open_study(req)
        if route == "/api/run-study":
            return _run_study(self, req)
        if route == "/api/case-diff":
            return _case_diff(self, req)
        if route == "/api/case-sld":
            return _case_sld(self, req)
        if route.startswith("/api/research/"):
            return _research_post(self, route, req)
        from tara_automation.study_request import StudyRequestError, create
        try:
            res = create(req, STUDIES)
        except StudyRequestError as e:
            return self._json(HTTPStatus.UNPROCESSABLE_ENTITY,
                              {"ok": False, "problems": e.problems})
        except Exception as e:  # noqa: BLE001 — report, never crash the desk
            return self._json(HTTPStatus.INTERNAL_SERVER_ERROR,
                              {"ok": False, "problems": [f"{type(e).__name__}: {e}"]})
        sys.stderr.write(f"  created study {res['name']} at {res['path']}\n")
        return self._json(HTTPStatus.CREATED, res)


ENGINE_LOCK = threading.Lock()     # one TARA run at a time (licence, CPU)
MIN_CASE_BYTES = 10_000


def _solve(case: Path) -> int:
    """Run a TARA basecase on `case` (replaced in tests)."""
    from tara_automation.footprint_preview import solve_basecase
    from tara_automation.machine import load_machine
    return solve_basecase(case, RUNS, load_machine().tara_exe,
                          label=f"study desk basecase {case.stem}")


def _resolve_impl(handler: "Handler", req: dict) -> None:
    """Compile the page's footprint with the real compiler, against either a
    given run or a case. For a case, the newest basecase run made after the
    case file last changed is used; if there is none, TARA solves the case
    first (a normal run-manager run). Nothing else is written."""
    import time

    from tara_automation.footprint_preview import (FootprintError, basecase_runs,
                                                   find_basecase, preview)
    text = str(req.get("yaml") or "")
    if "study_area" not in text:
        return handler._json(HTTPStatus.BAD_REQUEST, {"error": "no footprint YAML"})
    solved = None
    if req.get("case"):
        case = Path(str(req["case"]).strip().strip('"'))
        if not case.is_absolute() or not case.is_file():
            return handler._json(HTTPStatus.NOT_FOUND, {"error": f"case not found: {case}"})
        if not inside_roots(case, browse_roots()):
            return handler._json(HTTPStatus.FORBIDDEN, {"error": "the case is outside the "
                                                                 "browse roots"})
        if case.suffix.lower() not in KINDS["case"] or case.stat().st_size < MIN_CASE_BYTES:
            return handler._json(HTTPStatus.BAD_REQUEST,
                                 {"error": f"{case.name} is not a usable power-flow case"})
        hit = find_basecase(case, RUNS)
        if hit:
            run = hit["run"]
        else:
            if not ENGINE_LOCK.acquire(timeout=2):
                return handler._json(HTTPStatus.CONFLICT, {"error": busy_message()})
            try:                          # re-check: another click may have solved it
                hit = find_basecase(case, RUNS)
                if hit:
                    run = hit["run"]
                else:
                    t0 = time.time()
                    try:
                        run = _solve(case)
                    except Exception as e:  # noqa: BLE001
                        return handler._json(HTTPStatus.INTERNAL_SERVER_ERROR,
                                             {"error": f"TARA could not solve {case.name}: {e}"})
                    solved = {"run": run, "seconds": round(time.time() - t0, 1)}
                    sys.stderr.write(f"  solved {case.name} as run {run} "
                                     f"in {solved['seconds']} s\n")
            finally:
                ENGINE_LOCK.release()
    else:
        try:
            run = int(req.get("run"))
        except (TypeError, ValueError):
            return handler._json(HTTPStatus.BAD_REQUEST, {"error": "choose a case"})
        if run not in {r["run"] for r in basecase_runs(RUNS)}:
            return handler._json(HTTPStatus.NOT_FOUND, {"error": f"run {run} is not a "
                                                                 f"solved basecase run"})
    try:
        out = preview(text, RUNS / str(run) / "results")
    except FootprintError as e:
        return handler._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(e), "run": run})
    except Exception as e:  # noqa: BLE001 — report, never crash the desk
        return handler._json(HTTPStatus.INTERNAL_SERVER_ERROR,
                             {"error": f"{type(e).__name__}: {e}"})
    out["run"] = run
    out["solved"] = solved
    return handler._json(HTTPStatus.OK, out)


Handler._resolve = _resolve_impl


def _solved_run(case: Path) -> tuple[int, dict | None]:
    """The basecase run of `case` (newest made after the file last changed);
    TARA solves it first when there is none (run manager, RUN_LOG line).
    Returns (run, solved-info or None). Caller holds no lock."""
    import time

    from tara_automation.footprint_preview import find_basecase
    hit = find_basecase(case, RUNS)
    if hit:
        return hit["run"], None
    if not ENGINE_LOCK.acquire(timeout=2):
        raise BusyError(busy_message())
    try:
        hit = find_basecase(case, RUNS)        # another click may have solved it
        if hit:
            return hit["run"], None
        t0 = time.time()
        run = _solve(case)
        info = {"case": case.name, "run": run, "seconds": round(time.time() - t0, 1)}
        sys.stderr.write(f"  solved {case.name} as run {run} in {info['seconds']} s\n")
        return run, info
    finally:
        ENGINE_LOCK.release()


class BusyError(RuntimeError):
    pass


def _case_diff(handler: "Handler", req: dict) -> None:
    """Differences between two cases, grouped into projects (task 27). Each
    case is solved by TARA (a basecase run, once) and compared from TARA's own
    case reports. No contingency analysis."""
    from tara_automation.case_diff import compare
    from tara_automation.case_reports import ReportError
    paths = {}
    for k in ("study_case", "donor_case"):
        c = Path(str(req.get(k) or "").strip().strip('"'))
        if not str(c) or not c.is_absolute() or not c.is_file():
            return handler._json(HTTPStatus.NOT_FOUND, {"error": f"case not found: {c}"})
        if not inside_roots(c, browse_roots()):
            return handler._json(HTTPStatus.FORBIDDEN, {"error": f"{c.name} is outside the browse roots"})
        if c.suffix.lower() not in KINDS["case"] or c.stat().st_size < MIN_CASE_BYTES:
            return handler._json(HTTPStatus.BAD_REQUEST, {"error": f"{c.name} is not a usable power-flow case"})
        paths[k] = c
    if paths["study_case"].resolve() == paths["donor_case"].resolve():
        return handler._json(HTTPStatus.BAD_REQUEST, {"error": "choose two different cases"})
    runs, solved = {}, []
    for k, c in paths.items():
        try:
            runs[k], info = _solved_run(c)
        except BusyError as e:
            return handler._json(HTTPStatus.CONFLICT, {"error": str(e)})
        except Exception as e:  # noqa: BLE001
            return handler._json(HTTPStatus.INTERNAL_SERVER_ERROR,
                                 {"error": f"TARA could not solve {c.name}: {e}"})
        if info:
            solved.append(info)
    try:
        out = compare(RUNS / str(runs["study_case"]) / "results", RUNS / str(runs["donor_case"]) / "results",
                      study_run=runs["study_case"], donor_run=runs["donor_case"],
                      study_name=paths["study_case"].name, donor_name=paths["donor_case"].name)
    except ReportError as e:
        return handler._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(e)})
    except Exception as e:  # noqa: BLE001 — report, never crash the desk
        return handler._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"{type(e).__name__}: {e}"})
    out["solved"] = solved
    sys.stderr.write(f"  compared run {runs['study_case']} <- run {runs['donor_case']}: "
                     f"{out['summary']['groups']} projects\n")
    return handler._json(HTTPStatus.OK, out)


_CASES: dict = {}                  # run -> case_reports.Case, the last few read
_CASES_LOCK = threading.Lock()


def _case_for_run(run: int):
    """A solved case read from its reports, kept in memory (a diagram click
    must not re-read 30,000 buses)."""
    from tara_automation.case_reports import read_results
    with _CASES_LOCK:
        if run in _CASES:
            _CASES[run] = _CASES.pop(run)       # most recent last
            return _CASES[run]
    case = read_results(RUNS / str(run) / "results", run)
    with _CASES_LOCK:
        _CASES[run] = case
        while len(_CASES) > 6:
            _CASES.pop(next(iter(_CASES)))
    return case


def _case_sld(handler: "Handler", req: dict) -> None:
    """Diagram data for a set of buses in two solved cases (task 27)."""
    from tara_automation.case_reports import ReportError
    from tara_automation.case_sld import view
    from tara_automation.footprint_preview import basecase_runs
    try:
        runs = {k: int(req.get(k)) for k in ("study_run", "donor_run")}
        buses = [int(b) for b in req.get("buses") or []]
        around = [int(b) for b in req.get("around") or []]
    except (TypeError, ValueError):
        return handler._json(HTTPStatus.BAD_REQUEST, {"error": "study_run, donor_run and buses are numbers"})
    if not buses and not around:
        return handler._json(HTTPStatus.BAD_REQUEST, {"error": "no buses to draw"})
    solved = {r["run"] for r in basecase_runs(RUNS)}
    for k, n in runs.items():
        if n not in solved:
            return handler._json(HTTPStatus.NOT_FOUND, {"error": f"run {n} is not a solved basecase run"})
    try:
        out = view(_case_for_run(runs["study_run"]), _case_for_run(runs["donor_run"]), buses, around)
    except (ReportError, ValueError) as e:
        return handler._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(e)})
    except Exception as e:  # noqa: BLE001 — report, never crash the desk
        return handler._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"{type(e).__name__}: {e}"})
    return handler._json(HTTPStatus.OK, out)


# ------------------------------------------------- study-area research ----
RESEARCH_JOBS: dict[str, str] = {}      # area id -> running job id (one at a time per area)


def _research_list(handler: "Handler") -> None:
    from tara_automation import research as R
    out = []
    for a in R.areas():
        rows = a.sources()
        out.append({"id": a.id, "name": a.meta().get("name", a.id), "description": a.meta().get("description", ""),
                    "sources": sum(r.get("status") == "active" for r in rows),
                    "proposed": sum(r.get("status") == "proposed" for r in rows),
                    "pending": len(R.pending(a)), "summary": (a.root / "summary.md").exists(),
                    "job": RESEARCH_JOBS.get(a.id)})
    from tara_automation import eia
    ready, msg = R.api_ready()
    eok, emsg = eia.key_status()
    cfg = R.settings()
    return handler._json(HTTPStatus.OK, {"areas": out, "api": {"ready": ready, "message": msg},
                                         "eia": {"ready": eok, "message": emsg},
                                         "model": cfg["model"], "effort": cfg["effort"]})


def _research_area(handler: "Handler", area_id: str) -> None:
    from tara_automation import research as R
    try:
        a = R.get_area(area_id)
        R.scan(a)                                   # pick up files dropped into the folder
        view = R.area_view(a)
    except R.ResearchError as e:
        return handler._json(HTTPStatus.NOT_FOUND, {"error": str(e)})
    view["job"] = RESEARCH_JOBS.get(a.id)
    return handler._json(HTTPStatus.OK, view)


def _queue_geo(handler: "Handler", area_id: str) -> None:
    """Locations for the queue tab's map (first call reads the coordinate workbook, then cached)."""
    import importlib

    from tara_automation import research as R
    from tara_automation.ingest import ADAPTERS, queue_data
    try:
        a = R.get_area(area_id)
    except R.ResearchError as e:
        return handler._json(HTTPStatus.NOT_FOUND, {"error": str(e)})
    qd = queue_data(a.id, a.root)
    if qd is None:
        return handler._json(HTTPStatus.NOT_FOUND, {"error": f"{a.id} has no queue data"})
    pkg = ADAPTERS[a.id.upper()]
    try:
        reqs = importlib.import_module(pkg + ".desk").view(qd)["requests"]
        return handler._json(HTTPStatus.OK, importlib.import_module(pkg + ".geo").view(reqs, qd))
    except Exception as e:  # noqa: BLE001
        return handler._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"{type(e).__name__}: {e}"})


def _queue_file(handler: "Handler", area_id: str, rel: str, download: bool) -> None:
    """One file of an area's queue data. Only the folders and types in desk.SERVE."""
    import importlib

    from tara_automation import research as R
    from tara_automation.ingest import ADAPTERS, queue_data
    try:
        a = R.get_area(area_id)
    except R.ResearchError as e:
        return handler._json(HTTPStatus.NOT_FOUND, {"error": str(e)})
    qd = queue_data(a.id, a.root)
    if qd is None:
        return handler._json(HTTPStatus.NOT_FOUND, {"error": f"{a.id} has no queue data"})
    desk = importlib.import_module(ADAPTERS[a.id.upper()] + ".desk")
    p = desk.resolve(qd, rel)
    if p is None:
        return handler._json(HTTPStatus.NOT_FOUND, {"error": f"not a queue file: {rel}"})
    body = p.read_bytes()
    handler.send_response(HTTPStatus.OK)
    handler.send_header("Content-Type", desk.CTYPE.get(p.suffix.lower(), "application/octet-stream"))
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Content-Type-Options", "nosniff")
    if download or p.suffix.lower() != ".html":
        handler.send_header("Content-Disposition", f'attachment; filename="{p.name}"')
    handler.end_headers()
    handler.wfile.write(body)


def _research_job(job: dict, area_id: str, actions: list[str]) -> None:
    import time
    from tara_automation import research as R

    def progress(line: str) -> None:
        job["_log"].append(line)
        del job["_log"][:-400]
    try:
        a = R.get_area(area_id)
        client = R._client() if set(actions) - {"eia"} else None
        for act in actions:
            job["stage"] = act
            t0 = time.time()
            if act == "eia":
                from tara_automation import eia
                res = eia.update(a, progress)
            elif act == "ingest":
                R.scan(a)
                res = R.ingest(a, progress, client)
            elif act == "summarize":
                res = R.synthesize(a, progress, client)
            else:
                res = R.discover(a, progress, client)
            job["cost"] = round(job.get("cost", 0) + res.get("cost", 0), 4)
            job["done"].append({"stage": act, "seconds": round(time.time() - t0, 1), "result": res})
        job["status"] = "done"
    except Exception as e:  # noqa: BLE001 — the page shows it
        from tara_automation.eia import EiaError
        job["status"] = "error"
        job["error"] = str(e) if isinstance(e, (R.ResearchError, EiaError)) else f"{type(e).__name__}: {e}"
        job["_log"].append(f"STOP: {job['error']}")
    finally:
        job["stage"] = None
        job["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        RESEARCH_JOBS.pop(area_id, None)
        sys.stderr.write(f"  research {area_id} {actions}: {job['status']} (${job.get('cost', 0):.3f})\n")


def _research_post(handler: "Handler", route: str, req: dict) -> None:
    import base64
    import time
    import uuid
    from tara_automation import research as R
    act = route.rsplit("/", 1)[-1]
    try:
        if act == "create":
            areas = [int(x) for x in req.get("areas") or []]
            a = R.create_area(str(req.get("id") or ""), str(req.get("name") or "").strip() or str(req.get("id")),
                              str(req.get("description") or ""), {"areas": areas} if areas else {},
                              [u for u in req.get("utilities") or [] if str(u).strip()],
                              [k for k in req.get("keywords") or [] if str(k).strip()],
                              str(req.get("eia_ba") or ""), float(req.get("utc_offset") or -7))
            return handler._json(HTTPStatus.CREATED, {"id": a.id})
        a = R.get_area(str(req.get("area") or ""))
        if act == "url":
            r = R.add_url(a, str(req.get("url") or "").strip(), str(req.get("title") or "").strip())
            return handler._json(HTTPStatus.CREATED, r)
        if act == "status":
            R.set_status(a, str(req.get("source") or ""), str(req.get("status") or ""))
            return handler._json(HTTPStatus.OK, {"ok": True})
        if act == "upload":
            name = Path(str(req.get("name") or "")).name           # never a path
            if not name or Path(name).suffix.lower() not in R.FILE_TYPES or name.startswith((".", "~$")):
                return handler._json(HTTPStatus.BAD_REQUEST, {"error": f"{name}: allowed types are "
                                                              f"{', '.join(sorted(R.FILE_TYPES))}"})
            data = base64.b64decode(str(req.get("data") or ""), validate=True)
            a.files.mkdir(exist_ok=True)
            dest = a.files / name
            if dest.exists() and dest.read_bytes() != data:
                stem, k = dest.stem, 2
                while dest.exists():
                    dest = a.files / f"{stem}_{k}{dest.suffix}"
                    k += 1
            dest.write_bytes(data)
            added = R.scan(a)
            return handler._json(HTTPStatus.CREATED, {"file": dest.name, "added": added})
        if act == "open":
            import os
            folder = a.files
            if req.get("folder") in ("queue", "queue_library"):
                from tara_automation.ingest import queue_data
                qd = queue_data(a.id, a.root)
                if qd is None:
                    return handler._json(HTTPStatus.NOT_FOUND, {"error": f"{a.id} has no queue data"})
                folder = qd.library if req["folder"] == "queue_library" else qd.root
            folder.mkdir(exist_ok=True)
            os.startfile(str(folder))  # type: ignore[attr-defined]  # Windows desk
            return handler._json(HTTPStatus.OK, {"ok": True})
        if act == "run":
            actions = [x for x in ("eia", "ingest", "summarize", "discover") if x in (req.get("actions") or [])]
            if not actions:
                return handler._json(HTTPStatus.BAD_REQUEST, {"error": "choose eia, ingest, summarize or discover"})
            from tara_automation import eia
            checks = ([R.api_ready()] if set(actions) - {"eia"} else []) + ([eia.key_status()] if "eia" in actions else [])
            for ok, msg in checks:
                if not ok:
                    return handler._json(HTTPStatus.PRECONDITION_FAILED, {"error": msg})
            if a.id in RESEARCH_JOBS:
                return handler._json(HTTPStatus.CONFLICT, {"error": f"the agent is already working on {a.id}"})
            job = {"id": uuid.uuid4().hex[:10], "study": f"research:{a.id}", "stages": actions,
                   "stage": actions[0], "status": "running", "done": [], "error": None, "cost": 0.0,
                   "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "finished": None, "_log": []}
            JOBS[job["id"]] = job
            RESEARCH_JOBS[a.id] = job["id"]
            threading.Thread(target=_research_job, args=(job, a.id, actions), daemon=True).start()
            return handler._json(HTTPStatus.ACCEPTED, {"job": job["id"]})
    except R.ResearchError as e:
        return handler._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(e)})
    except (ValueError, OSError) as e:
        return handler._json(HTTPStatus.BAD_REQUEST, {"error": f"{type(e).__name__}: {e}"})
    return handler._json(HTTPStatus.NOT_FOUND, {"error": "not found"})


def _open_study_impl(handler: "Handler", req: dict) -> None:
    """Open an existing study folder in the file explorer. Only a study name
    (a direct child of studies/) is accepted, never a path."""
    import os
    import subprocess
    name = str(req.get("name") or "")
    folder = STUDIES / name
    if not name or "/" in name or "\\" in name or name in (".", "..")             or not (folder / "study.json").is_file():
        return handler._json(HTTPStatus.NOT_FOUND, {"error": f"no study named '{name}'"})
    try:
        if os.name == "nt":
            os.startfile(folder)  # noqa: S606 — a folder under studies/, by name only
        else:
            subprocess.Popen(["xdg-open", str(folder)])
    except OSError as e:
        return handler._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(e)})
    return handler._json(HTTPStatus.OK, {"ok": True, "path": str(folder)})


Handler._open_study = _open_study_impl

# ------------------------------------------------------------ study runs ----
# "Run study" on the page: the pipeline stages in a background thread, one at
# a time (ENGINE_LOCK), every engine run through the run manager as always.
RUNNABLE = ["basecase", "footprint", "caseinfo", "contingency"]   # pipeline order
JOBS: dict[str, dict] = {}


def busy_message() -> str:
    running = [j for j in JOBS.values() if j["status"] == "running"]
    if running:
        j = running[0]
        return (f"TARA is busy running study {j['study']} (stage {j['stage']}). "
                f"Try again when it finishes.")
    return "TARA is busy with another run. Try again in a few seconds."


class _JobLog:
    """Collects what the pipeline prints, for the page to show."""

    def __init__(self, job: dict):
        self.job = job
        self._buf = ""

    def write(self, text: str) -> int:
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                self.job["_log"].append(line.rstrip())
                del self.job["_log"][:-400]
        return len(text)

    def flush(self) -> None:
        pass


def _run_job(job: dict, force: bool) -> None:
    import contextlib
    import time

    from tara_automation.study import Study
    from tara_automation.study_pipeline import run_all
    try:
        log = _JobLog(job)
        with contextlib.redirect_stdout(log):
            for stage in job["stages"]:
                job["stage"] = stage
                t0 = time.time()
                study = Study.load(STUDIES, job["study"])
                run_all(study, [stage], force=force, jobs=1)
                job["done"].append({"stage": stage, "seconds": round(time.time() - t0, 1)})
        job["status"] = "done"
    except Exception as e:  # noqa: BLE001 — the page shows it
        job["status"] = "error"
        job["error"] = f"{type(e).__name__}: {e}"
        job["_log"].append(f"STOP: {e}")
    finally:
        job["stage"] = None
        job["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        ENGINE_LOCK.release()
        sys.stderr.write(f"  study run {job['id']} ({job['study']}) {job['status']}\n")


def _run_study(handler: "Handler", req: dict) -> None:
    import time
    import uuid
    name = str(req.get("name") or "")
    if not name or not (STUDIES / name / "study.json").is_file() or "/" in name or "\\" in name:
        return handler._json(HTTPStatus.NOT_FOUND, {"error": f"no study named '{name}'"})
    stages = [s for s in RUNNABLE if s in (req.get("stages") or [])]
    if not stages:
        return handler._json(HTTPStatus.BAD_REQUEST,
                             {"error": f"choose stages from {RUNNABLE}"})
    if not ENGINE_LOCK.acquire(blocking=False):
        return handler._json(HTTPStatus.CONFLICT, {"error": busy_message()})
    job = {"id": uuid.uuid4().hex[:10], "study": name, "stages": stages, "stage": stages[0],
           "status": "running", "done": [], "error": None, "force": bool(req.get("force")),
           "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "finished": None, "_log": []}
    JOBS[job["id"]] = job
    threading.Thread(target=_run_job, args=(job, bool(req.get("force"))), daemon=True).start()
    sys.stderr.write(f"  study run {job['id']}: {name} {stages}\n")
    return handler._json(HTTPStatus.ACCEPTED, {"job": job["id"], "stages": stages})


def _study_info(handler: "Handler", name: str) -> None:
    """Everything the page shows about a study: cases, the runs behind them,
    case information, N-1 summaries, footprint groups and review gates."""
    from tara_automation.run_manager import RunManager
    from tara_automation.study import Study
    if not name or not (STUDIES / name / "study.json").is_file() or "/" in name or "\\" in name:
        return handler._json(HTTPStatus.NOT_FOUND, {"error": f"no study named '{name}'"})
    st = Study.load(STUDIES, name)
    c = st.config
    rm = RunManager(RUNS)
    info_dir = st.analysis_dir / "case_info"
    cases = []
    for cs in st.ordered():
        rec = {"label": cs.label, "role": cs.role, "path": cs.path, "fmt": cs.fmt,
               "version": cs.version,
               "basecase_run": c.runs.get("basecase", {}).get(cs.label),
               "contingency_run": c.runs.get("contingency", {}).get(cs.label),
               "info": None, "n1": None}
        f = info_dir / f"{cs.label}.json"
        if f.exists():
            rec["info"] = json.loads(f.read_text(encoding="utf-8"))
        if rec["contingency_run"]:
            try:
                m = rm.load_run(rec["contingency_run"]).manifest
                rec["n1"] = m.get("violations_summary")
            except FileNotFoundError:
                pass
        cases.append(rec)
    fp = None
    if c.deliverables.get("footprint") and Path(c.deliverables["footprint"]).exists():
        r = json.loads(Path(c.deliverables["footprint"]).read_text(encoding="utf-8"))
        fp = {"groups": r.get("groups"), "warnings": r.get("warnings"),
              "study_area": r.get("study_area"), "appended": r.get("appended")}
    gates = {s: {"status": st.gate_status(s), **st.gate(s)} for s in c.gates}
    running = [j["id"] for j in JOBS.values() if j["study"] == name and j["status"] == "running"]
    return handler._json(HTTPStatus.OK, {
        "name": c.name, "objective": c.objective, "area": c.area, "software": c.software,
        "rating_cont": c.rating_cont, "loading_cutoff": c.loading_cutoff,
        "has_footprint": bool(c.footprint), "sub": c.sub, "con": c.con, "mon": c.mon,
        "cases": cases, "footprint": fp, "gates": gates, "running_job": running[0] if running else None,
        "approve": f"python scripts/study.py approve --name {c.name} --stage contingency "
                   f"--verdict <review verdict>"})


class _Server(ThreadingHTTPServer):
    # HTTPServer sets SO_REUSEADDR, and on Windows that lets a second desk
    # bind the same port while the first keeps answering with its old code
    # (seen 2026-10-21). One desk per port: a second start must fail loudly.
    allow_reuse_address = False
    daemon_threads = True


def make_server(port: int) -> ThreadingHTTPServer:
    Handler.port = port
    return _Server(("127.0.0.1", port), Handler)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    try:
        srv = make_server(args.port)
    except OSError as e:
        print(f"STOP: cannot listen on 127.0.0.1:{args.port} ({e}). Is the desk already "
              f"running? Try --port {args.port + 1}.")
        return 1
    url = f"http://127.0.0.1:{args.port}/"
    print(f"Study desk at {url}  (Ctrl+C to stop)")
    print(f"  browse roots: {', '.join(map(str, browse_roots()))}")
    print(f"  studies are created in {STUDIES}")
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
