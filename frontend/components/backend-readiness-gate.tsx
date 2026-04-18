"use client";

import React, { useMemo } from "react";
import { AlertTriangle, CheckCircle2, LoaderCircle, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useBackendReadiness } from "@/hooks/useBackendReadiness";

function StatusLine(props: {
  label: string;
  ready: boolean;
  downloading?: boolean;
  downloadingProgress?: number;
  error?: string;
}) {
  const downloadLabel = props.downloading
    ? "Downloading"
    : undefined;

  const downloadProgress = useMemo(() => {
    if (!props.downloading) return null;
    const p = props.downloadingProgress ?? 0;
    const barWidth = Math.max(2, Math.min(100, p));
    return (
      <div className="mt-2 space-y-1">
        <div className="flex items-center justify-between text-[11px]">
          <span className="text-muted">Downloading bge-m3 model... (~1.3 GB)</span>
          <span className="text-muted">{barWidth}%</span>
        </div>
        <div className="h-1.5 w-full overflow-hidden rounded-full bg-border">
          <div
            className="h-full rounded-full bg-primary transition-all duration-500"
            style={{ width: `${barWidth}%` }}
          />
        </div>
        <p className="text-[10px] text-muted">
          First-time download may take several minutes
        </p>
      </div>
    );
  }, [props.downloading, props.downloadingProgress]);

  return (
    <div className="rounded-lg border border-border bg-surface px-3 py-2">
      <div className="flex items-center justify-between gap-4">
        <span className="text-sm font-medium text-text-main">{props.label}</span>
        {props.ready ? (
          <span className="inline-flex items-center gap-1 text-xs font-medium text-accent">
            <CheckCircle2 className="size-3.5" />
            Ready
          </span>
        ) : props.downloading ? (
          <span className="inline-flex items-center gap-1 text-xs font-medium text-primary">
            <LoaderCircle className="size-3.5 animate-spin" />
            {downloadLabel}
          </span>
        ) : (
          <span className="inline-flex items-center gap-1 text-xs font-medium text-primary">
            <LoaderCircle className="size-3.5 animate-spin" />
            Waiting
          </span>
        )}
      </div>
      {downloadProgress}
      {!!props.error && (
        <p className="mt-1 text-xs text-danger">{props.error}</p>
      )}
    </div>
  );
}

export function BackendReadinessGate({
  children,
}: {
  children: React.ReactNode;
}) {
  const readiness = useBackendReadiness();

  if (readiness.isChecking) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background p-6">
        <div className="w-full max-w-lg rounded-2xl border border-border bg-surface p-6 shadow-sm">
          <div className="mb-4 flex items-center gap-2 text-text-main">
            <LoaderCircle className="size-5 animate-spin" />
            <h1 className="text-lg font-semibold">Preparing services</h1>
          </div>
          <p className="mb-4 text-sm text-muted">
            Waiting for Agent and RAG services to become fully operational.
          </p>
          <div className="space-y-2">
            <StatusLine
              label="Agent model service"
              ready={readiness.agentReady}
              error={readiness.agentError}
            />
            <StatusLine
              label="RAG retrieval service"
              ready={readiness.ragReady}
              downloading={readiness.ragDownloading}
              downloadingProgress={readiness.downloadProgress}
              error={readiness.ragError}
            />
          </div>
        </div>
      </div>
    );
  }

  return (
    <>
      {readiness.isDegraded && (
        <div className="sticky top-0 z-50 border-b border-danger/40 bg-danger/10 px-4 py-3">
          <div className="mx-auto flex w-full max-w-6xl flex-wrap items-center justify-between gap-3">
            <div className="flex items-start gap-2 text-danger">
              <AlertTriangle className="mt-0.5 size-4" />
              <div>
                <p className="text-sm font-medium">Running in degraded mode</p>
                <p className="text-xs text-muted">
                  Some services are still unavailable. You can continue, or retry readiness checks.
                </p>
              </div>
            </div>
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={readiness.retry}
              className="border-danger/40 bg-surface text-danger hover:bg-danger/20"
            >
              <RefreshCw className="size-4" />
              Retry
            </Button>
          </div>
        </div>
      )}
      {children}
    </>
  );
}
