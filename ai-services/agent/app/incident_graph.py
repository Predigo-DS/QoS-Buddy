from __future__ import annotations

from typing import Any, NotRequired, TypedDict

from langgraph.graph import END, StateGraph


class IncidentState(TypedDict):
    incident: dict[str, Any]
    risk: NotRequired[dict[str, Any]]
    remaining_plan: NotRequired[list[dict[str, Any]]]
    next_tool: NotRequired[dict[str, Any] | None]
    tool_trace: NotRequired[list[dict[str, Any]]]
    validation: NotRequired[dict[str, Any]]
    decision: NotRequired[str]
    expected_recovery_seconds: NotRequired[int | None]


# Memory store for simulation mode only.
_INCIDENT_MEMORY: dict[str, list[dict[str, Any]]] = {}


# Diagnostics tools

def get_device_status(device: str) -> dict[str, Any]:
    return {"cpu": 91, "memory": 74, "status": "congested"}



def get_interface_errors(device: str) -> dict[str, Any]:
    return {
        "device": device,
        "input_errors": 11,
        "output_errors": 3,
        "crc_errors": 1,
        "status": "elevated",
    }



def get_route_table(device: str) -> dict[str, Any]:
    return {
        "device": device,
        "default_route": "10.0.0.1",
        "next_hops": ["10.0.0.2", "10.0.0.3"],
        "route_health": "degraded",
    }


# Action tools

def reroute_traffic(device: str) -> str:
    return f"Traffic rerouted for {device}"



def restart_interface(device: str) -> str:
    return f"Interface restarted for {device}"



def limit_bandwidth(device: str) -> str:
    return f"Bandwidth limited for {device}"


# Safety tools

def simulate_change(
    device: str, change: str, risk_level: str = "medium"
) -> dict[str, Any]:
    safe = risk_level != "critical"
    return {
        "device": device,
        "change": change,
        "safe": safe,
        "reason": "approved" if safe else "risk too high for auto-remediation",
    }



def rollback_last_change(device: str) -> str:
    return f"Rollback completed for {device}"


# Memory tools

def search_past_incidents(device: str) -> list[dict[str, Any]]:
    entries = _INCIDENT_MEMORY.get(device, [])
    return entries[-5:]



def store_resolution(device: str, incident: dict[str, Any], resolution: str) -> str:
    _INCIDENT_MEMORY.setdefault(device, []).append(
        {
            "incident": incident,
            "resolution": resolution,
        }
    )
    return f"Resolution stored for {device}"


TOOL_REGISTRY = {
    # Diagnostics
    "get_device_status": get_device_status,
    "get_interface_errors": get_interface_errors,
    "get_route_table": get_route_table,
    # Actions
    "reroute_traffic": reroute_traffic,
    "restart_interface": restart_interface,
    "limit_bandwidth": limit_bandwidth,
    # Safety
    "simulate_change": simulate_change,
    "rollback_last_change": rollback_last_change,
    # Memory
    "search_past_incidents": search_past_incidents,
    "store_resolution": store_resolution,
}


def available_placeholder_tools() -> dict[str, list[str]]:
    return {
        "diagnostics": [
            "get_device_status",
            "get_interface_errors",
            "get_route_table",
        ],
        "actions": [
            "reroute_traffic",
            "restart_interface",
            "limit_bandwidth",
        ],
        "safety": ["simulate_change", "rollback_last_change"],
        "memory": ["search_past_incidents", "store_resolution"],
    }



def _tool_step(name: str, **kwargs) -> dict[str, Any]:
    return {"name": name, "args": kwargs}



def _execute_tool(step: dict[str, Any]) -> dict[str, Any]:
    name = step.get("name", "")
    args = step.get("args", {})
    fn = TOOL_REGISTRY.get(name)

    if fn is None:
        return {
            "tool": name,
            "args": args,
            "result": {"error": f"Unknown tool: {name}"},
        }

    try:
        result = fn(**args)
    except Exception as exc:  # noqa: BLE001
        result = {"error": str(exc)}

    return {
        "tool": name,
        "args": args,
        "result": result,
    }



def _risk_level(score: int) -> str:
    if score >= 80:
        return "critical"
    if score >= 55:
        return "high"
    if score >= 30:
        return "medium"
    return "low"



def risk_analysis_node(state: IncidentState) -> IncidentState:
    incident = state.get("incident", {})
    latency = float(incident.get("latency") or 0)
    cpu = float(incident.get("cpu") or 0)
    memory = float(incident.get("memory") or 0)
    packet_loss = float(incident.get("packet_loss") or 0)

    score = 0
    score += 40 if latency >= 150 else 25 if latency >= 100 else 0
    score += 35 if cpu >= 90 else 20 if cpu >= 75 else 0
    score += 15 if memory >= 85 else 0
    score += 25 if packet_loss >= 2 else 10 if packet_loss >= 1 else 0

    device = str(incident.get("device") or "unknown-device")

    return {
        "risk": {
            "device": device,
            "latency": latency,
            "cpu": cpu,
            "memory": memory,
            "packet_loss": packet_loss,
            "score": score,
            "level": _risk_level(score),
        },
        "remaining_plan": [_tool_step("get_device_status", device=device)],
        "next_tool": None,
        "tool_trace": [],
        "validation": {"status": "pending", "notes": []},
        "decision": "",
        "expected_recovery_seconds": None,
    }



def planner_node(state: IncidentState) -> IncidentState:
    next_tool = state.get("next_tool")
    if next_tool:
        return {}

    remaining = list(state.get("remaining_plan") or [])
    if not remaining:
        return {"next_tool": None}

    return {
        "next_tool": remaining[0],
        "remaining_plan": remaining[1:],
    }



def call_tool_node(state: IncidentState) -> IncidentState:
    next_tool = state.get("next_tool")
    if not next_tool:
        return {}

    trace = list(state.get("tool_trace") or [])
    trace.append(_execute_tool(next_tool))

    return {
        "tool_trace": trace,
        "next_tool": None,
    }



def validator_node(state: IncidentState) -> IncidentState:
    trace = list(state.get("tool_trace") or [])
    if not trace:
        return {
            "validation": {
                "status": "failed",
                "notes": ["No tools were executed."],
            }
        }

    latest = trace[-1]
    tool_name = latest.get("tool", "")
    tool_result = latest.get("result")

    incident = state.get("incident", {})
    device = str(incident.get("device") or "unknown-device")
    risk = state.get("risk", {})

    remaining = list(state.get("remaining_plan") or [])
    validation = dict(state.get("validation") or {})
    notes = list(validation.get("notes") or [])
    status = str(validation.get("status") or "in_progress")

    decision = state.get("decision", "")
    expected_recovery_seconds = state.get("expected_recovery_seconds")

    if tool_name == "get_device_status":
        device_state = ""
        if isinstance(tool_result, dict):
            device_state = str(tool_result.get("status") or "").lower()

        if device_state == "congested":
            notes.append("Congestion confirmed; scheduling remediation path.")
            remaining.extend(
                [
                    _tool_step("get_interface_errors", device=device),
                    _tool_step("get_route_table", device=device),
                    _tool_step("search_past_incidents", device=device),
                    _tool_step(
                        "simulate_change",
                        device=device,
                        change="reroute_traffic",
                        risk_level=str(risk.get("level") or "medium"),
                    ),
                    _tool_step("reroute_traffic", device=device),
                ]
            )
        else:
            notes.append("No congestion reported; applying conservative controls.")
            remaining.extend(
                [
                    _tool_step(
                        "simulate_change",
                        device=device,
                        change="limit_bandwidth",
                        risk_level=str(risk.get("level") or "medium"),
                    ),
                    _tool_step("limit_bandwidth", device=device),
                ]
            )

        status = "in_progress"

    elif tool_name == "get_interface_errors":
        if isinstance(tool_result, dict) and int(tool_result.get("input_errors") or 0) > 30:
            notes.append("Interface errors are high; scheduling interface restart.")
            remaining.extend(
                [
                    _tool_step(
                        "simulate_change",
                        device=device,
                        change="restart_interface",
                        risk_level=str(risk.get("level") or "medium"),
                    ),
                    _tool_step("restart_interface", device=device),
                ]
            )

    elif tool_name == "simulate_change":
        safe = True
        if isinstance(tool_result, dict):
            safe = bool(tool_result.get("safe"))

        if not safe:
            notes.append("Safety simulation rejected the planned action.")
            remaining = [_tool_step("rollback_last_change", device=device)]
            decision = "Change blocked by safety simulation. Rollback initiated."
            expected_recovery_seconds = None
            status = "blocked"

    elif tool_name in {"reroute_traffic", "restart_interface", "limit_bandwidth"}:
        action_decisions = {
            "reroute_traffic": "Traffic rerouted successfully.",
            "restart_interface": "Interface restarted successfully.",
            "limit_bandwidth": "Bandwidth limit applied successfully.",
        }
        decision = f"{action_decisions[tool_name]} Expected SLA recovery in 60 sec."
        expected_recovery_seconds = 60
        remaining.append(
            _tool_step(
                "store_resolution",
                device=device,
                incident=incident,
                resolution=decision,
            )
        )
        status = "stabilizing"

    elif tool_name == "rollback_last_change":
        decision = f"Rollback completed for {device}. Manual review required."
        expected_recovery_seconds = None
        status = "rolled_back"

    elif tool_name == "store_resolution":
        status = "done"

    if not remaining and status not in {"rolled_back", "failed"}:
        status = "done" if decision else "in_progress"

    return {
        "remaining_plan": remaining,
        "validation": {
            "status": status,
            "notes": notes,
        },
        "decision": decision,
        "expected_recovery_seconds": expected_recovery_seconds,
    }



def final_decision_node(state: IncidentState) -> IncidentState:
    decision = str(state.get("decision") or "").strip()
    trace = list(state.get("tool_trace") or [])

    if not decision:
        if trace:
            decision = "Incident analyzed. No automated action applied."
        else:
            decision = "No actionable incident details were provided."

    return {
        "decision": decision,
    }



def _route_after_planner(state: IncidentState) -> str:
    return "call_tool" if state.get("next_tool") else "final_decision"



def _route_after_validator(state: IncidentState) -> str:
    validation = state.get("validation", {})
    status = str(validation.get("status") or "")
    has_pending = bool(state.get("remaining_plan"))

    if has_pending and status not in {"done", "rolled_back", "failed"}:
        return "planner"

    return "final_decision"



def build_incident_graph():
    builder = StateGraph(IncidentState)

    builder.add_node("risk_analysis", risk_analysis_node)
    builder.add_node("planner", planner_node)
    builder.add_node("call_tool", call_tool_node)
    builder.add_node("validator", validator_node)
    builder.add_node("final_decision", final_decision_node)

    builder.set_entry_point("risk_analysis")
    builder.add_edge("risk_analysis", "planner")
    builder.add_conditional_edges(
        "planner",
        _route_after_planner,
        {
            "call_tool": "call_tool",
            "final_decision": "final_decision",
        },
    )
    builder.add_edge("call_tool", "validator")
    builder.add_conditional_edges(
        "validator",
        _route_after_validator,
        {
            "planner": "planner",
            "final_decision": "final_decision",
        },
    )
    builder.add_edge("final_decision", END)

    return builder.compile()
