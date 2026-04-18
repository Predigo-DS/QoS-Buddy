'use client'

import { useState } from 'react'
import Link from 'next/link'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { motion } from 'framer-motion'
import { Eye, EyeOff, Activity, Loader2, AlertCircle, CheckCircle2 } from 'lucide-react'
import { registerApi } from '@/lib/api'
import { useAuth } from '@/hooks/useAuth'
import axios from 'axios'

const schema = z
  .object({
    username: z
      .string()
      .min(3, 'Username must be at least 3 characters')
      .max(20, 'Username must be at most 20 characters')
      .regex(/^[a-zA-Z0-9_]+$/, 'Only letters, numbers, and underscores allowed'),
    email: z.string().email('Please enter a valid email address'),
    password: z
      .string()
      .min(8, 'Password must be at least 8 characters')
      .regex(/[A-Z]/, 'Must contain at least one uppercase letter')
      .regex(/[0-9]/, 'Must contain at least one number')
      .regex(/[^a-zA-Z0-9]/, 'Must contain at least one special character'),
    confirmPassword: z.string(),
  })
  .refine((d) => d.password === d.confirmPassword, {
    path: ['confirmPassword'],
    message: 'Passwords do not match',
  })

type FormData = z.infer<typeof schema>

function getPasswordStrength(pw: string): { label: string; score: number; color: string } {
  let score = 0
  if (pw.length >= 8) score++
  if (/[A-Z]/.test(pw)) score++
  if (/[0-9]/.test(pw)) score++
  if (/[^a-zA-Z0-9]/.test(pw)) score++
  if (pw.length >= 12) score++

  if (score <= 2) return { label: 'Weak', score, color: 'bg-danger' }
  if (score <= 3) return { label: 'Medium', score, color: 'bg-yellow-400' }
  return { label: 'Strong', score, color: 'bg-accent' }
}

export default function RegisterForm() {
  const { login } = useAuth()
  const [showPassword, setShowPassword] = useState(false)
  const [showConfirm, setShowConfirm] = useState(false)
  const [globalError, setGlobalError] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)

  const {
    register,
    handleSubmit,
    watch,
    setError,
    formState: { errors, isValid },
  } = useForm<FormData>({
    resolver: zodResolver(schema),
    mode: 'onChange',
  })

  const password = watch('password', '')
  const strength = getPasswordStrength(password)

  const onSubmit = async (data: FormData) => {
    setGlobalError(null)
    setIsSubmitting(true)
    try {
      const response = await registerApi({
        username: data.username,
        email: data.email,
        password: data.password,
      })
      login(response.token, response.username, response.role)
    } catch (err) {
      if (axios.isAxiosError(err)) {
        const msg: string = err.response?.data?.message ?? err.response?.data ?? ''
        if (err.response?.status === 400) {
          if (typeof msg === 'string' && msg.toLowerCase().includes('username')) {
            setError('username', { message: 'Username is already taken' })
          } else if (typeof msg === 'string' && msg.toLowerCase().includes('email')) {
            setError('email', { message: 'Email is already in use' })
          } else {
            setGlobalError('Registration failed. Please check your details.')
          }
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
        <h1 className="text-2xl font-bold text-text-main">Create your account</h1>
        <p className="text-muted text-sm mt-1">Join QoSentry today</p>
      </div>

      <div className="glass rounded-2xl p-8">
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
            <label className="block text-sm font-medium text-text-main mb-1.5">Username</label>
            <input
              {...register('username')}
              type="text"
              autoComplete="username"
              placeholder="john_doe"
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

          {/* Email */}
          <div>
            <label className="block text-sm font-medium text-text-main mb-1.5">Email</label>
            <input
              {...register('email')}
              type="email"
              autoComplete="email"
              placeholder="john@example.com"
              className={`w-full bg-background border rounded-xl px-4 py-3 text-text-main placeholder-muted/50 text-sm outline-none transition-all focus:ring-2 focus:ring-primary/50 ${
                errors.email ? 'border-danger' : 'border-border focus:border-primary'
              }`}
            />
            {errors.email && (
              <p className="mt-1.5 text-xs text-danger flex items-center gap-1">
                <AlertCircle className="w-3 h-3" />
                {errors.email.message}
              </p>
            )}
          </div>

          {/* Password */}
          <div>
            <label className="block text-sm font-medium text-text-main mb-1.5">Password</label>
            <div className="relative">
              <input
                {...register('password')}
                type={showPassword ? 'text' : 'password'}
                autoComplete="new-password"
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

            {/* Strength indicator */}
            {password.length > 0 && (
              <div className="mt-2">
                <div className="flex gap-1 mb-1">
                  {[1, 2, 3, 4, 5].map((s) => (
                    <div
                      key={s}
                      className={`h-1 flex-1 rounded-full transition-all duration-300 ${
                        strength.score >= s ? strength.color : 'bg-border'
                      }`}
                    />
                  ))}
                </div>
                <p className="text-xs text-muted">
                  Password strength:{' '}
                  <span
                    className={
                      strength.label === 'Strong'
                        ? 'text-accent'
                        : strength.label === 'Medium'
                        ? 'text-yellow-400'
                        : 'text-danger'
                    }
                  >
                    {strength.label}
                  </span>
                </p>
              </div>
            )}

            {/* Rules */}
            <ul className="mt-2 space-y-1">
              {[
                { test: password.length >= 8, label: 'At least 8 characters' },
                { test: /[A-Z]/.test(password), label: '1 uppercase letter' },
                { test: /[0-9]/.test(password), label: '1 number' },
                { test: /[^a-zA-Z0-9]/.test(password), label: '1 special character' },
              ].map((rule) => (
                <li key={rule.label} className="flex items-center gap-1.5 text-xs">
                  <CheckCircle2
                    className={`w-3 h-3 transition-colors ${rule.test ? 'text-accent' : 'text-border'}`}
                  />
                  <span className={rule.test ? 'text-muted' : 'text-border'}>{rule.label}</span>
                </li>
              ))}
            </ul>

            {errors.password && (
              <p className="mt-1.5 text-xs text-danger flex items-center gap-1">
                <AlertCircle className="w-3 h-3" />
                {errors.password.message}
              </p>
            )}
          </div>

          {/* Confirm password */}
          <div>
            <label className="block text-sm font-medium text-text-main mb-1.5">
              Confirm password
            </label>
            <div className="relative">
              <input
                {...register('confirmPassword')}
                type={showConfirm ? 'text' : 'password'}
                autoComplete="new-password"
                placeholder="••••••••"
                className={`w-full bg-background border rounded-xl px-4 py-3 pr-11 text-text-main placeholder-muted/50 text-sm outline-none transition-all focus:ring-2 focus:ring-primary/50 ${
                  errors.confirmPassword ? 'border-danger' : 'border-border focus:border-primary'
                }`}
              />
              <button
                type="button"
                onClick={() => setShowConfirm(!showConfirm)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-muted hover:text-text-main transition-colors"
                aria-label={showConfirm ? 'Hide password' : 'Show password'}
              >
                {showConfirm ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
              </button>
            </div>
            {errors.confirmPassword && (
              <p className="mt-1.5 text-xs text-danger flex items-center gap-1">
                <AlertCircle className="w-3 h-3" />
                {errors.confirmPassword.message}
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
                Creating account…
              </>
            ) : (
              'Create account'
            )}
          </button>
        </form>

        <p className="text-center text-sm text-muted mt-6">
          Already have an account?{' '}
          <Link href="/login" className="text-primary hover:text-primary/80 font-medium transition-colors">
            Sign in
          </Link>
        </p>
      </div>
    </motion.div>
  )
}
