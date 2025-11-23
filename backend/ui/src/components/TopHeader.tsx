import { Button, Layout } from 'antd'
import { MenuFoldOutlined, MenuUnfoldOutlined } from '@ant-design/icons'
import React from 'react'

type Props = {
  collapsed: boolean
  onToggle: () => void
}

const TopHeader: React.FC<Props> = ({ collapsed, onToggle }) => (
  <Layout.Header className="app-header">
    <Button
      type="text"
      icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
      aria-label={collapsed ? 'Expand navigation' : 'Collapse navigation'}
      onClick={onToggle}
      className="fold-btn"
    />
  </Layout.Header>
)

export default TopHeader
