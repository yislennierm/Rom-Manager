import { useCallback, useEffect, useState } from 'react'
import type { ModuleEntry } from '../types'

export const useModulesData = () => {
  const [modulesData, setModulesData] = useState<ModuleEntry[]>([])
  const [modulesLoading, setModulesLoading] = useState(false)

  const fetchModulesPayload = useCallback(async () => {
    setModulesLoading(true)
    try {
      const response = await fetch('/update?target=modules')
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
  }, [])

  useEffect(() => {
    fetchModulesPayload()
  }, [fetchModulesPayload])

  return { modulesData, setModulesData, modulesLoading, fetchModulesPayload }
}

export default useModulesData
