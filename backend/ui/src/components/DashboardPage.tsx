import React from 'react'
import { Alert, Button, Empty, Spin, Tag, Typography } from 'antd'
import {
  CheckCircleOutlined,
  CloudDownloadOutlined,
  DatabaseOutlined,
  HddOutlined,
  KeyOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  ToolOutlined,
  WarningOutlined,
} from '@ant-design/icons'
import dayjs from 'dayjs'
import type { DashboardAlert, DashboardConsole, DashboardResponse, DashboardRomDataset } from '../types'

type Props = {
  dashboard: DashboardResponse | null
  loading: boolean
  error: string | null
  logoUrl: string
  onRefresh: () => void
  onOpenConsole: (guid?: string | null) => void
  onOpenUsers: () => void
  onOpenProviders: () => void
  onOpenModules: () => void
}

type MetricProps = {
  icon: React.ReactNode
  label: string
  value: string
  detail: string
  tone?: 'mint' | 'violet' | 'blue' | 'rose' | 'amber'
}

const numberFormatter = new Intl.NumberFormat()

const formatNumber = (value?: number | null) => numberFormatter.format(value ?? 0)

const formatPercent = (value?: number | null) => `${(value ?? 0).toFixed(value && value % 1 ? 1 : 0)}%`

const formatBytes = (size?: number) => {
  if (!size || Number.isNaN(size)) {
    return '0 B'
  }
  if (size < 1024) {
    return `${size} B`
  }
  if (size < 1024 * 1024) {
    return `${(size / 1024).toFixed(1)} KB`
  }
  if (size < 1024 * 1024 * 1024) {
    return `${(size / 1024 / 1024).toFixed(1)} MB`
  }
  return `${(size / 1024 / 1024 / 1024).toFixed(1)} GB`
}

const alertType = (alert: DashboardAlert) => {
  if (alert.severity === 'critical') return 'error'
  if (alert.severity === 'warning') return 'warning'
  if (alert.severity === 'success') return 'success'
  return 'info'
}

const statusLabel = (value: string) =>
  value
    .split('_')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')

const completionTone = (completion: number) => {
  if (completion >= 100) return 'ready'
  if (completion >= 90) return 'strong'
  if (completion >= 70) return 'partial'
  if (completion >= 35) return 'setup'
  return 'blocked'
}

const MetricCard: React.FC<MetricProps> = ({ icon, label, value, detail, tone = 'mint' }) => (
  <article className={`dashboard-metric dashboard-metric-${tone}`}>
    <span className="dashboard-metric-icon" aria-hidden="true">
      {icon}
    </span>
    <div className="dashboard-metric-copy">
      <Typography.Text className="dashboard-metric-label">{label}</Typography.Text>
      <Typography.Text className="dashboard-metric-value">{value}</Typography.Text>
      <Typography.Text className="dashboard-metric-detail">{detail}</Typography.Text>
    </div>
  </article>
)

const CompletionBar: React.FC<{ value: number; label: string }> = ({ value, label }) => (
  <div className="dashboard-bar" aria-label={`${label}: ${formatPercent(value)}`}>
    <div className="dashboard-bar-track">
      <span className={`dashboard-bar-fill dashboard-bar-${completionTone(value)}`} style={{ width: `${Math.min(value, 100)}%` }} />
    </div>
    <span>{formatPercent(value)}</span>
  </div>
)

const ConsoleRow: React.FC<{
  console: DashboardConsole
  onOpenConsole: (guid?: string | null) => void
}> = ({ console, onOpenConsole }) => (
  <div className="dashboard-console-row">
    <div className="dashboard-console-main">
      <Typography.Text className="dashboard-console-name">{console.module}</Typography.Text>
      <div className="dashboard-console-meta">
        <Tag className={`dashboard-status dashboard-status-${completionTone(console.completion)}`}>
          {console.completion}%
        </Tag>
        <span>{statusLabel(console.status)}</span>
        <span>{formatNumber(console.provider_count)} providers</span>
      </div>
    </div>
    <CompletionBar value={console.coverage_percent} label={`${console.module} source coverage`} />
    <Typography.Text className="dashboard-next-action">{console.next_action}</Typography.Text>
    <Button size="small" onClick={() => onOpenConsole(console.guid)} aria-label={`Open ${console.module}`}>
      Open
    </Button>
  </div>
)

const DatasetRow: React.FC<{ dataset: DashboardRomDataset }> = ({ dataset }) => (
  <div className="dashboard-dataset-row">
    <div>
      <Typography.Text className="dashboard-console-name">{dataset.module || dataset.slug}</Typography.Text>
      <Typography.Text className="dashboard-muted">
        {formatNumber(dataset.entry_count)} entries · {formatNumber(dataset.provider_linked_entries)} linked
      </Typography.Text>
    </div>
    <CompletionBar value={dataset.coverage_percent} label={`${dataset.module || dataset.slug} coverage`} />
  </div>
)

const DashboardPage: React.FC<Props> = ({
  dashboard,
  loading,
  error,
  logoUrl,
  onRefresh,
  onOpenConsole,
  onOpenUsers,
  onOpenProviders,
  onOpenModules,
}) => {
  if (loading && !dashboard) {
    return (
      <section className="dashboard-loading" aria-label="Loading dashboard">
        <Spin />
      </section>
    )
  }

  if (error && !dashboard) {
    return (
      <Alert
        type="error"
        showIcon
        message="Unable to load dashboard"
        description={error}
        action={
          <Button icon={<ReloadOutlined />} onClick={onRefresh}>
            Retry
          </Button>
        }
      />
    )
  }

  if (!dashboard) {
    return (
      <section className="dashboard-empty">
        <Empty description="No dashboard data is available" />
      </section>
    )
  }

  const readyPercent = dashboard.readiness.total
    ? (dashboard.readiness.ready_for_assignment / dashboard.readiness.total) * 100
    : 0
  const assignedRisk = dashboard.users.assigned_at_risk ?? 0
  const userMetricValue = dashboard.users.visible
    ? formatNumber(dashboard.users.enabled)
    : formatNumber(dashboard.users.assigned_console_count)
  const userMetricDetail = dashboard.users.visible
    ? `${formatNumber(dashboard.users.clients)} clients · ${formatNumber(assignedRisk)} assigned at risk`
    : 'Assigned consoles in preview scope'

  return (
    <section className="dashboard-page" aria-label="ROMs Manager dashboard">
      <section className="dashboard-hero" aria-labelledby="dashboard-title">
        <div className="dashboard-brand">
          <img src={logoUrl} alt="ROMs Manager logo" />
          <div>
            <Typography.Text className="dashboard-kicker">Operations home</Typography.Text>
            <Typography.Title id="dashboard-title" level={2}>
              ROMs Manager
            </Typography.Title>
            <Typography.Text className="dashboard-muted">
              Generated {dayjs(dashboard.generated_at).format('YYYY-MM-DD HH:mm')} · {dashboard.scope === 'admin' ? 'Full admin scope' : 'Preview scope'}
            </Typography.Text>
          </div>
        </div>
        <div className="dashboard-hero-actions">
          <div className="dashboard-score" aria-label={`Average completion ${formatPercent(dashboard.readiness.average_completion)}`}>
            <span>{formatPercent(dashboard.readiness.average_completion)}</span>
            <small>avg completion</small>
          </div>
          <Button icon={<ReloadOutlined />} onClick={onRefresh} loading={loading}>
            Refresh
          </Button>
        </div>
      </section>

      <section className="dashboard-metrics" aria-label="Platform metrics">
        <MetricCard
          icon={<SafetyCertificateOutlined />}
          label="Ready Consoles"
          value={`${formatNumber(dashboard.readiness.ready_for_assignment)} / ${formatNumber(dashboard.readiness.total)}`}
          detail={`${formatPercent(readyPercent)} runtime validated`}
          tone="mint"
        />
        <MetricCard
          icon={<CloudDownloadOutlined />}
          label="Providers"
          value={formatNumber(dashboard.providers.total)}
          detail={`${formatNumber(dashboard.providers.with_providers)} consoles covered`}
          tone="violet"
        />
        <MetricCard
          icon={<DatabaseOutlined />}
          label="ROM Sources"
          value={formatPercent(dashboard.roms.coverage_percent)}
          detail={`${formatNumber(dashboard.roms.provider_linked_entries)} of ${formatNumber(dashboard.roms.entries)} linked`}
          tone="blue"
        />
        <MetricCard
          icon={<KeyOutlined />}
          label={dashboard.users.visible ? 'Enabled Users' : 'Assigned Scope'}
          value={userMetricValue}
          detail={userMetricDetail}
          tone={assignedRisk ? 'rose' : 'amber'}
        />
        <MetricCard
          icon={<ToolOutlined />}
          label="Runtime Metadata"
          value={`${formatNumber(dashboard.runtime.mapped_consoles)} mapped`}
          detail={`${formatNumber(dashboard.runtime.missing_core_metadata)} missing core metadata`}
          tone="mint"
        />
        <MetricCard
          icon={<HddOutlined />}
          label="Backend Cache"
          value={formatBytes(dashboard.datasets.cache.size)}
          detail={`${formatNumber(dashboard.datasets.cache.count)} cached files`}
          tone="violet"
        />
      </section>

      <section className="dashboard-alerts" aria-label="Dashboard alerts">
        {dashboard.alerts.length ? (
          dashboard.alerts.map((alert) => (
            <Alert
              key={`${alert.title}-${alert.message}`}
              type={alertType(alert)}
              showIcon
              message={alert.title}
              description={`${alert.message}${alert.action ? ` Action: ${alert.action}.` : ''}`}
            />
          ))
        ) : (
          <Alert
            type="success"
            showIcon
            message="No platform alerts"
            description="The current dashboard scope has no blocking metadata issues."
          />
        )}
      </section>

      <section className="dashboard-grid">
        <article className="dashboard-panel dashboard-panel-wide">
          <div className="dashboard-panel-header">
            <div>
              <Typography.Title level={3}>Next Console Work</Typography.Title>
              <Typography.Text className="dashboard-muted">
                Console-family modules first, sorted by ROM dataset, provider coverage, core metadata, BIOS metadata, and runtime validation.
              </Typography.Text>
            </div>
            <Button onClick={onOpenModules}>All consoles</Button>
          </div>
          {dashboard.work_queue.length ? (
            <div className="dashboard-console-list">
              {dashboard.work_queue.map((console) => (
                <ConsoleRow key={console.guid || console.module} console={console} onOpenConsole={onOpenConsole} />
              ))}
            </div>
          ) : (
            <Empty description={dashboard.other_work_queue.length ? 'Console backlog is complete; other module types still need work' : 'All consoles in this scope are complete'} />
          )}
        </article>

        <article className="dashboard-panel">
          <div className="dashboard-panel-header">
            <div>
              <Typography.Title level={3}>Completion Buckets</Typography.Title>
              <Typography.Text className="dashboard-muted">Current platform distribution.</Typography.Text>
            </div>
          </div>
          <div className="dashboard-bucket-list">
            {['100', '90', '75', '70', '35', '10'].map((bucket) => (
              <div key={bucket} className="dashboard-bucket-row">
                <span>{bucket}%</span>
                <div className="dashboard-bucket-track">
                  <span
                    style={{
                      width: `${dashboard.readiness.total ? ((dashboard.readiness.buckets[bucket] || 0) / dashboard.readiness.total) * 100 : 0}%`,
                    }}
                  />
                </div>
                <strong>{formatNumber(dashboard.readiness.buckets[bucket] || 0)}</strong>
              </div>
            ))}
          </div>
          <div className="dashboard-category-list" aria-label="Module categories">
            {['console', 'computer', 'arcade', 'engine'].map((category) => (
              <span key={category}>
                {statusLabel(category)}
                <strong>{formatNumber(dashboard.readiness.categories[category] || 0)}</strong>
              </span>
            ))}
          </div>
        </article>

        <article className="dashboard-panel">
          <div className="dashboard-panel-header">
            <div>
              <Typography.Title level={3}>Provider Health</Typography.Title>
              <Typography.Text className="dashboard-muted">Registry and cache readiness.</Typography.Text>
            </div>
            <Button onClick={onOpenProviders}>Providers</Button>
          </div>
          <div className="dashboard-facts">
            <span>Brands<strong>{formatNumber(dashboard.providers.brands)}</strong></span>
            <span>Covered consoles<strong>{formatNumber(dashboard.providers.with_providers)}</strong></span>
            <span>No providers<strong>{formatNumber(dashboard.providers.without_providers)}</strong></span>
            <span>Cached providers<strong>{formatNumber(dashboard.providers.with_cache)}</strong></span>
          </div>
        </article>

        <article className="dashboard-panel">
          <div className="dashboard-panel-header">
            <div>
              <Typography.Title level={3}>ROM Catalog Health</Typography.Title>
              <Typography.Text className="dashboard-muted">Master lists, source links, and artwork indexes.</Typography.Text>
            </div>
          </div>
          <div className="dashboard-facts">
            <span>Datasets<strong>{formatNumber(dashboard.roms.datasets)}</strong></span>
            <span>Entries<strong>{formatNumber(dashboard.roms.entries)}</strong></span>
            <span>Thumbnail indexes<strong>{formatNumber(dashboard.roms.thumbnail_indexes)}</strong></span>
            <span>Indexed artwork<strong>{formatNumber(dashboard.roms.thumbnail_images)}</strong></span>
          </div>
        </article>

        <article className="dashboard-panel">
          <div className="dashboard-panel-header">
            <div>
              <Typography.Title level={3}>Runtime Requirements</Typography.Title>
              <Typography.Text className="dashboard-muted">Core, BIOS, and special launch metadata.</Typography.Text>
            </div>
          </div>
          <div className="dashboard-facts">
            <span>Core records<strong>{formatNumber(dashboard.runtime.cores)}</strong></span>
            <span>BIOS records<strong>{formatNumber(dashboard.runtime.bios_files)}</strong></span>
            <span>BIOS with sources<strong>{formatNumber(dashboard.runtime.bios_with_sources)}</strong></span>
            <span>Special strategies<strong>{formatNumber(dashboard.runtime.special_strategy_consoles.length)}</strong></span>
          </div>
          <div className="dashboard-strategy-list">
            {dashboard.runtime.special_strategy_consoles.slice(0, 5).map((console) => (
              <span key={console.guid || console.module}>
                <PlayCircleOutlined />
                {console.module}
                <Tag>{console.strategy_types.join(', ')}</Tag>
              </span>
            ))}
          </div>
        </article>

        <article className="dashboard-panel">
          <div className="dashboard-panel-header">
            <div>
              <Typography.Title level={3}>User Access</Typography.Title>
              <Typography.Text className="dashboard-muted">Assignments without exposing keys.</Typography.Text>
            </div>
            {dashboard.users.visible && <Button onClick={onOpenUsers}>Users</Button>}
          </div>
          {dashboard.users.visible ? (
            <div className="dashboard-facts">
              <span>Total users<strong>{formatNumber(dashboard.users.total)}</strong></span>
              <span>Enabled<strong>{formatNumber(dashboard.users.enabled)}</strong></span>
              <span>Zero access<strong>{formatNumber(dashboard.users.zero_access)}</strong></span>
              <span>Assigned at risk<strong>{formatNumber(dashboard.users.assigned_at_risk)}</strong></span>
            </div>
          ) : (
            <Alert type="info" showIcon message="Preview scope" description="User management is available only to admin sessions." />
          )}
        </article>

        <article className="dashboard-panel dashboard-panel-wide">
          <div className="dashboard-panel-header">
            <div>
              <Typography.Title level={3}>Largest Catalogs</Typography.Title>
              <Typography.Text className="dashboard-muted">Useful for provider and disk-space planning.</Typography.Text>
            </div>
          </div>
          <div className="dashboard-dataset-list">
            {dashboard.roms.largest_datasets.slice(0, 6).map((dataset) => (
              <DatasetRow key={dataset.guid || dataset.slug} dataset={dataset} />
            ))}
          </div>
        </article>

        <article className="dashboard-panel">
          <div className="dashboard-panel-header">
            <div>
              <Typography.Title level={3}>Ready For Assignment</Typography.Title>
              <Typography.Text className="dashboard-muted">Highest-confidence runtime consoles.</Typography.Text>
            </div>
          </div>
          <div className="dashboard-ready-list">
            {dashboard.ready_consoles.slice(0, 6).map((console) => (
              <button key={console.guid || console.module} type="button" onClick={() => onOpenConsole(console.guid)}>
                <CheckCircleOutlined />
                <span>{console.module}</span>
                <strong>{console.completion}%</strong>
              </button>
            ))}
          </div>
        </article>

        <article className="dashboard-panel">
          <div className="dashboard-panel-header">
            <div>
              <Typography.Title level={3}>Attention Shortcuts</Typography.Title>
              <Typography.Text className="dashboard-muted">Fast paths for common admin work.</Typography.Text>
            </div>
          </div>
          <div className="dashboard-shortcuts">
            <Button icon={<WarningOutlined />} onClick={onOpenModules}>
              Console readiness
            </Button>
            <Button icon={<CloudDownloadOutlined />} onClick={onOpenProviders}>
              Provider registry
            </Button>
            {dashboard.users.visible && (
              <Button icon={<KeyOutlined />} onClick={onOpenUsers}>
                User access
              </Button>
            )}
          </div>
        </article>
      </section>
    </section>
  )
}

export default DashboardPage
