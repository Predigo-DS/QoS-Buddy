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


TOOL_REGISTRY = {
    "reroute_traffic": reroute_traffic,
    "throttle_link": throttle_link,
    "restart_interface": restart_interface,
    "apply_qos_profile": apply_qos_profile,
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


BOUND_TOOLS = [
    reroute_traffic_tool,
    throttle_link_tool,
    restart_interface_tool,
    apply_qos_profile_tool,
]

OPTIMIZATION_SYSTEM_PROMPT = """You are a network optimization agent.
Prioritize safety and SLA stability.
Use anomaly, SLA, and avg_30s jointly to make decisions.
Use tools for actionable remediations.
If confidence is low, return a monitor-only action.
Never fabricate tool execution results and rely only on tool outputs.

Action policy based on available features:
- High plr, e2e_delay_ms, jitter_ms, and high SLA risk → prefer reroute_traffic or apply_qos_profile.
- High dataplane_latency_ms or ctrl_plane_rtt_ms with rising rx_dropped or tx_dropped → prefer reroute_traffic or throttle_link cautiously.
- Low streaming_mos or mos_voice with high buffering_ratio, rebuffering_freq, rebuffering_count, or total_stall_seconds → prefer apply_qos_profile first, then reroute if needed.
- Low throughput_mbps with degraded effective_bitrate_mbps and high flow_count → prefer selective throttling and QoS profile changes.
- Critical uncertainty → no-op plus escalate recommendation.

After using tools, summarize your decision as a JSON object with keys:
  decision_summary, recommended_actions (list of strings), confidence (0.0-1.0), risk_level (low/medium/high/critical)
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
    anomaly = state.get("anomaly_result")
    sla = state.get("sla_result")

    summary = (
        f"Device: {device}\n"
        f"Anomaly result: {json.dumps(anomaly, default=str)}\n"
        f"SLA result: {json.dumps(sla, default=str)}\n"
        f"30-second averages: {json.dumps(avg, default=str)}\n"
        f"Context: {state.get('context', '')}"
    )
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
    messages = state["messages"]
    # Extract last AI message text as final summary
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
    # Attempt to parse structured JSON embedded in last AI message
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
