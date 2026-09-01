import React, { useEffect, useMemo, useState } from 'react'
import { Skeleton, Typography } from 'antd'
import { apiFetch } from '../api'

type ConsoleLogoInfo = {
  status?: 'ok' | 'not_found' | 'error' | string
  logo_url?: string | null
  logo_source_url?: string | null
  logo_credit?: string | null
  logo_license?: string | null
}

type Props = {
  brand?: string | null
  console?: string | null
  guid?: string | null
  module?: string | null
  fallback: string
}

const ConsoleLogoTitle: React.FC<Props> = ({ brand, console, guid, module, fallback }) => {
  const [info, setInfo] = useState<ConsoleLogoInfo | null>(null)
  const [loading, setLoading] = useState(false)

  const requestPayload = useMemo(() => {
    const normalizedBrand = brand?.trim()
    const normalizedConsole = console?.trim()
    if (!normalizedBrand || !normalizedConsole) {
      return null
    }
    return {
      brand: normalizedBrand,
      console: normalizedConsole,
      guid: guid?.trim() || undefined,
      module: module?.trim() || undefined,
    }
  }, [brand, console, guid, module])

  useEffect(() => {
    if (!requestPayload) {
      setInfo(null)
      return
    }
    let cancelled = false
    const loadLogo = async () => {
      setLoading(true)
      try {
        const response = await apiFetch('/consoles/info', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(requestPayload),
        })
        if (!response.ok) {
          throw new Error(await response.text())
        }
        const payload = (await response.json()) as ConsoleLogoInfo
        if (!cancelled) {
          setInfo(payload)
        }
      } catch {
        if (!cancelled) {
          setInfo(null)
        }
      } finally {
        if (!cancelled) {
          setLoading(false)
        }
      }
    }
    loadLogo()
    return () => {
      cancelled = true
    }
  }, [requestPayload])

  if (loading && !info) {
    return <Skeleton.Input active size="small" className="rom-console-logo-skeleton" />
  }

  if (info?.status === 'ok' && info.logo_url) {
    const logo = (
      <img
        src={info.logo_url}
        alt={`${fallback} logo`}
        className="rom-console-logo"
        loading="lazy"
      />
    )
    if (info.logo_source_url) {
      return (
        <a
          href={info.logo_source_url}
          target="_blank"
          rel="noreferrer"
          className="rom-console-logo-link"
          title={[info.logo_credit, info.logo_license].filter(Boolean).join(' · ') || `${fallback} logo`}
        >
          {logo}
        </a>
      )
    }
    return logo
  }

  return (
    <Typography.Title level={3} className="rom-browser-title">
      {fallback}
    </Typography.Title>
  )
}

export default ConsoleLogoTitle
