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
  ShieldAlert,
  Wrench,
} from 'lucide-react'
import { getRole, getUsername, isAuthenticated } from '@/lib/auth'
import {
  getIncidentTools,
  IncidentResponse,
  IncidentToolsResponse,
  respondToIncident,
} from '@/lib/api'

type IncidentFormState = {
  device: string
  latency: string
  cpu: string
  memory: string
  packetLoss: string
  dryRun: boolean
}

const DEFAULT_FORM: IncidentFormState = {
  device: 'Router_A',
  latency: '155',
  cpu: '92',
  memory: '74',
  packetLoss: '0.5',
  dryRun: true,
}

function toOptionalNumber(raw: string): number | undefined {
  const value = raw.trim()
  if (!value) return undefined

  const parsed = Number(value)
  if (!Number.isFinite(parsed)) {
    throw new Error(`Invalid number: ${raw}`)
  }

  return parsed
}

function prettyJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

export default function IncidentInferencePage() {
  const router = useRouter()

  const [mounted, setMounted] = useState(false)
  const [username, setUsername] = useState<string | null>(null)
  const [role, setRole] = useState<string | null>(null)

  const [form, setForm] = useState<IncidentFormState>(DEFAULT_FORM)

  const [tools, setTools] = useState<IncidentToolsResponse | null>(null)
  const [toolsLoading, setToolsLoading] = useState(true)
  const [toolsError, setToolsError] = useState<string | null>(null)

  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [result, setResult] = useState<IncidentResponse | null>(null)

  const risk = result?.risk ?? {}
  const riskLevel = String(risk.level ?? 'unknown')
  const riskScore = Number(risk.score ?? 0)

  const riskTone = useMemo(() => {
    if (riskLevel === 'critical') return 'text-danger border-danger/40 bg-danger/10'
    if (riskLevel === 'high') return 'text-orange-400 border-orange-400/40 bg-orange-400/10'
    if (riskLevel === 'medium') return 'text-yellow-400 border-yellow-400/40 bg-yellow-400/10'
    return 'text-accent border-accent/40 bg-accent/10'
  }, [riskLevel])

  useEffect(() => {
    if (!isAuthenticated()) {
      router.replace('/login')
      return
    }

    setMounted(true)
    setUsername(getUsername())
    setRole(getRole())

    const loadTools = async () => {
      setToolsLoading(true)
      setToolsError(null)
      try {
        const payload = await getIncidentTools()
        setTools(payload)
      } catch (error) {
        setToolsError(
          error instanceof Error
            ? error.message
            : 'Failed to load incident placeholder tools.',
        )
      } finally {
        setToolsLoading(false)
      }
    }

    void loadTools()
  }, [router])

  const setNominalCase = () => {
    setForm({
      device: 'Router_A',
      latency: '155',
      cpu: '92',
      memory: '74',
      packetLoss: '0.5',
      dryRun: true,
    })
  }

  const setCriticalCase = () => {
    setForm({
      device: 'Router_A',
      latency: '200',
      cpu: '99',
      memory: '95',
      packetLoss: '5',
      dryRun: true,
    })
  }

  const handleRun = async () => {
    setSubmitError(null)
    setResult(null)

    const device = form.device.trim()
    if (!device) {
      setSubmitError('Device is required.')
      return
    }

    try {
      const payload = {
        device,
        latency: toOptionalNumber(form.latency),
        cpu: toOptionalNumber(form.cpu),
        memory: toOptionalNumber(form.memory),
        packet_loss: toOptionalNumber(form.packetLoss),
        dry_run: form.dryRun,
      }

      setSubmitting(true)
      const response = await respondToIncident(payload)
      setResult(response)
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : 'Incident response failed.')
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
            <span className="text-sm text-muted font-medium">Incident Response Inference</span>
          </div>

          <div className="flex items-center gap-2">
            <Link
              href="/inference/anomaly"
              className="text-xs sm:text-sm px-3 py-2 rounded-lg border border-primary/40 text-primary hover:bg-primary/10 transition-colors"
            >
              Anomaly
            </Link>
            <Link
              href="/inference/sla"
              className="text-xs sm:text-sm px-3 py-2 rounded-lg border border-secondary/40 text-secondary hover:bg-secondary/10 transition-colors"
            >
              SLA
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
          <h1 className="text-3xl font-bold text-text-main">Incident Response Orchestration Test</h1>
          <p className="text-muted mt-2">
            Logged in as <span className="text-primary font-semibold">{username ?? 'User'}</span>
            {role ? <span className="ml-2 text-xs uppercase tracking-wide text-secondary">{role}</span> : null}
          </p>
        </motion.div>

        {toolsLoading ? (
          <div className="glass rounded-2xl p-8 border border-border flex items-center gap-3 text-muted">
            <Loader2 className="w-5 h-5 animate-spin" />
            Loading placeholder tools catalog...
          </div>
        ) : toolsError ? (
          <div className="glass rounded-2xl p-6 border border-danger/40 bg-danger/10 text-danger flex items-start gap-3">
            <AlertTriangle className="w-5 h-5 mt-0.5" />
            <p className="text-sm">{toolsError}</p>
          </div>
        ) : tools ? (
          <div className="glass rounded-2xl p-6 border border-border space-y-4">
            <div className="flex items-center gap-2">
              <Wrench className="w-4 h-4 text-primary" />
              <h2 className="text-xl font-bold text-text-main">Available Placeholder Tools</h2>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {Object.entries(tools.tools).map(([category, names]) => (
                <div key={category} className="rounded-xl border border-border bg-surface/30 p-4">
                  <p className="text-xs text-muted uppercase tracking-wide">{category}</p>
                  <div className="mt-2 flex flex-wrap gap-2">
                    {names.map((name) => (
                      <span
                        key={name}
                        className="inline-flex items-center rounded-full px-2.5 py-1 text-xs font-semibold border border-border text-text-main"
                      >
                        {name}
                      </span>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>
        ) : null}

        <div className="glass rounded-2xl p-6 border border-border space-y-5">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-xl font-bold text-text-main mr-2">Incident Payload</h2>
            <button
              onClick={setNominalCase}
              className="text-xs sm:text-sm px-3 py-2 rounded-lg border border-primary/40 text-primary hover:bg-primary/10 transition-colors"
            >
              Load Nominal Congestion Case
            </button>
            <button
              onClick={setCriticalCase}
              className="text-xs sm:text-sm px-3 py-2 rounded-lg border border-danger/40 text-danger hover:bg-danger/10 transition-colors"
            >
              Load Critical Rollback Case
            </button>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <label className="text-sm text-muted">
              <span className="mb-1 block">Device</span>
              <input
                value={form.device}
                onChange={(e) => setForm((prev) => ({ ...prev, device: e.target.value }))}
                className="w-full bg-background border border-border rounded-xl px-4 py-2 text-text-main outline-none focus:ring-2 focus:ring-primary/50"
              />
            </label>

            <label className="text-sm text-muted">
              <span className="mb-1 block">Latency</span>
              <input
                value={form.latency}
                onChange={(e) => setForm((prev) => ({ ...prev, latency: e.target.value }))}
                className="w-full bg-background border border-border rounded-xl px-4 py-2 text-text-main outline-none focus:ring-2 focus:ring-primary/50"
              />
            </label>

            <label className="text-sm text-muted">
              <span className="mb-1 block">CPU</span>
              <input
                value={form.cpu}
                onChange={(e) => setForm((prev) => ({ ...prev, cpu: e.target.value }))}
                className="w-full bg-background border border-border rounded-xl px-4 py-2 text-text-main outline-none focus:ring-2 focus:ring-primary/50"
              />
            </label>

            <label className="text-sm text-muted">
              <span className="mb-1 block">Memory</span>
              <input
                value={form.memory}
                onChange={(e) => setForm((prev) => ({ ...prev, memory: e.target.value }))}
                className="w-full bg-background border border-border rounded-xl px-4 py-2 text-text-main outline-none focus:ring-2 focus:ring-primary/50"
              />
            </label>

            <label className="text-sm text-muted">
              <span className="mb-1 block">Packet Loss</span>
              <input
                value={form.packetLoss}
                onChange={(e) => setForm((prev) => ({ ...prev, packetLoss: e.target.value }))}
                className="w-full bg-background border border-border rounded-xl px-4 py-2 text-text-main outline-none focus:ring-2 focus:ring-primary/50"
              />
            </label>

            <label className="text-sm text-muted flex items-center gap-2 pt-6">
              <input
                type="checkbox"
                checked={form.dryRun}
                onChange={(e) => setForm((prev) => ({ ...prev, dryRun: e.target.checked }))}
                className="rounded border-border bg-background"
              />
              Dry Run
            </label>
          </div>

          {submitError ? (
            <div className="rounded-xl border border-danger/40 bg-danger/10 px-4 py-3 text-sm text-danger">
              {submitError}
            </div>
          ) : null}

          <button
            onClick={handleRun}
            disabled={submitting}
            className="inline-flex items-center gap-2 rounded-xl px-4 py-2 bg-gradient-to-r from-primary to-secondary text-white font-semibold hover:opacity-90 transition-opacity disabled:opacity-60"
          >
            {submitting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
            Run Incident Response
          </button>
        </div>

        {result ? (
          <div className="glass rounded-2xl p-6 border border-border space-y-5">
            <div className="flex flex-wrap items-center gap-3">
              <h2 className="text-xl font-bold text-text-main">Incident Output</h2>
              <span className="inline-flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full border border-accent/40 bg-accent/10 text-accent">
                <CheckCircle2 className="w-3.5 h-3.5" />
                Response Generated
              </span>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
              <div className="rounded-xl border border-border bg-surface/30 px-4 py-3">
                <p className="text-xs text-muted uppercase tracking-wide">Risk Score</p>
                <p className="text-lg font-bold text-text-main mt-1">{riskScore}</p>
              </div>
              <div className="rounded-xl border border-border bg-surface/30 px-4 py-3">
                <p className="text-xs text-muted uppercase tracking-wide">Risk Level</p>
                <span className={`inline-flex mt-1 rounded-full px-2.5 py-1 text-xs font-semibold border ${riskTone}`}>
                  {riskLevel.toUpperCase()}
                </span>
              </div>
              <div className="rounded-xl border border-border bg-surface/30 px-4 py-3">
                <p className="text-xs text-muted uppercase tracking-wide">Plan Steps</p>
                <p className="text-lg font-bold text-text-main mt-1">{result.plan.length}</p>
              </div>
              <div className="rounded-xl border border-border bg-surface/30 px-4 py-3">
                <p className="text-xs text-muted uppercase tracking-wide">Expected Recovery</p>
                <p className="text-lg font-bold text-secondary mt-1">
                  {result.expected_recovery_seconds != null
                    ? `${result.expected_recovery_seconds}s`
                    : 'n/a'}
                </p>
              </div>
            </div>

            <div className="rounded-xl border border-border bg-surface/20 p-4">
              <p className="text-xs text-muted uppercase tracking-wide">Decision</p>
              <p className="text-sm text-text-main mt-2 leading-relaxed">{result.decision}</p>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              <div className="rounded-xl border border-border bg-surface/20 p-4">
                <p className="text-xs text-muted uppercase tracking-wide">Executed Plan</p>
                <div className="mt-3 flex flex-wrap gap-2">
                  {result.plan.map((step) => (
                    <span
                      key={step}
                      className="inline-flex items-center rounded-full px-2.5 py-1 text-xs font-semibold border border-primary/40 bg-primary/10 text-primary"
                    >
                      {step}
                    </span>
                  ))}
                </div>
              </div>

              <div className="rounded-xl border border-border bg-surface/20 p-4">
                <p className="text-xs text-muted uppercase tracking-wide">Validation Notes</p>
                <div className="mt-3 space-y-2 text-sm text-text-main">
                  {(result.validation?.notes ?? []).length > 0 ? (
                    (result.validation?.notes ?? []).map((note, idx) => (
                      <p key={`${note}-${idx}`}>- {note}</p>
                    ))
                  ) : (
                    <p className="text-muted">No validation notes.</p>
                  )}
                </div>
              </div>
            </div>

            <div className="rounded-xl border border-border overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-surface/40">
                  <tr className="text-left">
                    <th className="px-4 py-3 text-xs text-muted uppercase tracking-wide">Tool</th>
                    <th className="px-4 py-3 text-xs text-muted uppercase tracking-wide">Args</th>
                    <th className="px-4 py-3 text-xs text-muted uppercase tracking-wide">Result</th>
                  </tr>
                </thead>
                <tbody>
                  {result.tool_trace.map((item, idx) => (
                    <tr key={`${item.tool}-${idx}`} className="border-t border-border/70 align-top">
                      <td className="px-4 py-3 text-text-main font-semibold">{item.tool}</td>
                      <td className="px-4 py-3 text-text-main">
                        <pre className="text-xs whitespace-pre-wrap break-all">{prettyJson(item.args)}</pre>
                      </td>
                      <td className="px-4 py-3 text-text-main">
                        <pre className="text-xs whitespace-pre-wrap break-all">{prettyJson(item.result)}</pre>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="rounded-xl border border-border bg-surface/20 p-4">
              <p className="text-xs text-muted uppercase tracking-wide mb-2">Raw Incident Response JSON</p>
              <pre className="text-xs whitespace-pre-wrap break-all text-text-main">{prettyJson(result)}</pre>
            </div>
          </div>
        ) : (
          <div className="glass rounded-2xl p-6 border border-border text-muted flex items-start gap-3">
            <ShieldAlert className="w-5 h-5 mt-0.5 text-secondary" />
            <p className="text-sm">
              Run a payload above to validate the incident orchestration graph directly from UI.
            </p>
          </div>
        )}
      </main>
    </div>
  )
}
