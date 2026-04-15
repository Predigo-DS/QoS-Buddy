'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { motion } from 'framer-motion'
import { Activity, LogOut, LayoutDashboard, Cpu, AlertTriangle, TrendingUp, Shield } from 'lucide-react'
import { isAuthenticated, getUsername, getRole } from '@/lib/auth'
import { useAuth } from '@/hooks/useAuth'

export default function DashboardPage() {
  const router = useRouter()
  const { logout } = useAuth()
  const [username, setUsername] = useState<string | null>(null)
  const [role, setRole] = useState<string | null>(null)
  const [mounted, setMounted] = useState(false)

  useEffect(() => {
    if (!isAuthenticated()) {
      router.replace('/login')
      return
    }
    setUsername(getUsername())
    setRole(getRole())
    setMounted(true)
  }, [router])

  if (!mounted) {
    return (
      <div className="min-h-screen bg-background flex items-center justify-center">
        <div className="w-8 h-8 rounded-full border-2 border-primary border-t-transparent animate-spin" />
      </div>
    )
  }

  const cards = [
    { icon: Cpu, label: 'AI Models', value: 'Online', color: 'text-accent', bg: 'bg-accent/10' },
    { icon: AlertTriangle, label: 'Active Alerts', value: '0', color: 'text-danger', bg: 'bg-danger/10' },
    { icon: TrendingUp, label: 'SLA Status', value: 'Nominal', color: 'text-primary', bg: 'bg-primary/10' },
    { icon: LayoutDashboard, label: 'Telemetry', value: 'Streaming', color: 'text-secondary', bg: 'bg-secondary/10' },
  ]

  return (
    <div className="min-h-screen bg-background">
      {/* Top bar */}
      <header className="border-b border-border bg-surface/40 backdrop-blur-md sticky top-0 z-10">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-primary to-secondary flex items-center justify-center">
              <Activity className="w-4 h-4 text-white" />
            </div>
            <span className="font-bold text-gradient">QoSentry</span>
          </div>
          <div className="flex items-center gap-2">
            {role === 'ADMIN' && (
              <button
                onClick={() => router.push('/admin')}
                className="flex items-center gap-2 text-sm text-secondary hover:text-secondary/80 transition-colors px-3 py-2 rounded-lg hover:bg-surface border border-secondary/30"
              >
                <Shield className="w-4 h-4" />
                Admin
              </button>
            )}
            <button
              onClick={logout}
              className="flex items-center gap-2 text-sm text-muted hover:text-text-main transition-colors px-3 py-2 rounded-lg hover:bg-surface"
            >
              <LogOut className="w-4 h-4" />
              Sign out
            </button>
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-12">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5 }}
        >
          {/* Welcome */}
          <div className="mb-10">
            <h1 className="text-3xl font-bold text-text-main">
              Welcome back,{' '}
              <span className="text-gradient">{username ?? 'User'}</span>
            </h1>
            {role && (
              <span className="inline-block mt-2 px-2.5 py-0.5 text-xs font-semibold rounded-full bg-primary/10 border border-primary/30 text-primary">
                {role}
              </span>
            )}
          </div>

          {/* Status cards */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6 mb-12">
            {cards.map((card, i) => {
              const Icon = card.icon
              return (
                <motion.div
                  key={card.label}
                  initial={{ opacity: 0, y: 30 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: i * 0.1, duration: 0.5 }}
                  className="glass rounded-2xl p-6 border border-border hover:border-primary/30 transition-colors"
                >
                  <div className={`inline-flex p-2.5 rounded-xl ${card.bg} mb-3`}>
                    <Icon className={`w-5 h-5 ${card.color}`} />
                  </div>
                  <p className="text-muted text-xs font-medium mb-1">{card.label}</p>
                  <p className={`text-lg font-bold ${card.color}`}>{card.value}</p>
                </motion.div>
              )
            })}
          </div>

          {/* Inference actions */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-12">
            <motion.div
              initial={{ opacity: 0, y: 30 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.2, duration: 0.5 }}
              className="glass rounded-2xl p-6 border border-border hover:border-primary/40 transition-colors"
            >
              <p className="text-xs uppercase tracking-wide text-primary font-semibold mb-2">Inference</p>
              <h3 className="text-xl font-bold text-text-main mb-2">Anomaly Detection</h3>
              <p className="text-sm text-muted mb-5">
                Run autoencoder-based anomaly inference using live telemetry rows and threshold controls.
              </p>
              <Link
                href="/inference/anomaly"
                className="inline-flex items-center justify-center rounded-xl px-4 py-2 text-sm font-semibold bg-primary/20 text-primary border border-primary/40 hover:bg-primary/30 transition-colors"
              >
                Open Anomaly Inference
              </Link>
            </motion.div>

            <motion.div
              initial={{ opacity: 0, y: 30 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.3, duration: 0.5 }}
              className="glass rounded-2xl p-6 border border-border hover:border-secondary/40 transition-colors"
            >
              <p className="text-xs uppercase tracking-wide text-secondary font-semibold mb-2">Inference</p>
              <h3 className="text-xl font-bold text-text-main mb-2">SLA Forecasting</h3>
              <p className="text-sm text-muted mb-5">
                Score future QoE classes and SLA risk windows for selected run and segment combinations.
              </p>
              <Link
                href="/inference/sla"
                className="inline-flex items-center justify-center rounded-xl px-4 py-2 text-sm font-semibold bg-secondary/20 text-secondary border border-secondary/40 hover:bg-secondary/30 transition-colors"
              >
                Open SLA Inference
              </Link>
            </motion.div>
          </div>

          {/* Coming soon */}
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ delay: 0.5 }}
            className="glass rounded-2xl p-12 text-center border border-border/50"
          >
            <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-primary/20 to-secondary/20 border border-primary/30 flex items-center justify-center mx-auto mb-4">
              <LayoutDashboard className="w-8 h-8 text-primary" />
            </div>
            <h2 className="text-2xl font-bold text-text-main mb-3">Dashboard coming soon</h2>
            <p className="text-muted max-w-lg mx-auto leading-relaxed">
              The full QoSentry dashboard — real-time anomaly detection, SLA breach forecasts,
              RL-based remediation recommendations, and AI executive reports — is under active
              development.
            </p>
            <div className="flex flex-wrap justify-center gap-3 mt-6">
              {['Anomaly Detection', 'SLA Forecasting', 'Digital Twin', 'Executive Reports'].map(
                (feat) => (
                  <span
                    key={feat}
                    className="text-xs px-3 py-1.5 rounded-full bg-surface border border-border text-muted"
                  >
                    {feat}
                  </span>
                )
              )}
            </div>
          </motion.div>
        </motion.div>
      </main>
    </div>
  )
}
