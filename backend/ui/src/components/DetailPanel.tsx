import React from 'react'
import { Button, Card, Descriptions, Empty, Flex, Space, Typography } from 'antd'
import { CopyOutlined, DeleteOutlined, EditOutlined } from '@ant-design/icons'
import ProviderDetail from './ProviderDetail'
import type {
  BrandSelection,
  ConsoleSelection,
  ModuleEntry,
  ModuleSelection,
  ProviderSelection,
  RomBrandSelection,
  RomConsoleSelection,
  Selection,
} from '../types'

type Props = {
  selection: Selection
  selectedProvider: ProviderSelection | null
  selectedModule: ModuleSelection | null
  selectedRomConsole: RomConsoleSelection | null
  selectedRomBrand: RomBrandSelection | null
  selectedConsole: ConsoleSelection | null
  selectedBrand: BrandSelection | null
  isRomContext: boolean
  moduleByGuid: Record<string, ModuleEntry>
  providerStatus: Record<string, any> | null
  providerFetchRunning: boolean
  providerExportRunning: boolean
  onFetchAssets: () => void
  onExportRoms: () => void
  onEditProvider: () => void
  onDuplicateProvider: () => void
  onDeleteProvider: () => void
  onEditModule: () => void
  onDuplicateModule: () => void
  onDeleteModule: () => void
  renderRomConsoleDetail: (selection: RomConsoleSelection) => React.ReactNode
  renderRomBrandDetail: (selection: RomBrandSelection) => React.ReactNode
  renderConsoleDetail: (selection: ConsoleSelection) => React.ReactNode
  renderBrandDetail: (selection: BrandSelection) => React.ReactNode
}

const DetailPanel: React.FC<Props> = ({
  selection,
  selectedProvider,
  selectedModule,
  selectedRomConsole,
  selectedRomBrand,
  selectedConsole,
  selectedBrand,
  isRomContext,
  moduleByGuid,
  providerStatus,
  providerFetchRunning,
  providerExportRunning,
  onFetchAssets,
  onExportRoms,
  onEditProvider,
  onDuplicateProvider,
  onDeleteProvider,
  onEditModule,
  onDuplicateModule,
  onDeleteModule,
  renderRomConsoleDetail,
  renderRomBrandDetail,
  renderConsoleDetail,
  renderBrandDetail,
}) => (
  <Card className="detail-card" title={isRomContext ? undefined : 'Details'}>
    {selectedProvider ? (
      <ProviderDetail
        provider={selectedProvider}
        providerStatus={providerStatus}
        moduleByGuid={moduleByGuid}
        onFetchAssets={onFetchAssets}
        onExportRoms={onExportRoms}
        onEdit={onEditProvider}
        onDuplicate={onDuplicateProvider}
        onDelete={onDeleteProvider}
        fetchRunning={providerFetchRunning}
        exportRunning={providerExportRunning}
      />
    ) : selectedModule ? (
      <Flex vertical gap="large">
        <Flex justify="space-between" align="center" wrap>
          <div>
            <Typography.Title level={4} style={{ marginBottom: 0 }}>
              {selectedModule.data.name || 'Unnamed module'}
            </Typography.Title>
            <Typography.Text type="secondary">
              {selectedModule.data.guid || 'GUID not assigned'}
            </Typography.Text>
          </div>
          <Space wrap>
            <Button icon={<EditOutlined />} onClick={onEditModule}>
              Edit
            </Button>
            <Button icon={<CopyOutlined />} onClick={onDuplicateModule}>
              Duplicate
            </Button>
            <Button icon={<DeleteOutlined />} danger onClick={onDeleteModule}>
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
            { key: 'path', label: 'Path', children: selectedModule.data.path || '—' },
            { key: 'branch', label: 'Default branch', children: selectedModule.data.branch || '—' },
            {
              key: 'url',
              label: 'Repository URL',
              span: 2,
              children: selectedModule.data.url ? (
                <a href={selectedModule.data.url} target="_blank" rel="noreferrer">
                  {selectedModule.data.url}
                </a>
              ) : (
                '—'
              ),
            },
            { key: 'ignore', label: 'Ignore rule', children: selectedModule.data.ignore || '—' },
            { key: 'shallow', label: 'Shallow clone', children: selectedModule.data.shallow || '—' },
          ]}
        />
      </Flex>
    ) : selectedRomConsole ? (
      renderRomConsoleDetail(selectedRomConsole)
    ) : selectedRomBrand ? (
      renderRomBrandDetail(selectedRomBrand)
    ) : selectedConsole ? (
      renderConsoleDetail(selectedConsole)
    ) : selectedBrand ? (
      renderBrandDetail(selectedBrand)
    ) : selection?.kind === 'dataset' ? (
      <Typography.Paragraph>
        Dataset controls will live here soon. For now, use the summary cards above to monitor libretro module sync status.
      </Typography.Paragraph>
    ) : (
      <Empty description="Select a resource from the left tree to view its metadata" />
    )}
  </Card>
)

export default DetailPanel
