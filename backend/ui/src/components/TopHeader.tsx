import { Button, Layout, Space, Tag, Typography } from 'antd'
import { LogoutOutlined, MenuFoldOutlined, MenuUnfoldOutlined } from '@ant-design/icons'
import React from 'react'
import type { AccessUser } from '../types'

type Props = {
  collapsed: boolean
  user: AccessUser | null
  onToggle: () => void
  onLogout: () => void
}

const TopHeader: React.FC<Props> = ({ collapsed, user, onToggle, onLogout }) => (
  <Layout.Header className="app-header">
    <Button
      type="text"
      icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
      aria-label={collapsed ? 'Expand navigation' : 'Collapse navigation'}
      onClick={onToggle}
      className="fold-btn"
    />
    <Space className="header-session">
      {user && (
        <>
          <Typography.Text>{user.name || user.id}</Typography.Text>
          <Tag color={user.admin ? 'gold' : 'blue'}>{user.admin ? 'Admin' : 'Preview'}</Tag>
        </>
      )}
      <Button icon={<LogoutOutlined />} onClick={onLogout}>
        Logout
      </Button>
    </Space>
  </Layout.Header>
)

export default TopHeader
