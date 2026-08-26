import React, { useEffect, useMemo, useState } from 'react'
import { Alert, Button, Flex, Skeleton, Typography } from 'antd'
import { LinkOutlined } from '@ant-design/icons'
import { apiFetch } from '../api'

type ConsoleInfo = {
  brand?: string
  console?: string
  guid?: string
  query?: string
  source?: string
  status?: 'ok' | 'not_found' | 'error' | string
  title?: string | null
  summary?: string | null
  page_url?: string | null
  image_url?: string | null
  message?: string
}

type Props = {
  brand?: string | null
  console?: string | null
  guid?: string | null
  module?: string | null
}

const ConsoleInfoCard: React.FC<Props> = ({ brand, console, guid, module }) => {
  const [info, setInfo] = useState<ConsoleInfo | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

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
      setError(null)
      return
    }
    let cancelled = false
    const loadInfo = async () => {
      setLoading(true)
      setError(null)
      try {
        const response = await apiFetch('/consoles/info', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(requestPayload),
        })
        if (!response.ok) {
          throw new Error(await response.text())
        }
        const payload = (await response.json()) as ConsoleInfo
        if (!cancelled) {
          setInfo(payload)
        }
      } catch (err) {
        if (!cancelled) {
          setInfo(null)
          setError(err instanceof Error ? err.message : 'Unable to load console information')
        }
      } finally {
        if (!cancelled) {
          setLoading(false)
        }
      }
    }
    loadInfo()
    return () => {
      cancelled = true
    }
  }, [requestPayload])

  if (!requestPayload) {
    return null
  }

  if (loading && !info) {
    return (
      <section className="console-info-card">
        <Skeleton.Image active className="console-info-skeleton-image" />
        <div className="console-info-body">
          <Skeleton active paragraph={{ rows: 3 }} />
        </div>
      </section>
    )
  }

  if (error) {
    return <Alert type="warning" showIcon message="Console information unavailable" description={error} />
  }

  if (!info || info.status !== 'ok') {
    return null
  }

  return (
    <section className="console-info-card">
      {info.image_url && (
        <img
          src={info.image_url}
          alt={info.title ? `${info.title} console` : `${requestPayload.brand} ${requestPayload.console}`}
          className="console-info-image"
          loading="lazy"
        />
      )}
      <div className="console-info-body">
        <Flex justify="space-between" align="flex-start" gap="middle" wrap>
          <div>
            <Typography.Title level={3} className="console-info-title">
              {info.title || `${requestPayload.brand} ${requestPayload.console}`}
            </Typography.Title>
            <Typography.Text type="secondary">
              {requestPayload.brand} · {requestPayload.console}
            </Typography.Text>
          </div>
          {info.page_url && (
            <Button href={info.page_url} target="_blank" rel="noreferrer" icon={<LinkOutlined />}>
              Wikipedia
            </Button>
          )}
        </Flex>
        {info.summary && (
          <Typography.Paragraph className="console-info-summary">
            {info.summary}
          </Typography.Paragraph>
        )}
      </div>
    </section>
  )
}

export default ConsoleInfoCard
