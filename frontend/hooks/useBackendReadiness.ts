import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { refreshModels } from "@/lib/model-cache";
import { resolveAgentApiUrl, resolveRagApiUrl } from "@/lib/service-urls";

type ReadinessPhase = "checking" | "ready" | "degraded";

type ReadinessSnapshot = {
  phase: ReadinessPhase;
  agentReady: boolean;
  ragReady: boolean;
  ragWarmup: "idle" | "warming" | "ready" | "error";
  ragDownloading: boolean;
  agentError?: string;
  ragError?: string;
  lastCheckedAt?: number;
};

const DEFAULT_TIMEOUT_MS = 30_000;
const DEFAULT_POLL_INTERVAL_MS = 2_000;

const MODEL_DOWNLOAD_TIMEOUT_MS = 5 * 60 * 1000; // 5 minutes for model download

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
    ragDownloading: false,
  });

  const timerRef = useRef<number | null>(null);
  const runIdRef = useRef(0);
  const ragWarmupRef = useRef<"idle" | "warming" | "ready" | "error">("idle");
  const ragDownloadingRef = useRef(false);
  const downloadStartedAtRef = useRef<number | null>(null);

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

          const payload = (await response.json()) as { status?: string; warmup?: { status?: string } };
          const ready = payload?.status === "ok";
          const warmupStatus = payload?.warmup?.status;

          // Detect model download phase: container is up but model still loading
          const isDownloading = !ready && (warmupStatus === "warming" || payload?.status === "starting");

          return {
            ready,
            error: ready ? undefined : `RAG reported status: ${payload?.status ?? "unknown"}.`,
            isDownloading,
          };
        } catch (error) {
          return {
            ready: false,
            error: error instanceof Error ? error.message : "RAG check failed.",
            isDownloading: false,
          };
        }
      })(),
    ]);

    if (ragResult.ready && ragWarmupRef.current !== "warming" && ragWarmupRef.current !== "ready") {
      void triggerRagWarmup();
    }

    // Track download state
    if (ragResult.isDownloading && !ragDownloadingRef.current) {
      ragDownloadingRef.current = true;
      downloadStartedAtRef.current = Date.now();
    } else if (!ragResult.isDownloading) {
      ragDownloadingRef.current = false;
      downloadStartedAtRef.current = null;
    }

    return {
      agentReady: agentResult.ready,
      ragReady: ragResult.ready,
      ragDownloading: ragDownloadingRef.current,
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
      ragDownloading: false,
    });
    ragWarmupRef.current = "idle";
    ragDownloadingRef.current = false;
    downloadStartedAtRef.current = null;

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

      // Check model download timeout
      const downloadTimedOut =
        result.ragDownloading &&
        downloadStartedAtRef.current !== null &&
        now - downloadStartedAtRef.current >= MODEL_DOWNLOAD_TIMEOUT_MS;

      const timedOut = !result.ragDownloading && now - startedAt >= timeoutMs;

      if (downloadTimedOut || timedOut) {
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

  // Calculate download progress percentage (0-100) based on elapsed time
  const downloadProgress = useMemo(() => {
    if (!snapshot.ragDownloading || !downloadStartedAtRef.current) return 0;
    const elapsed = Date.now() - downloadStartedAtRef.current;
    const progress = Math.min(95, (elapsed / MODEL_DOWNLOAD_TIMEOUT_MS) * 100);
    return Math.round(progress);
  }, [snapshot.ragDownloading]);

  return {
    ...snapshot,
    isChecking: snapshot.phase === "checking",
    isReady: snapshot.phase === "ready",
    isDegraded: snapshot.phase === "degraded",
    retry: startChecks,
    downloadProgress,
  };
}
