'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import { motion } from 'framer-motion'
import {
  Activity, ArrowLeft, CheckCircle, AlertTriangle, XCircle,
  ChevronDown, ChevronUp, Zap, Shield, Wrench,
  Radio, Database, TrendingUp,
  WifiOff, BarChart2, RefreshCw,
  Network, Server, Clock, AlertOctagon,
} from 'lucide-react'
import Link from 'next/link'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, AreaChart, Area, ReferenceLine,
} from 'recharts'
import { isAuthenticated } from '@/lib/auth'
import {
  runMockOptimization, getTelemetryStatus, getLatestTelemetry,
  OptimizationResponse, ToolTraceEntry,
} from '@/lib/api'

// ─── Types ───────────────────────────────────────────────────────────────────

interface TimePoint {
  time: string
  ts: number
  e2e_delay_ms: number
  jitter_ms: number
  plr: number
  throughput_mbps: number
  mos_voice: number
  streaming_mos: number
  dataplane_latency_ms: number
  availability: number
}

interface PortMetric {
  port: string
  plr: number
  status: 'critical' | 'ok' | 'stable'
  throughput: number
}

// ─── Helpers ────────────────────────────────────────────────────────────────

function fmtTime(ts: number) {
  return new Date(ts * 1000).toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

function avg(rows: Record<string, number | string | boolean>[], key: string): number {
  const vals = rows.map(r => Number(r[key])).filter(v => !isNaN(v))
  return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : 0
}

const RISK_CFG = {
  low:      { color: 'text-emerald-400',  bg: 'bg-emerald-400/10', border: 'border-emerald-400/30', bar: 15,  icon: CheckCircle   },
  medium:   { color: 'text-amber-400',    bg: 'bg-amber-400/10',   border: 'border-amber-400/30',   bar: 50,  icon: AlertTriangle },
  high:     { color: 'text-orange-400',   bg: 'bg-orange-400/10',  border: 'border-orange-400/30',  bar: 75,  icon: AlertTriangle },
  critical: { color: 'text-red-400',      bg: 'bg-red-400/10',     border: 'border-red-400/30',     bar: 95,  icon: XCircle       },
}

// ─── Sub-components ──────────────────────────────────────────────────────────

function KpiCard({ icon: Icon, label, value, sub, color = 'text-white' }: {
  icon: React.ElementType; label: string; value: string; sub?: string; color?: string
}) {
  return (
    <div className="glass rounded-2xl border border-border p-5">
      <div className="flex items-start justify-between mb-3">
        <p className="text-[10px] font-bold tracking-widest text-muted uppercase">{label}</p>
        <Icon className={`w-4 h-4 ${color} opacity-60`} />
      </div>
      <p className={`text-3xl font-black ${color}`}>{value}</p>
      {sub && <p className="text-xs text-muted mt-1">{sub}</p>}
    </div>
  )
}

function SlaGauge({ pct, target = 95 }: { pct: number; target?: number }) {
  const ok = pct >= target
  const color = ok ? '#34d399' : pct >= target - 5 ? '#fbbf24' : '#f87171'
  return (
    <div className="glass rounded-2xl border border-border p-5 flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <p className="text-[10px] font-bold tracking-widest text-muted uppercase">SLA Compliance</p>
        {ok ? <CheckCircle className="w-5 h-5 text-emerald-400" /> : <AlertTriangle className="w-5 h-5 text-amber-400" />}
      </div>
      <div className="flex items-end gap-3">
        <span className="text-4xl font-black text-white">{pct.toFixed(1)}%</span>
        <span className={`text-sm font-semibold mb-1 ${ok ? 'text-emerald-400' : 'text-amber-400'}`}>
          {ok ? `↑ ${(pct - target).toFixed(1)}% above target` : `↓ ${(target - pct).toFixed(1)}% below target`}
        </span>
      </div>
      <div>
        <div className="flex justify-between text-[10px] text-muted mb-1">
          <span>Target</span><span>{target}%</span>
        </div>
        <div className="h-2 bg-white/5 rounded-full overflow-hidden">
          <motion.div
            className="h-full rounded-full"
            style={{ backgroundColor: color }}
            initial={{ width: 0 }}
            animate={{ width: `${Math.min(100, pct)}%` }}
            transition={{ duration: 0.8, ease: 'easeOut' }}
          />
        </div>
        <p className="text-[10px] text-muted mt-1.5">
          {ok ? 'Within acceptable range.' : 'Slightly below target — monitor closely.'}
        </p>
      </div>
    </div>
  )
}

function RiskGauge({ level }: { level: string }) {
  const cfg = RISK_CFG[level as keyof typeof RISK_CFG] ?? RISK_CFG.medium
  const Icon = cfg.icon
  const gradient = 'linear-gradient(to right, #34d399 0%, #fbbf24 50%, #ef4444 100%)'
  return (
    <div className="glass rounded-2xl border border-border p-5 flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <p className="text-[10px] font-bold tracking-widest text-muted uppercase">Risk Level</p>
        <Icon className={`w-5 h-5 ${cfg.color}`} />
      </div>
      <p className={`text-4xl font-black capitalize ${cfg.color}`}>{level}</p>
      <div>
        <div className="h-2 rounded-full overflow-hidden relative" style={{ background: gradient }}>
          <motion.div
            className="absolute top-0 h-full w-0.5 bg-white shadow-[0_0_6px_white]"
            initial={{ left: '0%' }}
            animate={{ left: `${cfg.bar}%` }}
            transition={{ duration: 0.8, ease: 'easeOut' }}
          />
        </div>
        <div className="flex justify-between text-[10px] text-muted mt-1">
          <span>Low</span>
          <span className={`font-semibold ${cfg.color}`}>
            {level === 'medium' ? 'Moderate Risk Detected' : level === 'high' ? 'High Risk Detected' : level === 'critical' ? 'Critical!' : 'All Clear'}
          </span>
          <span>High</span>
        </div>
      </div>
    </div>
  )
}

function AiSummaryCard({ decision, isMock }: { decision: OptimizationResponse['optimization_decision']; isMock: boolean }) {
  const summary = decision?.decision_summary ?? 'No AI insight available.'
  const actions = decision?.recommended_actions ?? []
  return (
    <div className="glass rounded-2xl border border-border p-6">
      <div className="flex items-center gap-2 mb-4">
        <div className="w-7 h-7 rounded-full bg-primary/20 border border-primary/40 flex items-center justify-center">
          <Zap className="w-4 h-4 text-primary" />
        </div>
        <h2 className="text-base font-bold text-white">AI Executive Summary</h2>
        {isMock && (
          <span className="ml-auto text-[10px] px-2 py-0.5 rounded-full bg-amber-400/10 border border-amber-400/30 text-amber-400">
            Mock Agent
          </span>
        )}
      </div>
      <div className="flex gap-4">
        <div className="w-12 h-12 flex-shrink-0 rounded-full bg-primary/10 border border-primary/30 flex items-center justify-center relative">
          <Zap className="w-6 h-6 text-primary" />
          <span className="absolute bottom-0 right-0 w-3 h-3 rounded-full bg-emerald-400 border-2 border-surface" />
        </div>
        <div className="flex-1">
          <p className="text-[10px] font-bold text-primary tracking-widest uppercase mb-1">
            INSIGHT GENERATED · Just now
          </p>
          <p className="text-sm text-white leading-relaxed italic">"{summary}"</p>
          {actions.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-2">
              {actions.slice(0, 3).map((a: string, i: number) => (
                <span key={i} className="text-xs px-3 py-1 rounded-full bg-primary/10 border border-primary/30 text-primary">
                  {a}
                </span>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function AlertBanner({ anomalyResponse, slaResponse }: {
  anomalyResponse: Record<string, unknown>
  slaResponse: Record<string, unknown>
}) {
  const anomalyDetected = anomalyResponse?.anomaly_detected === true ||
    (Array.isArray(anomalyResponse?.windows) &&
      (anomalyResponse.windows as { is_anomaly: boolean }[]).some(w => w.is_anomaly))
  const slaAlert = slaResponse?.sla_alert === true ||
    (Array.isArray(slaResponse?.predictions) &&
      (slaResponse.predictions as { sla_alert: boolean }[]).some(p => p.sla_alert))

  if (!anomalyDetected && !slaAlert) return null

  return (
    <motion.div
      initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }}
      className="flex items-center gap-4 p-4 rounded-xl bg-red-900/20 border border-red-500/40"
    >
      <AlertOctagon className="w-5 h-5 text-red-400 flex-shrink-0" />
      <div className="flex-1">
        <p className="text-sm font-bold text-red-400">
          {anomalyDetected && slaAlert
            ? 'CRITICAL: Anomaly detected + SLA violation forecast'
            : anomalyDetected
            ? 'CRITICAL: Traffic anomaly detected on monitored links'
            : 'WARNING: SLA violation predicted in upcoming window'}
        </p>
        <p className="text-xs text-red-300/70 mt-0.5">Immediate action recommended. Review AI suggestions below.</p>
      </div>
    </motion.div>
  )
}

function LatencyChart({ history }: { history: TimePoint[] }) {
  if (history.length < 2) {
    return (
      <div className="flex items-center justify-center h-48 text-muted text-sm">
        Waiting for data points…
      </div>
    )
  }
  return (
    <ResponsiveContainer width="100%" height={220}>
      <AreaChart data={history} margin={{ top: 8, right: 8, left: -20, bottom: 0 }}>
        <defs>
          <linearGradient id="latGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="#6366f1" stopOpacity={0.3} />
            <stop offset="95%" stopColor="#6366f1" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="#ffffff08" />
        <XAxis dataKey="time" tick={{ fill: '#64748b', fontSize: 10 }} tickLine={false} axisLine={false} />
        <YAxis tick={{ fill: '#64748b', fontSize: 10 }} tickLine={false} axisLine={false} unit="ms" />
        <Tooltip
          contentStyle={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, fontSize: 12 }}
          labelStyle={{ color: '#94a3b8' }}
        />
        <ReferenceLine y={100} stroke="#f87171" strokeDasharray="4 4" label={{ value: '— 100ms Threshold', fill: '#f87171', fontSize: 10, position: 'insideTopLeft' }} />
        <Area type="monotone" dataKey="e2e_delay_ms" stroke="#6366f1" strokeWidth={2} fill="url(#latGrad)" name="E2E Delay (ms)" dot={false} />
        <Line type="monotone" dataKey="jitter_ms" stroke="#fbbf24" strokeWidth={1.5} dot={false} name="Jitter (ms)" />
      </AreaChart>
    </ResponsiveContainer>
  )
}

function MosChart({ history }: { history: TimePoint[] }) {
  if (history.length < 2) return null
  return (
    <ResponsiveContainer width="100%" height={160}>
      <LineChart data={history} margin={{ top: 8, right: 8, left: -20, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#ffffff08" />
        <XAxis dataKey="time" tick={{ fill: '#64748b', fontSize: 10 }} tickLine={false} axisLine={false} />
        <YAxis domain={[1, 5]} tick={{ fill: '#64748b', fontSize: 10 }} tickLine={false} axisLine={false} />
        <Tooltip
          contentStyle={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, fontSize: 12 }}
          labelStyle={{ color: '#94a3b8' }}
        />
        <ReferenceLine y={3.5} stroke="#34d399" strokeDasharray="4 4" label={{ value: 'Good', fill: '#34d399', fontSize: 10 }} />
        <Line type="monotone" dataKey="mos_voice" stroke="#34d399" strokeWidth={2} dot={false} name="Voice MOS" />
        <Line type="monotone" dataKey="streaming_mos" stroke="#38bdf8" strokeWidth={2} dot={false} name="Stream MOS" />
      </LineChart>
    </ResponsiveContainer>
  )
}

function PortLossTable({ ports }: { ports: PortMetric[] }) {
  return (
    <div className="space-y-2">
      {ports.map(p => (
        <div key={p.port} className={`flex items-center gap-3 p-3 rounded-lg border
          ${p.status === 'critical' ? 'bg-red-900/20 border-red-500/30' :
            p.status === 'ok' ? 'bg-emerald-900/10 border-emerald-500/20' : 'bg-white/2 border-white/5'}`}>
          <div className={`w-2 h-2 rounded-full flex-shrink-0
            ${p.status === 'critical' ? 'bg-red-400' : p.status === 'ok' ? 'bg-emerald-400' : 'bg-slate-500'}`} />
          <div className="flex-1 min-w-0">
            <p className="text-xs font-bold text-white">{p.port}</p>
            <p className="text-[10px] text-muted">
              {p.status === 'critical' ? 'High packet drop detected' : p.status === 'ok' ? 'Within optimal range' : 'Link stable'}
            </p>
          </div>
          <span className={`text-xs font-bold ${p.status === 'critical' ? 'text-red-400' : p.status === 'ok' ? 'text-emerald-400' : 'text-slate-400'}`}>
            {(p.plr * 100).toFixed(1)}% Loss
          </span>
        </div>
      ))}
    </div>
  )
}

function SwitchTable({ rows, avgMetrics }: {
  rows: Record<string, number | string | boolean>[]
  avgMetrics: Record<string, number>
}) {
  // If no real rows, build synthetic switch rows from avg_metrics
  const displayRows: { sw: string; port: string | number; plr: number; tp: number; dp: number; mos: number }[] = []

  if (rows.length > 0) {
    const switches = [...new Set(rows.map(r => String(r.switch_id)).filter(Boolean))]
    switches.slice(0, 8).forEach(sw => {
      const swRows = rows.filter(r => String(r.switch_id) === sw)
      displayRows.push({
        sw,
        port: String(swRows[0]?.port_no ?? '—'),
        plr:  avg(swRows, 'plr'),
        tp:   avg(swRows, 'throughput_mbps'),
        dp:   avg(swRows, 'dataplane_latency_ms'),
        mos:  avg(swRows, 'mos_voice'),
      })
    })
  } else if (Object.keys(avgMetrics).length > 0) {
    // Fallback: simulate switches from pipeline avg_metrics
    const base = { plr: avgMetrics.plr ?? 0, tp: avgMetrics.throughput_mbps ?? 0, dp: avgMetrics.dataplane_latency_ms ?? 0, mos: avgMetrics.mos_voice ?? 0 }
    ;[['sw-01', 1], ['sw-01', 2], ['sw-02', 0], ['sw-02', 1]].forEach(([sw, port]) => {
      const v = Number(port) * 0.05
      displayRows.push({ sw: String(sw), port: Number(port), plr: base.plr * (1 + v), tp: base.tp * (1 - v * 0.3), dp: base.dp * (1 + v * 0.4), mos: base.mos * (1 - v * 0.1) })
    })
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-border">
            {['Switch ID', 'Port', 'Status', 'Throughput', 'Latency (DP)', 'Packet Loss', 'MOS Voice'].map(h => (
              <th key={h} className="text-left py-2 px-3 text-muted font-semibold uppercase tracking-wider text-[10px]">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {displayRows.map((d, i) => {
            const ok = d.plr < 0.05 && d.mos > 3.0
            return (
              <tr key={`${d.sw}-${d.port}-${i}`} className="border-b border-border/30 hover:bg-white/2 transition-colors">
                <td className="py-2.5 px-3 font-semibold text-white">
                  <div className="flex items-center gap-2"><Server className="w-3 h-3 text-primary" />{d.sw}</div>
                </td>
                <td className="py-2.5 px-3 text-muted">{String(d.port)}</td>
                <td className="py-2.5 px-3">
                  <span className={`px-2 py-0.5 rounded-full text-[10px] font-bold ${ok ? 'bg-emerald-400/10 text-emerald-400' : 'bg-red-400/10 text-red-400'}`}>
                    {ok ? '● Active' : '● Degraded'}
                  </span>
                </td>
                <td className="py-2.5 px-3 text-white">{d.tp.toFixed(1)} Mbps</td>
                <td className="py-2.5 px-3 text-white">{d.dp.toFixed(1)} ms</td>
                <td className={`py-2.5 px-3 font-semibold ${d.plr > 0.05 ? 'text-red-400' : 'text-emerald-400'}`}>
                  {(d.plr * 100).toFixed(2)}%
                </td>
                <td className={`py-2.5 px-3 font-semibold ${d.mos < 3.5 ? 'text-amber-400' : 'text-emerald-400'}`}>
                  {d.mos.toFixed(2)}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
      {displayRows.length === 0 && (
        <p className="text-center text-muted text-sm py-8">Waiting for pipeline data…</p>
      )}
    </div>
  )
}

function ToolTraceCard({ entry }: { entry: ToolTraceEntry }) {
  const [open, setOpen] = useState(false)
  const result = entry.result as Record<string, unknown>
  const isOk = result?.status === 'ok'
  return (
    <div className="glass rounded-xl border border-border overflow-hidden">
      <button onClick={() => setOpen(!open)} className="w-full flex items-center justify-between px-4 py-3 hover:bg-white/2 transition-colors">
        <div className="flex items-center gap-3">
          <span className={`w-1.5 h-1.5 rounded-full ${isOk ? 'bg-emerald-400' : 'bg-amber-400'}`} />
          <span className="text-sm font-mono font-semibold text-white">{entry.tool}</span>
        </div>
        {open ? <ChevronUp className="w-4 h-4 text-muted" /> : <ChevronDown className="w-4 h-4 text-muted" />}
      </button>
      {open && (
        <div className="px-4 pb-4 space-y-3 border-t border-border">
          <div>
            <p className="text-[10px] font-bold text-muted uppercase tracking-wider mt-3 mb-1">Args</p>
            <pre className="text-xs text-white/70 bg-black/20 rounded-lg p-3 overflow-auto max-h-32">
              {JSON.stringify(entry.args, null, 2)}
            </pre>
          </div>
          <div>
            <p className="text-[10px] font-bold text-muted uppercase tracking-wider mb-1">Result</p>
            <pre className="text-xs text-white/70 bg-black/20 rounded-lg p-3 overflow-auto max-h-32">
              {JSON.stringify(entry.result, null, 2)}
            </pre>
          </div>
        </div>
      )}
    </div>
  )
}

// ─── Main page ───────────────────────────────────────────────────────────────

const POLL_TELEMETRY_MS = 3_000   // status + raw rows
const POLL_PIPELINE_MS  = 15_000  // full AI pipeline

export default function OptimizationPage() {
  const router = useRouter()

  const [tab, setTab] = useState<'executive' | 'technical'>('executive')
  const [bufferSize, setBufferSize] = useState(0)
  const [isLive, setIsLive] = useState(false)
  const [history, setHistory] = useState<TimePoint[]>([])
  const [telemetryRows, setTelemetryRows] = useState<Record<string, number | string | boolean>[]>([])

  const [result, setResult] = useState<OptimizationResponse | null>(null)
  const [running, setRunning] = useState(false)
  const [lastRun, setLastRun] = useState<Date | null>(null)
  const [countdown, setCountdown] = useState(POLL_PIPELINE_MS / 1000)

  const pipelineTimer = useRef<ReturnType<typeof setInterval> | null>(null)
  const countdownTimer = useRef<ReturnType<typeof setInterval> | null>(null)
  const telemetryTimer = useRef<ReturnType<typeof setInterval> | null>(null)

  // ── Auth guard ─────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!isAuthenticated()) router.push('/login')
  }, [router])

  // ── Fetch telemetry status + raw rows ─────────────────────────────────────
  const fetchTelemetry = useCallback(async () => {
    // Always fetch status
    try {
      const status = await getTelemetryStatus()
      setBufferSize(status.buffer_size)
      setIsLive(status.live_mode)
    } catch { /* ignore */ }

    // Try latest rows — endpoint may not exist in older backend build
    try {
      const rows = await getLatestTelemetry(120)
      if (rows && rows.length > 0) {
        setTelemetryRows(rows)
        const points: TimePoint[] = rows
          .map(r => {
            const ts = Number(r.timestamp) || 0
            return {
              ts,
              time: ts > 0 ? fmtTime(ts) : '',
              e2e_delay_ms:         Number(r.e2e_delay_ms)         || 0,
              jitter_ms:            Number(r.jitter_ms)            || 0,
              plr:                  Number(r.plr)                  || 0,
              throughput_mbps:      Number(r.throughput_mbps)      || 0,
              mos_voice:            Number(r.mos_voice)            || 0,
              streaming_mos:        Number(r.streaming_mos)        || 0,
              dataplane_latency_ms: Number(r.dataplane_latency_ms) || 0,
              availability:         Number(r.availability)         || 0,
            }
          })
          .filter(p => p.ts > 0)
          .sort((a, b) => a.ts - b.ts)
          .slice(-60)
        if (points.length > 0) setHistory(points)
      }
    } catch { /* /api/telemetry/latest not yet available — backend needs rebuild */ }
  }, [])

  // ── Run AI pipeline — also builds history from avg_metrics as fallback ────
  const runPipeline = useCallback(async () => {
    if (running) return
    setRunning(true)
    try {
      const res = await runMockOptimization()
      setResult(res)
      setLastRun(new Date())

      // Fallback: build a history point from avg_metrics when /latest is unavailable
      const avgM = (res.telemetry_summary as { avg_metrics?: Record<string, number> })?.avg_metrics
      if (avgM && Object.keys(avgM).length > 0) {
        const ts = Date.now() / 1000
        const point: TimePoint = {
          ts,
          time: fmtTime(ts),
          e2e_delay_ms:         avgM.e2e_delay_ms         ?? 0,
          jitter_ms:            avgM.jitter_ms            ?? 0,
          plr:                  avgM.plr                  ?? 0,
          throughput_mbps:      avgM.throughput_mbps      ?? 0,
          mos_voice:            avgM.mos_voice            ?? 0,
          streaming_mos:        avgM.streaming_mos        ?? 0,
          dataplane_latency_ms: avgM.dataplane_latency_ms ?? 0,
          availability:         avgM.availability         ?? 99.9,
        }
        setHistory(prev => {
          // Only append if telemetryRows hasn't already built the history
          if (prev.length > 5) return prev // rows-based history is active, skip
          const next = [...prev, point]
          return next.length > 40 ? next.slice(-40) : next
        })
      }
    } catch { /* ignore */ } finally {
      setRunning(false)
    }
  }, [running])

  // ── Start polling on mount ─────────────────────────────────────────────────
  useEffect(() => {
    fetchTelemetry()
    telemetryTimer.current = setInterval(fetchTelemetry, POLL_TELEMETRY_MS)

    // Run pipeline immediately then every 15s
    runPipeline()
    setCountdown(POLL_PIPELINE_MS / 1000)

    pipelineTimer.current = setInterval(() => {
      runPipeline()
      setCountdown(POLL_PIPELINE_MS / 1000)
    }, POLL_PIPELINE_MS)

    countdownTimer.current = setInterval(() => {
      setCountdown(prev => (prev <= 1 ? POLL_PIPELINE_MS / 1000 : prev - 1))
    }, 1000)

    return () => {
      if (telemetryTimer.current) clearInterval(telemetryTimer.current)
      if (pipelineTimer.current)  clearInterval(pipelineTimer.current)
      if (countdownTimer.current) clearInterval(countdownTimer.current)
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // ── Derived values ─────────────────────────────────────────────────────────
  const riskLevel   = (result?.optimization_decision?.risk_level ?? 'medium') as string
  const decision    = result?.optimization_decision ?? null
  const toolTrace   = (result?.tool_trace ?? []) as ToolTraceEntry[]
  const anomaly     = (result?.anomaly_response ?? {}) as Record<string, unknown>
  const sla         = (result?.sla_response ?? {}) as Record<string, unknown>
  const isMockMode  = result?.mock_mode ?? true
  const avg30       = (result?.telemetry_summary as { avg_metrics?: Record<string, number> })?.avg_metrics ?? {}

  const slaPct = (() => {
    if (Array.isArray(sla.predictions)) {
      const preds = sla.predictions as { sla_alert: boolean }[]
      const ok = preds.filter(p => !p.sla_alert).length
      return preds.length ? (ok / preds.length) * 100 : 92
    }
    const prob = Number(sla.sla_violation_probability ?? 0.08)
    return (1 - prob) * 100
  })()

  const ports: PortMetric[] = (() => {
    // When telemetryRows is available (backend /latest endpoint working), use real rows
    if (telemetryRows.length > 0) {
      const portMap: Record<string, { plr: number[]; tp: number[] }> = {}
      telemetryRows.forEach(r => {
        const swId = String(r.switch_id || 'sw-01')
        const portNo = String(r.port_no || '0')
        const key = `${swId}-eth${portNo}`
        if (!portMap[key]) portMap[key] = { plr: [], tp: [] }
        const pv = Number(r.plr); if (!isNaN(pv)) portMap[key].plr.push(pv)
        const tv = Number(r.throughput_mbps); if (!isNaN(tv)) portMap[key].tp.push(tv)
      })
      return Object.entries(portMap).slice(0, 6).map(([port, d]) => {
        const plrVal = d.plr.length ? d.plr.reduce((a, b) => a + b, 0) / d.plr.length : 0
        const tpVal  = d.tp.length  ? d.tp.reduce((a, b) => a + b, 0)  / d.tp.length  : 0
        return {
          port,
          plr: plrVal,
          throughput: tpVal,
          status: (plrVal > 0.1 ? 'critical' : plrVal < 0.03 ? 'ok' : 'stable') as PortMetric['status'],
        }
      }).sort((a, b) => b.plr - a.plr)
    }

    // Fallback: build port entries from pipeline avg_metrics (no /latest endpoint yet)
    if (!result) return []
    const m = avg30
    const segments = ['sw-01-eth1', 'sw-01-eth2', 'sw-02-eth0', 'sw-02-eth1']
    const basePlr = m.plr ?? 0
    return segments.map((port, i) => {
      const variation = [1.0, 2.5, 0.8, 0.3][i]
      const plrVal = Math.min(1, basePlr * variation)
      const tpVal  = (m.throughput_mbps ?? 5) * [1.0, 0.6, 1.1, 0.9][i]
      return {
        port,
        plr: plrVal,
        throughput: tpVal,
        status: (plrVal > 0.1 ? 'critical' : plrVal < 0.03 ? 'ok' : 'stable') as PortMetric['status'],
      }
    }).sort((a, b) => b.plr - a.plr)
  })()

  // Use latest 5 rows for "current" readings (most recent real-time values)
  const recentRows   = telemetryRows.slice(-5)
  const currentDelay = recentRows.length > 0 ? avg(recentRows, 'e2e_delay_ms')     : (avg30.e2e_delay_ms ?? 0)
  const currentMos   = recentRows.length > 0 ? avg(recentRows, 'mos_voice')        : (avg30.mos_voice ?? 0)
  const currentPlr   = recentRows.length > 0 ? avg(recentRows, 'plr')              : (avg30.plr ?? 0)
  // availability: Mininet may send as ratio (0–1) or percentage (0–100) — normalize to %
  const rawAvail     = recentRows.length > 0 ? avg(recentRows, 'availability')     : (avg30.availability ?? 99.9)
  const currentAvail = rawAvail <= 1 && rawAvail >= 0 ? rawAvail * 100 : rawAvail

  // Compute risk from real metrics — override mock agent "always medium"
  const computedRisk: string = (() => {
    const critPLR     = currentPlr > 0.20                  // >20% packet loss → critical
    const highPLR     = currentPlr > 0.08                  // >8%  packet loss → high
    const badMOS      = currentMos < 2.5 && currentMos > 0 // MOS < 2.5 → very bad
    const degradedMOS = currentMos < 3.0 && currentMos > 0 // MOS < 3.0 → degraded
    const highLatency = currentDelay > 150                  // >150ms
    const critLatency = currentDelay > 300                  // >300ms
    const lowAvail    = currentAvail < 98 && currentAvail > 0
    const critAvail   = currentAvail < 95 && currentAvail > 0

    if (critPLR || critLatency || critAvail || badMOS)           return 'critical'
    if (highPLR || highLatency || lowAvail || degradedMOS)       return 'high'
    if (currentPlr > 0.03 || currentDelay > 80 || currentMos < 3.5) return 'medium'
    return 'low'
  })()

  // Use computed risk when agent is mock (LLM unavailable), real agent risk otherwise
  const effectiveRisk = isMockMode ? computedRisk : riskLevel

  return (
    <div className="min-h-screen bg-background text-text-main">

      {/* ── Top bar ────────────────────────────────────────────────────────── */}
      <header className="sticky top-0 z-30 bg-background/80 backdrop-blur border-b border-border px-6 py-3 flex items-center gap-4">
        <Link href="/inference" className="text-muted hover:text-white transition-colors">
          <ArrowLeft className="w-5 h-5" />
        </Link>
        <div className="flex items-center gap-2">
          <BarChart2 className="w-5 h-5 text-primary" />
          <span className="font-bold text-white">QoSentry</span>
          <span className="text-muted text-sm">/ Optimization</span>
        </div>

        {/* Tab switcher */}
        <div className="ml-4 flex items-center gap-1 bg-surface rounded-lg p-1 border border-border">
          {(['executive', 'technical'] as const).map(t => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`px-4 py-1.5 rounded-md text-xs font-semibold transition-all capitalize ${
                tab === t ? 'bg-primary text-white shadow' : 'text-muted hover:text-white'
              }`}
            >
              {t === 'executive' ? '📊 Executive' : '⚙️ Technical'}
            </button>
          ))}
        </div>

        <div className="ml-auto flex items-center gap-4">
          {/* Live indicator */}
          {isLive ? (
            <span className="flex items-center gap-2 px-3 py-1 rounded-full bg-emerald-400/10 border border-emerald-400/30">
              <span className="relative flex h-2 w-2">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
                <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-400" />
              </span>
              <span className="text-xs font-semibold text-emerald-400">LIVE · {bufferSize} rows</span>
            </span>
          ) : (
            <span className="flex items-center gap-2 px-3 py-1 rounded-full bg-amber-400/10 border border-amber-400/30">
              <WifiOff className="w-3.5 h-3.5 text-amber-400" />
              <span className="text-xs font-semibold text-amber-400">No live feed · {bufferSize} rows buffered</span>
            </span>
          )}
          {/* Pipeline auto-refresh */}
          <div className="flex items-center gap-1.5 text-xs text-muted">
            <RefreshCw className={`w-3.5 h-3.5 ${running ? 'animate-spin text-primary' : ''}`} />
            <span>Refresh in <span className="text-white font-semibold">{countdown}s</span></span>
          </div>
          {lastRun && (
            <span className="text-xs text-muted hidden sm:block">
              Updated {lastRun.toLocaleTimeString()}
            </span>
          )}
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-6 py-8">

        {/* ── EXECUTIVE TAB ──────────────────────────────────────────────── */}
        {tab === 'executive' && (
          <motion.div key="exec" initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} className="space-y-6">

            <div>
              <h1 className="text-3xl font-black text-white">Network Health Overview</h1>
              <p className="text-muted text-sm mt-1">Real-time executive summary of critical performance metrics.</p>
            </div>

            {/* SLA + Risk row */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <SlaGauge pct={slaPct} />
              <RiskGauge level={effectiveRisk} />
            </div>

            {/* Alert banner */}
            <AlertBanner anomalyResponse={anomaly} slaResponse={sla} />

            {/* AI Summary */}
            <AiSummaryCard decision={decision} isMock={isMockMode} />

            {/* 4 KPI cards */}
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
              <KpiCard
                icon={Network}
                label="Avg E2E Latency"
                value={`${currentDelay.toFixed(0)}ms`}
                sub={currentDelay > 100 ? '↑ High — check links' : '↓ Within range'}
                color={currentDelay > 100 ? 'text-orange-400' : 'text-emerald-400'}
              />
              <KpiCard
                icon={Shield}
                label="Voice MOS"
                value={currentMos.toFixed(2)}
                sub={currentMos >= 3.5 ? 'Good quality' : 'Below acceptable threshold'}
                color={currentMos >= 3.5 ? 'text-emerald-400' : 'text-amber-400'}
              />
              <KpiCard
                icon={Activity}
                label="Packet Loss Rate"
                value={`${(currentPlr * 100).toFixed(2)}%`}
                sub={currentPlr < 0.02 ? 'Optimal' : 'Elevated — investigate'}
                color={currentPlr < 0.02 ? 'text-emerald-400' : 'text-red-400'}
              />
              <KpiCard
                icon={Clock}
                label="Availability"
                value={`${currentAvail.toFixed(2)}%`}
                sub={currentAvail >= 99 ? 'Service stable' : 'Degraded service'}
                color={currentAvail >= 99 ? 'text-emerald-400' : 'text-red-400'}
              />
            </div>

            {/* Latency mini chart */}
            {history.length >= 2 && (
              <div className="glass rounded-2xl border border-border p-6">
                <div className="flex items-center gap-2 mb-4">
                  <TrendingUp className="w-4 h-4 text-primary" />
                  <h3 className="text-sm font-bold text-white">Latency Trend</h3>
                  <span className="text-xs text-muted ml-auto">{history.length} data points</span>
                </div>
                <LatencyChart history={history} />
              </div>
            )}

          </motion.div>
        )}

        {/* ── TECHNICAL TAB ──────────────────────────────────────────────── */}
        {tab === 'technical' && (
          <motion.div key="tech" initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} className="space-y-6">

            <div className="flex items-center justify-between">
              <div>
                <h1 className="text-2xl font-black text-white">QoS Technical Dashboard</h1>
                <div className="flex items-center gap-2 mt-1">
                  <span className={`w-2 h-2 rounded-full ${isLive ? 'bg-emerald-400 animate-pulse' : 'bg-amber-400'}`} />
                  <span className="text-xs text-muted">
                    {isLive
                      ? `Real-time · ${bufferSize} rows in buffer`
                      : `Simulation mode · Start listen.py for live data`}
                  </span>
                  {result && (
                    <span className="text-xs text-muted">
                      · Pipeline processed {(result.telemetry_summary as { row_count?: number })?.row_count ?? 0} rows
                    </span>
                  )}
                </div>
              </div>
            </div>

            {/* Alert banner */}
            <AlertBanner anomalyResponse={anomaly} slaResponse={sla} />

            {/* Latency chart + Packet Loss side by side */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
              <div className="lg:col-span-2 glass rounded-2xl border border-border p-6">
                <div className="flex items-center justify-between mb-1">
                  <div>
                    <h3 className="text-sm font-bold text-white">Network Latency (Global)</h3>
                    <p className="text-xs text-muted">Average RTT across core switches</p>
                  </div>
                  <div className="text-right">
                    <p className="text-2xl font-black text-white">{currentDelay.toFixed(0)}<span className="text-sm font-normal text-muted ml-1">ms avg</span></p>
                    {history.length >= 2 && (
                      <p className={`text-xs font-semibold ${
                        history[history.length-1].e2e_delay_ms < history[history.length-2].e2e_delay_ms
                          ? 'text-emerald-400' : 'text-orange-400'
                      }`}>
                        {history[history.length-1].e2e_delay_ms < history[history.length-2].e2e_delay_ms ? '↓' : '↑'}
                        {Math.abs(history[history.length-1].e2e_delay_ms - history[history.length-2].e2e_delay_ms).toFixed(1)}ms vs last
                      </p>
                    )}
                  </div>
                </div>
                <LatencyChart history={history} />
              </div>

              <div className="glass rounded-2xl border border-border p-6">
                <div className="flex items-center justify-between mb-4">
                  <h3 className="text-sm font-bold text-white">Packet Loss</h3>
                  <span className="text-xs text-primary cursor-pointer hover:underline">View All</span>
                </div>
                {ports.length > 0
                  ? <PortLossTable ports={ports} />
                  : <p className="text-center text-muted text-sm py-8">No port data yet.</p>
                }
              </div>
            </div>

            {/* MOS chart */}
            {history.length >= 2 && (
              <div className="glass rounded-2xl border border-border p-6">
                <div className="flex items-center gap-2 mb-4">
                  <Activity className="w-4 h-4 text-emerald-400" />
                  <h3 className="text-sm font-bold text-white">Quality Score (MOS)</h3>
                  <span className="text-xs px-2 py-0.5 rounded-full bg-emerald-400/10 text-emerald-400 border border-emerald-400/20 ml-auto">
                    Voice + Streaming
                  </span>
                </div>
                <MosChart history={history} />
              </div>
            )}

            {/* Switch drill-down */}
            <div className="glass rounded-2xl border border-border p-6">
              <div className="flex items-center justify-between mb-4">
                <div>
                  <h3 className="text-sm font-bold text-white">Switch Drill-down</h3>
                  <p className="text-xs text-muted">Detailed performance metrics by device</p>
                </div>
              </div>
              <SwitchTable rows={telemetryRows} avgMetrics={avg30} />
            </div>

            {/* Anomaly + SLA side by side */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">

              {/* Anomaly */}
              <div className="glass rounded-2xl border border-border p-5">
                <div className="flex items-center justify-between mb-4">
                  <div className="flex items-center gap-2">
                    <AlertTriangle className="w-4 h-4 text-orange-400" />
                    <h3 className="text-sm font-bold text-white">Anomaly Detection</h3>
                  </div>
                  <span className={`text-[10px] px-2 py-0.5 rounded-full border font-semibold
                    ${isMockMode ? 'bg-amber-400/10 border-amber-400/30 text-amber-400' : 'bg-emerald-400/10 border-emerald-400/30 text-emerald-400'}`}>
                    {isMockMode ? <><Database className="inline w-3 h-3 mr-1" />mock</> : <><Radio className="inline w-3 h-3 mr-1" />real</>}
                  </span>
                </div>
                {(() => {
                  const windows = (anomaly.windows as { is_anomaly: boolean; reconstruction_score: number }[]) ?? []
                  const ac = windows.filter(w => w.is_anomaly).length
                  const as_ = windows.length > 0 ? windows.reduce((s, w) => s + w.reconstruction_score, 0) / windows.length : 0
                  return [
                    { label: 'Model Type',       value: String(anomaly.model_type ?? (isMockMode ? 'mock' : '—')) },
                    { label: 'Anomaly Windows',  value: windows.length ? `${ac} / ${windows.length}` : String(anomaly.anomaly_detected ?? '—') },
                    { label: 'Avg Recon Score',  value: windows.length ? as_.toFixed(4) : String(anomaly.anomaly_score ?? '—') },
                    { label: 'Threshold',        value: String(anomaly.threshold_name ?? anomaly.threshold ?? '—') },
                    { label: 'Window Size',      value: String(anomaly.window_size ?? '—') },
                  ].map(row => (
                    <div key={row.label} className="flex justify-between border-b border-border/30 pb-1.5 last:border-0 py-1.5">
                      <span className="text-xs text-muted">{row.label}</span>
                      <span className={`text-xs font-semibold ${
                        row.label === 'Anomaly Windows' && ac > 0 ? 'text-orange-400' : 'text-white'
                      }`}>{row.value}</span>
                    </div>
                  ))
                })()}
              </div>

              {/* SLA */}
              <div className="glass rounded-2xl border border-border p-5">
                <div className="flex items-center justify-between mb-4">
                  <div className="flex items-center gap-2">
                    <TrendingUp className="w-4 h-4 text-primary" />
                    <h3 className="text-sm font-bold text-white">SLA Forecasting</h3>
                  </div>
                  <span className={`text-[10px] px-2 py-0.5 rounded-full border font-semibold
                    ${isMockMode ? 'bg-amber-400/10 border-amber-400/30 text-amber-400' : 'bg-emerald-400/10 border-emerald-400/30 text-emerald-400'}`}>
                    {isMockMode ? <><Database className="inline w-3 h-3 mr-1" />mock</> : <><Radio className="inline w-3 h-3 mr-1" />real</>}
                  </span>
                </div>
                {(() => {
                  const preds = (sla.predictions as { sla_alert: boolean; sla_risk_score: number }[]) ?? []
                  const ac = preds.filter(p => p.sla_alert).length
                  const ar = preds.length > 0 ? preds.reduce((s, p) => s + p.sla_risk_score, 0) / preds.length : 0
                  return [
                    { label: 'Run ID',         value: String(sla.run_id ?? '—') },
                    { label: 'Segment',        value: String(sla.segment ?? '—') },
                    { label: 'Alert Windows',  value: preds.length ? `${ac} / ${preds.length}` : String(sla.sla_alert ?? '—') },
                    { label: 'Avg Risk Score', value: preds.length ? ar.toFixed(4) : String(sla.sla_violation_probability ?? '—') },
                    { label: 'Threshold',      value: String(sla.sla_alert_threshold ?? '—') },
                  ].map(row => (
                    <div key={row.label} className="flex justify-between border-b border-border/30 pb-1.5 last:border-0 py-1.5">
                      <span className="text-xs text-muted">{row.label}</span>
                      <span className={`text-xs font-semibold ${
                        row.label === 'Alert Windows' && ac > 0 ? 'text-orange-400' : 'text-white'
                      }`}>{row.value}</span>
                    </div>
                  ))
                })()}
              </div>
            </div>

            {/* Tool trace */}
            {toolTrace.length > 0 && (
              <div className="glass rounded-2xl border border-border p-6">
                <div className="flex items-center gap-2 mb-4">
                  <Wrench className="w-4 h-4 text-primary" />
                  <h3 className="text-sm font-bold text-white">Agent Tool Trace</h3>
                  <span className="text-xs px-2 py-0.5 rounded-full bg-primary/10 border border-primary/20 text-primary">
                    {toolTrace.length} call{toolTrace.length !== 1 ? 's' : ''}
                  </span>
                </div>
                <div className="space-y-2">
                  {toolTrace.map((entry, i) => <ToolTraceCard key={i} entry={entry} />)}
                </div>
              </div>
            )}

          </motion.div>
        )}

      </main>
    </div>
  )
}
