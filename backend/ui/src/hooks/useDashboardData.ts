import { useCallback, useEffect, useState } from 'react'
import { apiFetch } from '../api'
import type { DashboardResponse } from '../types'

export const useDashboardData = (authVersion = '') => {
  const [dashboard, setDashboard] = useState<DashboardResponse | null>(null)
  const [dashboardLoading, setDashboardLoading] = useState(false)
  const [dashboardError, setDashboardError] = useState<string | null>(null)

  const fetchDashboard = useCallback(async () => {
    if (!authVersion) {
      setDashboard(null)
      setDashboardError(null)
      return undefined
    }
    setDashboardLoading(true)
    setDashboardError(null)
    try {
      const response = await apiFetch('/dashboard')
      if (!response.ok) {
        throw new Error('Failed to load dashboard')
      }
      const payload = (await response.json()) as DashboardResponse
      setDashboard(payload)
      return payload
    } catch (err) {
      setDashboardError((err as Error).message)
      setDashboard(null)
      return undefined
    } finally {
      setDashboardLoading(false)
    }
  }, [authVersion])

  useEffect(() => {
    fetchDashboard()
  }, [fetchDashboard])

  return { dashboard, dashboardLoading, dashboardError, fetchDashboard }
}

export default useDashboardData
