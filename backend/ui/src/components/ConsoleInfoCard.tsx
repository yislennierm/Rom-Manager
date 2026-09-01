import React, { useEffect, useMemo, useState } from 'react'
import { Alert, Button, Flex, Skeleton, Typography } from 'antd'
import { LeftOutlined, LinkOutlined, RightOutlined } from '@ant-design/icons'
import { apiFetch } from '../api'

type ConsoleImageOption = {
  title?: string | null
  url?: string | null
  thumbnail_url?: string | null
  mime?: string | null
  width?: number | null
  height?: number | null
  source_url?: string | null
}

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
  image_index?: number | null
  selected_image_index?: number | null
  selected_image_title?: string | null
  selected_image_url?: string | null
  image_options?: ConsoleImageOption[]
  can_select_image?: boolean
  logo_url?: string | null
  logo_source_url?: string | null
  logo_license?: string | null
  logo_credit?: string | null
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
  const [imageError, setImageError] = useState<string | null>(null)
  const [savingImage, setSavingImage] = useState(false)

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
      setImageError(null)
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

  const imageOptions = (info.image_options || []).filter((option) => option.url || option.thumbnail_url)
  const rawImageIndex = typeof info.image_index === 'number' ? info.image_index : info.selected_image_index
  const imageIndex =
    typeof rawImageIndex === 'number' && rawImageIndex >= 0 && rawImageIndex < imageOptions.length
      ? rawImageIndex
      : 0
  const activeImage = imageOptions[imageIndex]
  const imageUrl = activeImage?.url || activeImage?.thumbnail_url || info.image_url
  const imageTitle = activeImage?.title?.replace(/^File:/, '').replace(/_/g, ' ')
  const canSelectImages = Boolean(info.can_select_image && imageOptions.length > 1)

  const selectImage = async (nextIndex: number) => {
    if (!requestPayload || !info || !canSelectImages) {
      return
    }
    const previousInfo = info
    const nextOption = imageOptions[nextIndex]
    const nextUrl = nextOption?.url || nextOption?.thumbnail_url || null
    setImageError(null)
    setInfo({
      ...info,
      image_index: nextIndex,
      selected_image_index: nextIndex,
      selected_image_title: nextOption?.title || null,
      selected_image_url: nextUrl,
      image_url: nextUrl,
    })
    setSavingImage(true)
    try {
      const response = await apiFetch('/consoles/info/image', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...requestPayload,
          image_index: nextIndex,
        }),
      })
      if (!response.ok) {
        throw new Error(await response.text())
      }
      setInfo((await response.json()) as ConsoleInfo)
    } catch (err) {
      setInfo(previousInfo)
      setImageError(err instanceof Error ? err.message : 'Unable to save console image')
    } finally {
      setSavingImage(false)
    }
  }

  const stepImage = (direction: -1 | 1) => {
    if (!imageOptions.length) {
      return
    }
    const nextIndex = (imageIndex + direction + imageOptions.length) % imageOptions.length
    void selectImage(nextIndex)
  }

  return (
    <section className="console-info-card">
      {imageUrl && (
        <div className="console-info-media">
          <div className="console-info-image-frame">
            <img
              src={imageUrl}
              alt={info.title ? `${info.title} console` : `${requestPayload.brand} ${requestPayload.console}`}
              className="console-info-image"
              loading="lazy"
            />
            {canSelectImages && (
              <div className="console-info-image-controls" aria-label="Console image selection">
                <Button
                  type="text"
                  icon={<LeftOutlined />}
                  aria-label="Previous console image"
                  className="console-info-image-button"
                  disabled={savingImage}
                  onClick={() => stepImage(-1)}
                />
                <span className="console-info-image-count">
                  {imageIndex + 1} / {imageOptions.length}
                </span>
                <Button
                  type="text"
                  icon={<RightOutlined />}
                  aria-label="Next console image"
                  className="console-info-image-button"
                  disabled={savingImage}
                  onClick={() => stepImage(1)}
                />
              </div>
            )}
          </div>
          {imageTitle && <span className="console-info-image-caption">{imageTitle}</span>}
        </div>
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
        {imageError && (
          <Typography.Text type="warning" className="console-info-image-error">
            {imageError}
          </Typography.Text>
        )}
      </div>
    </section>
  )
}

export default ConsoleInfoCard
