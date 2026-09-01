import { useCallback, useEffect, useState } from 'react'
import { apiFetch } from '../api'
import type { RomEntriesPage, RomEntry, RomEntryFilters, RomSetMeta } from '../types'

const DEFAULT_ROM_PAGE_SIZE = 60

export const useRomData = (authVersion = '') => {
  const [romSets, setRomSets] = useState<RomSetMeta[]>([])
  const [romMetaLoading, setRomMetaLoading] = useState(false)
  const [romError, setRomError] = useState<string | null>(null)
  const [romEntriesCache, setRomEntriesCache] = useState<Record<string, RomEntriesPage>>({})
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
    async (meta: RomSetMeta, page = 1, pageSize = DEFAULT_ROM_PAGE_SIZE, filters: RomEntryFilters = {}) => {
      const identifier = meta.slug ?? meta.guid
      if (!identifier) {
        return
      }
      const offset = Math.max(page - 1, 0) * pageSize
      const filterKey = JSON.stringify({
        q: filters.q || '',
        availability: filters.availability || '',
        region: filters.region || '',
        format: filters.format || '',
        sort: filters.sort || 'name',
      })
      const cacheKey = `${meta.slug || meta.guid || identifier}:${offset}:${pageSize}:${filterKey}`
      if (romEntriesCache[cacheKey]) {
        return
      }
      setRomEntriesLoading(true)
      try {
        const params = new URLSearchParams({
          limit: String(pageSize),
          offset: String(offset),
          sort: filters.sort || 'name',
        })
        if (filters.q) params.set('q', filters.q)
        if (filters.availability) params.set('availability', filters.availability)
        if (filters.region) params.set('region', filters.region)
        if (filters.format) params.set('format', filters.format)
        const response = await apiFetch(
          `/roms/${encodeURIComponent(identifier)}?${params.toString()}`,
        )
        if (!response.ok) {
          throw new Error(`Failed to load ROM data for ${meta.console || meta.module}`)
        }
        const payload = await response.json()
        const entries = Array.isArray(payload.entries) ? (payload.entries as RomEntry[]) : []
        setRomEntriesCache((prev) => ({
          ...prev,
          [cacheKey]: {
            entries,
            total: Number(payload.total ?? payload.entry_count ?? entries.length),
            catalog_total: Number(payload.catalog_total ?? payload.entry_count ?? entries.length),
            limit: Number(payload.limit ?? pageSize),
            offset: Number(payload.offset ?? offset),
          },
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
    defaultRomPageSize: DEFAULT_ROM_PAGE_SIZE,
  }
}

export default useRomData
