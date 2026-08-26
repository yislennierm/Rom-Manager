import React from 'react'
import { Card, Descriptions, Flex, Space, Spin, Tag, Typography } from 'antd'
import type { ModuleReadiness } from '../types'

type Props = {
  readiness: ModuleReadiness | null
  loading: boolean
}

const ModuleReadinessCard: React.FC<Props> = ({ readiness, loading }) => {
  if (loading) {
    return (
      <Card size="small" title="Server Readiness">
        <Spin />
      </Card>
    )
  }
  if (!readiness) {
    return null
  }
  const checks = readiness.checks || {}
  return (
    <Card size="small" title="Server Readiness">
      <Flex vertical gap="middle">
        <Flex justify="space-between" align="center" wrap>
          <div>
            <Typography.Text strong>{readiness.summary?.label || readiness.score}</Typography.Text>
            <br />
            <Typography.Text type="secondary">
              {readiness.brand || 'Unknown'} · {readiness.console || 'Unknown console'}
            </Typography.Text>
          </div>
          <Tag color={readiness.summary?.ready ? 'green' : readiness.score === 'partial' ? 'orange' : 'red'}>
            {readiness.score}
          </Tag>
        </Flex>
        <Space size="small" wrap>
          {Object.entries(checks).map(([key, check]) => (
            <Tag key={key} color={check.state === 'ok' ? 'green' : check.state === 'partial' ? 'orange' : 'red'}>
              {key}: {check.label}
            </Tag>
          ))}
        </Space>
        <Descriptions
          bordered
          size="small"
          column={3}
          items={[
            { key: 'providers', label: 'Providers', children: readiness.providers?.length ?? 0 },
            { key: 'cores', label: 'Core mappings', children: readiness.core_metadata?.length ?? 0 },
            { key: 'bios', label: 'BIOS metadata', children: readiness.bios_metadata?.length ?? 0 },
          ]}
        />
      </Flex>
    </Card>
  )
}

export default ModuleReadinessCard
