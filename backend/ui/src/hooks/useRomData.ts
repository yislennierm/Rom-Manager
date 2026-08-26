import { useCallback, useEffect, useState } from 'react'
import { apiFetch } from '../api'
import type { RomEntry, RomSetMeta } from '../types'

export const useRomData = (authVersion = '') => {
  const [romSets, setRomSets] = useState<RomSetMeta[]>([])
  const [romMetaLoading, setRomMetaLoading] = useState(false)
  const [romError, setRomError] = useState<string | null>(null)
  const [romEntriesCache, setRomEntriesCache] = useState<Record<string, RomEntry[]>>({})
  const [romEntriesLoading, setRomEntriesLoading] = useState(false)

  const fetchRomMetadata = useCallback(async () => {
    if (!authVersion) {
      setRomSets([])
      return
    }
    setRomMetaLoading(true)
    setRomError(null)
    try {
      const response = await apiFetch('/roms')
      if (!response.ok) {
        throw new Error('Failed to load ROM metadata')
      }
      const payload = await response.json()
      if (Array.isArray(payload.roms)) {
        setRomSets(payload.roms as RomSetMeta[])
      } else {
        setRomSets([])
      }
    } catch (err) {
      setRomError((err as Error).message)
    } finally {
      setRomMetaLoading(false)
    }
  }, [authVersion])

  const fetchRomEntries = useCallback(
    async (meta: RomSetMeta) => {
      const identifier = meta.slug ?? meta.guid
      if (!identifier) {
        return
      }
      const cacheKey = meta.slug || meta.guid || identifier
      if (romEntriesCache[cacheKey]) {
        return
      }
      setRomEntriesLoading(true)
      try {
        const response = await apiFetch(`/roms/${encodeURIComponent(identifier)}`)
        if (!response.ok) {
          throw new Error(`Failed to load ROM data for ${meta.console || meta.module}`)
        }
        const payload = await response.json()
        const entries = Array.isArray(payload.entries) ? (payload.entries as RomEntry[]) : []
        setRomEntriesCache((prev) => ({
          ...prev,
          [cacheKey]: entries,
        }))
      } catch (err) {
        setRomError((err as Error).message)
      } finally {
        setRomEntriesLoading(false)
      }
    },
    [romEntriesCache],
  )

  useEffect(() => {
    setRomEntriesCache({})
  }, [authVersion])

  useEffect(() => {
    fetchRomMetadata()
  }, [authVersion, fetchRomMetadata])

  return {
    romSets,
    romMetaLoading,
    romError,
    romEntriesCache,
    romEntriesLoading,
    fetchRomMetadata,
    fetchRomEntries,
  }
}

export default useRomData
