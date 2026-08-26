import type { MenuProps } from 'antd'
import { Flex, Input, Layout, Menu, Space, Spin, Typography } from 'antd'
import React from 'react'

type Props = {
  collapsed: boolean
  width: number
  logoUrl: string
  searchQuery: string
  onSearchChange: (value: string) => void
  onSearchSubmit: (value: string) => void
  menuItems: MenuProps['items']
  selectedKeys: string[]
  openKeys: string[]
  onOpenChange: MenuProps['onOpenChange']
  onSelect: MenuProps['onSelect']
  providerError: string | null
  romMetaLoading: boolean
}

const NavigationSider: React.FC<Props> = ({
  collapsed,
  width,
  logoUrl,
  searchQuery,
  onSearchChange,
  onSearchSubmit,
  menuItems,
  selectedKeys,
  openKeys,
  onOpenChange,
  onSelect,
  providerError,
  romMetaLoading,
}) => (
  <Layout.Sider
    width={width}
    className="nav-sider"
    collapsible
    collapsed={collapsed}
    collapsedWidth={64}
    theme="dark"
    trigger={null}
  >
    <Flex align="center" gap="small" className="nav-brand">
      <img src={logoUrl} alt="ROMs Manager logo" className="app-logo" />
      {!collapsed && (
        <Typography.Text strong className="nav-title">
          ROMs Manager
        </Typography.Text>
      )}
    </Flex>

    <div className="tree-header">
      <Space size="small">
        {romMetaLoading && <Spin size="small" />}
        {providerError && (
          <Typography.Text type="danger" className="tree-error">
            {providerError}
          </Typography.Text>
        )}
      </Space>
    </div>

    <Input.Search
      placeholder="Search ROMs, providers, modules…"
      allowClear
      value={searchQuery}
      onChange={(event) => onSearchChange(event.target.value)}
      onSearch={onSearchSubmit}
      className="sider-search"
    />

    <Menu
      inlineCollapsed={collapsed}
      mode="inline"
      className="nav-tree"
      items={menuItems}
      selectedKeys={selectedKeys}
      openKeys={collapsed ? [] : openKeys}
      onOpenChange={onOpenChange}
      onSelect={onSelect}
    />
  </Layout.Sider>
)

export default NavigationSider
