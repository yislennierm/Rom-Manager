import { useCallback, useEffect, useState } from 'react'
import { Alert, Button, Card, Descriptions, Flex, Input, Popconfirm, Progress, Space, Table, Tag, Typography } from 'antd'
import { apiFetch } from '../api'
import type { RomEntry } from '../types'

type Job = {
  id: string; title: string; destination: string; size: number; state: string; system: string
  downloaded: number; error: string | null; attempts: number
  preparation?: { downloaded?: number; total?: number }
}
type Snapshot = {
  device: null | { name: string; account_id: string; enabled: boolean; online: boolean; last_seen: number | null; assigned_systems?: string[]
    telemetry: { games_root?: string; free_bytes?: number; total_bytes?: number; core_present?: boolean; architecture?: string
      systems?: Record<string, { games_root?: string; free_bytes?: number; core_present?: boolean; error?: string }> } }
  jobs: Job[]
}

async function readResponse(response: Response) {
  const payload = await response.json()
  if (!response.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : 'MiSTer request failed')
  return payload
}

export function MisterDownload({ entry, dataset, onOpen }: { entry: RomEntry; dataset?: string; onOpen: () => void }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [jobState, setJobState] = useState('')
  const supported = entry.mister?.supported && dataset
  const enqueue = async () => {
    setBusy(true)
    setError('')
    try {
      const result = await readResponse(await apiFetch('/mister/admin/jobs', { method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ dataset, entry_key: entry.mister?.entry_key }) }))
      setJobState(result.job.state)
    } catch (err) { setError((err as Error).message) } finally { setBusy(false) }
  }
  return <Flex vertical gap="small">
    <Space wrap>
      <Button type="primary" disabled={!supported || Boolean(jobState)} loading={busy} onClick={enqueue}>
        {jobState === 'completed' ? 'Installed on MiSTer' : jobState === 'failed' ? 'Existing job failed' : jobState ? 'Queued for MiSTer' : 'Download to MiSTer'}
      </Button>
      <Button onClick={onOpen}>MiSTer device & jobs</Button>
    </Space>
    {!supported && <Typography.Text type="secondary">{entry.mister?.reason || 'Refresh the catalog to check MiSTer compatibility.'}</Typography.Text>}
    {supported && ['zip_member', 'archive'].includes(entry.mister?.source_kind || '') && <Typography.Text type="secondary">MiSTer downloads the provider source, prepares the selected ROM locally, and verifies it before installation.</Typography.Text>}
    {jobState && <Typography.Text>{jobState === 'completed' ? 'MiSTer has already verified and installed this ROM.' : jobState === 'failed' ? 'Open MiSTer device & jobs to review the error and retry.' : 'Run Scripts → roms_manager on MiSTer to sync pending downloads.'}</Typography.Text>}
    {error && <Alert type="error" message={error} showIcon />}
  </Flex>
}

export default function MisterPage() {
  const [snapshot, setSnapshot] = useState<Snapshot>({ device: null, jobs: [] })
  const [error, setError] = useState('')
  const [pollError, setPollError] = useState('')
  const [busy, setBusy] = useState(false)
  const [backendUrl, setBackendUrl] = useState(window.location.origin)
  const [configReady, setConfigReady] = useState(false)
  const refresh = useCallback(async (signal?: AbortSignal) => {
    try {
      const result = await readResponse(await apiFetch('/mister/admin', { signal }))
      if (!signal?.aborted) { setSnapshot(result); setPollError('') }
    } catch (err) { if (!signal?.aborted) setPollError((err as Error).message) }
  }, [])
  useEffect(() => {
    const controller = new AbortController()
    void refresh(controller.signal)
    const timer = window.setInterval(() => void refresh(controller.signal), 3000)
    return () => { controller.abort(); window.clearInterval(timer) }
  }, [refresh])

  const action = async (path: string, method: string, body?: object) => {
    setBusy(true)
    setError('')
    try {
      const result = await readResponse(await apiFetch(path, { method,
        headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined }))
      await refresh()
      return result
    } catch (err) { setError((err as Error).message) } finally { setBusy(false) }
  }
  const provision = async () => {
    try {
      const url = new URL(backendUrl)
      if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) throw new Error('Enter a plain HTTP or HTTPS backend address.')
      if (['localhost', '127.0.0.1', '[::1]', '0.0.0.0'].includes(url.hostname)) throw new Error('Use the backend computer’s LAN hostname or IP so MiSTer can reach it.')
      const result = await action('/mister/admin/device', 'POST', { name: 'MiSTer' })
      if (!result) return
      const config = { backend_url: backendUrl.replace(/\/$/, ''), device_token: result.device_token, games_root: '' }
      const objectUrl = URL.createObjectURL(new Blob([JSON.stringify(config, null, 2) + '\n'], { type: 'application/json' }))
      const link = document.createElement('a')
      link.href = objectUrl
      link.download = 'config.json'
      link.click()
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000)
      setConfigReady(true)
    } catch (err) { setError((err as Error).message) }
  }
  const device = snapshot.device
  const telemetry = device?.telemetry
  const assigned = device?.assigned_systems || (device ? ['snes'] : [])
  return <Flex vertical gap="large">
    <Card className="detail-card" title="MiSTer FPGA">
      <Flex vertical gap="middle">
        <Typography.Paragraph>Queue a supported assigned ROM assigned to the client account, then run Scripts → roms_manager on MiSTer. Sync runs only when you launch it.</Typography.Paragraph>
        {(error || pollError) && <Alert type="error" message={error || pollError} showIcon />}
        <Descriptions column={{ xs: 1, md: 2 }} items={[
          { key: 'device', label: 'Device', children: device?.name || 'Not set up' },
          { key: 'account', label: 'Client account', children: device?.account_id ? <Typography.Link href="?view=access-root">{device.account_id} · Users & access</Typography.Link> : 'Created during setup' },
          { key: 'state', label: 'Status', children: <Tag color={device?.enabled ? (device.online ? 'green' : 'blue') : 'default'}>{!device ? 'Not set up' : !device.enabled ? 'Account disabled or no supported console assigned' : device.online ? 'Active recently' : 'Idle'}</Tag> },
          { key: 'consoles', label: 'Assigned supported consoles', children: assigned.map(system => system.toUpperCase()).join(', ') || 'None' },
          { key: 'contact', label: 'Last contact', children: device?.last_seen ? new Date(device.last_seen * 1000).toLocaleString() : 'No client contact yet' },
          { key: 'root', label: 'Games folders', children: telemetry?.systems ? assigned.map(system => <div key={system}>{system.toUpperCase()}: {telemetry.systems?.[system]?.games_root || 'Unavailable'}</div>) : telemetry?.games_root || 'Discovered by client on first sync' },
          { key: 'space', label: 'Available storage', children: telemetry?.free_bytes !== undefined ? `${(telemetry.free_bytes / 1024 ** 3).toFixed(2)} GB free of ${((telemetry.total_bytes || 0) / 1024 ** 3).toFixed(2)} GB` : 'Not reported' },
          { key: 'core', label: 'FPGA cores', children: telemetry?.systems ? assigned.map(system => <div key={system}>{system.toUpperCase()}: {telemetry.systems?.[system]?.core_present ? 'Installed' : 'Not found'}</div>) : telemetry?.core_present === undefined ? 'Not checked' : telemetry.core_present ? 'Installed' : 'Not found' },
        ]} />
        <label htmlFor="mister-backend-url">Backend address reachable from MiSTer</label>
        <Input id="mister-backend-url" value={backendUrl} onChange={e => setBackendUrl(e.target.value)} placeholder="http://192.168.1.45:8000" />
        <Space wrap>
          <Popconfirm title={device ? 'Generate a replacement device config?' : 'Set up this MiSTer?'} description="This creates or enables the MiSTer client account and rotates its backend API key. Previous configs stop working." onConfirm={provision}>
            <Button type="primary" loading={busy}>Generate device config</Button>
          </Popconfirm>
          {device?.enabled && <Popconfirm title="Disable the MiSTer client account?" onConfirm={() => action('/mister/admin/device/credential', 'DELETE')}><Button danger disabled={busy}>Disable client account</Button></Popconfirm>}
          <Button onClick={() => void refresh()}>Refresh</Button>
        </Space>
        {configReady && <Alert type="success" message="Device config downloaded" description="Install config.json beside roms_manager_client.py in Scripts/.config/roms_manager on MiSTer. Keep the credential private. Setup instructions: docs/mister-mvp.md in the repository." />}
      </Flex>
    </Card>
    <Card className="detail-card" title="Download jobs">
      <Table<Job> rowKey="id" dataSource={snapshot.jobs} scroll={{ x: 700 }} pagination={{ pageSize: 10 }} columns={[
        { title: 'Game', dataIndex: 'title', render: (title: string, job) => <Flex vertical><Typography.Text>{job.system?.toUpperCase()} · {title}</Typography.Text><Typography.Text type="secondary">{job.destination}</Typography.Text></Flex> },
        { title: 'State', dataIndex: 'state', render: (state: string) => <Tag color={state === 'failed' ? 'red' : state === 'completed' ? 'green' : 'blue'}>{state}</Tag> },
        { title: 'Progress', width: 150, render: (_, job) => <Progress percent={Math.min(100, Math.floor(job.state === 'preparing' ? (job.preparation?.downloaded || 0) * 100 / (job.preparation?.total || 1) : job.downloaded * 100 / Math.max(1, job.size)))} status={job.state === 'failed' ? 'exception' : job.state === 'completed' ? 'success' : job.state === 'preparing' ? 'active' : 'normal'} /> },
        { title: 'Details', dataIndex: 'error' },
        { title: 'Actions', render: (_, job) => job.state === 'failed' && <Button disabled={busy} onClick={() => void action(`/mister/admin/jobs/${job.id}/retry`, 'POST')}>Retry</Button> },
      ]} locale={{ emptyText: 'No jobs yet. Open a supported ROM detail and choose Download to MiSTer.' }} />
    </Card>
  </Flex>
}
