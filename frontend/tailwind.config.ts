import type { Config } from 'tailwindcss'

const config: Config = {
  darkMode: 'class',
  content: [
    './pages/**/*.{js,ts,jsx,tsx,mdx}',
    './components/**/*.{js,ts,jsx,tsx,mdx}',
    './app/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        primary: '#0EA5E9',
        secondary: '#6366F1',
        accent: '#10B981',
        danger: '#EF4444',
        background: '#0F172A',
        surface: '#1E293B',
        border: '#334155',
        'text-main': '#F8FAFC',
        muted: '#94A3B8',
      },
      fontFamily: {
        sans: ['Inter', 'sans-serif'],
        mono: ['JetBrains Mono', 'monospace'],
      },
      animation: {
        'pulse-glow': 'pulseGlow 2s ease-in-out infinite',
        'gradient-shift': 'gradientShift 6s ease infinite',
        'draw-line': 'drawLine 1.5s ease forwards',
      },
      keyframes: {
        pulseGlow: {
          '0%, 100%': { boxShadow: '0 0 5px rgba(14,165,233,0.3)' },
          '50%': { boxShadow: '0 0 20px rgba(14,165,233,0.7), 0 0 40px rgba(14,165,233,0.3)' },
        },
        gradientShift: {
          '0%, 100%': { backgroundPosition: '0% 50%' },
          '50%': { backgroundPosition: '100% 50%' },
        },
        drawLine: {
          '0%': { width: '0%' },
          '100%': { width: '100%' },
        },
      },
      backgroundSize: {
        '300%': '300%',
      },
    },
  },
  plugins: [],
}

export default config
