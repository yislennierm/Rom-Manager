import { Button, Layout, Space, Tag, Typography } from 'antd'
import { LogoutOutlined, MenuFoldOutlined, MenuUnfoldOutlined, ReloadOutlined } from '@ant-design/icons'
import React from 'react'
import type { AccessUser } from '../types'

type Props = {
  collapsed: boolean
  user: AccessUser | null
  title: string
  subtitle?: string
  busy?: boolean
  onToggle: () => void
  onRefresh: () => void
  onLogout: () => void
}

const TopHeader: React.FC<Props> = ({ collapsed, user, title, subtitle, busy, onToggle, onRefresh, onLogout }) => (
  <Layout.Header className="app-header">
    <div className="topbar-main">
      <Button
        type="text"
        icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
        aria-label={collapsed ? 'Expand navigation' : 'Collapse navigation'}
        onClick={onToggle}
        className="fold-btn"
      />
      <div className="topbar-title">
        <Typography.Text className="topbar-kicker">Admin console</Typography.Text>
        <Typography.Title level={4} className="topbar-heading">
          {title}
        </Typography.Title>
        {subtitle && <Typography.Text className="topbar-subtitle">{subtitle}</Typography.Text>}
      </div>
    </div>
    <Space className="header-session">
      <Tag className="status-tag">Online</Tag>
      <Button icon={<ReloadOutlined />} onClick={onRefresh} loading={busy}>
        Refresh
      </Button>
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
