import { useCallback, useEffect, useState } from 'react'
import { apiFetch } from '../api'
import type { ProvidersResponse } from '../types'

export const useProvidersData = (authVersion = '') => {
  const [providerData, setProviderData] = useState<ProvidersResponse | null>(null)
  const [providerLoading, setProviderLoading] = useState(false)
  const [providerError, setProviderError] = useState<string | null>(null)

  const fetchProviders = useCallback(async () => {
    if (!authVersion) {
      setProviderData(null)
      return undefined
    }
    setProviderLoading(true)
    setProviderError(null)
    try {
      const response = await apiFetch('/update?target=providers')
      if (!response.ok) {
        throw new Error('Failed to load providers payload')
      }
      const payload = (await response.json()) as ProvidersResponse
      setProviderData(payload)
      return payload
    } catch (err) {
      setProviderError((err as Error).message)
      return undefined
    } finally {
      setProviderLoading(false)
    }
  }, [authVersion])

  useEffect(() => {
    fetchProviders()
  }, [authVersion, fetchProviders])

  return { providerData, providerLoading, providerError, fetchProviders }
}

export default useProvidersData
