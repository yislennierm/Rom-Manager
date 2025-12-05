import type { MenuProps } from 'antd'
import { Flex, Layout, Menu, Space, Spin, Typography } from 'antd'
import React from 'react'

type Props = {
  collapsed: boolean
  logoUrl: string
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
  logoUrl,
  menuItems,
  selectedKeys,
  openKeys,
  onOpenChange,
  onSelect,
  providerError,
  romMetaLoading,
}) => (
  <Layout.Sider
    width={320}
    className="nav-sider"
    collapsible
    collapsed={collapsed}
    collapsedWidth={80}
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
