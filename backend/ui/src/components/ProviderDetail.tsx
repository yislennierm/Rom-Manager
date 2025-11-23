import React from 'react'
import { Button, Descriptions, Flex, Space, Table, Tag, Typography } from 'antd'
import {
  CloudDownloadOutlined,
  CopyOutlined,
  DeleteOutlined,
  EditOutlined,
  ExportOutlined,
} from '@ant-design/icons'

import type { ProviderEntry } from '../types'

type Props = {
  provider: {
    collectionLabel: string
    brand: string
    console: string
    archiveId: string
    data: ProviderEntry
  }
  providerStatus: Record<string, any> | null
  moduleByGuid: Record<string, any>
  onFetchAssets: () => void
  onExportRoms: () => void
  onEdit: () => void
  onDuplicate: () => void
  onDelete: () => void
  fetchRunning: boolean
  exportRunning: boolean
}

export const ProviderDetail: React.FC<Props> = ({
  provider,
  providerStatus,
  moduleByGuid,
  onFetchAssets,
  onExportRoms,
  onEdit,
  onDuplicate,
  onDelete,
  fetchRunning,
  exportRunning,
}) => (
  <Flex vertical gap="large">
    <Flex justify="space-between" align="center" wrap>
      <div>
        <Typography.Title level={4} style={{ marginBottom: 0 }}>
          {provider.collectionLabel}
        </Typography.Title>
        <Typography.Text type="secondary">
          {provider.brand} · {provider.console}
        </Typography.Text>
      </div>
      <Space wrap>
        <Button
          icon={<CloudDownloadOutlined />}
          onClick={onFetchAssets}
          loading={fetchRunning}
          type={providerStatus?.metadata && providerStatus?.listings && providerStatus?.torrent ? 'primary' : undefined}
        >
          Fetch assets
        </Button>
        <Button
          icon={<ExportOutlined />}
          onClick={onExportRoms}
          loading={exportRunning}
          type={providerStatus?.rom_json ? 'primary' : undefined}
        >
          Export ROMs
        </Button>
        <Button icon={<EditOutlined />} onClick={onEdit}>
          Edit
        </Button>
        <Button icon={<CopyOutlined />} onClick={onDuplicate}>
          Duplicate
        </Button>
        <Button icon={<DeleteOutlined />} danger onClick={onDelete}>
          Delete
        </Button>
      </Space>
    </Flex>

    <Descriptions
      bordered
      size="small"
      column={2}
      labelStyle={{ width: 180 }}
      items={[
        { key: 'provider', label: 'Provider', children: provider.data.provider || '—' },
        { key: 'archive', label: 'Archive ID', children: provider.data.archive_id || '—' },
        {
          key: 'baseUrl',
          label: 'Base URL',
          span: 2,
          children: provider.data.base_url ? (
            <a href={provider.data.base_url} target="_blank" rel="noreferrer">
              {provider.data.base_url}
            </a>
          ) : (
            '—'
          ),
        },
        { key: 'updated', label: 'Updated', children: provider.data.updated || '—' },
        { key: 'size', label: 'Reported size', children: provider.data.size || '—' },
        {
          key: 'guid',
          label: 'Console / GUID',
          span: 2,
          children: provider.data.libretro_guid ? (
            <div className="guid-display">
              <Typography.Text>{provider.data.libretro_guid}</Typography.Text>
              {moduleByGuid[provider.data.libretro_guid] && (
                <Typography.Text type="secondary">{moduleByGuid[provider.data.libretro_guid].name}</Typography.Text>
              )}
            </div>
          ) : (
            '—'
          ),
        },
      ]}
    />

    <div>
      <Typography.Title level={5}>Cached assets</Typography.Title>
      <Table
        size="small"
        pagination={false}
        dataSource={[
          { key: 'metadata', label: 'Metadata DB', value: providerStatus?.metadata },
          { key: 'listings', label: 'Listings XML', value: providerStatus?.listings },
          { key: 'torrent', label: 'Torrent', value: providerStatus?.torrent },
          { key: 'rom_json', label: 'ROM JSON', value: providerStatus?.rom_json },
        ]}
        columns={[
          { title: 'Asset', dataIndex: 'label', key: 'label', width: 160 },
          {
            title: 'Status',
            dataIndex: 'value',
            key: 'value',
            render: (value: boolean | undefined) => (value ? <Tag color="green">Cached</Tag> : <Tag color="orange">Missing</Tag>),
          },
        ]}
      />
    </div>

    <div>
      <Typography.Title level={5}>Available files</Typography.Title>
      {provider.data.files ? (
        <Table
          size="small"
          pagination={false}
          dataSource={Object.entries(provider.data.files).map(([key, value]) => ({
            key,
            type: key,
            url: value,
          }))}
          columns={[
            { title: 'Type', dataIndex: 'type', key: 'type', width: 180 },
            {
              title: 'URL',
              dataIndex: 'url',
              key: 'url',
              render: (value: string) =>
                value ? (
                  <a href={value} target="_blank" rel="noreferrer">
                    {value}
                  </a>
                ) : (
                  '—'
                ),
            },
          ]}
        />
      ) : (
        <Typography.Text type="secondary">No file links configured.</Typography.Text>
      )}
    </div>

    <div>
      <Typography.Title level={5}>ROM extensions</Typography.Title>
      {provider.data.rom_extensions?.length ? (
        <Flex gap="small" wrap>
          {provider.data.rom_extensions.map((ext) => (
            <Tag key={ext} color="blue">
              {ext}
            </Tag>
          ))}
        </Flex>
      ) : (
        <Typography.Text type="secondary">Not specified.</Typography.Text>
      )}
    </div>
  </Flex>
)

export default ProviderDetail
