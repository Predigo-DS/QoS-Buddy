'use client'

import { useState, useEffect, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import {
  saveToken,
  getToken,
  removeToken,
  isAuthenticated,
  getUsername,
  getRole,
  saveUserInfo,
} from '@/lib/auth'

interface AuthState {
  isAuthenticated: boolean
  username: string | null
  role: string | null
}

interface UseAuthReturn extends AuthState {
  login: (token: string, username: string, role: string) => void
  logout: () => void
}

export function useAuth(): UseAuthReturn {
  const router = useRouter()
  const [authState, setAuthState] = useState<AuthState>({
    isAuthenticated: false,
    username: null,
    role: null,
  })

  useEffect(() => {
    setAuthState({
      isAuthenticated: isAuthenticated(),
      username: getUsername(),
      role: getRole(),
    })
  }, [])

  const login = useCallback(
    (token: string, username: string, role: string) => {
      saveToken(token)
      saveUserInfo(username, role)
      setAuthState({ isAuthenticated: true, username, role })
      router.push('/dashboard')
    },
    [router]
  )

  const logout = useCallback(() => {
    removeToken()
    setAuthState({ isAuthenticated: false, username: null, role: null })
    router.push('/login')
  }, [router])

  return { ...authState, login, logout }
}
