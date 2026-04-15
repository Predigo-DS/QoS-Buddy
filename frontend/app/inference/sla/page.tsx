'use client'

import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { motion } from 'framer-motion'
import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  Loader2,
  Play,
  Sparkles,
} from 'lucide-react'
import { getRole, getUsername, isAuthenticated } from '@/lib/auth'
import {
  SlaMetadataResponse,
  SlaPredictResponse,
  getSlaMetadata,
  predictSla,
} from '@/lib/api'

type InputRow = Record<string, number | string | boolean>

function buildSlaSampleRows(windowSize: number): InputRow[] {
  const rowCount = Math.max(windowSize + 6, windowSize * 2)
  const start = new Date('2026-01-01T00:00:00Z').getTime()

  return Array.from({ length: rowCount }, (_, i) => ({
    timestamp: new Date(start + i * 2000).toISOString(),
    throughput_mbps: 78 - (i % 12) * 3,
    e2e_delay_ms: 12 + (i % 9) * 5,
    mos_voice: Math.max(1.0, 4.2 - (i % 10) * 0.2),
    plr: Number(((i % 8) * 0.012).toFixed(4)),
    jitter_ms: Number((2 + (i % 7) * 1.1).toFixed(3)),
  }))
}

export default function SlaInferencePage() {
  const router = useRouter()

  const [mounted, setMounted] = useState(false)
  const [username, setUsername] = useState<string | null>(null)
  const [role, setRole] = useState<string | null>(null)

  const [metadata, setMetadata] = useState<SlaMetadataResponse | null>(null)
  const [metadataLoading, setMetadataLoading] = useState(true)
  const [metadataError, setMetadataError] = useState<string | null>(null)

  const [selectedRunSegment, setSelectedRunSegment] = useState('')
  const [rowsJson, setRowsJson] = useState('[]')
  const [useAllWindows, setUseAllWindows] = useState(true)
  const [stride, setStride] = useState('1')
  const [slaAlertThreshold, setSlaAlertThreshold] = useState('0.30')

  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [result, setResult] = useState<SlaPredictResponse | null>(null)

  const selectedKey = useMemo(() => {
    if (!metadata || !selectedRunSegment) return null
    const [runId, segment] = selectedRunSegment.split('::')
    return runId && segment ? { runId, segment } : null
  }, [metadata, selectedRunSegment])

  const regenerateRows = (meta: SlaMetadataResponse) => {
    const generated = buildSlaSampleRows(meta.window_size)
    setRowsJson(JSON.stringify(generated, null, 2))
    setStride('1')
    setSlaAlertThreshold('0.30')
  }

  useEffect(() => {
    if (!isAuthenticated()) {
      router.replace('/login')
      return
    }

    setMounted(true)
    setUsername(getUsername())
    setRole(getRole())

    const load = async () => {
      setMetadataLoading(true)
      setMetadataError(null)
      try {
        const meta = await getSlaMetadata()
        setMetadata(meta)
        const first = meta.run_segment_keys[0]
        if (first) {
          setSelectedRunSegment(`${first.run_id}::${first.segment}`)
        }
        regenerateRows(meta)
      } catch (error) {
        setMetadataError(error instanceof Error ? error.message : 'Failed to load SLA metadata.')
      } finally {
        setMetadataLoading(false)
      }
    }

    void load()
  }, [router])

  const handlePredict = async () => {
    setSubmitError(null)
    setResult(null)

    if (!selectedKey) {
      setSubmitError('Please select a run/segment pair.')
      return
    }

    let parsedRows: InputRow[]
    try {
      const parsed = JSON.parse(rowsJson) as unknown
      if (!Array.isArray(parsed) || parsed.length === 0) {
        throw new Error('Rows JSON must be a non-empty array.')
      }
      parsedRows = parsed as InputRow[]
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : 'Rows JSON is invalid.')
      return
    }

    const strideNum = Number(stride)
    const thresholdNum = Number(slaAlertThreshold)

    if (!Number.isFinite(strideNum) || strideNum < 1) {
      setSubmitError('Stride must be a number greater than or equal to 1.')
      return
    }

    if (!Number.isFinite(thresholdNum) || thresholdNum < 0 || thresholdNum > 1) {
      setSubmitError('SLA alert threshold must be between 0 and 1.')
      return
    }

    setSubmitting(true)
    try {
      const response = await predictSla({
        run_id: selectedKey.runId,
        segment: selectedKey.segment,
        rows: parsedRows,
        use_all_windows: useAllWindows,
        stride: strideNum,
        sla_alert_threshold: thresholdNum,
      })
      setResult(response)
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : 'Inference failed.')
    } finally {
      setSubmitting(false)
    }
  }

  const predictionCount = result?.predictions.length ?? 0
  const alertCount =
    result?.alert_count ?? result?.predictions.filter((prediction) => prediction.sla_alert).length ?? 0

  if (!mounted) {
    return (
      <div className="min-h-screen bg-background flex items-center justify-center">
        <div className="w-8 h-8 rounded-full border-2 border-primary border-t-transparent animate-spin" />
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-background">
      <header className="border-b border-border bg-surface/40 backdrop-blur-md sticky top-0 z-10">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-primary to-secondary flex items-center justify-center">
              <Activity className="w-4 h-4 text-white" />
            </div>
            <span className="font-bold text-gradient">QoSentry</span>
            <span className="text-border mx-2">|</span>
            <span className="text-sm text-muted font-medium">SLA Inference</span>
          </div>

          <div className="flex items-center gap-2">
            <Link
              href="/inference/anomaly"
              className="text-xs sm:text-sm px-3 py-2 rounded-lg border border-primary/40 text-primary hover:bg-primary/10 transition-colors"
            >
              Anomaly Page
            </Link>
            <button
              onClick={() => router.push('/dashboard')}
              className="text-xs sm:text-sm px-3 py-2 rounded-lg border border-border text-muted hover:text-text-main hover:bg-surface transition-colors inline-flex items-center gap-1.5"
            >
              <ArrowLeft className="w-3.5 h-3.5" />
              Dashboard
            </button>
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-10 space-y-6">
        <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }}>
          <h1 className="text-3xl font-bold text-text-main">SLA Forecasting Inference</h1>
          <p className="text-muted mt-2">
            Logged in as <span className="text-secondary font-semibold">{username ?? 'User'}</span>
            {role ? <span className="ml-2 text-xs uppercase tracking-wide text-primary">{role}</span> : null}
          </p>
        </motion.div>

        {metadataLoading ? (
          <div className="glass rounded-2xl p-8 border border-border flex items-center gap-3 text-muted">
            <Loader2 className="w-5 h-5 animate-spin" />
            Loading metadata...
          </div>
        ) : metadataError ? (
          <div className="glass rounded-2xl p-6 border border-danger/40 bg-danger/10 text-danger flex items-start gap-3">
            <AlertTriangle className="w-5 h-5 mt-0.5" />
            <p className="text-sm">{metadataError}</p>
          </div>
        ) : metadata ? (
          <>
            <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
              <div className="glass rounded-xl p-4 border border-border">
                <p className="text-xs text-muted uppercase tracking-wide">Window Size</p>
                <p className="text-sm font-semibold text-text-main mt-1">{metadata.window_size}</p>
              </div>
              <div className="glass rounded-xl p-4 border border-border">
                <p className="text-xs text-muted uppercase tracking-wide">Horizon</p>
                <p className="text-sm font-semibold text-secondary mt-1">{metadata.horizon ?? 'n/a'}</p>
              </div>
              <div className="glass rounded-xl p-4 border border-border">
                <p className="text-xs text-muted uppercase tracking-wide">Run/Segment Pairs</p>
                <p className="text-sm font-semibold text-text-main mt-1">{metadata.run_segment_keys.length}</p>
              </div>
              <div className="glass rounded-xl p-4 border border-border">
                <p className="text-xs text-muted uppercase tracking-wide">Classes</p>
                <p className="text-sm font-semibold text-primary mt-1">{metadata.class_names?.length ?? 'n/a'}</p>
              </div>
            </div>

            <div className="glass rounded-2xl p-6 border border-border space-y-5">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <h2 className="text-xl font-bold text-text-main">Input Payload</h2>
                <button
                  onClick={() => regenerateRows(metadata)}
                  className="text-xs sm:text-sm px-3 py-2 rounded-lg border border-secondary/40 text-secondary hover:bg-secondary/10 transition-colors inline-flex items-center gap-1.5"
                >
                  <Sparkles className="w-3.5 h-3.5" />
                  Regenerate Sample Rows
                </button>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <label className="text-sm text-muted">
                  <span className="mb-1 block">Run/Segment</span>
                  <select
                    value={selectedRunSegment}
                    onChange={(e) => setSelectedRunSegment(e.target.value)}
                    className="w-full bg-background border border-border rounded-xl px-4 py-2 text-text-main outline-none focus:ring-2 focus:ring-secondary/50"
                  >
                    {metadata.run_segment_keys.map((key) => {
                      const value = `${key.run_id}::${key.segment}`
                      return (
                        <option key={value} value={value}>
                          {key.run_id} / {key.segment}
                        </option>
                      )
                    })}
                  </select>
                </label>

                <label className="text-sm text-muted">
                  <span className="mb-1 block">Stride</span>
                  <input
                    value={stride}
                    onChange={(e) => setStride(e.target.value)}
                    className="w-full bg-background border border-border rounded-xl px-4 py-2 text-text-main outline-none focus:ring-2 focus:ring-secondary/50"
                  />
                </label>

                <label className="text-sm text-muted">
                  <span className="mb-1 block">SLA Alert Threshold (0..1)</span>
                  <input
                    value={slaAlertThreshold}
                    onChange={(e) => setSlaAlertThreshold(e.target.value)}
                    className="w-full bg-background border border-border rounded-xl px-4 py-2 text-text-main outline-none focus:ring-2 focus:ring-secondary/50"
                  />
                </label>

                <label className="text-sm text-muted flex items-center gap-2 pt-6">
                  <input
                    type="checkbox"
                    checked={useAllWindows}
                    onChange={(e) => setUseAllWindows(e.target.checked)}
                    className="rounded border-border bg-background"
                  />
                  Use all windows
                </label>
              </div>

              <label className="text-sm text-muted block">
                <span className="mb-1 block">Rows (JSON array)</span>
                <textarea
                  value={rowsJson}
                  onChange={(e) => setRowsJson(e.target.value)}
                  rows={16}
                  className="w-full bg-background border border-border rounded-xl px-4 py-3 text-text-main font-mono text-xs outline-none focus:ring-2 focus:ring-secondary/50"
                />
              </label>

              {submitError ? (
                <div className="rounded-xl border border-danger/40 bg-danger/10 px-4 py-3 text-sm text-danger">
                  {submitError}
                </div>
              ) : null}

              <button
                onClick={handlePredict}
                disabled={submitting}
                className="inline-flex items-center gap-2 rounded-xl px-4 py-2 bg-gradient-to-r from-secondary to-primary text-white font-semibold hover:opacity-90 transition-opacity disabled:opacity-60"
              >
                {submitting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
                Run Inference
              </button>
            </div>

            {result ? (
              <div className="glass rounded-2xl p-6 border border-border space-y-5">
                <div className="flex flex-wrap items-center gap-3">
                  <h2 className="text-xl font-bold text-text-main">Prediction Output</h2>
                  <span className="inline-flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full border border-accent/40 bg-accent/10 text-accent">
                    <CheckCircle2 className="w-3.5 h-3.5" />
                    Success
                  </span>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                  <div className="rounded-xl border border-border bg-surface/30 px-4 py-3">
                    <p className="text-xs text-muted uppercase tracking-wide">Predictions</p>
                    <p className="text-lg font-bold text-text-main mt-1">{predictionCount}</p>
                  </div>
                  <div className="rounded-xl border border-border bg-surface/30 px-4 py-3">
                    <p className="text-xs text-muted uppercase tracking-wide">Alerts</p>
                    <p className="text-lg font-bold text-danger mt-1">{alertCount}</p>
                  </div>
                  <div className="rounded-xl border border-border bg-surface/30 px-4 py-3">
                    <p className="text-xs text-muted uppercase tracking-wide">Alert Rate</p>
                    <p className="text-lg font-bold text-secondary mt-1">
                      {predictionCount > 0 ? `${((alertCount / predictionCount) * 100).toFixed(1)}%` : '0.0%'}
                    </p>
                  </div>
                </div>

                <div className="overflow-x-auto rounded-xl border border-border">
                  <table className="w-full text-sm">
                    <thead className="bg-surface/40">
                      <tr className="text-left">
                        <th className="px-4 py-3 text-xs text-muted uppercase tracking-wide">Window</th>
                        <th className="px-4 py-3 text-xs text-muted uppercase tracking-wide">Rows</th>
                        <th className="px-4 py-3 text-xs text-muted uppercase tracking-wide">Class</th>
                        <th className="px-4 py-3 text-xs text-muted uppercase tracking-wide">Risk Score</th>
                        <th className="px-4 py-3 text-xs text-muted uppercase tracking-wide">Alert</th>
                      </tr>
                    </thead>
                    <tbody>
                      {result.predictions.map((item) => (
                        <tr key={item.window_index} className="border-t border-border/70">
                          <td className="px-4 py-3 text-text-main font-mono">#{item.window_index}</td>
                          <td className="px-4 py-3 text-muted">
                            {item.start_row} - {item.end_row}
                          </td>
                          <td className="px-4 py-3 text-text-main">{item.predicted_class}</td>
                          <td className="px-4 py-3 text-text-main font-mono">{item.sla_risk_score.toFixed(4)}</td>
                          <td className="px-4 py-3">
                            <span
                              className={`inline-flex items-center rounded-full px-2.5 py-1 text-xs font-semibold border ${
                                item.sla_alert
                                  ? 'text-danger bg-danger/10 border-danger/40'
                                  : 'text-accent bg-accent/10 border-accent/40'
                              }`}
                            >
                              {item.sla_alert ? 'ALERT' : 'OK'}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            ) : null}
          </>
        ) : null}
      </main>
    </div>
  )
}
