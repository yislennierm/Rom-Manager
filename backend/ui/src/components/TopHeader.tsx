import { Button, Input, Layout } from 'antd'
import { MenuFoldOutlined, MenuUnfoldOutlined } from '@ant-design/icons'
import React from 'react'

type Props = {
  collapsed: boolean
  onToggle: () => void
  searchQuery: string
  onSearchChange: (value: string) => void
  onSearchSubmit: (value: string) => void
}

const TopHeader: React.FC<Props> = ({ collapsed, onToggle, searchQuery, onSearchChange, onSearchSubmit }) => (
  <Layout.Header className="app-header">
    <Button
      type="text"
      icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
      aria-label={collapsed ? 'Expand navigation' : 'Collapse navigation'}
      onClick={onToggle}
      className="fold-btn"
    />
    <Input.Search
      placeholder="Search…"
      allowClear
      value={searchQuery}
      onChange={(event) => onSearchChange(event.target.value)}
      onSearch={onSearchSubmit}
      className="header-search"
    />
  </Layout.Header>
)

export default TopHeader
