'use client'

import { useState } from 'react'
import Link from 'next/link'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { motion } from 'framer-motion'
import { Eye, EyeOff, Activity, Loader2, AlertCircle } from 'lucide-react'
import { loginApi } from '@/lib/api'
import { useAuth } from '@/hooks/useAuth'
import axios from 'axios'

const schema = z.object({
  username: z.string().min(3, 'Username must be at least 3 characters'),
  password: z.string().min(6, 'Password must be at least 6 characters'),
})

type FormData = z.infer<typeof schema>

export default function LoginForm() {
  const { login } = useAuth()
  const [showPassword, setShowPassword] = useState(false)
  const [globalError, setGlobalError] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)

  const {
    register,
    handleSubmit,
    formState: { errors, isValid },
  } = useForm<FormData>({
    resolver: zodResolver(schema),
    mode: 'onBlur',
  })

  const onSubmit = async (data: FormData) => {
    setGlobalError(null)
    setIsSubmitting(true)
    try {
      const response = await loginApi(data)
      login(response.token, response.username, response.role)
    } catch (err) {
      if (axios.isAxiosError(err)) {
        if (err.response?.status === 401) {
          setGlobalError('Invalid username or password.')
        } else if (!err.response) {
          setGlobalError('Cannot connect to server. Make sure the backend is running.')
        } else {
          setGlobalError('An unexpected error occurred. Please try again.')
        }
      } else {
        setGlobalError('Cannot connect to server. Make sure the backend is running.')
      }
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 40 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.6, ease: 'easeOut' }}
      className="w-full max-w-md"
    >
      {/* Logo */}
      <div className="flex flex-col items-center mb-8">
        <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-primary to-secondary flex items-center justify-center mb-4 shadow-lg shadow-primary/30">
          <Activity className="w-6 h-6 text-white" />
        </div>
        <h1 className="text-2xl font-bold text-text-main">Welcome back</h1>
        <p className="text-muted text-sm mt-1">Sign in to QoSentry</p>
      </div>

      <div className="glass rounded-2xl p-8">
        {/* Global error */}
        {globalError && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            className="flex items-start gap-3 bg-danger/10 border border-danger/30 rounded-xl p-4 mb-6"
          >
            <AlertCircle className="w-4 h-4 text-danger mt-0.5 shrink-0" />
            <p className="text-danger text-sm">{globalError}</p>
          </motion.div>
        )}

        <form onSubmit={handleSubmit(onSubmit)} noValidate className="space-y-5">
          {/* Username */}
          <div>
            <label className="block text-sm font-medium text-text-main mb-1.5">
              Username
            </label>
            <input
              {...register('username')}
              type="text"
              autoComplete="username"
              placeholder="your_username"
              className={`w-full bg-background border rounded-xl px-4 py-3 text-text-main placeholder-muted/50 text-sm outline-none transition-all focus:ring-2 focus:ring-primary/50 ${
                errors.username ? 'border-danger' : 'border-border focus:border-primary'
              }`}
            />
            {errors.username && (
              <p className="mt-1.5 text-xs text-danger flex items-center gap-1">
                <AlertCircle className="w-3 h-3" />
                {errors.username.message}
              </p>
            )}
          </div>

          {/* Password */}
          <div>
            <label className="block text-sm font-medium text-text-main mb-1.5">
              Password
            </label>
            <div className="relative">
              <input
                {...register('password')}
                type={showPassword ? 'text' : 'password'}
                autoComplete="current-password"
                placeholder="••••••••"
                className={`w-full bg-background border rounded-xl px-4 py-3 pr-11 text-text-main placeholder-muted/50 text-sm outline-none transition-all focus:ring-2 focus:ring-primary/50 ${
                  errors.password ? 'border-danger' : 'border-border focus:border-primary'
                }`}
              />
              <button
                type="button"
                onClick={() => setShowPassword(!showPassword)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-muted hover:text-text-main transition-colors"
                aria-label={showPassword ? 'Hide password' : 'Show password'}
              >
                {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
              </button>
            </div>
            {errors.password && (
              <p className="mt-1.5 text-xs text-danger flex items-center gap-1">
                <AlertCircle className="w-3 h-3" />
                {errors.password.message}
              </p>
            )}
          </div>

          {/* Submit */}
          <button
            type="submit"
            disabled={!isValid || isSubmitting}
            className="w-full flex items-center justify-center gap-2 bg-gradient-to-r from-primary to-secondary text-white font-semibold py-3 rounded-xl transition-all duration-200 hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed mt-2"
          >
            {isSubmitting ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                Signing in…
              </>
            ) : (
              'Sign in'
            )}
          </button>
        </form>

        <p className="text-center text-sm text-muted mt-6">
          Don&apos;t have an account?{' '}
          <Link href="/register" className="text-primary hover:text-primary/80 font-medium transition-colors">
            Create one
          </Link>
        </p>
      </div>
    </motion.div>
  )
}
