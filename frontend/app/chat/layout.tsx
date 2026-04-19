'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import { Activity, Database } from 'lucide-react'
import { NuqsAdapter } from 'nuqs/adapters/next/app'
import { isAuthenticated } from '@/lib/auth'
import { BackendReadinessGate } from '@/components/backend-readiness-gate'
import './chat-theme.css'

export default function ChatLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter()
  const pathname = usePathname()
  const [mounted, setMounted] = useState(false)

  useEffect(() => {
    if (!isAuthenticated()) {
      router.replace('/login')
      return
    }
    setMounted(true)
  }, [router])

  if (!mounted) {
    return (
      <div className="min-h-screen bg-background flex items-center justify-center">
        <div className="w-8 h-8 rounded-full border-2 border-primary border-t-transparent animate-spin" />
      </div>
    )
  }

  return (
    <NuqsAdapter>
      <BackendReadinessGate>
        <div className="chat-theme min-h-screen bg-background text-text-main">
          <header className="sticky top-0 z-40 border-b border-border bg-surface/70 backdrop-blur-md">
            <div className="mx-auto flex h-14 w-full max-w-7xl items-center justify-between px-4 sm:px-6 lg:px-8">
              <Link href="/dashboard" className="flex items-center gap-3 group">
                <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-primary to-secondary flex items-center justify-center group-hover:scale-105 transition-transform">
                  <Activity className="w-4 h-4 text-white" />
                </div>
                <span className="font-bold text-gradient">QoSentry</span>
              </Link>
              <div className="flex items-center gap-2">
                {pathname !== '/chat/documents' && (
                  <Link
                    href="/chat/documents"
                    className="inline-flex items-center gap-2 rounded-lg border border-border px-3 py-1.5 text-xs font-medium text-muted hover:text-text-main hover:bg-surface"
                  >
                    <Database className="h-3.5 w-3.5" />
                    Documents
                  </Link>
                )}
              </div>
            </div>
          </header>
          {children}
        </div>
      </BackendReadinessGate>
    </NuqsAdapter>
  )
}
