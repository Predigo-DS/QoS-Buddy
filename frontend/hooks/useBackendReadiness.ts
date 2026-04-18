import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { refreshModels } from "@/lib/model-cache";
import { resolveAgentApiUrl, resolveRagApiUrl } from "@/lib/service-urls";

type ReadinessPhase = "checking" | "ready" | "degraded";

type ReadinessSnapshot = {
  phase: ReadinessPhase;
  agentReady: boolean;
  ragReady: boolean;
  ragWarmup: "idle" | "warming" | "ready" | "error";
  agentError?: string;
  ragError?: string;
  lastCheckedAt?: number;
};

const DEFAULT_TIMEOUT_MS = 30_000;
const DEFAULT_POLL_INTERVAL_MS = 2_000;

function resolveMs(raw: string | undefined, fallback: number): number {
  const parsed = Number(raw);
  if (!Number.isFinite(parsed) || parsed <= 0) return fallback;
  return Math.floor(parsed);
}

export function useBackendReadiness() {
  const agentApiUrl = useMemo(
    () => resolveAgentApiUrl(process.env.NEXT_PUBLIC_AGENT_API_URL),
    [],
  );
  const ragApiUrl = useMemo(
    () => resolveRagApiUrl(process.env.NEXT_PUBLIC_RAG_API_URL),
    [],
  );

  const timeoutMs = resolveMs(
    process.env.NEXT_PUBLIC_READINESS_TIMEOUT_MS,
    DEFAULT_TIMEOUT_MS,
  );
  const pollIntervalMs = resolveMs(
    process.env.NEXT_PUBLIC_READINESS_POLL_INTERVAL_MS,
    DEFAULT_POLL_INTERVAL_MS,
  );

  const [snapshot, setSnapshot] = useState<ReadinessSnapshot>({
    phase: "checking",
    agentReady: false,
    ragReady: false,
    ragWarmup: "idle",
  });

  const timerRef = useRef<number | null>(null);
  const runIdRef = useRef(0);
  const ragWarmupRef = useRef<"idle" | "warming" | "ready" | "error">("idle");

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const triggerRagWarmup = useCallback(async () => {
    if (ragWarmupRef.current === "warming" || ragWarmupRef.current === "ready") {
      return;
    }

    ragWarmupRef.current = "warming";
    try {
      const response = await fetch(`${ragApiUrl}/warmup`, { method: "POST" });
      if (!response.ok) {
        throw new Error(`RAG warmup failed (${response.status}).`);
      }

      const payload = (await response.json()) as { status?: string };
      ragWarmupRef.current = payload?.status === "ready" ? "ready" : "warming";
    } catch {
      ragWarmupRef.current = "error";
    }
  }, [ragApiUrl]);

  const checkOnce = useCallback(async () => {
    const [agentResult, ragResult] = await Promise.all([
      (async () => {
        try {
          const models = await refreshModels(agentApiUrl);
          const ready = Array.isArray(models) && models.length > 0;
          return {
            ready,
            error: ready ? undefined : "Agent returned no models.",
          };
        } catch (error) {
          return {
            ready: false,
            error: error instanceof Error ? error.message : "Agent check failed.",
          };
        }
      })(),
      (async () => {
        try {
          const response = await fetch(`${ragApiUrl}/health`);
          if (!response.ok) {
            throw new Error(`RAG health check failed (${response.status}).`);
          }

          const payload = (await response.json()) as { status?: string };
          const ready = payload?.status === "ok";
          return {
            ready,
            error: ready ? undefined : `RAG reported status: ${payload?.status ?? "unknown"}.`,
          };
        } catch (error) {
          return {
            ready: false,
            error: error instanceof Error ? error.message : "RAG check failed.",
          };
        }
      })(),
    ]);

    if (ragResult.ready && ragWarmupRef.current !== "warming" && ragWarmupRef.current !== "ready") {
      void triggerRagWarmup();
    }

    return {
      agentReady: agentResult.ready,
      ragReady: ragResult.ready,
      agentError: agentResult.error,
      ragError: ragResult.error,
      ragWarmup: ragWarmupRef.current,
    };
  }, [agentApiUrl, ragApiUrl, triggerRagWarmup]);

  const startChecks = useCallback(() => {
    clearTimer();

    const runId = runIdRef.current + 1;
    runIdRef.current = runId;
    const startedAt = Date.now();

    setSnapshot({
      phase: "checking",
      agentReady: false,
      ragReady: false,
      ragWarmup: "idle",
    });
    ragWarmupRef.current = "idle";

    const loop = async () => {
      const result = await checkOnce();
      if (runId !== runIdRef.current) return;

      const now = Date.now();
      const healthy = result.agentReady && result.ragReady;

      if (healthy) {
        setSnapshot({
          phase: "ready",
          ...result,
          lastCheckedAt: now,
        });
        return;
      }

      const timedOut = now - startedAt >= timeoutMs;
      if (timedOut) {
        setSnapshot({
          phase: "degraded",
          ...result,
          lastCheckedAt: now,
        });
        return;
      }

      setSnapshot((prev) => ({
        phase: prev.phase === "degraded" ? "degraded" : "checking",
        ...result,
        lastCheckedAt: now,
      }));

      timerRef.current = window.setTimeout(loop, pollIntervalMs);
    };

    void loop();
  }, [checkOnce, clearTimer, pollIntervalMs, timeoutMs]);

  useEffect(() => {
    startChecks();
    return () => {
      runIdRef.current += 1;
      clearTimer();
    };
  }, [clearTimer, startChecks]);

  return {
    ...snapshot,
    isChecking: snapshot.phase === "checking",
    isReady: snapshot.phase === "ready",
    isDegraded: snapshot.phase === "degraded",
    retry: startChecks,
  };
}