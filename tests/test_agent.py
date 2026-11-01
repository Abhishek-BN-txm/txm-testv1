"""Tests for the agent framework's deterministic layers (no API calls)."""

import json

from tara_automation.agents import list_agents, run_agent
from tara_automation.agents import runner, tools


def _fake_runs(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    for n in (3, 10):
        d = runs / str(n)
        (d / "results").mkdir(parents=True)
        (d / "logs").mkdir()
        (d / "manifest.json").write_text(json.dumps(
            {"run": n, "engine": "comm", "status": "success"}), encoding="utf-8")
        (d / "results" / "Warn_List.csv").write_text(
            "1, 1417, 1, shunt at bus 65585 cannot reach target\n", encoding="utf-8")
        (d / "logs" / "tara_trace.log").write_text("line1\nline2\n", encoding="utf-8")
    digests = runs / "output" / "digests"
    digests.mkdir(parents=True)
    (digests / "compare_3_vs_10.md").write_text("# digest\n- finding", encoding="utf-8")
    monkeypatch.setattr(tools, "RUNS_ROOT", runs)
    monkeypatch.setattr(runner, "RUNS_ROOT", runs)
    return runs


def test_registry_and_definition():
    assert "review_runs" in list_agents()
    defn = runner.load_def("review_runs")
    assert defn["evidence"] in tools.GATHERERS
    for t in defn["tools"]:
        assert t in tools.TOOLS
    assert (runner.REPO_ROOT / defn["recipe"]).exists()


def test_gatherer_evidence_pack(tmp_path, monkeypatch):
    _fake_runs(tmp_path, monkeypatch)
    user = tools.gather_runs_review({"mode": "compare", "runs": [3, 10]})
    assert "# digest" in user and "65585" in user and '"status": "success"' in user


def test_dry_run_end_to_end(tmp_path, monkeypatch):
    _fake_runs(tmp_path, monkeypatch)
    stats = run_agent("review_runs", {"mode": "compare", "runs": [3, 10]},
                      dry_run=True)
    assert stats["agent"] == "review_runs"
    assert stats["approx_tokens"] > 100  # recipe + evidence present


def test_search_tool_read_only_and_capped(tmp_path, monkeypatch):
    runs = _fake_runs(tmp_path, monkeypatch)
    out = tools.search_artifacts("65585", 3)
    assert "Warn_List.csv" in out
    assert tools.search_artifacts("no_such_string", 3) == "(no matches)"
    assert "bad regex" in tools.search_artifacts("(", 3)
    assert sorted(p.name for p in (runs / "3" / "results").iterdir()) == ["Warn_List.csv"]


def test_write_back_namespaced_and_logged(tmp_path, monkeypatch):
    runs = _fake_runs(tmp_path, monkeypatch)
    defn = runner.load_def("review_runs")
    out = runner._write_back(defn, {"mode": "compare", "runs": [3, 10]},
                             "# Review: verdict body", "test-model",
                             {"input_tokens": 10, "output_tokens": 5})
    assert out.parent == runs / "output" / "review_runs"
    assert out.name == "review_compare_3_vs_10.md"
    assert "verdict body" in out.read_text(encoding="utf-8")
    assert "test-model" in (runs / "RUN_LOG.md").read_text(encoding="utf-8")
    agent_log = (runs / "agents" / "AGENT_LOG.md").read_text(encoding="utf-8")
    assert "tokens_in=10" in agent_log
