import json
import os
import time
import urllib.request
import urllib.error
from typing import Any, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph

# ──────────────────────────────────────────
# HTTP action dispatcher → FastAPI in Mininet VM
# ──────────────────────────────────────────

MININET_API_URL = os.getenv("MININET_API_URL", "http://192.168.249.132:8000")


def _send_action(payload: dict) -> dict:
    """POST action to the FastAPI SDN executor running inside the Mininet VM."""
    payload["timestamp"] = time.time()
    url = MININET_API_URL.rstrip("/") + "/action"
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = json.loads(resp.read().decode())
            print(f"[ACTION] {payload['action']} → Mininet API: {body.get('status')}")
            return body
    except urllib.error.URLError as e:
        print(f"[ACTION] Mininet API unreachable ({url}): {e.reason}")
        return {"status": "error", "action": payload["action"], "error": str(e.reason)}
    except Exception as e:
        print(f"[ACTION] Mininet API error: {e}")
        return {"status": "error", "action": payload["action"], "error": str(e)}


# ──────────────────────────────────────────
# Network tools — send HTTP to Mininet FastAPI
# ──────────────────────────────────────────

def reroute_traffic(device: str, path: str) -> dict:
    print(f"[TOOL] reroute_traffic device={device} path={path}")
    return _send_action({"action": "reroute_traffic", "device": device, "path": path})


def throttle_link(device: str, interface: str, rate_limit_mbps: float) -> dict:
    print(f"[TOOL] throttle_link device={device} interface={interface} rate={rate_limit_mbps}Mbps")
    return _send_action({"action": "throttle_link", "device": device, "interface": interface, "rate_limit_mbps": rate_limit_mbps})


def restart_interface(device: str, interface: str) -> dict:
    print(f"[TOOL] restart_interface device={device} interface={interface}")
    return _send_action({"action": "restart_interface", "device": device, "interface": interface})


def apply_qos_profile(device: str, profile: str) -> dict:
    print(f"[TOOL] apply_qos_profile device={device} profile={profile}")
    return _send_action({"action": "apply_qos_profile", "device": device, "profile": profile})


def monitor_only(device: str, reason: str = "") -> dict:
    print(f"[TOOL] monitor_only device={device} reason={reason}")
    return {"status": "ok", "action": "monitor_only", "device": device, "reason": reason}


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

OPTIMIZATION_SYSTEM_PROMPT = """You are an autonomous SDN optimization agent for a Mininet telecom testbed.

== NETWORK TOPOLOGY ==
Core switch : s1
  s1-eth1 -> OUTDOOR_RAN  (baseline: loss=2%,  delay=5ms,  jitter=2ms)
  s1-eth2 -> INDOOR_RAN   (baseline: loss=8%,  delay=15ms, jitter=5ms)
  s1-eth3 -> IMS_CDN      (baseline: loss=1%,  delay=2ms,  jitter=1ms)
  s1-eth4 -> INTERNET     (baseline: loss=2%,  delay=20ms, jitter=3ms)

ALWAYS use device="s1" and interfaces like "s1-eth1". NEVER use "switch-core-01" or "eth0".

== KNOWN NETWORK SCENARIOS ==
The network generates these degradation scenarios artificially via tc/netem:
- CALL_DROP          : burst loss 50-85% on s1-eth1 and s1-eth2 -> high plr, cdr_flag=1
- POOR_VOICE_QUALITY : moderate loss 12-20% + high delay on s1-eth2 -> mos_voice < 3.0
- LOW_THROUGHPUT     : loss 25-40% on s1-eth3 (IMS_CDN) -> low throughput, streaming degraded
- HIGH_LATENCY       : delay 150-400ms on s1-eth1/eth2 -> high e2e_delay_ms, dataplane_latency_ms
- CAPACITY_EXHAUSTED : loss 20-35% all interfaces + high flow_count -> all metrics degraded
- NORMAL             : baseline values, all metrics within normal range

== AVAILABLE TOOLS ==
You have full autonomy to choose any combination of tools that fits the situation:

- reroute_traffic(device, path)
    Reduces loss on a backup interface (path like "backup-path-via-eth2").
    Best for: HIGH_LATENCY where rerouting avoids the congested path.

- throttle_link(device, interface, rate_limit_mbps)
    Restores tc/netem on a specific interface back to baseline loss.
    Best for: LOW_THROUGHPUT on s1-eth3, CAPACITY_EXHAUSTED on congested interfaces.

- apply_qos_profile(device, profile)
    profile="voice-video-priority" : reduces loss to near-zero on all voice/video paths.
    profile="high-priority-qos"    : restores all interfaces to baseline loss values.
    Best for: CALL_DROP, POOR_VOICE_QUALITY, general multi-interface degradation.

- restart_interface(device, interface)
    Full netem reset on one interface back to its baseline values.
    Best for: a single interface with extreme degradation that needs a hard reset.

- monitor_only(device, reason)
    No network change. Best for: NORMAL state or when uncertainty is too high to act.

- decision_summary_tool (ALWAYS call this last)

== YOUR TASK ==
You receive telemetry every 15-30 seconds enriched with anomaly detection and SLA forecasting results.
Analyze the metrics freely, identify the most likely scenario, and choose the most appropriate action.
You are not limited to one tool — use your judgment.

PROCESS:
  Step 1 -- call your chosen action tool(s), ONE at a time
  Step 2 -- call decision_summary_tool with your reasoning

In decision_summary, always mention:
  - which scenario you identified and why (which metric was the evidence)
  - what action you took and on which interface
  - confidence: float 0.0-1.0
  - risk_level: low / medium / high / critical
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
