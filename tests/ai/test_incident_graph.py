import asyncio
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[2] / "ai-services" / "agent" / "app"
sys.path.insert(0, str(APP_DIR))

from incident_graph import build_incident_graph


def _run_graph(incident: dict):
    graph = build_incident_graph()
    return asyncio.run(graph.ainvoke({"incident": incident}))


def test_congested_incident_reroutes_traffic():
    result = _run_graph({"device": "Router_A", "latency": 155, "cpu": 92})

    tools = [step["tool"] for step in result.get("tool_trace", [])]

    assert "get_device_status" in tools
    assert "reroute_traffic" in tools
    assert result.get("decision", "").startswith("Traffic rerouted successfully")
    assert result.get("expected_recovery_seconds") == 60


def test_critical_risk_triggers_safety_rollback():
    result = _run_graph(
        {
            "device": "Router_A",
            "latency": 200,
            "cpu": 99,
            "memory": 95,
            "packet_loss": 5,
        }
    )

    tools = [step["tool"] for step in result.get("tool_trace", [])]

    assert "simulate_change" in tools
    assert "rollback_last_change" in tools
    assert "Rollback completed" in result.get("decision", "")
