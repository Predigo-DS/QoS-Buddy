'use client'

import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { motion } from 'framer-motion'
import {
  Activity, LogOut, Users, Shield, Trash2,
  ChevronUp, ChevronDown, Loader2, AlertCircle, RefreshCw,
} from 'lucide-react'
import { isAuthenticated, getUsername, getRole } from '@/lib/auth'
import { useAuth } from '@/hooks/useAuth'
import { getAdminUsers, updateUserRole, deleteUser, UserDto } from '@/lib/api'

export default function AdminPage() {
  const router = useRouter()
  const { logout } = useAuth()
  const [users, setUsers] = useState<UserDto[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [updatingId, setUpdatingId] = useState<string | null>(null)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [mounted, setMounted] = useState(false)
  const [search, setSearch] = useState('')

  const fetchUsers = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await getAdminUsers()
      setUsers(data)
    } catch {
      setError('Failed to load users.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!isAuthenticated()) { router.replace('/login'); return }
    if (getRole() !== 'ADMIN') { router.replace('/dashboard'); return }
    setMounted(true)
    fetchUsers()
  }, [router, fetchUsers])

  const handleRoleToggle = async (user: UserDto) => {
    const newRole = user.role === 'ADMIN' ? 'USER' : 'ADMIN'
    setUpdatingId(user.id)
    try {
      const updated = await updateUserRole(user.id, newRole)
      setUsers(prev => prev.map(u => u.id === updated.id ? updated : u))
    } catch {
      setError('Failed to update role.')
    } finally {
      setUpdatingId(null)
    }
  }

  const handleDelete = async (user: UserDto) => {
    if (!confirm(`Delete user "${user.username}"? This cannot be undone.`)) return
    setDeletingId(user.id)
    try {
      await deleteUser(user.id)
      setUsers(prev => prev.filter(u => u.id !== user.id))
    } catch {
      setError('Failed to delete user.')
    } finally {
      setDeletingId(null)
    }
  }

  const filtered = users.filter(
    u =>
      u.username.toLowerCase().includes(search.toLowerCase()) ||
      u.email.toLowerCase().includes(search.toLowerCase())
  )

  const adminCount = users.filter(u => u.role === 'ADMIN').length
  const userCount = users.filter(u => u.role === 'USER').length

  if (!mounted) {
    return (
      <div className="min-h-screen bg-background flex items-center justify-center">
        <div className="w-8 h-8 rounded-full border-2 border-primary border-t-transparent animate-spin" />
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-background">
      {/* Header */}
      <header className="border-b border-border bg-surface/40 backdrop-blur-md sticky top-0 z-10">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-primary to-secondary flex items-center justify-center">
              <Activity className="w-4 h-4 text-white" />
            </div>
            <span className="font-bold text-gradient">QoSentry</span>
            <span className="text-border mx-2">|</span>
            <span className="text-sm text-muted font-medium">Admin Panel</span>
          </div>
          <div className="flex items-center gap-4">
            <span className="text-sm text-muted hidden sm:block">
              Signed in as <span className="text-primary font-semibold">{getUsername()}</span>
            </span>
            <button
              onClick={() => router.push('/dashboard')}
              className="text-sm text-muted hover:text-text-main transition-colors px-3 py-2 rounded-lg hover:bg-surface"
            >
              Dashboard
            </button>
            <button
              onClick={logout}
              className="flex items-center gap-2 text-sm text-muted hover:text-text-main transition-colors px-3 py-2 rounded-lg hover:bg-surface"
            >
              <LogOut className="w-4 h-4" />
              Sign out
            </button>
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-10">
        <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.5 }}>

          {/* Title */}
          <div className="mb-8">
            <h1 className="text-3xl font-bold text-text-main">User Management</h1>
            <p className="text-muted text-sm mt-1">Manage accounts, roles and permissions</p>
          </div>

          {/* Stats */}
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-8">
            {[
              { label: 'Total Users', value: users.length, icon: Users, color: 'text-primary', bg: 'bg-primary/10' },
              { label: 'Admins', value: adminCount, icon: Shield, color: 'text-secondary', bg: 'bg-secondary/10' },
              { label: 'Regular Users', value: userCount, icon: Users, color: 'text-accent', bg: 'bg-accent/10' },
            ].map((s, i) => {
              const Icon = s.icon
              return (
                <motion.div
                  key={s.label}
                  initial={{ opacity: 0, y: 20 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: i * 0.1 }}
                  className="glass rounded-2xl p-5 border border-border flex items-center gap-4"
                >
                  <div className={`p-3 rounded-xl ${s.bg}`}>
                    <Icon className={`w-5 h-5 ${s.color}`} />
                  </div>
                  <div>
                    <p className="text-2xl font-bold text-text-main">{s.value}</p>
                    <p className="text-xs text-muted">{s.label}</p>
                  </div>
                </motion.div>
              )
            })}
          </div>

          {/* Error */}
          {error && (
            <div className="flex items-center gap-3 bg-danger/10 border border-danger/30 rounded-xl p-4 mb-6">
              <AlertCircle className="w-4 h-4 text-danger shrink-0" />
              <p className="text-danger text-sm">{error}</p>
              <button onClick={() => setError(null)} className="ml-auto text-danger/60 hover:text-danger text-xs">Dismiss</button>
            </div>
          )}

          {/* Table card */}
          <div className="glass rounded-2xl border border-border overflow-hidden">
            {/* Toolbar */}
            <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 p-5 border-b border-border">
              <input
                type="text"
                placeholder="Search by username or email…"
                value={search}
                onChange={e => setSearch(e.target.value)}
                className="w-full sm:w-72 bg-background border border-border rounded-xl px-4 py-2 text-sm text-text-main placeholder-muted/50 outline-none focus:ring-2 focus:ring-primary/50 focus:border-primary transition-all"
              />
              <button
                onClick={fetchUsers}
                disabled={loading}
                className="flex items-center gap-2 text-sm text-muted hover:text-text-main transition-colors px-3 py-2 rounded-lg hover:bg-surface border border-border"
              >
                <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
                Refresh
              </button>
            </div>

            {/* Table */}
            {loading ? (
              <div className="flex items-center justify-center py-20 gap-3 text-muted">
                <Loader2 className="w-5 h-5 animate-spin" />
                Loading users…
              </div>
            ) : filtered.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-20 text-muted gap-2">
                <Users className="w-8 h-8 opacity-30" />
                <p className="text-sm">No users found</p>
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-border text-left">
                      <th className="px-6 py-3 text-xs font-semibold text-muted uppercase tracking-wide">Username</th>
                      <th className="px-6 py-3 text-xs font-semibold text-muted uppercase tracking-wide">Email</th>
                      <th className="px-6 py-3 text-xs font-semibold text-muted uppercase tracking-wide">Role</th>
                      <th className="px-6 py-3 text-xs font-semibold text-muted uppercase tracking-wide">Joined</th>
                      <th className="px-6 py-3 text-xs font-semibold text-muted uppercase tracking-wide">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border/50">
                    {filtered.map(user => (
                      <tr key={user.id} className="hover:bg-surface/30 transition-colors">
                        <td className="px-6 py-4 font-medium text-text-main">{user.username}</td>
                        <td className="px-6 py-4 text-muted">{user.email}</td>
                        <td className="px-6 py-4">
                          <span className={`inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-semibold border ${
                            user.role === 'ADMIN'
                              ? 'bg-secondary/10 text-secondary border-secondary/30'
                              : 'bg-primary/10 text-primary border-primary/30'
                          }`}>
                            {user.role === 'ADMIN' ? <Shield className="w-3 h-3" /> : <Users className="w-3 h-3" />}
                            {user.role}
                          </span>
                        </td>
                        <td className="px-6 py-4 text-muted text-xs font-mono">
                          {user.createdAt ? new Date(user.createdAt).toLocaleDateString() : '—'}
                        </td>
                        <td className="px-6 py-4">
                          <div className="flex items-center gap-2">
                            {/* Toggle role */}
                            <button
                              onClick={() => handleRoleToggle(user)}
                              disabled={updatingId === user.id}
                              title={user.role === 'ADMIN' ? 'Demote to USER' : 'Promote to ADMIN'}
                              className={`p-1.5 rounded-lg border transition-all ${
                                user.role === 'ADMIN'
                                  ? 'border-secondary/30 text-secondary hover:bg-secondary/10'
                                  : 'border-primary/30 text-primary hover:bg-primary/10'
                              } disabled:opacity-40`}
                            >
                              {updatingId === user.id
                                ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                                : user.role === 'ADMIN'
                                ? <ChevronDown className="w-3.5 h-3.5" />
                                : <ChevronUp className="w-3.5 h-3.5" />}
                            </button>

                            {/* Delete */}
                            <button
                              onClick={() => handleDelete(user)}
                              disabled={deletingId === user.id || user.username === getUsername()}
                              title={user.username === getUsername() ? "Can't delete yourself" : 'Delete user'}
                              className="p-1.5 rounded-lg border border-danger/30 text-danger hover:bg-danger/10 transition-all disabled:opacity-30"
                            >
                              {deletingId === user.id
                                ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                                : <Trash2 className="w-3.5 h-3.5" />}
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {/* Footer */}
            {!loading && filtered.length > 0 && (
              <div className="px-6 py-3 border-t border-border text-xs text-muted">
                Showing {filtered.length} of {users.length} users
              </div>
            )}
          </div>
        </motion.div>
      </main>
    </div>
  )
}
