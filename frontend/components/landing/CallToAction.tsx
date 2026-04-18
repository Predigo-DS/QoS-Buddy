'use client'

import { useRef } from 'react'
import Link from 'next/link'
import { motion, useInView } from 'framer-motion'
import { ArrowRight } from 'lucide-react'

export default function CallToAction() {
  const ref = useRef<HTMLDivElement>(null)
  const inView = useInView(ref, { once: true, margin: '-80px' })

  return (
    <section ref={ref} className="py-24 relative overflow-hidden">
      {/* Animated gradient background */}
      <motion.div
        initial={{ opacity: 0 }}
        animate={inView ? { opacity: 1 } : {}}
        transition={{ duration: 1 }}
        className="absolute inset-0 bg-gradient-to-br from-primary/20 via-secondary/20 to-accent/10 animate-gradient-shift bg-[length:300%_300%]"
      />

      {/* Grid pattern overlay */}
      <div
        className="absolute inset-0 opacity-10"
        style={{
          backgroundImage: `linear-gradient(rgba(14,165,233,0.3) 1px, transparent 1px),
            linear-gradient(90deg, rgba(14,165,233,0.3) 1px, transparent 1px)`,
          backgroundSize: '60px 60px',
        }}
      />

      <div className="relative z-10 max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 text-center">
        <motion.div
          initial={{ opacity: 0, y: 30 }}
          animate={inView ? { opacity: 1, y: 0 } : {}}
          transition={{ duration: 0.7 }}
        >
          <h2 className="text-3xl sm:text-4xl lg:text-5xl font-extrabold mb-6 text-text-main">
            Ready to eliminate{' '}
            <span className="text-gradient">alert fatigue?</span>
          </h2>
          <p className="text-lg text-muted mb-8 max-w-2xl mx-auto leading-relaxed">
            Join network engineers and IT managers who have reduced alert volume by over 60% with
            QoSentry&apos;s AI-native approach. From raw telemetry to executive insight — in under
            5 minutes.
          </p>

          <motion.div
            initial={{ opacity: 0, scale: 0.9 }}
            animate={inView ? { opacity: 1, scale: 1 } : {}}
            transition={{ duration: 0.5, delay: 0.3 }}
          >
            <Link
              href="/register"
              className="group inline-flex items-center gap-3 bg-gradient-to-r from-primary to-secondary text-white font-bold px-10 py-4 rounded-xl text-lg hover:opacity-90 hover:scale-105 transition-all duration-200 shadow-2xl shadow-primary/30"
            >
              Start for free
              <ArrowRight className="w-5 h-5 group-hover:translate-x-1 transition-transform" />
            </Link>
          </motion.div>

          <p className="text-xs text-muted mt-4">No credit card required · Deploy in minutes</p>
        </motion.div>
      </div>
    </section>
  )
}
