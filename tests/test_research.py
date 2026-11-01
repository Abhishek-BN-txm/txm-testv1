"""Task 29: study-area research repository and agent (Anthropic API stubbed)."""

from __future__ import annotations

import json

import pytest

from tara_automation import research as R


@pytest.fixture
def area(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "ROOT", tmp_path / "areas")
    return R.create_area("APS", "Arizona Public Service", "test area", {"areas": [14]}, ["APS"], ["Phoenix"])


DIGEST = {"title": "APS 2025 Ten-Year Plan", "publisher": "APS", "date": "2025-01-31", "doc_type": "ten-year plan",
          "summary": "Planned 500 kV and 230 kV projects.", "relevance": "high",
          "findings": [{"topic": "transmission_plans", "statement": "New 500 kV line in 2028", "location": "p. 12"}],
          "projects": [{"name": "Line X", "type": "line", "kv": "500", "mw": "", "poi": "", "status": "planned",
                        "date": "2028"}], "open_questions": []}


def fake_ask(calls):
    def ask(client, cfg, content, schema, effort, tools=None):
        calls.append({"schema": schema, "tools": tools, "content": content})
        if schema is R.DIGEST_SCHEMA:
            return dict(DIGEST), {"input": 1000, "output": 200, "cache_read": 0}
        if schema is R.SYNTH_SCHEMA:
            return ({"overview": "APS is growing [S1].", "conflicts": [], "gaps": ["queue data"],
                     "status": [{"topic": "transmission_plans", "status": "current", "as_of": "2025-01-31",
                                 "headline": [{"label": "New 500 kV", "value": "1 line", "sources": ["S1"]}],
                                 "gaps": [], "next_action": "Add the 2026 plan."}],
                     "sections": [{"topic": "transmission_plans",
                                   "points": [{"text": "Line X, 500 kV, 2028", "sources": ["S1"]}]}]},
                    {"input": 500, "output": 300, "cache_read": 0})
        return ({"finds": [{"title": "APS 2026 APR", "url": "https://example.com/aps2026.pdf", "publisher": "WECC",
                            "date": "2026", "doc_type": "report", "why": "newest project status"}]},
                {"input": 300, "output": 100, "cache_read": 0})
    return ask


def test_files_are_registered_once_and_reread_when_changed(area):
    (area.files / "plan.txt").write_text("500 kV line planned", encoding="utf-8")
    (area.files / "~$lock.docx").write_text("x", encoding="utf-8")       # Office lock file: ignored
    assert R.scan(area) == ["S1"]
    assert R.scan(area) == []
    assert [r["id"] for r in R.pending(area)] == ["S1"]
    rows = area.sources()
    rows[0]["digested_sha"] = rows[0]["sha"]
    area.save_sources(rows)
    assert R.pending(area) == []
    (area.files / "plan.txt").write_text("500 kV line planned, now 2029", encoding="utf-8")
    R.scan(area)
    assert [r["id"] for r in R.pending(area)] == ["S1"]                   # changed: read again


def test_ingest_summarize_and_discover(area, monkeypatch):
    calls = []
    monkeypatch.setattr(R, "_ask", fake_ask(calls))
    (area.files / "plan.txt").write_text("500 kV line planned", encoding="utf-8")
    R.scan(area)
    res = R.ingest(area, progress=lambda *_: None, client=object())
    assert res["digested"] == 1 and res["cost"] > 0
    assert calls[0]["tools"] is None                                       # a file needs no web tools
    assert calls[0]["content"][0]["source"]["data"] == "500 kV line planned"
    assert area.digest("S1")["findings"][0]["statement"] == "New 500 kV line in 2028"
    assert area.sources()[0]["title"] == "APS 2025 Ten-Year Plan"          # placeholder replaced
    assert R.pending(area) == []
    R.synthesize(area, progress=lambda *_: None, client=object())
    md = (area.root / "summary.md").read_text(encoding="utf-8")
    assert "## Ten-year transmission plans" in md and "Line X, 500 kV, 2028 [S1]" in md
    assert "| Ten-year transmission plans | Current | 2025-01-31 | New 500 kV: 1 line [S1] | Add the 2026 plan. |" in md
    assert R.coverage(area)["transmission_plans"]["ids"] == ["S1"]
    f = json.loads((area.root / "findings.json").read_text(encoding="utf-8"))
    assert f["projects"][0]["source"] == "S1"
    out = R.discover(area, progress=lambda *_: None, client=object())
    assert out["proposed"] == 1
    prop = [r for r in area.sources() if r["status"] == "proposed"]
    assert prop[0]["added_by"] == "agent" and prop[0]["url"] == "https://example.com/aps2026.pdf"
    assert R.pending(area) == []                                           # proposed: not read until approved
    R.set_status(area, prop[0]["id"], "active")
    assert [r["id"] for r in R.pending(area)] == [prop[0]["id"]]
    assert "summary written" in (area.root / "log.md").read_text(encoding="utf-8")


def test_url_rules_and_area_ids(area):
    with pytest.raises(R.ResearchError):
        R.add_url(area, "ftp://x")
    R.add_url(area, "https://example.com/a")
    with pytest.raises(R.ResearchError):
        R.add_url(area, "https://example.com/a")                           # no duplicates
    with pytest.raises(R.ResearchError):
        R.create_area("../evil", "x")
    with pytest.raises(R.ResearchError):
        R.get_area("NOPE")


def test_desk_research_endpoints(tmp_path, monkeypatch):
    import base64
    import threading

    import scripts.study_desk as sd
    from tests.test_study_desk import call
    monkeypatch.setattr(R, "ROOT", tmp_path / "areas")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    srv = sd.make_server(0)
    port = srv.server_address[1]
    sd.Handler.port = port
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    hdr = {"Content-Type": "application/json", "Origin": base}
    post = lambda act, body: call(f"{base}/api/research/{act}", json.dumps(body).encode(), hdr)  # noqa: E731
    try:
        assert post("create", {"id": "APS", "name": "APS", "areas": [14]})[0] == 201
        code, j = call(base + "/api/research")
        assert code == 200 and j["areas"][0]["id"] == "APS" and j["api"]["ready"] is False
        assert post("url", {"area": "APS", "url": "https://example.com/x"})[0] == 201
        code, j = post("upload", {"area": "APS", "name": "../../notes.txt",
                                  "data": base64.b64encode(b"hello").decode()})
        assert code == 201 and j["file"] == "notes.txt" and j["added"] == ["S2"]   # path stripped
        assert (tmp_path / "areas" / "APS" / "files" / "notes.txt").read_bytes() == b"hello"
        assert post("upload", {"area": "APS", "name": "x.exe", "data": ""})[0] == 400
        code, j = call(base + "/api/research/area?id=APS")
        assert code == 200 and {r["id"] for r in j["sources"]} == {"S1", "S2"} and j["pending"] == ["S1", "S2"]
        assert post("status", {"area": "APS", "source": "S1", "status": "rejected"})[0] == 200
        code, j = post("run", {"area": "APS", "actions": ["ingest"]})
        assert code == 412 and "API key" in j["error"]                    # no key: refused, nothing spent
        assert call(f"{base}/api/research/url", b"{}", {"Content-Type": "application/json",
                                                        "Origin": "https://evil.example"})[0] == 403
    finally:
        srv.shutdown()
