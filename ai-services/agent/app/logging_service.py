"""
logging_service.py - Agent action, telemetry, and alert logging

Persists agent decisions, tool traces, telemetry snapshots, and alerts to PostgreSQL.
Provides query functions for the RAG agent to retrieve recent context.
"""

import json
from datetime import datetime, timezone
from typing import Any, Optional

from psycopg import AsyncConnection
from psycopg.rows import dict_row

from db import CHECKPOINT_DB_URI, get_db_connection


async def _ensure_logs_table():
    """Create the agent_logs table if it doesn't exist."""
    if not CHECKPOINT_DB_URI:
        return

    conn = await get_db_connection()
    async with conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_logs (
                id SERIAL PRIMARY KEY,
                timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                log_type TEXT NOT NULL,
                data JSONB NOT NULL DEFAULT '{}',
                summary TEXT
            )
            """
        )
        # Index for fast queries by type and recency
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_agent_logs_type_ts 
            ON agent_logs (log_type, timestamp DESC)
            """
        )


async def log_agent_action(
    avg_metrics: dict[str, float],
    tool_trace: list[dict],
    decision: dict[str, Any],
    anomaly_result: Optional[dict] = None,
    sla_result: Optional[dict] = None,
):
    """Log an optimization response: metrics, actions taken, and decision."""
    if not CHECKPOINT_DB_URI:
        return

    actions_taken = []
    for entry in tool_trace:
        tool_name = entry.get("tool", "unknown")
        tool_args = entry.get("args", {})
        result = entry.get("result", {})
        if tool_name != "decision_summary_tool":
            actions_taken.append({
                "tool": tool_name,
                "args": tool_args,
                "status": result.get("status", "unknown"),
            })

    summary_parts = []
    risk = decision.get("risk_level", "unknown")
    confidence = decision.get("confidence", 0)
    summary_parts.append(f"Risk: {risk}, Confidence: {confidence:.2f}")

    if actions_taken:
        tool_names = [a["tool"] for a in actions_taken]
        summary_parts.append(f"Actions: {', '.join(tool_names)}")

    plr = avg_metrics.get("plr", 0)
    delay = avg_metrics.get("e2e_delay_ms", 0)
    mos = avg_metrics.get("mos_voice", 0)
    summary_parts.append(f"PLR: {plr*100:.1f}%, Latency: {delay:.0f}ms, MOS: {mos:.2f}")

    data = {
        "avg_metrics": avg_metrics,
        "tool_trace": tool_trace,
        "decision": decision,
        "anomaly_result": anomaly_result or {},
        "sla_result": sla_result or {},
    }

    conn = await get_db_connection()
    async with conn:
        await conn.execute(
            """
            INSERT INTO agent_logs (log_type, data, summary, timestamp)
            VALUES (%s, %s::jsonb, %s, NOW())
            """,
            ("agent_action", json.dumps(data), " | ".join(summary_parts)),
        )


async def log_alert(
    alert_type: str,
    segment: str,
    severity: str,
    details: dict[str, Any],
):
    """Log an alert (anomaly detected, SLA violation forecast, etc.)."""
    if not CHECKPOINT_DB_URI:
        return

    summary = f"[{severity.upper()}] {alert_type} on {segment}"
    if details:
        key_metric = next(iter(details.items()), None)
        if key_metric:
            k, v = key_metric
            summary += f" ({k}={v})"

    data = {
        "alert_type": alert_type,
        "segment": segment,
        "severity": severity,
        "details": details,
    }

    conn = await get_db_connection()
    async with conn:
        await conn.execute(
            """
            INSERT INTO agent_logs (log_type, data, summary, timestamp)
            VALUES (%s, %s::jsonb, %s, NOW())
            """,
            ("alert", json.dumps(data), summary),
        )


async def log_telemetry_snapshot(metrics: dict[str, float], label: str = ""):
    """Log a periodic telemetry snapshot."""
    if not CHECKPOINT_DB_URI:
        return

    plr = metrics.get("plr", 0)
    delay = metrics.get("e2e_delay_ms", 0)
    mos = metrics.get("mos_voice", 0)
    summary = f"Snapshot: label={label or 'unknown'}, PLR={plr*100:.1f}%, delay={delay:.0f}ms, MOS={mos:.2f}"

    data = {
        "metrics": metrics,
        "label": label,
    }

    conn = await get_db_connection()
    async with conn:
        await conn.execute(
            """
            INSERT INTO agent_logs (log_type, data, summary, timestamp)
            VALUES (%s, %s::jsonb, %s, NOW())
            """,
            ("telemetry_snapshot", json.dumps(data), summary),
        )


async def get_recent_logs_for_rag(
    max_telemetry_rows: int = 1000,
    max_actions: int = 10,
    max_alerts: int = 5,
) -> dict[str, Any]:
    """
    Query recent logs and return a structured summary for RAG context injection.
    
    Returns:
        {
            "telemetry_summary": { avg/min/max of key metrics from recent snapshots },
            "recent_actions": [last N agent actions as formatted strings],
            "recent_alerts": [last N alerts as formatted strings],
        }
    """
    if not CHECKPOINT_DB_URI:
        return {
            "telemetry_summary": {},
            "recent_actions": [],
            "recent_alerts": [],
        }

    result = {
        "telemetry_summary": {},
        "recent_actions": [],
        "recent_alerts": [],
    }

    conn = await get_db_connection()
    async with conn:
        # Telemetry summary: aggregate stats from recent snapshots
        rows = await conn.fetch(
            """
            SELECT data FROM agent_logs 
            WHERE log_type = 'telemetry_snapshot'
            ORDER BY timestamp DESC 
            LIMIT %s
            """,
            (max_telemetry_rows,),
        )
        if rows:
            plrs, delays, moss, jitters, throughputs = [], [], [], [], []
            for row in rows:
                try:
                    m = json.loads(row["data"]).get("metrics", {})
                    if "plr" in m:
                        plrs.append(float(m["plr"]))
                    if "e2e_delay_ms" in m:
                        delays.append(float(m["e2e_delay_ms"]))
                    if "mos_voice" in m:
                        moss.append(float(m["mos_voice"]))
                    if "jitter_ms" in m:
                        jitters.append(float(m["jitter_ms"]))
                    if "throughput_mbps" in m:
                        throughputs.append(float(m["throughput_mbps"]))
                except (json.JSONDecodeError, TypeError, ValueError):
                    continue

            def _stats(values: list[float]) -> dict:
                if not values:
                    return {}
                return {
                    "avg": round(sum(values) / len(values), 3),
                    "min": round(min(values), 3),
                    "max": round(max(values), 3),
                }

            result["telemetry_summary"] = {
                "rows_analyzed": len(rows),
                "plr": _stats(plrs),
                "e2e_delay_ms": _stats(delays),
                "mos_voice": _stats(moss),
                "jitter_ms": _stats(jitters),
                "throughput_mbps": _stats(throughputs),
            }

        # Recent agent actions
        rows = await conn.fetch(
            """
            SELECT summary, timestamp FROM agent_logs 
            WHERE log_type = 'agent_action'
            ORDER BY timestamp DESC 
            LIMIT %s
            """,
            (max_actions,),
        )
        for row in rows:
            ts = row["timestamp"].strftime("%H:%M:%S") if row["timestamp"] else "?"
            result["recent_actions"].append(f"[{ts}] {row['summary']}")

        # Recent alerts
        rows = await conn.fetch(
            """
            SELECT summary, timestamp FROM agent_logs 
            WHERE log_type = 'alert'
            ORDER BY timestamp DESC 
            LIMIT %s
            """,
            (max_alerts,),
        )
        for row in rows:
            ts = row["timestamp"].strftime("%H:%M:%S") if row["timestamp"] else "?"
            result["recent_alerts"].append(f"[{ts}] {row['summary']}")

    return result


def format_rag_log_context(logs: dict[str, Any]) -> str:
    """Format the log query result as a context block for the RAG system prompt."""
    lines = []

    telemetry = logs.get("telemetry_summary", {})
    if telemetry:
        lines.append("=== RECENT NETWORK STATE ===")
        rows = telemetry.get("rows_analyzed", 0)
        lines.append(f"Analyzed last {rows} telemetry snapshots:")

        plr = telemetry.get("plr", {})
        if plr:
            lines.append(
                f"  Packet Loss:  avg={plr.get('avg', 0)*100:.1f}% "
                f"(min={plr.get('min', 0)*100:.1f}%, max={plr.get('max', 0)*100:.1f}%)"
            )

        delay = telemetry.get("e2e_delay_ms", {})
        if delay:
            lines.append(
                f"  Latency:      avg={delay.get('avg', 0):.0f}ms "
                f"(min={delay.get('min', 0):.0f}ms, max={delay.get('max', 0):.0f}ms)"
            )

        mos = telemetry.get("mos_voice", {})
        if mos:
            lines.append(
                f"  Voice MOS:    avg={mos.get('avg', 0):.2f} "
                f"(min={mos.get('min', 0):.2f}, max={mos.get('max', 0):.2f})"
            )

        jit = telemetry.get("jitter_ms", {})
        if jit:
            lines.append(
                f"  Jitter:       avg={jit.get('avg', 0):.1f}ms "
                f"(min={jit.get('min', 0):.1f}ms, max={jit.get('max', 0):.1f}ms)"
            )

        tp = telemetry.get("throughput_mbps", {})
        if tp:
            lines.append(
                f"  Throughput:   avg={tp.get('avg', 0):.2f}Mbps "
                f"(min={tp.get('min', 0):.2f}, max={tp.get('max', 0):.2f})"
            )

    actions = logs.get("recent_actions", [])
    if actions:
        lines.append("\n=== RECENT AGENT ACTIONS ===")
        for action in actions:
            lines.append(f"  • {action}")

    alerts = logs.get("recent_alerts", [])
    if alerts:
        lines.append("\n=== RECENT ALERTS ===")
        for alert in alerts:
            lines.append(f"  ⚠ {alert}")

    return "\n".join(lines) if lines else ""
