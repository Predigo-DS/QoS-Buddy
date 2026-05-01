import json
from typing import Any, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph

# ──────────────────────────────────────────
# Mock network tools
# ──────────────────────────────────────────

def reroute_traffic(device: str, path: str) -> dict:
    args = json.dumps({"device": device, "path": path})
    print(f"[MOCK_TOOL] reroute_traffic args={args}")
    return {"status": "ok", "action": "reroute_traffic", "device": device, "path": path}


def throttle_link(device: str, interface: str, rate_limit_mbps: float) -> dict:
    args = json.dumps({"device": device, "interface": interface, "rate_limit_mbps": rate_limit_mbps})
    print(f"[MOCK_TOOL] throttle_link args={args}")
    return {"status": "ok", "action": "throttle_link", "device": device, "interface": interface, "rate_limit_mbps": rate_limit_mbps}


def restart_interface(device: str, interface: str) -> dict:
    args = json.dumps({"device": device, "interface": interface})
    print(f"[MOCK_TOOL] restart_interface args={args}")
    return {"status": "ok", "action": "restart_interface", "device": device, "interface": interface}


def apply_qos_profile(device: str, profile: str) -> dict:
    args = json.dumps({"device": device, "profile": profile})
    print(f"[MOCK_TOOL] apply_qos_profile args={args}")
    return {"status": "ok", "action": "apply_qos_profile", "device": device, "profile": profile}


def monitor_only(device: str, reason: str = "") -> dict:
    args = json.dumps({"device": device, "reason": reason})
    print(f"[MOCK_TOOL] monitor_only args={args}")
    return {"status": "ok", "action": "monitor_only", "device": device, "reason": reason, "message": "No action taken — monitoring only."}


def _decision_summary(decision_summary: str, recommended_actions: list, confidence: float, risk_level: str) -> dict:
    """Captures the agent's final structured decision."""
    print(f"[DECISION] risk={risk_level} confidence={confidence}")
    return {
        "status": "ok",
        "decision_summary": decision_summary,
        "recommended_actions": recommended_actions,
        "confidence": confidence,
        "risk_level": risk_level,
    }


TOOL_REGISTRY = {
    "reroute_traffic": reroute_traffic,
    "throttle_link": throttle_link,
    "restart_interface": restart_interface,
    "apply_qos_profile": apply_qos_profile,
    "monitor_only": monitor_only,
}

# LangChain tool schemas for binding
from langchain_core.tools import tool as lc_tool

@lc_tool
def reroute_traffic_tool(device: str, path: str) -> dict:
    """Reroute network traffic for a device through a specified path."""
    return reroute_traffic(device, path)

@lc_tool
def throttle_link_tool(device: str, interface: str, rate_limit_mbps: float) -> dict:
    """Throttle a network link to the given rate limit in Mbps."""
    return throttle_link(device, interface, rate_limit_mbps)

@lc_tool
def restart_interface_tool(device: str, interface: str) -> dict:
    """Restart a network interface on a device."""
    return restart_interface(device, interface)

@lc_tool
def apply_qos_profile_tool(device: str, profile: str) -> dict:
    """Apply a QoS profile to a device."""
    return apply_qos_profile(device, profile)


@lc_tool
def monitor_only_tool(device: str, reason: str = "") -> dict:
    """Take no remediation action — continue monitoring the device. Use when uncertainty is high or situation is stable."""
    return monitor_only(device, reason)


@lc_tool
def decision_summary_tool(decision_summary: str, recommended_actions: list, confidence: float, risk_level: str) -> dict:
    """REQUIRED: Call this tool LAST to submit your final structured decision after all actions are complete.
    Args:
        decision_summary: One sentence describing what was done and why.
        recommended_actions: List of action strings taken or recommended.
        confidence: Your confidence score between 0.0 and 1.0.
        risk_level: Current risk level: low, medium, high, or critical.
    """
    return _decision_summary(decision_summary, recommended_actions, confidence, risk_level)


BOUND_TOOLS = [
    reroute_traffic_tool,
    throttle_link_tool,
    restart_interface_tool,
    apply_qos_profile_tool,
    monitor_only_tool,
    decision_summary_tool,
]

OPTIMIZATION_SYSTEM_PROMPT = """You are a network optimization agent for a QoS monitoring system.
Your job is to take CONCRETE remediation actions when network metrics show degradation.
Never refuse to act when anomaly_detected=true or sla_alert=true — these are confirmed signals, not estimates.

Decision rules (apply in order):
1. If anomaly_detected=true AND sla_alert=true → CRITICAL situation. Call reroute_traffic OR apply_qos_profile immediately, then decision_summary_tool.
2. If plr > 0.05 OR e2e_delay_ms > 100 OR jitter_ms > 20 → call apply_qos_profile to prioritize traffic.
3. If dataplane_latency_ms > 10 OR rx_dropped > 20 → call reroute_traffic to bypass congested path.
4. If mos_voice < 3.0 OR streaming_mos < 3.0 → call apply_qos_profile with a voice/video priority profile.
5. If throughput_mbps < 5 AND flow_count > 120 → call throttle_link to reduce congestion.
6. ONLY call monitor_only if ALL metrics are within normal range (plr<0.02, e2e_delay<80ms, mos>3.5, no anomaly, no sla_alert).

confidence scoring:
- anomaly_detected=true + sla_alert=true → confidence >= 0.85
- only one of them → confidence 0.65-0.80
- neither → confidence 0.50, use monitor_only

After taking action(s), you MUST call decision_summary_tool as the FINAL step with:
  - decision_summary: one sentence describing what action was taken and why
  - recommended_actions: list of action strings (e.g. ["apply_qos_profile on switch-core-01", "monitor traffic"])
  - confidence: float 0.0-1.0
  - risk_level: low / medium / high / critical
Do NOT output JSON as text. Always call decision_summary_tool last.
"""

# ──────────────────────────────────────────
# Graph state
# ──────────────────────────────────────────

class OptimizationState(TypedDict):
    messages: list
    anomaly_result: Any
    sla_result: Any
    avg_30s: dict
    device: str
    context: str
    tool_trace: list[dict]
    decision_output: dict


# ──────────────────────────────────────────
# Graph nodes
# ──────────────────────────────────────────

def input_validation_node(state: OptimizationState) -> dict:
    avg = state.get("avg_30s") or {}
    device = state.get("device") or "unknown-device"
    anomaly = state.get("anomaly_result") or {}
    sla = state.get("sla_result") or {}
    context = state.get("context", "")

    # Extract key signals explicitly so LLM doesn't have to parse nested JSON
    anomaly_detected = anomaly.get("anomaly_detected", False) or anomaly.get("anomaly_windows", 0) > 0
    sla_alert = sla.get("sla_alert", False) or sla.get("alert_count", 0) > 0
    plr = avg.get("plr", 0)
    delay = avg.get("e2e_delay_ms", 0)
    mos = avg.get("mos_voice", 0)
    jitter = avg.get("jitter_ms", 0)
    dp_latency = avg.get("dataplane_latency_ms", 0)
    rx_dropped = avg.get("rx_dropped", 0)
    throughput = avg.get("throughput_mbps", 0)
    streaming_mos = avg.get("streaming_mos", 0)

    alert_lines = []
    if anomaly_detected:
        alert_lines.append(f"  ⚠ ANOMALY DETECTED (score={anomaly.get('anomaly_score', anomaly.get('anomaly_windows', '?'))})")
    if sla_alert:
        alert_lines.append(f"  ⚠ SLA VIOLATION FORECASTED (alert_rate={sla.get('alert_rate', sla.get('sla_violation_probability', '?'))})")
    if plr > 0.05:
        alert_lines.append(f"  ⚠ HIGH PACKET LOSS: {plr:.4f} ({plr*100:.2f}%)")
    if delay > 100:
        alert_lines.append(f"  ⚠ HIGH LATENCY: {delay:.1f}ms")
    if mos > 0 and mos < 3.0:
        alert_lines.append(f"  ⚠ POOR VOICE QUALITY: MOS={mos:.2f}")

    alerts_str = "\n".join(alert_lines) if alert_lines else "  ✓ No active alerts"

    summary = f"""NETWORK OPTIMIZATION REQUEST
Device: {device}
Context: {context}

=== ACTIVE ALERTS ===
{alerts_str}

=== KEY METRICS (30s avg) ===
  Packet Loss Rate  : {plr:.4f} ({plr*100:.2f}%)
  E2E Delay         : {delay:.1f} ms
  Jitter            : {jitter:.1f} ms
  Voice MOS         : {mos:.2f}
  Streaming MOS     : {streaming_mos:.2f}
  Dataplane Latency : {dp_latency:.1f} ms
  RX Dropped        : {rx_dropped:.0f} packets
  Throughput        : {throughput:.2f} Mbps

=== ANOMALY DETECTION RESULT ===
{json.dumps(anomaly, default=str)}

=== SLA FORECASTING RESULT ===
{json.dumps(sla, default=str)}

Based on the above, take the appropriate remediation action now.
"""
    messages = [
        SystemMessage(content=OPTIMIZATION_SYSTEM_PROMPT),
        HumanMessage(content=summary),
    ]
    return {"messages": messages, "tool_trace": []}


def llm_decision_node(state: OptimizationState, llm_with_tools) -> dict:
    response = llm_with_tools.invoke(state["messages"])
    return {"messages": state["messages"] + [response]}


def tool_execution_node(state: OptimizationState) -> dict:
    messages = state["messages"]
    last_msg = messages[-1]
    tool_trace = list(state.get("tool_trace") or [])
    new_messages = list(messages)

    if not hasattr(last_msg, "tool_calls") or not last_msg.tool_calls:
        return {"messages": new_messages, "tool_trace": tool_trace}

    for tc in last_msg.tool_calls:
        tool_name = tc["name"]
        tool_args = tc["args"]
        tool_id = tc.get("id", tool_name)

        fn_map = {
            "reroute_traffic_tool": reroute_traffic_tool,
            "throttle_link_tool": throttle_link_tool,
            "restart_interface_tool": restart_interface_tool,
            "apply_qos_profile_tool": apply_qos_profile_tool,
            "monitor_only_tool": monitor_only_tool,
            "decision_summary_tool": decision_summary_tool,
        }
        fn = fn_map.get(tool_name)
        if fn:
            result = fn.invoke(tool_args)
        else:
            result = {"error": f"Unknown tool: {tool_name}"}

        tool_trace.append({"tool": tool_name, "args": tool_args, "result": result})
        new_messages.append(ToolMessage(content=json.dumps(result), tool_call_id=tool_id))

    return {"messages": new_messages, "tool_trace": tool_trace}


def final_decision_node(state: OptimizationState) -> dict:
    # First: check if decision_summary_tool was called — it contains the structured decision
    tool_trace = state.get("tool_trace") or []
    for entry in reversed(tool_trace):
        if entry.get("tool") == "decision_summary_tool":
            result = entry.get("result", {})
            if isinstance(result, dict) and "decision_summary" in result:
                return {"decision_output": {
                    "decision_summary": result.get("decision_summary", ""),
                    "recommended_actions": result.get("recommended_actions", []),
                    "confidence": float(result.get("confidence", 0.5)),
                    "risk_level": str(result.get("risk_level", "medium")),
                }}

    # Fallback: extract from last AI message text
    messages = state["messages"]
    last_ai = None
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and isinstance(msg.content, str) and msg.content.strip():
            last_ai = msg.content
            break

    decision = {
        "decision_summary": last_ai or "No decision produced.",
        "recommended_actions": [],
        "confidence": 0.5,
        "risk_level": "medium",
    }
    if last_ai:
        try:
            start = last_ai.find("{")
            end = last_ai.rfind("}") + 1
            if start >= 0 and end > start:
                parsed = json.loads(last_ai[start:end])
                decision.update(parsed)
        except Exception:
            pass

    return {"decision_output": decision}


# ──────────────────────────────────────────
# Routing
# ──────────────────────────────────────────

def should_call_tools(state: OptimizationState) -> str:
    messages = state["messages"]
    last_msg = messages[-1] if messages else None
    if last_msg and hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
        return "tool_execution"
    return "finalize"


# ──────────────────────────────────────────
# Graph builder
# ──────────────────────────────────────────

def build_optimization_graph(base_url: str, api_key: str | None = None, model: str = "gpt-4o-mini"):
    llm = ChatOpenAI(
        model=model,
        base_url=base_url,
        api_key=api_key or "unused",
        temperature=0.2,
    )
    llm_with_tools = llm.bind_tools(BOUND_TOOLS)

    def _llm_node(state: OptimizationState) -> dict:
        return llm_decision_node(state, llm_with_tools)

    graph = StateGraph(OptimizationState)
    graph.add_node("input_validation", input_validation_node)
    graph.add_node("llm_decision", _llm_node)
    graph.add_node("tool_execution", tool_execution_node)
    graph.add_node("finalize", final_decision_node)

    graph.set_entry_point("input_validation")
    graph.add_edge("input_validation", "llm_decision")
    graph.add_conditional_edges("llm_decision", should_call_tools, {
        "tool_execution": "tool_execution",
        "finalize": "finalize",
    })
    graph.add_edge("tool_execution", "llm_decision")
    graph.add_edge("finalize", END)

    return graph.compile()
