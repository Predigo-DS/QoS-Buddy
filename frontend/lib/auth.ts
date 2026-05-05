import { jwtDecode } from 'jwt-decode'

const TOKEN_KEY = 'qosentry_token'
const USERNAME_KEY = 'qosentry_username'
const ROLE_KEY = 'qosentry_role'
const PROFILE_ROLE_KEY = 'qosentry_profile_role'

interface JwtPayload {
  sub?: string
  exp?: number
  [key: string]: unknown
}

export function saveToken(token: string): void {
  if (typeof window !== 'undefined') {
    localStorage.setItem(TOKEN_KEY, token)
  }
}

export function getToken(): string | null {
  if (typeof window === 'undefined') return null
  return localStorage.getItem(TOKEN_KEY)
}

export function removeToken(): void {
  if (typeof window !== 'undefined') {
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(USERNAME_KEY)
    localStorage.removeItem(ROLE_KEY)
    localStorage.removeItem(PROFILE_ROLE_KEY)
  }
}

export function isAuthenticated(): boolean {
  const token = getToken()
  if (!token) return false
  try {
    const decoded = jwtDecode<JwtPayload>(token)
    if (!decoded.exp) return true
    return decoded.exp * 1000 > Date.now()
  } catch {
    return false
  }
}

export function getUsername(): string | null {
  if (typeof window === 'undefined') return null
  const stored = localStorage.getItem(USERNAME_KEY)
  if (stored) return stored
  const token = getToken()
  if (!token) return null
  try {
    const decoded = jwtDecode<JwtPayload>(token)
    return decoded.sub ?? null
  } catch {
    return null
  }
}

export function saveUserInfo(username: string, role: string, profileRole?: string): void {
  if (typeof window !== 'undefined') {
    localStorage.setItem(USERNAME_KEY, username)
    localStorage.setItem(ROLE_KEY, role)
    if (profileRole) {
      localStorage.setItem(PROFILE_ROLE_KEY, profileRole)
    }
  }
}

export function getRole(): string | null {
  if (typeof window === 'undefined') return null
  return localStorage.getItem(ROLE_KEY)
}

export function getProfileRole(): string | null {
  if (typeof window === 'undefined') return null
  return localStorage.getItem(PROFILE_ROLE_KEY)
}
