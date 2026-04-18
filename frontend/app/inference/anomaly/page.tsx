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
  AnomalyMetadataResponse,
  AnomalyPredictResponse,
  getAnomalyMetadata,
  predictAnomaly,
} from '@/lib/api'

type InputRow = Record<string, number | string | boolean>

function sampleValue(feature: string, index: number): number {
  const f = feature.toLowerCase()
  if (f.includes('delay') || f.includes('latency') || f.includes('jitter')) return 14 + (index % 8) * 2
  if (f.includes('loss') || f.includes('drop') || f.includes('plr')) return (index % 6) * 0.02
  if (f.includes('throughput') || f.includes('bitrate')) return 90 - (index % 10) * 4
  if (f.includes('mos') || f.includes('qoe')) return 4 - (index % 7) * 0.18
  if (f.includes('count')) return 4 + (index % 5)
  if (f.includes('bytes') || f.includes('packets')) return 1000 + index * 25
  return (index % 9) + 1
}

export default function AnomalyInferencePage() {
  const router = useRouter()

  const [mounted, setMounted] = useState(false)
  const [username, setUsername] = useState<string | null>(null)
  const [role, setRole] = useState<string | null>(null)

  const [metadata, setMetadata] = useState<AnomalyMetadataResponse | null>(null)
  const [metadataLoading, setMetadataLoading] = useState(true)
  const [metadataError, setMetadataError] = useState<string | null>(null)

  const [rowsJson, setRowsJson] = useState('[]')
  const [stride, setStride] = useState('1')
  const [thresholdName, setThresholdName] = useState('best')

  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [result, setResult] = useState<AnomalyPredictResponse | null>(null)

  const thresholdOptions = useMemo(() => Object.keys(metadata?.thresholds ?? {}), [metadata])

  const regenerateRows = (meta: AnomalyMetadataResponse) => {
    const rowCount = Math.max(meta.window_size * 2, meta.window_size + 1)
    const generated: InputRow[] = Array.from({ length: rowCount }, (_, i) => {
      const row: InputRow = {}
      meta.features.forEach((feature) => {
        row[feature] = sampleValue(feature, i)
      })
      return row
    })
    setRowsJson(JSON.stringify(generated, null, 2))
    setStride(String(Math.max(1, Math.floor(meta.window_size / 2))))
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
        const meta = await getAnomalyMetadata()
        setMetadata(meta)
        if (Object.keys(meta.thresholds ?? {}).length > 0) {
          setThresholdName(Object.keys(meta.thresholds ?? {})[0])
        }
        regenerateRows(meta)
      } catch (error) {
        setMetadataError(error instanceof Error ? error.message : 'Failed to load anomaly metadata.')
      } finally {
        setMetadataLoading(false)
      }
    }

    void load()
  }, [router])

  const handlePredict = async () => {
    setSubmitError(null)
    setResult(null)

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
    if (!Number.isFinite(strideNum) || strideNum < 1) {
      setSubmitError('Stride must be a number greater than or equal to 1.')
      return
    }

    setSubmitting(true)
    try {
      const response = await predictAnomaly({
        rows: parsedRows,
        stride: strideNum,
        threshold_name: thresholdName || undefined,
      })
      setResult(response)
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : 'Inference failed.')
    } finally {
      setSubmitting(false)
    }
  }

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
            <span className="text-sm text-muted font-medium">Anomaly Inference</span>
          </div>

          <div className="flex items-center gap-2">
            <Link
              href="/inference/sla"
              className="text-xs sm:text-sm px-3 py-2 rounded-lg border border-secondary/40 text-secondary hover:bg-secondary/10 transition-colors"
            >
              SLA Page
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
          <h1 className="text-3xl font-bold text-text-main">Anomaly Detection Inference</h1>
          <p className="text-muted mt-2">
            Logged in as <span className="text-primary font-semibold">{username ?? 'User'}</span>
            {role ? <span className="ml-2 text-xs uppercase tracking-wide text-secondary">{role}</span> : null}
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
                <p className="text-xs text-muted uppercase tracking-wide">Model</p>
                <p className="text-sm font-semibold text-primary mt-1">{metadata.model_type}</p>
              </div>
              <div className="glass rounded-xl p-4 border border-border">
                <p className="text-xs text-muted uppercase tracking-wide">Window Size</p>
                <p className="text-sm font-semibold text-text-main mt-1">{metadata.window_size}</p>
              </div>
              <div className="glass rounded-xl p-4 border border-border">
                <p className="text-xs text-muted uppercase tracking-wide">Features</p>
                <p className="text-sm font-semibold text-text-main mt-1">{metadata.features.length}</p>
              </div>
              <div className="glass rounded-xl p-4 border border-border">
                <p className="text-xs text-muted uppercase tracking-wide">Thresholds</p>
                <p className="text-sm font-semibold text-secondary mt-1">{thresholdOptions.length}</p>
              </div>
            </div>

            <div className="glass rounded-2xl p-6 border border-border space-y-5">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <h2 className="text-xl font-bold text-text-main">Input Payload</h2>
                <button
                  onClick={() => regenerateRows(metadata)}
                  className="text-xs sm:text-sm px-3 py-2 rounded-lg border border-primary/40 text-primary hover:bg-primary/10 transition-colors inline-flex items-center gap-1.5"
                >
                  <Sparkles className="w-3.5 h-3.5" />
                  Regenerate Sample Rows
                </button>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <label className="text-sm text-muted">
                  <span className="mb-1 block">Stride</span>
                  <input
                    value={stride}
                    onChange={(e) => setStride(e.target.value)}
                    className="w-full bg-background border border-border rounded-xl px-4 py-2 text-text-main outline-none focus:ring-2 focus:ring-primary/50"
                  />
                </label>

                <label className="text-sm text-muted">
                  <span className="mb-1 block">Threshold Name</span>
                  {thresholdOptions.length > 0 ? (
                    <select
                      value={thresholdName}
                      onChange={(e) => setThresholdName(e.target.value)}
                      className="w-full bg-background border border-border rounded-xl px-4 py-2 text-text-main outline-none focus:ring-2 focus:ring-primary/50"
                    >
                      {thresholdOptions.map((opt) => (
                        <option key={opt} value={opt}>
                          {opt}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <input
                      value={thresholdName}
                      onChange={(e) => setThresholdName(e.target.value)}
                      className="w-full bg-background border border-border rounded-xl px-4 py-2 text-text-main outline-none focus:ring-2 focus:ring-primary/50"
                    />
                  )}
                </label>
              </div>

              <label className="text-sm text-muted block">
                <span className="mb-1 block">Rows (JSON array)</span>
                <textarea
                  value={rowsJson}
                  onChange={(e) => setRowsJson(e.target.value)}
                  rows={16}
                  className="w-full bg-background border border-border rounded-xl px-4 py-3 text-text-main font-mono text-xs outline-none focus:ring-2 focus:ring-primary/50"
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
                className="inline-flex items-center gap-2 rounded-xl px-4 py-2 bg-gradient-to-r from-primary to-secondary text-white font-semibold hover:opacity-90 transition-opacity disabled:opacity-60"
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

                <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                  <div className="rounded-xl border border-border bg-surface/30 px-4 py-3">
                    <p className="text-xs text-muted uppercase tracking-wide">Total Windows</p>
                    <p className="text-lg font-bold text-text-main mt-1">{result.total_windows}</p>
                  </div>
                  <div className="rounded-xl border border-border bg-surface/30 px-4 py-3">
                    <p className="text-xs text-muted uppercase tracking-wide">Anomaly Windows</p>
                    <p className="text-lg font-bold text-danger mt-1">{result.anomaly_windows}</p>
                  </div>
                  <div className="rounded-xl border border-border bg-surface/30 px-4 py-3">
                    <p className="text-xs text-muted uppercase tracking-wide">Threshold</p>
                    <p className="text-lg font-bold text-secondary mt-1">{result.threshold_value.toFixed(4)}</p>
                  </div>
                  <div className="rounded-xl border border-border bg-surface/30 px-4 py-3">
                    <p className="text-xs text-muted uppercase tracking-wide">Stride</p>
                    <p className="text-lg font-bold text-text-main mt-1">{result.stride}</p>
                  </div>
                </div>

                <div className="overflow-x-auto rounded-xl border border-border">
                  <table className="w-full text-sm">
                    <thead className="bg-surface/40">
                      <tr className="text-left">
                        <th className="px-4 py-3 text-xs text-muted uppercase tracking-wide">Window</th>
                        <th className="px-4 py-3 text-xs text-muted uppercase tracking-wide">Rows</th>
                        <th className="px-4 py-3 text-xs text-muted uppercase tracking-wide">Score</th>
                        <th className="px-4 py-3 text-xs text-muted uppercase tracking-wide">Anomaly</th>
                      </tr>
                    </thead>
                    <tbody>
                      {result.windows.map((item) => (
                        <tr key={item.window_index} className="border-t border-border/70">
                          <td className="px-4 py-3 text-text-main font-mono">#{item.window_index}</td>
                          <td className="px-4 py-3 text-muted">
                            {item.start_row} - {item.end_row}
                          </td>
                          <td className="px-4 py-3 text-text-main font-mono">{item.reconstruction_score.toFixed(6)}</td>
                          <td className="px-4 py-3">
                            <span
                              className={`inline-flex items-center rounded-full px-2.5 py-1 text-xs font-semibold border ${
                                item.is_anomaly
                                  ? 'text-danger bg-danger/10 border-danger/40'
                                  : 'text-accent bg-accent/10 border-accent/40'
                              }`}
                            >
                              {item.is_anomaly ? 'ANOMALY' : 'NORMAL'}
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
