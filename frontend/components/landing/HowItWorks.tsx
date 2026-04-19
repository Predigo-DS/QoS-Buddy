'use client'

import { useRef } from 'react'
import { motion, useInView } from 'framer-motion'
import { Radio, Cpu, Zap } from 'lucide-react'

const steps = [
  {
    number: '01',
    icon: Radio,
    title: 'Collect',
    description:
      'Mininet + Ryu SDN Controller generates 900K+ labeled telemetry events every 2 seconds via Redis pub/sub. Every packet, flow table entry, and link state is captured with millisecond precision.',
    color: 'primary',
  },
  {
    number: '02',
    icon: Cpu,
    title: 'Analyze',
    description:
      'AI models detect anomalies — DDoS, Link Failure, Congestion — with >85% accuracy using LSTM autoencoders. Prophet models forecast degradation trends up to 15 minutes ahead of impact.',
    color: 'secondary',
  },
  {
    number: '03',
    icon: Zap,
    title: 'Act',
    description:
      'Spring Boot API Gateway delivers real-time alerts, forecasts, and AI executive summaries to your dashboard. RL agents recommend corrective actions; reports are generated in under 2 minutes.',
    color: 'accent',
  },
]

const colorMap: Record<string, { text: string; border: string; bg: string; connector: string }> = {
  primary: { text: 'text-primary', border: 'border-primary/40', bg: 'bg-primary/10', connector: 'bg-gradient-to-r from-primary to-secondary' },
  secondary: { text: 'text-secondary', border: 'border-secondary/40', bg: 'bg-secondary/10', connector: 'bg-gradient-to-r from-secondary to-accent' },
  accent: { text: 'text-accent', border: 'border-accent/40', bg: 'bg-accent/10', connector: '' },
}

export default function HowItWorks() {
  const ref = useRef<HTMLDivElement>(null)
  const inView = useInView(ref, { once: true, margin: '-100px' })

  return (
    <section id="how-it-works" ref={ref} className="py-24 bg-background">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={inView ? { opacity: 1, y: 0 } : {}}
          transition={{ duration: 0.6 }}
          className="text-center mb-20"
        >
          <span className="inline-block px-3 py-1 text-xs font-semibold rounded-full bg-accent/10 border border-accent/30 text-accent tracking-wide uppercase mb-4">
            Architecture
          </span>
          <h2 className="text-3xl sm:text-4xl font-bold mb-4">
            How <span className="text-gradient">QoSentry</span> works
          </h2>
          <p className="text-muted max-w-2xl mx-auto">
            A three-stage pipeline from raw network telemetry to actionable business intelligence.
          </p>
        </motion.div>

        {/* Timeline */}
        <div className="relative">
          {/* Connector line — desktop */}
          <div className="hidden lg:block absolute top-16 left-[16.66%] right-[16.66%] h-0.5 bg-border/50 z-0">
            <motion.div
              initial={{ width: '0%' }}
              animate={inView ? { width: '100%' } : {}}
              transition={{ duration: 1.2, delay: 0.4, ease: 'easeInOut' }}
              className="h-full bg-gradient-to-r from-primary via-secondary to-accent"
            />
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-3 gap-10 lg:gap-8 relative z-10">
            {steps.map((step, i) => {
              const Icon = step.icon
              const c = colorMap[step.color]

              return (
                <motion.div
                  key={step.number}
                  initial={{ opacity: 0, y: 50 }}
                  animate={inView ? { opacity: 1, y: 0 } : {}}
                  transition={{ duration: 0.7, delay: 0.2 + i * 0.2 }}
                  className="flex flex-col items-center text-center"
                >
                  {/* Icon circle */}
                  <div
                    className={`relative w-16 h-16 rounded-full ${c.bg} border-2 ${c.border} flex items-center justify-center mb-6 shadow-lg`}
                  >
                    <Icon className={`w-7 h-7 ${c.text}`} />
                    <span className={`absolute -top-2 -right-2 text-xs font-mono font-bold ${c.text} bg-background px-1 rounded`}>
                      {step.number}
                    </span>
                  </div>

                  <h3 className="text-xl font-bold text-text-main mb-3">{step.title}</h3>
                  <p className="text-muted text-sm leading-relaxed max-w-xs">{step.description}</p>

                  {/* Mobile connector */}
                  {i < steps.length - 1 && (
                    <div className="lg:hidden w-0.5 h-8 bg-gradient-to-b from-border to-transparent mt-8" />
                  )}
                </motion.div>
              )
            })}
          </div>
        </div>
      </div>
    </section>
  )
}
