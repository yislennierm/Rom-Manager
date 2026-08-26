import { useCallback, useEffect, useState } from 'react'
import { apiFetch } from '../api'
import type { ModuleEntry } from '../types'

export const useModulesData = (authVersion = '') => {
  const [modulesData, setModulesData] = useState<ModuleEntry[]>([])
  const [modulesLoading, setModulesLoading] = useState(false)

  const fetchModulesPayload = useCallback(async () => {
    if (!authVersion) {
      setModulesData([])
      return
    }
    setModulesLoading(true)
    try {
      const response = await apiFetch('/update?target=modules')
      if (!response.ok) {
        throw new Error('Failed to load modules payload')
      }
      const payload = await response.json()
      if (Array.isArray(payload.modules)) {
        setModulesData(payload.modules as ModuleEntry[])
      }
    } catch (err) {
      console.error('Failed to load modules payload', err)
    } finally {
      setModulesLoading(false)
    }
  }, [authVersion])

  useEffect(() => {
    fetchModulesPayload()
  }, [authVersion, fetchModulesPayload])

  return { modulesData, setModulesData, modulesLoading, fetchModulesPayload }
}

export default useModulesData
