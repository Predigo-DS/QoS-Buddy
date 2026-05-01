import axios from 'axios'
import { getToken, removeToken } from './auth'

const BASE_URL = 'http://localhost:8081'

export const apiClient = axios.create({
  baseURL: BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
})

apiClient.interceptors.request.use((config) => {
  const token = getToken()
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      removeToken()
      if (typeof window !== 'undefined') {
        window.location.href = '/login'
      }
    }
    return Promise.reject(error)
  }
)

export interface AuthResponse {
  token: string
  username: string
  role: string
}

export interface LoginRequest {
  username: string
  password: string
}

export interface RegisterRequest {
  username: string
  email: string
  password: string
}

export async function loginApi(data: LoginRequest): Promise<AuthResponse> {
  const response = await apiClient.post<AuthResponse>('/api/auth/login', data)
  return response.data
}

export async function registerApi(data: RegisterRequest): Promise<AuthResponse> {
  const response = await apiClient.post<AuthResponse>('/api/auth/register', data)
  return response.data
}

export interface UserDto {
  id: string
  username: string
  email: string
  role: string
  createdAt: string
}

export async function getAdminUsers(): Promise<UserDto[]> {
  const response = await apiClient.get<UserDto[]>('/api/admin/users')
  return response.data
}

export async function updateUserRole(id: string, role: string): Promise<UserDto> {
  const response = await apiClient.put<UserDto>(`/api/admin/users/${id}/role`, { role })
  return response.data
}

export async function deleteUser(id: string): Promise<void> {
  await apiClient.delete(`/api/admin/users/${id}`)
}

export interface AnomalyMetadataResponse {
  model_type: string
  window_size: number
  features: string[]
  thresholds?: Record<string, number>
}

export interface AnomalyWindowPrediction {
  window_index: number
  start_row: number
  end_row: number
  reconstruction_score: number
  threshold_used: number
  is_anomaly: boolean
}

export interface AnomalyPredictRequest {
  rows: Record<string, number | string | boolean>[]
  stride?: number
  threshold_name?: string
}

export interface AnomalyPredictResponse {
  model_type: string
  window_size: number
  stride: number
  threshold_name: string
  threshold_value: number
  total_windows: number
  anomaly_windows: number
  windows: AnomalyWindowPrediction[]
}

export async function getAnomalyMetadata(): Promise<AnomalyMetadataResponse> {
  const response = await apiClient.get<AnomalyMetadataResponse>('/api/ai/anomaly/metadata')
  return response.data
}

export async function predictAnomaly(data: AnomalyPredictRequest): Promise<AnomalyPredictResponse> {
  const response = await apiClient.post<AnomalyPredictResponse>('/api/ai/anomaly/predict', data)
  return response.data
}

export interface SlaRunSegmentKey {
  run_id: string
  segment: string
}

export interface SlaMetadataResponse {
  model_type?: string
  window_size: number
  horizon?: number
  run_segment_keys: SlaRunSegmentKey[]
  class_names?: string[]
}

export interface SlaPredictRequest {
  run_id: string
  segment: string
  rows: Record<string, number | string | boolean>[]
  use_all_windows?: boolean
  stride?: number
  sla_alert_threshold?: number
}

export interface SlaPredictionItem {
  window_index: number
  start_row: number
  end_row: number
  predicted_class: string
  predicted_class_index: number
  probabilities: Record<string, number>
  sla_risk_score: number
  sla_alert: boolean
}

export interface SlaPredictResponse {
  run_id?: string
  segment?: string
  window_size?: number
  predictions: SlaPredictionItem[]
  alert_count?: number
  alert_rate?: number
  sla_alert_threshold?: number
}

export async function getSlaMetadata(): Promise<SlaMetadataResponse> {
  const response = await apiClient.get<SlaMetadataResponse>('/api/ai/sla/metadata')
  return response.data
}

export async function predictSla(data: SlaPredictRequest): Promise<SlaPredictResponse> {
  const response = await apiClient.post<SlaPredictResponse>('/api/ai/sla/predict', data)
  return response.data
}

export interface ToolTraceEntry {
  tool: string
  args: Record<string, unknown>
  result: Record<string, unknown> | unknown
}

export interface OptimizationDecision {
  decision_summary: string
  recommended_actions: string[]
  confidence: number
  risk_level: string
  [key: string]: unknown
}

export interface OptimizationTelemetrySummary {
  row_count: number
  window_seconds: number
  avg_metrics: Record<string, number>
  [key: string]: unknown
}

export interface OptimizationResponse {
  telemetry_summary: OptimizationTelemetrySummary
  anomaly_response: Record<string, unknown>
  sla_response: Record<string, unknown>
  optimization_decision: OptimizationDecision | null
  tool_trace: ToolTraceEntry[]
  mock_mode: boolean
  [key: string]: unknown
}

export async function runMockOptimization(): Promise<OptimizationResponse> {
  const response = await apiClient.post<OptimizationResponse>('/api/ai/optimize/mock')
  return response.data
}

export function getApiErrorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const status = error.response?.status
    const data = error.response?.data as Record<string, unknown> | string | undefined
    if (typeof data === 'string' && data.trim()) return data
    if (data && typeof data === 'object') {
      const message = data.message || data.error || data.detail
      if (typeof message === 'string' && message.trim()) return message
    }
    if (status) return `HTTP ${status}`
    return 'Network error'
  }
  if (error instanceof Error && error.message) return error.message
  return 'Unexpected error'
}

export interface TelemetryStatus {
  buffer_size: number
  live_mode: boolean
}

export async function getTelemetryStatus(): Promise<TelemetryStatus> {
  const response = await apiClient.get<TelemetryStatus>('/api/telemetry/status')
  return response.data
}

export async function getLatestTelemetry(n = 60): Promise<Record<string, number | string | boolean>[]> {
  const response = await apiClient.get<Record<string, number | string | boolean>[]>(`/api/telemetry/latest?n=${n}`)
  return response.data
}
