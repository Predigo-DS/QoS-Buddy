import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: 'QoSentry — AI-Native Network Intelligence Platform',
  description:
    'QoSentry combines SDN telemetry, machine learning, and generative AI to predict SLA breaches, detect anomalies, and generate executive reports.',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className="min-h-screen bg-background text-text-main antialiased">{children}</body>
    </html>
  )
}
