---
name: optimization_agent_pipeline
description: Optimization agent pipeline added to QoSentry - backend orchestration + LangGraph agent
type: project
---

New end-to-end optimization flow added (April 2026).

**Why:** Enable automated network remediation decisions using anomaly + SLA + telemetry data before real telemetry integration.

**How to apply:** Keep mock mode intact; do not break existing incident flow when modifying agent service.

Key files changed:
- Backend: `OptimizationRequestDto`, `OptimizationResponseDto`, `AiInferenceService#runMockOptimization`, `AiInferenceController POST /api/ai/optimize/mock`
- Agent: `optimization_graph.py` (LangGraph), `main.py POST /optimization/respond`
- Config: `application.yml` `app.ai.agent-base-url`, `docker-compose.yml AI_AGENT_BASE_URL`
