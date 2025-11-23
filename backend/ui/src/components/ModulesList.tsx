import React from 'react'
import { Card, Collapse, Descriptions, Flex, Switch, Typography } from 'antd'

type ModuleEntry = {
  name?: string
  guid?: string
  path?: string
  url?: string
  branch?: string
  ignore?: string
  shallow?: string
}

type Props = {
  modules: ModuleEntry[]
  onToggleIgnore: (guid: string | undefined, next: boolean, index: number) => void
}

const slugify = (value: string | undefined | null) => {
  if (!value) return 'default'
  const slug = value.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '')
  return slug || 'default'
}

export const ModulesList: React.FC<Props> = ({ modules, onToggleIgnore }) => {
  const rows = modules.map((entry, index) => {
    const slug = slugify(entry.name || entry.guid || `module-${index}`)
    return {
      key: entry.guid || `module-${index}`,
      name: entry.name || 'Unnamed module',
      guid: entry.guid || '—',
      rdbPath: `data/index/rdb/${slug}.json`,
      ignore: entry.ignore === 'true' || entry.ignore === '1' || entry.ignore === 'yes',
      index,
      path: entry.path || entry.name || '—',
      branch: entry.branch || 'master',
      url: entry.url || '',
      ignoreRule: entry.ignore || '—',
      shallow: entry.shallow || '—',
    }
  })

  return (
    <Card className="summary-card" size="small" bodyStyle={{ padding: 12 }}>
      <Typography.Title level={4} style={{ marginBottom: 12 }}>
        Libretro modules
      </Typography.Title>
      <Collapse accordion>
        {rows.map((row) => (
          <Collapse.Panel
            header={
              <Flex justify="space-between" align="center">
                <Typography.Text>{row.name}</Typography.Text>
                <Flex align="center" gap="small">
                  <Typography.Text type="secondary">{row.guid}</Typography.Text>
                  <Switch
                    size="small"
                    checked={row.ignore}
                    onChange={(checked) => onToggleIgnore(row.guid as string | undefined, checked, row.index)}
                  />
                </Flex>
              </Flex>
            }
            key={row.key}
          >
            <Descriptions
              bordered
              size="small"
              column={1}
              items={[
                { key: 'path', label: 'Path', children: row.path },
                { key: 'branch', label: 'Default branch', children: row.branch },
                {
                  key: 'url',
                  label: 'Repository URL',
                  children: row.url ? (
                    <a href={row.url} target="_blank" rel="noreferrer">
                      {row.url}
                    </a>
                  ) : (
                    '—'
                  ),
                },
                { key: 'ignore', label: 'Ignore rule', children: row.ignoreRule },
                { key: 'shallow', label: 'Shallow clone', children: row.shallow },
                { key: 'rdb', label: 'RDB JSON path', children: row.rdbPath },
              ]}
            />
          </Collapse.Panel>
        ))}
      </Collapse>
    </Card>
  )
}

export default ModulesList
