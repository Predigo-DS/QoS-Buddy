'use client'

import { useRef, useState, useEffect } from 'react'
import { motion, useInView } from 'framer-motion'
import { Database, Shield, Clock, TrendingDown } from 'lucide-react'

const stats = [
  {
    value: 900,
    suffix: 'K+',
    label: 'Telemetry data points',
    icon: Database,
    color: 'primary',
  },
  {
    value: 85,
    suffix: '%+',
    label: 'Anomaly detection accuracy',
    icon: Shield,
    color: 'accent',
  },
  {
    value: 5,
    prefix: '<',
    suffix: ' min',
    label: 'Mean time to detect',
    icon: Clock,
    color: 'secondary',
  },
  {
    value: 60,
    suffix: '%+',
    label: 'Alert volume reduction',
    icon: TrendingDown,
    color: 'danger',
  },
]

const colorMap: Record<string, string> = {
  primary: 'text-primary border-primary/30 group-hover:border-primary/80 group-hover:shadow-[0_0_24px_rgba(14,165,233,0.3)]',
  accent: 'text-accent border-accent/30 group-hover:border-accent/80 group-hover:shadow-[0_0_24px_rgba(16,185,129,0.3)]',
  secondary: 'text-secondary border-secondary/30 group-hover:border-secondary/80 group-hover:shadow-[0_0_24px_rgba(99,102,241,0.3)]',
  danger: 'text-danger border-danger/30 group-hover:border-danger/80 group-hover:shadow-[0_0_24px_rgba(239,68,68,0.3)]',
}

const iconBgMap: Record<string, string> = {
  primary: 'bg-primary/10',
  accent: 'bg-accent/10',
  secondary: 'bg-secondary/10',
  danger: 'bg-danger/10',
}

function AnimatedNumber({ value, prefix = '', suffix = '' }: { value: number; prefix?: string; suffix?: string }) {
  const ref = useRef<HTMLSpanElement>(null)
  const inView = useInView(ref, { once: true })
  const [display, setDisplay] = useState(0)

  useEffect(() => {
    if (!inView) return
    const duration = 2000
    const start = Date.now()
    const tick = () => {
      const elapsed = Date.now() - start
      const progress = Math.min(elapsed / duration, 1)
      const eased = 1 - Math.pow(1 - progress, 3)
      setDisplay(Math.round(eased * value))
      if (progress < 1) requestAnimationFrame(tick)
    }
    requestAnimationFrame(tick)
  }, [inView, value])

  return (
    <span ref={ref} className="font-mono text-4xl font-bold">
      {prefix}{display}{suffix}
    </span>
  )
}

export default function Stats() {
  const sectionRef = useRef<HTMLDivElement>(null)
  const inView = useInView(sectionRef, { once: true, margin: '-100px' })

  return (
    <section id="stats" ref={sectionRef} className="py-24 bg-background">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={inView ? { opacity: 1, y: 0 } : {}}
          transition={{ duration: 0.6 }}
          className="text-center mb-16"
        >
          <h2 className="text-3xl sm:text-4xl font-bold mb-4">
            Platform <span className="text-gradient">Performance</span>
          </h2>
          <p className="text-muted max-w-2xl mx-auto">
            Backed by real experiments on a Mininet-based SDN topology with 900K+ labeled events.
          </p>
        </motion.div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6">
          {stats.map((stat, i) => {
            const Icon = stat.icon
            const colorsClass = colorMap[stat.color]
            const iconBg = iconBgMap[stat.color]

            return (
              <motion.div
                key={stat.label}
                initial={{ opacity: 0, y: 40 }}
                animate={inView ? { opacity: 1, y: 0 } : {}}
                transition={{ duration: 0.6, delay: i * 0.12 }}
                className={`group relative glass rounded-2xl p-6 border transition-all duration-300 cursor-default ${colorsClass}`}
              >
                <div className={`inline-flex p-3 rounded-xl ${iconBg} mb-4`}>
                  <Icon className={`w-6 h-6 ${stat.color === 'primary' ? 'text-primary' : stat.color === 'accent' ? 'text-accent' : stat.color === 'secondary' ? 'text-secondary' : 'text-danger'}`} />
                </div>
                <div className={stat.color === 'primary' ? 'text-primary' : stat.color === 'accent' ? 'text-accent' : stat.color === 'secondary' ? 'text-secondary' : 'text-danger'}>
                  <AnimatedNumber value={stat.value} prefix={stat.prefix} suffix={stat.suffix} />
                </div>
                <p className="text-muted text-sm mt-2 font-medium">{stat.label}</p>
              </motion.div>
            )
          })}
        </div>
      </div>
    </section>
  )
}
