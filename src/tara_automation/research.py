"""Study-area research: a repository of sources per area, digested and
summarised by an AI model (task 29).

    research/areas/<AREA>/
        area.yaml       what the area is: name, footprint, utilities, keywords   (tracked)
        sources.yaml    every source: file or URL, who added it, status          (tracked)
        files/          the documents themselves                                  (git-ignored)
        digests/<id>.json   one structured digest per source                      (tracked)
        summary.md      the area summary, every statement cited [S3]              (tracked)
        findings.json   the same summary as data                                  (tracked)
        log.md          every agent action, model, tokens and cost                (tracked)

The agent works in three steps, each its own Anthropic API call:

  ingest      a source not yet digested (or whose file changed) is read ONCE:
              PDFs whole (text, tables, figures), Word / Excel / CSV / text
              read locally, URLs fetched by the API's web fetch tool. The
              digest is cached by file hash: re-runs cost nothing.
  synthesize  the area summary is written from the digests only, never the
              raw files, so it stays cheap however large the repository grows.
  discover    web search for public data on the area (utility plans, queue
              reports, regulatory filings). Finds are only PROPOSED; nothing
              is read until the user approves it.

The API key comes from the environment (ANTHROPIC_API_KEY); it is never
written anywhere. Settings: settings/research.json.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

import yaml

from tara_automation.machine import REPO_ROOT

ROOT = REPO_ROOT / "research" / "areas"
SETTINGS = REPO_ROOT / "settings" / "research.json"
DEFAULTS = {
    "model": "claude-opus-5-5",
    "effort": "medium",             # digest and discovery; synthesis uses synth_effort
    "synth_effort": "high",
    "max_tokens": 32000,
    "web_search_max_uses": 12,
    "max_pdf_mb": 30,               # the API takes PDFs up to 32 MB
    "fallbacks": True,              # server-side refusal fallback (Anthropic API)
    "price_per_mtok": {"input": 4.0, "output": 20.0, "cache_read": 0.20},
}
# The four research pillars (the user's, 2026-10-21) and a catch-all.
TOPICS = ["interconnection_queue", "load_and_dispatch", "resource_planning", "transmission_plans", "other"]
TOPIC_TITLE = {
    "interconnection_queue": "Interconnection queue",
    "load_and_dispatch": "Historical load and generation dispatch (EIA)",
    "resource_planning": "Integrated resource plans (IRP)",
    "transmission_plans": "Ten-year transmission plans",
    "other": "Other",
}
FILE_TYPES = {".pdf", ".docx", ".xlsx", ".xlsm", ".csv", ".txt", ".md", ".html", ".htm", ".json"}
SAFE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,47}$")


class ResearchError(RuntimeError):
    pass


def settings() -> dict:
    cfg = dict(DEFAULTS)
    if SETTINGS.exists():
        cfg.update(json.loads(SETTINGS.read_text(encoding="utf-8")))
    return cfg


def _files_root() -> Path | None:
    from tara_automation.machine import MACHINE_FILE
    try:
        raw = json.loads(MACHINE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    r = raw.get("research_files_root")
    return Path(r) if r else None


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


# ------------------------------------------------------------------ areas --
@dataclass
class Area:
    id: str
    root: Path

    @property
    def files(self) -> Path:
        """The documents. In the repo (git-ignored) unless settings/machine.json
        sets research_files_root, e.g. a OneDrive folder that syncs to every
        machine: then <root>/<AREA>/files."""
        root = _files_root()
        return (root / self.id / "files") if root else (self.root / "files")

    def meta(self) -> dict:
        return yaml.safe_load((self.root / "area.yaml").read_text(encoding="utf-8")) or {}

    def sources(self) -> list[dict]:
        p = self.root / "sources.yaml"
        return (yaml.safe_load(p.read_text(encoding="utf-8")) or []) if p.exists() else []

    def save_sources(self, rows: list[dict]) -> None:
        (self.root / "sources.yaml").write_text(
            "# Sources for this study area. status: active | proposed | rejected | failed\n"
            + yaml.safe_dump(rows, sort_keys=False, allow_unicode=True, width=110), encoding="utf-8")

    def digest(self, sid: str) -> dict | None:
        p = self.root / "digests" / f"{sid}.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def log(self, line: str) -> None:
        p = self.root / "log.md"
        if not p.exists():
            p.write_text(f"# Research log: {self.id}\n\n", encoding="utf-8")
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(f"- {_now()} {line}\n")


def areas() -> list[Area]:
    ROOT.mkdir(parents=True, exist_ok=True)
    return [Area(p.name, p) for p in sorted(ROOT.iterdir()) if (p / "area.yaml").exists()]


def get_area(area_id: str) -> Area:
    if not SAFE_ID.match(area_id or ""):
        raise ResearchError(f"not a valid area id: {area_id!r}")
    a = Area(area_id, ROOT / area_id)
    if not (a.root / "area.yaml").exists():
        raise ResearchError(f"no study area {area_id}")
    return a


def create_area(area_id: str, name: str, description: str = "", footprint: dict | None = None,
                utilities=(), keywords=(), eia_ba: str = "", utc_offset: float = -7.0) -> Area:
    if not SAFE_ID.match(area_id or ""):
        raise ResearchError("area id: a letter, then letters, digits, _ or - (max 48)")
    a = Area(area_id, ROOT / area_id)
    if a.root.exists():
        raise ResearchError(f"study area {area_id} already exists")
    (a.root / "files").mkdir(parents=True)
    (a.root / "digests").mkdir()
    (a.root / "area.yaml").write_text(yaml.safe_dump({
        "id": area_id, "name": name, "description": description, "created": _now(),
        "footprint": footprint or {}, "utilities": list(utilities), "keywords": list(keywords),
        # EIA balancing authority code (e.g. AZPS) and its standard-time UTC offset (Arizona: -7)
        "eia_ba": eia_ba.strip().upper(), "utc_offset": utc_offset,
    }, sort_keys=False, allow_unicode=True), encoding="utf-8")
    a.save_sources([])
    a.log(f"area created: {name}")
    return a


def _next_id(rows: list[dict]) -> str:
    n = max([int(r["id"][1:]) for r in rows if re.fullmatch(r"S\d+", r.get("id", ""))] or [0]) + 1
    return f"S{n}"


def scan(area: Area) -> list[str]:
    """Register files dropped into files/ as sources; mark changed ones for re-reading."""
    rows = area.sources()
    by_path = {r.get("path"): r for r in rows if r.get("kind") == "file"}
    added = []
    area.files.mkdir(exist_ok=True)
    for f in sorted(area.files.iterdir()):
        if not f.is_file() or f.suffix.lower() not in FILE_TYPES or f.name.startswith(("~$", ".")):
            continue
        sha = _sha(f)
        r = by_path.get(f.name)
        if r is None:
            r = {"id": _next_id(rows), "kind": "file", "path": f.name, "title": f.stem, "added_by": "user",
                 "added": _now(), "status": "active", "sha": sha, "digested_sha": None}
            rows.append(r)
            added.append(r["id"])
        elif r.get("sha") != sha:
            r["sha"] = sha                          # changed: will be re-read
    area.save_sources(rows)
    if added:
        area.log(f"scan: {len(added)} new file(s) registered: {', '.join(added)}")
    return added


def add_url(area: Area, url: str, title: str = "", added_by: str = "user", why: str = "",
            status: str = "active", extra: dict | None = None) -> dict:
    if not re.match(r"^https?://", url or ""):
        raise ResearchError("a URL must start with http:// or https://")
    rows = area.sources()
    if any(r.get("url") == url for r in rows):
        raise ResearchError("that URL is already a source")
    r = {"id": _next_id(rows), "kind": "url", "url": url, "title": title or url, "added_by": added_by,
         "added": _now(), "status": status, "why": why, "digested_sha": None, **(extra or {})}
    rows.append(r)
    area.save_sources(rows)
    area.log(f"source {r['id']} added by {added_by} ({status}): {r['title']}")
    return r


def set_status(area: Area, sid: str, status: str) -> None:
    if status not in ("active", "rejected", "proposed"):
        raise ResearchError(f"unknown status {status}")
    rows = area.sources()
    for r in rows:
        if r["id"] == sid:
            r["status"] = status
            area.save_sources(rows)
            area.log(f"source {sid} -> {status}")
            return
    raise ResearchError(f"no source {sid}")


def pending(area: Area) -> list[dict]:
    """Active sources not yet digested, or whose file changed since."""
    out = []
    for r in area.sources():
        if r.get("status") != "active":
            continue
        if r["kind"] == "url" and not r.get("digested_sha"):
            out.append(r)
        elif r["kind"] == "file" and r.get("digested_sha") != r.get("sha"):
            out.append(r)
    return out


# ------------------------------------------------------- reading files ----
class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.skip:
            self.skip -= 1

    def handle_data(self, d):
        if not self.skip and d.strip():
            self.parts.append(d.strip())


def _docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8", "replace")
    xml = re.sub(r"</w:p>", "\n", xml)
    return re.sub(r"<[^>]+>", "", xml)


def _xlsx_text(path: Path, max_rows: int = 400) -> str:
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    out = []
    for ws in wb.worksheets:
        out.append(f"## Sheet: {ws.title}")
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i >= max_rows:
                out.append(f"... (rows after {max_rows} not read)")
                break
            if any(v is not None for v in row):
                out.append(",".join("" if v is None else str(v) for v in row))
    return "\n".join(out)


def download_pdf(area: Area, src: dict, cfg: dict) -> str | None:
    """An approved URL that serves a PDF is saved into files/ and read whole
    (the web fetch tool can cut long documents short). Returns the file name,
    or None when the URL is not a PDF (then the model fetches the page itself)."""
    import urllib.request
    url = src["url"]
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (tara-automation research agent)"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            ctype = resp.headers.get("Content-Type", "")
            if "pdf" not in ctype.lower() and not url.lower().split("?")[0].endswith(".pdf"):
                return None
            limit = int(cfg["max_pdf_mb"] * 1e6)
            data = resp.read(limit + 1)
    except OSError as e:
        raise ResearchError(f"could not download {url}: {e}") from e
    if len(data) > limit:
        raise ResearchError(f"the PDF at {url} is over {cfg['max_pdf_mb']} MB; download and split it, "
                            "then drop the parts into files/")
    if not data.startswith(b"%PDF"):
        return None
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(url.split("?")[0]).name or src["id"])[:80]
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    name = f"{src['id']}_{name}"
    area.files.mkdir(exist_ok=True)
    (area.files / name).write_bytes(data)
    return name


def content_for(area: Area, src: dict, cfg: dict) -> list[dict]:
    """The message content that hands one source to the model."""
    if src["kind"] == "url" and not src.get("path"):
        return [{"type": "text", "text": f"Source {src['id']} is the web page or document at:\n{src['url']}\n"
                                         f"Fetch it with the web fetch tool and read it."}]
    f = area.files / src["path"]
    if not f.exists():
        raise ResearchError(f"file missing: {f.name}")
    ext = f.suffix.lower()
    if ext == ".pdf":
        mb = f.stat().st_size / 1e6
        if mb > cfg["max_pdf_mb"]:
            raise ResearchError(f"{f.name} is {mb:.0f} MB; split it into parts under {cfg['max_pdf_mb']} MB")
        return [{"type": "document", "title": f.name,
                 "source": {"type": "base64", "media_type": "application/pdf",
                            "data": base64.standard_b64encode(f.read_bytes()).decode("ascii")}}]
    if ext == ".docx":
        text = _docx_text(f)
    elif ext in (".xlsx", ".xlsm"):
        text = _xlsx_text(f)
    elif ext in (".html", ".htm"):
        p = _Text()
        p.feed(f.read_text(encoding="utf-8", errors="replace"))
        text = "\n".join(p.parts)
    else:
        text = f.read_text(encoding="utf-8", errors="replace")
    return [{"type": "document", "title": f.name,
             "source": {"type": "text", "media_type": "text/plain", "data": text or "(empty)"}}]


# ----------------------------------------------------------- the agent ----
SYSTEM = """You are the research agent of a transmission planning team. You read \
sources about one study area (a utility footprint, e.g. APS in Arizona) and \
record what matters for power-flow studies under four pillars:
1. Interconnection queue: queue positions, project names, technology, MW, POI \
substation and kV, study phase or cluster, status, requested and expected \
in-service dates, withdrawals.
2. Historical load and generation dispatch: annual energy and peak demand, \
growth, peak hour and season, generation mix by fuel, imports and exports, \
large-load additions (data centres, industrial).
3. Integrated resource plans: the preferred portfolio, MW of each resource type \
by year, retirements, the load forecast, procurement.
4. Ten-year transmission plans: each planned line and substation with kV, \
endpoints, in-service year, purpose and status.
Anything else goes under "other". Be specific: names, kV, MW, dates, page \
numbers. Never invent a fact; if a source does not say it, leave it out. Say \
plainly when a source is old or superseded by a newer one."""

DIGEST_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["title", "publisher", "date", "doc_type", "summary", "relevance", "findings",
                 "projects", "open_questions"],
    "properties": {
        "title": {"type": "string"}, "publisher": {"type": "string"},
        "date": {"type": "string", "description": "publication date as written, or empty"},
        "doc_type": {"type": "string"},
        "summary": {"type": "string", "description": "at most 150 words"},
        "relevance": {"type": "string", "enum": ["high", "medium", "low", "none"]},
        "findings": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["topic", "statement", "location"],
            "properties": {"topic": {"type": "string", "enum": TOPICS}, "statement": {"type": "string"},
                           "location": {"type": "string", "description": "page / section / table"}}}},
        "projects": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["name", "type", "kv", "mw", "poi", "status", "date"],
            "properties": {k: {"type": "string"} for k in ("name", "type", "kv", "mw", "poi", "status", "date")}}},
        "open_questions": {"type": "array", "items": {"type": "string"}},
    },
}
SYNTH_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["overview", "status", "sections", "conflicts", "gaps"],
    "properties": {
        "overview": {"type": "string", "description": "5-8 sentences, cite sources as [S1]"},
        "sections": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["topic", "points"],
            "properties": {"topic": {"type": "string", "enum": TOPICS}, "points": {"type": "array", "items": {
                "type": "object", "additionalProperties": False, "required": ["text", "sources"],
                "properties": {"text": {"type": "string"}, "sources": {"type": "array", "items": {"type": "string"}}}}}}}},
        "status": {"type": "array", "description": "one entry per pillar: the status report the desk shows first",
                   "items": {
            "type": "object", "additionalProperties": False,
            "required": ["topic", "status", "as_of", "headline", "gaps", "next_action"],
            "properties": {
                "topic": {"type": "string", "enum": TOPICS},
                "status": {"type": "string", "enum": ["current", "partial", "missing"],
                           "description": "current: the latest primary edition is in hand; partial: only older or "
                                          "secondary sources; missing: nothing usable"},
                "as_of": {"type": "string", "description": "date of the newest data behind this pillar"},
                "headline": {"type": "array", "description": "2-4 key numbers a planner quotes first",
                             "items": {"type": "object", "additionalProperties": False,
                                       "required": ["label", "value", "sources"],
                                       "properties": {"label": {"type": "string"}, "value": {"type": "string"},
                                                      "sources": {"type": "array", "items": {"type": "string"}}}}},
                "gaps": {"type": "array", "items": {"type": "string"}},
                "next_action": {"type": "string", "description": "the one thing that would most improve this pillar"}}}},
        "conflicts": {"type": "array", "items": {"type": "string"}},
        "gaps": {"type": "array", "items": {"type": "string"}},
    },
}
STATUS_WORD = {"current": "Current", "partial": "Partial", "missing": "Missing"}
DISCOVER_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["finds"],
    "properties": {"finds": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["title", "url", "publisher", "date", "doc_type", "why"],
        "properties": {k: {"type": "string"} for k in ("title", "url", "publisher", "date", "doc_type", "why")}}}},
}


def _client():
    try:
        import anthropic
    except ImportError as e:
        raise ResearchError("the anthropic package is not installed (pip install anthropic)") from e
    try:
        return anthropic.Anthropic()
    except anthropic.AnthropicError as e:
        raise ResearchError("no Anthropic API key: set ANTHROPIC_API_KEY for your Windows user, then restart "
                            "the study desk") from e


def api_ready() -> tuple[bool, str]:
    import os
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return True, "Anthropic API key found"
    return False, ("No Anthropic API key on this machine. Set ANTHROPIC_API_KEY for your Windows user "
                   "(System settings > Environment variables), then restart the study desk.")


def _ask(client, cfg: dict, content: list, schema: dict, effort: str, tools=None) -> tuple[dict, dict]:
    """One structured call (streamed), resuming server-tool pauses. Returns (data, usage)."""
    import anthropic
    messages = [{"role": "user", "content": content}]
    kw = {"model": cfg["model"], "max_tokens": cfg["max_tokens"], "system": SYSTEM, "messages": messages,
          "output_config": {"effort": effort, "format": {"type": "json_schema", "schema": schema}}}
    if tools:
        kw["tools"] = tools
    if cfg.get("fallbacks"):
        kw["betas"] = ["server-side-fallback-2026-07-01"]
        kw["fallbacks"] = "default"
    usage = {"input": 0, "output": 0, "cache_read": 0}
    for _ in range(6):                                    # server tools may pause a long turn
        try:
            with client.beta.messages.stream(**kw) as stream:
                msg = stream.get_final_message()
        except anthropic.AuthenticationError as e:
            raise ResearchError("the Anthropic API key was rejected") from e
        except anthropic.RateLimitError as e:
            raise ResearchError("Anthropic API rate limit reached; try again in a minute") from e
        except anthropic.BadRequestError as e:
            raise ResearchError(f"the Anthropic API refused the request: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise ResearchError("could not reach the Anthropic API (network)") from e
        u = msg.usage
        usage["input"] += (u.input_tokens or 0) + (getattr(u, "cache_creation_input_tokens", 0) or 0)
        usage["output"] += u.output_tokens or 0
        usage["cache_read"] += getattr(u, "cache_read_input_tokens", 0) or 0
        if msg.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": msg.content})
            continue
        if msg.stop_reason == "refusal":
            raise ResearchError("the model declined to process this source")
        if msg.stop_reason == "max_tokens":
            raise ResearchError("the answer was cut off (max_tokens); raise max_tokens in settings/research.json")
        for block in reversed(msg.content):
            if block.type == "text":
                try:
                    return json.loads(block.text), usage
                except json.JSONDecodeError:
                    continue
        raise ResearchError("no structured answer in the response")
    raise ResearchError("the turn kept pausing; try again")


def cost(usage: dict, cfg: dict) -> float:
    p = cfg["price_per_mtok"]
    return (usage["input"] * p["input"] + usage["output"] * p["output"] + usage["cache_read"] * p["cache_read"]) / 1e6


def _area_brief(area: Area) -> str:
    m = area.meta()
    return (f"Study area {area.id}: {m.get('name')}. {m.get('description', '')}\n"
            f"Footprint: {json.dumps(m.get('footprint', {}))}\n"
            f"Utilities: {', '.join(m.get('utilities', []))}. Keywords: {', '.join(m.get('keywords', []))}.")


def ingest(area: Area, progress=print, client=None) -> dict:
    cfg = settings()
    todo = pending(area)
    if not todo:
        return {"digested": 0, "failed": 0, "cost": 0.0}
    client = client or _client()
    rows = {r["id"]: r for r in area.sources()}
    done = failed = 0
    total = 0.0
    tools = [{"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 3}]
    for src in todo:
        progress(f"reading {src['id']} {src.get('title', '')}")
        try:
            if src["kind"] == "url" and not src.get("path"):
                name = download_pdf(area, src, cfg)
                if name:
                    rows[src["id"]]["path"] = src["path"] = name
                    progress(f"  downloaded {name}")
            content = content_for(area, src, cfg) + [{"type": "text", "text":
                f"{_area_brief(area)}\n\nDigest source {src['id']} for this study area."}]
            data, usage = _ask(client, cfg, content, DIGEST_SCHEMA, cfg["effort"],
                               tools if src["kind"] == "url" and not src.get("path") else None)
        except ResearchError as e:
            failed += 1
            rows[src["id"]]["status"] = "failed" if src["kind"] == "url" else "active"
            rows[src["id"]]["error"] = str(e)
            area.log(f"ingest {src['id']} FAILED: {e}")
            progress(f"  failed: {e}")
            continue
        c = cost(usage, cfg)
        total += c
        data.update({"source_id": src["id"], "model": cfg["model"], "digested_at": _now(),
                     "usage": usage, "cost_usd": round(c, 4)})
        (area.root / "digests").mkdir(exist_ok=True)
        (area.root / "digests" / f"{src['id']}.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
        r = rows[src["id"]]
        r["digested_sha"] = r.get("sha") or "url"
        r.pop("error", None)
        # a placeholder title (the file name or the URL) becomes the document's own
        placeholder = {r.get("url"), Path(r.get("path") or "x").stem}
        if data.get("title") and r.get("title") in placeholder:
            r["title"] = data["title"]
        r["date"] = data.get("date", "")
        r["relevance"] = data.get("relevance", "")
        done += 1
        area.log(f"ingest {src['id']} ({data.get('relevance')}): {len(data['findings'])} findings, "
                 f"{len(data['projects'])} projects; {usage['input']}+{usage['output']} tokens, ${c:.3f}")
        progress(f"  {len(data['findings'])} findings, ${c:.3f}")
        area.save_sources(list(rows.values()))
    area.save_sources(list(rows.values()))
    return {"digested": done, "failed": failed, "cost": round(total, 4)}


def synthesize(area: Area, progress=print, client=None) -> dict:
    cfg = settings()
    digests = [d for r in area.sources() if r.get("status") == "active" and (d := area.digest(r["id"]))]
    if not digests:
        raise ResearchError("no digested sources yet: add files or URLs and run Ingest first")
    client = client or _client()
    progress(f"writing the summary from {len(digests)} digest(s)")
    slim = [{k: d[k] for k in ("source_id", "title", "publisher", "date", "doc_type", "summary", "relevance",
                               "findings", "projects", "open_questions")} for d in digests]
    content = [{"type": "text", "text": f"{_area_brief(area)}\n\nDigests of every source (JSON):\n"
                                        f"{json.dumps(slim, indent=1)}\n\nWrite the study-area summary. "
                                        "Cite every point with its source ids (S1, S2...). Prefer the newest "
                                        "source when they disagree, and list the disagreement under conflicts. "
                                        "Give a status entry for each of the four pillars (source EIA is the "
                                        "computed EIA-930 data for load and dispatch; source QUEUE is the "
                                        "utility's own posted interconnection queue and cluster study reports, "
                                        "parsed on this machine: it is the primary source for the queue pillar, "
                                        "so base the queue numbers on it and use secondary compilations only for "
                                        "history it lacks, such as withdrawals)."}]
    data, usage = _ask(client, cfg, content, SYNTH_SCHEMA, cfg["synth_effort"])
    c = cost(usage, cfg)
    projects = [{**p, "source": d["source_id"]} for d in digests for p in d.get("projects", [])]
    data.update({"generated_at": _now(), "model": cfg["model"], "sources": [d["source_id"] for d in digests],
                 "projects": projects, "usage": usage, "cost_usd": round(c, 4)})
    (area.root / "findings.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    (area.root / "summary.md").write_text(render_summary(area, data), encoding="utf-8")
    area.log(f"summary written from {len(digests)} sources; ${c:.3f}")
    return {"sources": len(digests), "cost": round(c, 4)}


def render_summary(area: Area, data: dict) -> str:
    m = area.meta()
    titles = {r["id"]: r.get("title", r["id"]) for r in area.sources()}
    cite = lambda ids: " " + " ".join(f"[{i}]" for i in ids) if ids else ""   # noqa: E731
    out = [f"# {m.get('name', area.id)}: study area research", "",
           f"_Generated {data['generated_at']} by {data['model']} from {len(data['sources'])} sources._", "",
           data["overview"], ""]
    if data.get("status"):
        out += ["## Status by pillar", "", "| Pillar | Status | As of | Key numbers | Next action |", "|---|---|---|---|---|"]
        for st in sorted(data["status"], key=lambda x: TOPICS.index(x["topic"]) if x["topic"] in TOPICS else 99):
            nums = "; ".join(f"{h['label']}: {h['value']}{cite(h['sources'])}" for h in st["headline"])
            out.append(f"| {TOPIC_TITLE.get(st['topic'], 'Other')} | {STATUS_WORD.get(st['status'], st['status'])} "
                       f"| {st['as_of']} | {nums} | {st['next_action']} |")
        out.append("")
    # a topic from an older digest that is no longer a pillar is shown under Other
    for sec in sorted(data["sections"], key=lambda s: TOPICS.index(s["topic"]) if s["topic"] in TOPICS else 99):
        out += [f"## {TOPIC_TITLE.get(sec['topic'], 'Other')}", ""] + [f"- {p['text']}{cite(p['sources'])}" for p in sec["points"]] + [""]
    if data.get("conflicts"):
        out += ["## Where sources disagree", ""] + [f"- {x}" for x in data["conflicts"]] + [""]
    if data.get("gaps"):
        out += ["## Gaps to fill", ""] + [f"- {x}" for x in data["gaps"]] + [""]
    out += ["## Sources", ""] + [f"- [{i}] {titles.get(i, i)}" for i in data["sources"]] + [""]
    return "\n".join(out)


def discover(area: Area, progress=print, client=None) -> dict:
    cfg = settings()
    client = client or _client()
    known = [r.get("url") or r.get("title") for r in area.sources()]
    progress("searching the web for public data on the area")
    content = [{"type": "text", "text": f"{_area_brief(area)}\n\nAlready in the repository (do not repeat):\n"
                + "\n".join(f"- {k}" for k in known if k) +
                "\n\nSearch the web for public documents on three pillars, newest editions first, primary "
                "sources (the utility, its OASIS, the state commission, the planning region) over news:\n"
                "1. Interconnection queue: the utility's posted queue list (OASIS), cluster or system impact "
                "study reports, queue statistics.\n"
                "2. Integrated resource plans: the latest IRP or all-source RFP filings and their updates.\n"
                "3. Ten-year transmission plans: the latest ten-year plan, WECC annual progress reports, the "
                "state commission's biennial transmission assessment.\n"
                "Do not search for EIA load or generation data: that is downloaded separately. "
                "Return each document's direct URL."}]
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": cfg["web_search_max_uses"]},
             {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 6}]
    data, usage = _ask(client, cfg, content, DISCOVER_SCHEMA, cfg["effort"], tools)
    c = cost(usage, cfg)
    added = 0
    for f in data["finds"]:
        try:
            add_url(area, f["url"], f["title"], added_by="agent", why=f["why"], status="proposed",
                    extra={"publisher": f["publisher"], "date": f["date"], "doc_type": f["doc_type"]})
            added += 1
        except ResearchError:
            continue
    area.log(f"discover: {added} new source(s) proposed; ${c:.3f}")
    progress(f"proposed {added} new source(s)")
    return {"proposed": added, "cost": round(c, 4)}


def coverage(area: Area) -> dict:
    """Per pillar: sources whose digest has findings there, and the findings."""
    out = {t: {"sources": 0, "findings": 0, "ids": []} for t in TOPICS}
    for r in area.sources():
        if r.get("status") != "active":
            continue
        d = area.digest(r["id"])
        if not d:
            continue
        topics = [f["topic"] for f in d.get("findings", [])]
        for t in set(topics):
            if t in out:
                out[t]["sources"] += 1
                out[t]["ids"].append(r["id"])
        for t in topics:
            if t in out:
                out[t]["findings"] += 1
    return out


def area_view(area: Area) -> dict:
    """Everything the desk shows for one area."""
    rows = area.sources()
    f = area.root / "findings.json"
    s = area.root / "summary.md"
    lg = area.root / "log.md"
    return {
        "area": area.meta(), "sources": rows,
        "pending": [r["id"] for r in pending(area)],
        "findings": json.loads(f.read_text(encoding="utf-8")) if f.exists() else None,
        "summary_md": s.read_text(encoding="utf-8") if s.exists() else "",
        "log": lg.read_text(encoding="utf-8").splitlines()[-60:] if lg.exists() else [],
        "digests": {r["id"]: area.digest(r["id"]) for r in rows if area.digest(r["id"])},
        "eia": (json.loads((area.root / "data" / "eia" / "summary.json").read_text(encoding="utf-8"))
                if (area.root / "data" / "eia" / "summary.json").exists() else None),
        "coverage": coverage(area),
        "files_dir": str(area.files),
        "queue": _queue_view(area),
        "large_loads": _large_loads_view(area),
    }


def _large_loads_view(area: Area):
    """The area's large-load research (data/large_loads; positions from scripts/large_loads.py build), or None."""
    from tara_automation import large_loads
    return large_loads.view(area.root)


def _queue_view(area: Area):
    """The area's interconnection queue data (task 23 adapter), when it has one."""
    from tara_automation.ingest import desk_view
    return desk_view(area.id, area.root)
