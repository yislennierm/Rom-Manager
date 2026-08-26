import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  ConfigProvider,
  Descriptions,
  Empty,
  Form,
  Flex,
  Input,
  Layout,
  List,
  Modal,
  Segmented,
  Select,
  Space,
  Spin,
  Statistic,
  Table,
  Tag,
  Transfer,
  Typography,
  message,
  theme,
} from 'antd'
import type { MenuProps } from 'antd'
import type { DataNode } from 'antd/es/tree'
import {
  CloudDownloadOutlined,
  EyeOutlined,
  FolderOpenOutlined,
  HomeOutlined,
  PlusOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  SettingOutlined,
} from '@ant-design/icons'
import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'
import { apiFetch, getAuthToken, isPreviewMode, setAuthToken } from './api'
import ModulesList from './components/ModulesList'
import SummaryCards from './components/SummaryCards'
import NavigationSider from './components/NavigationSider'
import TopHeader from './components/TopHeader'
import DetailPanel from './components/DetailPanel'
import ModuleReadinessCard from './components/ModuleReadinessCard'
import ConsoleInfoCard from './components/ConsoleInfoCard'
import useModulesData from './hooks/useModulesData'
import useProvidersData from './hooks/useProvidersData'
import useRomData from './hooks/useRomData'
import type {
  AccessUser,
  BrandSelection,
  ConsoleSelection,
  DatasetCard,
  DatasetKey,
  DatasetMeta,
  ModuleEntry,
  ModuleReadiness,
  ProviderCoverage,
  ProviderEntry,
  ProviderSelection,
  ProvidersResponse,
  RomBrandSelection,
  RomConsoleSelection,
  RomSetMeta,
  Selection,
} from './types'
import './App.css'

dayjs.extend(relativeTime)

const logoUrl = `${import.meta.env.BASE_URL}logo.webp`

const DATASETS: DatasetCard[] = [
  {
    key: 'modules',
    title: 'Libretro modules',
    description: 'Source of GUIDs, console metadata and artwork slugs.',
    endpoint: '/update?target=modules',
    accent: '#38bdf8',
  },
  {
    key: 'providers',
    title: 'Providers registry',
    description: 'Download mirrors, torrents and cache metadata.',
    endpoint: '/update?target=providers',
    accent: '#c084fc',
  },
]

const PROVIDER_FILE_FIELDS = [
  { key: 'meta_sqlite', label: 'Meta SQLite URL' },
  { key: 'files_xml', label: 'Files XML URL' },
  { key: 'torrent', label: 'Torrent URL' },
  { key: 'meta_xml', label: 'Meta XML URL' },
  { key: 'reviews_xml', label: 'Reviews XML URL' },
]

const formatTimestamp = (value?: string) => {
  if (!value) {
    return 'Never fetched'
  }
  return dayjs(value).format('YYYY-MM-DD HH:mm:ss')
}

const formatBytes = (size?: number) => {
  if (!size || Number.isNaN(size)) {
    return '—'
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

function App() {
  const [apiKey, setApiKey] = useState(() => getAuthToken())
  const previewMode = isPreviewMode()
  const [currentUser, setCurrentUser] = useState<AccessUser | null>(null)
  const [authLoading, setAuthLoading] = useState(false)
  const [authError, setAuthError] = useState<string | null>(null)
  const [accessUsers, setAccessUsers] = useState<AccessUser[]>([])
  const [accessLoading, setAccessLoading] = useState(false)
  const [accessError, setAccessError] = useState<string | null>(null)
  const [generatedApiKey, setGeneratedApiKey] = useState<string | null>(null)
  const [meta, setMeta] = useState<Record<DatasetKey, DatasetMeta | null>>({
    modules: null,
    providers: null,
  })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const { providerData, providerLoading, providerError, fetchProviders } = useProvidersData(apiKey)
  const { modulesData, setModulesData, modulesLoading, fetchModulesPayload } = useModulesData(apiKey)
  const [providerFetchRunning, setProviderFetchRunning] = useState(false)
  const [providerExportRunning, setProviderExportRunning] = useState(false)
  const [validationRunning, setValidationRunning] = useState(false)
  const [providerStatus, setProviderStatus] = useState<Record<string, any> | null>(null)
  const [providerCoverage, setProviderCoverage] = useState<ProviderCoverage | null>(null)
  const [providerCoverageLoading, setProviderCoverageLoading] = useState(false)
  const [moduleReadiness, setModuleReadiness] = useState<ModuleReadiness | null>(null)
  const [moduleReadinessLoading, setModuleReadinessLoading] = useState(false)
  const [rdbExportRunning, setRdbExportRunning] = useState(false)
  const {
    romSets,
    romMetaLoading,
    romError,
    romEntriesCache,
    romEntriesLoading,
    fetchRomMetadata,
    fetchRomEntries,
  } = useRomData(apiKey)
  const [romViewMode, setRomViewMode] = useState<'list' | 'cards'>('list')
  const [selectedKeys, setSelectedKeys] = useState<string[]>(['home'])
  const [selection, setSelection] = useState<Selection>({ kind: 'home' })
  const [searchQuery, setSearchQuery] = useState('')
  const [openKeys, setOpenKeys] = useState<string[]>(['modules-root', 'providers-root'])
  const [navCollapsed, setNavCollapsed] = useState(false)
  const [providerModalMode, setProviderModalMode] = useState<'edit' | 'create'>('edit')
  const [providerModalTarget, setProviderModalTarget] = useState<{ brand?: string; console?: string }>({})
  const [isEditModalVisible, setEditModalVisible] = useState(false)
  const [isModuleModalVisible, setModuleModalVisible] = useState(false)
  const [isAccessModalVisible, setAccessModalVisible] = useState(false)
  const [accessModalTarget, setAccessModalTarget] = useState<AccessUser | null>(null)
  const [form] = Form.useForm()
  const [moduleForm] = Form.useForm()
  const [accessForm] = Form.useForm()
  const [loginForm] = Form.useForm()
  const [messageApi, contextHolder] = message.useMessage()

  const fetchMeta = useCallback(async () => {
    if (!apiKey) {
      setMeta({ modules: null, providers: null })
      return
    }
    setLoading(true)
    setError(null)
    try {
      const [modules, providers] = await Promise.all(
        DATASETS.map(async (dataset) => {
          const response = await apiFetch(`/update/meta?target=${dataset.key}`)
          if (!response.ok) {
            throw new Error(`Failed to load ${dataset.title}`)
          }
          return (await response.json()) as DatasetMeta
        }),
      )
      setMeta({
        modules,
        providers,
      })
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchMeta()
  }, [apiKey, fetchMeta])

  const fetchCurrentUser = useCallback(async () => {
    if (!apiKey) {
      setCurrentUser(null)
      setAuthError(null)
      return null
    }
    setAuthLoading(true)
    setAuthError(null)
    try {
      const response = await apiFetch('/me')
      if (!response.ok) {
        throw new Error(await response.text())
      }
      const payload = await response.json()
      const user = payload.user as AccessUser
      if (!user.admin && !previewMode) {
        setAuthToken('')
        setApiKey('')
        throw new Error('Admin account required')
      }
      setCurrentUser(user)
      return user
    } catch (err) {
      setCurrentUser(null)
      setAuthError(err instanceof Error ? err.message : 'Login failed')
      return null
    } finally {
      setAuthLoading(false)
    }
  }, [apiKey, previewMode])

  const fetchAccessUsers = useCallback(async () => {
    setAccessLoading(true)
    setAccessError(null)
    try {
      const response = await apiFetch('/access/users')
      if (!response.ok) {
        throw new Error(await response.text())
      }
      const payload = await response.json()
      setAccessUsers(Array.isArray(payload.users) ? payload.users : [])
    } catch (err) {
      setAccessUsers([])
      setAccessError(err instanceof Error ? err.message : 'Failed to load users')
    } finally {
      setAccessLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchCurrentUser().then((user) => {
      if (user?.admin) {
        fetchAccessUsers()
      } else {
        setAccessUsers([])
      }
    })
  }, [apiKey, fetchAccessUsers, fetchCurrentUser])

  useEffect(() => {
    if (selection?.kind === 'rom-console') {
      fetchRomEntries(selection.meta)
    }
  }, [selection, fetchRomEntries])

  const headerTagline = useMemo(() => {
    const moduleVersion = meta.modules?.version
    const providerVersion = meta.providers?.version
    if (!moduleVersion && !providerVersion) {
      return 'No datasets fetched yet.'
    }
    return `Modules synced ${moduleVersion ? dayjs(moduleVersion).fromNow() : 'never'}, providers synced ${
      providerVersion ? dayjs(providerVersion).fromNow() : 'never'
    }.`
  }, [meta.modules?.version, meta.providers?.version])

  const consolesRoot = providerData?.providers?.console_root ?? {}
  const selectedProvider = selection?.kind === 'collection' ? selection : null
  const selectedBrand = selection?.kind === 'brand' ? selection : null
  const selectedConsole = selection?.kind === 'console' ? selection : null
  const selectedModule = selection?.kind === 'module' ? selection : null
  const selectedRomBrand = selection?.kind === 'rom-brand' ? selection : null
  const selectedRomConsole = selection?.kind === 'rom-console' ? selection : null
  const isRomContext = Boolean(selectedRomBrand || selectedRomConsole)
  const showHomeSummary = !isRomContext && (!selection || selection.kind === 'home')
  const showProvidersOverview = selection?.kind === 'providers-root'
  const showAccessOverview = selection?.kind === 'access-root'
  const isModulesDataset = selection?.kind === 'dataset' && selection.dataset === 'modules'
  const moduleByGuid = useMemo(() => {
    const lookup: Record<string, ModuleEntry> = {}
    modulesData.forEach((module) => {
      if (module.guid) {
        lookup[module.guid] = module
      }
    })
    return lookup
  }, [modulesData])

  const providerStats = useMemo(() => {
    const brands = Object.keys(consolesRoot).length
    let consoleCount = 0
    let providerCount = 0
    Object.values(consolesRoot).forEach((consoles) => {
      const consoleList = Object.values(consoles ?? {})
      consoleCount += consoleList.length
      consoleList.forEach((entry) => {
        if (Array.isArray(entry)) {
          providerCount += entry.length
        } else if (entry) {
          providerCount += 1
        }
      })
    })
    return { brands, consoleCount, providerCount }
  }, [consolesRoot])

  const moduleOptions = useMemo(
    () =>
      modulesData
        .filter((module): module is ModuleEntry & { guid: string } => Boolean(module.guid))
        .map((module) => ({
          label: `${module.name ?? module.guid} (${module.guid?.slice(0, 8)}…)`,
          value: module.guid!,
        })),
    [modulesData],
  )

  const getCollectionKeySuffix = (entry: ProviderEntry, index: number) =>
    entry.archive_id || `collection-${index}`

  const getCollectionLabel = (entry: ProviderEntry, suffix: string) =>
    entry.archive_id || entry.provider || entry.name || suffix

  const getModuleNodeKey = (entry: ModuleEntry, index: number) =>
    `module:${entry.guid ?? `module-${index}`}`

  const providerTree = useMemo(() => {
    const detailLookup: Record<string, ProviderSelection> = {}
    const treeNodes: DataNode[] = []

    const consolesRoot = providerData?.providers?.console_root ?? {}
    Object.entries(consolesRoot).forEach(([brand, consoles]) => {
      const consoleNodes: DataNode[] = []
      Object.entries(consoles ?? {}).forEach(([consoleName, entry]) => {
        const entries = Array.isArray(entry) ? entry : [entry]
        const collectionNodes: DataNode[] = entries.map((collection, index) => {
          const suffix = getCollectionKeySuffix(collection, index)
          const key = `provider:${brand}:${consoleName}:${suffix}`
          const collectionLabel = getCollectionLabel(collection, suffix)
          detailLookup[key] = {
            kind: 'collection',
            key,
            brand,
            console: consoleName,
            collectionLabel,
            data: collection,
            archiveId: collection.archive_id || suffix,
            nodeSuffix: suffix,
            entryIndex: index,
          }
          return {
            key,
            title: collectionLabel,
            icon: <CloudDownloadOutlined />,
          }
        })

        consoleNodes.push({
          key: `provider:${brand}:${consoleName}`,
          title: consoleName,
          selectable: true,
          children: collectionNodes,
          icon: <FolderOpenOutlined />,
        })
      })

      treeNodes.push({
        key: `provider:${brand}`,
        title: brand,
        selectable: true,
        children: consoleNodes,
        icon: <FolderOpenOutlined />,
      })
    })

    return {
      treeNodes,
      detailLookup,
    }
  }, [providerData])

  const getConsoleEntriesFromDataset = (
    dataset: ProvidersResponse | null,
    brand: string,
    consoleName: string,
  ): ProviderEntry[] => {
    const root = dataset?.providers?.console_root ?? {}
    const consoleEntry = root[brand]?.[consoleName]
    if (!consoleEntry) {
      return []
    }
    return Array.isArray(consoleEntry) ? consoleEntry : [consoleEntry]
  }

  const buildProviderSelection = (
    dataset: ProvidersResponse | null,
    brand: string,
    consoleName: string,
    archiveId?: string,
  ): ProviderSelection | null => {
    const entries = getConsoleEntriesFromDataset(dataset, brand, consoleName)
    if (!entries.length) {
      return null
    }
    const targetIndex = archiveId
      ? entries.findIndex((entry) => entry.archive_id === archiveId)
      : entries.length - 1
    if (targetIndex < 0) {
      return null
    }
    const entry = entries[targetIndex]
    const suffix = getCollectionKeySuffix(entry, targetIndex)
    return {
      kind: 'collection',
      key: `provider:${brand}:${consoleName}:${suffix}`,
      brand,
      console: consoleName,
      collectionLabel: getCollectionLabel(entry, suffix),
      data: entry,
      archiveId: entry.archive_id || suffix,
      nodeSuffix: suffix,
      entryIndex: targetIndex,
    }
  }

  const romTree = useMemo(() => {
    const brandSet = new Set<string>()
    romSets.forEach((meta) => {
      brandSet.add(meta.brand || 'Other')
    })
    const treeNodes: DataNode[] = Array.from(brandSet).map((brand) => ({
      key: `roms:${brand}`,
      title: brand,
      selectable: true,
      icon: <FolderOpenOutlined />,
    }))
    return { treeNodes }
  }, [romSets])

  const moduleTreeNodes = useMemo<DataNode[]>(() => {
    return modulesData.map((module, index) => {
      const key = getModuleNodeKey(module, index)
      const label = module.name || module.guid || `Module ${index + 1}`
      return {
        key,
        title: label,
        icon: <CloudDownloadOutlined />,
        selectable: true,
      }
    })
  }, [modulesData])

  const navigationTree = useMemo<DataNode[]>(() => {
    const homeNode: DataNode = {
      key: 'home',
      title: 'Home',
      selectable: true,
      icon: <HomeOutlined />,
    }
    const modulesNode: DataNode = {
      key: 'modules-root',
      title: 'Consoles',
      selectable: true,
      children: moduleTreeNodes,
      icon: <FolderOpenOutlined />,
    }
    const providersNode: DataNode = {
      key: 'providers-root',
      title: 'Providers',
      selectable: true,
      children: providerTree.treeNodes,
      icon: <FolderOpenOutlined />,
    }
    const romsNode: DataNode = {
      key: 'roms-root',
      title: 'ROMs',
      selectable: false,
      children: romTree.treeNodes,
      icon: <FolderOpenOutlined />,
    }
    const settingsNode: DataNode = {
      key: 'settings-root',
      title: 'Settings',
      selectable: false,
      icon: <SettingOutlined />,
      children: [
        {
          key: 'access-root',
          title: 'Users & access',
          selectable: true,
          icon: <SafetyCertificateOutlined />,
        },
      ],
    }
    return [
      homeNode,
      modulesNode,
      providersNode,
      romsNode,
      ...(currentUser?.admin ? [settingsNode] : []),
    ]
  }, [providerTree.treeNodes, moduleTreeNodes, romTree.treeNodes, currentUser?.admin])

  type LevelKeysProps = {
    key?: string
    children?: LevelKeysProps[]
  }

  const buildLevelKeys = (items: LevelKeysProps[]) => {
    const levels: Record<string, number> = {}
    const walk = (nodes: LevelKeysProps[], level = 1) => {
      nodes.forEach((node) => {
        if (node.key) levels[node.key] = level
        if (node.children) walk(node.children, level + 1)
      })
    }
    walk(items)
    return levels
  }

  const navigationMenuItems = useMemo<MenuProps['items']>(() => {
    const toMenuItems = (nodes: DataNode[]): MenuProps['items'] =>
      nodes.map((node) => ({
        key: node.key as string,
        label: node.title as React.ReactNode,
        icon: node.icon as React.ReactNode,
        children: node.children ? toMenuItems(node.children) : undefined,
      }))
    return toMenuItems(navigationTree)
  }, [navigationTree])

  const navigationWidth = useMemo(() => {
    const labels: string[] = []

    Object.values(providerData?.providers?.console_root ?? {}).forEach((consoles) => {
      Object.keys(consoles ?? {}).forEach((consoleName) => labels.push(consoleName))
    })
    modulesData.forEach((module) => {
      if (module.name) labels.push(module.name)
    })
    romSets.forEach((meta) => {
      const label = meta.console || meta.module || meta.slug
      if (label) labels.push(label)
    })

    if (!labels.length) {
      return 340
    }

    const averageLength = labels.reduce((sum, label) => sum + label.length, 0) / labels.length
    const longestLength = Math.max(...labels.map((label) => label.length))
    const targetChars = Math.max(24, Math.min(48, Math.round(averageLength + longestLength * 0.18)))

    return Math.max(340, Math.min(560, 128 + targetChars * 8))
  }, [modulesData, providerData, romSets])

  const levelKeys = useMemo(() => buildLevelKeys((navigationMenuItems as LevelKeysProps[]) || []), [navigationMenuItems])

  const handleMenuOpenChange: MenuProps['onOpenChange'] = (nextOpenKeys) => {
    const currentOpenKey = nextOpenKeys.find((key) => !openKeys.includes(key))
    if (currentOpenKey !== undefined) {
      const repeatIndex = nextOpenKeys
        .filter((key) => key !== currentOpenKey)
        .findIndex((key) => levelKeys[key] === levelKeys[currentOpenKey])
      setOpenKeys(
        nextOpenKeys
          .filter((_, index) => index !== repeatIndex)
          .filter((key) => levelKeys[key] <= levelKeys[currentOpenKey]),
      )
    } else {
      setOpenKeys(nextOpenKeys)
    }
  }

  const handleMenuSelect: MenuProps['onSelect'] = (info) => {
    setOpenKeys((prev) => Array.from(new Set([...prev, ...info.keyPath.slice(1)])))
    handleNavigationSelect(info.key as string)
  }

  const handleNavigationSelect = (key: string) => {
    const keys = [key]
    setSelectedKeys(keys)
    const targetKey = keys[0] as string | undefined
    if (!targetKey) {
      setSelection(null)
      return
    }
    if (targetKey === 'home') {
      setSelection({ kind: 'home' })
      return
    }
    if (targetKey === 'modules-root') {
      setSelection({ kind: 'dataset', dataset: 'modules' })
      return
    }
    if (targetKey === 'providers-root') {
      setSelection({ kind: 'providers-root' })
      return
    }
    if (targetKey === 'access-root') {
      setSelection({ kind: 'access-root' })
      return
    }
    if (targetKey.startsWith('provider:')) {
      const [, brand, consoleName, suffix] = targetKey.split(':')
      if (brand && consoleName && suffix) {
        const detail = providerTree.detailLookup[targetKey]
        if (detail) {
          setSelection(detail)
        }
        return
      }
      if (brand && consoleName) {
        setSelection({ kind: 'console', brand, console: consoleName })
        return
      }
      if (brand) {
        setSelection({ kind: 'brand', brand })
        return
      }
    } else if (targetKey.startsWith('module:')) {
      const moduleEntryIndex = modulesData.findIndex((module, idx) => getModuleNodeKey(module, idx) === targetKey)
      const data = modulesData[moduleEntryIndex]
      if (data) {
        setSelection({
          kind: 'module',
          index: moduleEntryIndex,
          data,
        })
        return
      }
    } else if (targetKey.startsWith('roms:')) {
      const [, brand] = targetKey.split(':')
      setSelection({ kind: 'rom-brand', brand })
      return
    }
    setSelection(null)
  }

  const fetchProviderStatus = useCallback(async (selection: ProviderSelection | null) => {
    if (!selection) {
      setProviderStatus(null)
      return
    }
    try {
      const response = await apiFetch('/providers/status', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          brand: selection.brand,
          console: selection.console,
          provider_slug: selection.archiveId,
        }),
      })
      if (!response.ok) {
        throw new Error(await response.text())
      }
      const payload = await response.json()
      setProviderStatus(payload.status || null)
    } catch (err) {
      setProviderStatus(null)
      console.error('Failed to fetch provider status', err)
    }
  }, [])

  const fetchProviderCoverage = useCallback(async (target: { brand: string; console: string; guid?: string } | null) => {
    if (!target) {
      setProviderCoverage(null)
      return
    }
    setProviderCoverageLoading(true)
    try {
      const response = await apiFetch('/providers/coverage', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(target),
      })
      if (!response.ok) {
        throw new Error(await response.text())
      }
      setProviderCoverage((await response.json()) as ProviderCoverage)
    } catch (err) {
      setProviderCoverage(null)
      console.error('Failed to fetch provider coverage', err)
    } finally {
      setProviderCoverageLoading(false)
    }
  }, [])

  const fetchModuleReadiness = useCallback(async (target: { guid?: string; name?: string } | null) => {
    if (!target) {
      setModuleReadiness(null)
      return
    }
    setModuleReadinessLoading(true)
    try {
      const response = await apiFetch('/modules/readiness', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(target),
      })
      if (!response.ok) {
        throw new Error(await response.text())
      }
      setModuleReadiness((await response.json()) as ModuleReadiness)
    } catch (err) {
      setModuleReadiness(null)
      console.error('Failed to fetch module readiness', err)
    } finally {
      setModuleReadinessLoading(false)
    }
  }, [])

  const handleRefreshAll = useCallback(() => {
    fetchMeta()
    fetchProviders()
    fetchModulesPayload()
    fetchRomMetadata()
  }, [fetchMeta, fetchProviders, fetchModulesPayload, fetchRomMetadata])

  const handleApiKeyChange = useCallback((value: string) => {
    setAuthToken(value)
    setApiKey(value.trim())
  }, [])

  const handleLoginSubmit = async () => {
    try {
      const values = await loginForm.validateFields()
      setAuthLoading(true)
      setAuthError(null)
      const response = await fetch('/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          username: values.username,
          password: values.password,
        }),
      })
      if (!response.ok) {
        throw new Error(await response.text())
      }
      const payload = await response.json()
      handleApiKeyChange(payload.access_token || '')
      setCurrentUser(payload.user ?? null)
    } catch (err) {
      if (err && typeof err === 'object' && 'errorFields' in err) {
        return
      }
      setAuthError(err instanceof Error ? err.message : 'Login failed')
    } finally {
      setAuthLoading(false)
    }
  }

  const handleLogout = useCallback(() => {
    setAuthToken('')
    setApiKey('')
    setCurrentUser(null)
    setAccessUsers([])
    setSelectedKeys(['home'])
    setSelection({ kind: 'home' })
    loginForm.resetFields()
  }, [loginForm])

  const handleDownloadDataset = useCallback(
    async (dataset: DatasetCard) => {
      try {
        const response = await apiFetch(dataset.endpoint)
        if (!response.ok) {
          throw new Error(await response.text())
        }
        const blob = await response.blob()
        const url = URL.createObjectURL(blob)
        const link = document.createElement('a')
        link.href = url
        link.download = `${dataset.key}.json`
        document.body.appendChild(link)
        link.click()
        link.remove()
        URL.revokeObjectURL(url)
      } catch (err) {
        messageApi.error(err instanceof Error ? err.message : `Failed to download ${dataset.title}`)
      }
    },
    [messageApi],
  )

  const openCreateAccessModal = () => {
    setAccessModalTarget(null)
    setGeneratedApiKey(null)
    accessForm.setFieldsValue({
      id: '',
      name: '',
      enabled: true,
      admin: false,
      allowed_console_guids: [],
    })
    setAccessModalVisible(true)
  }

  const openEditAccessModal = (user: AccessUser) => {
    setAccessModalTarget(user)
    setGeneratedApiKey(null)
    accessForm.setFieldsValue({
      id: user.id,
      name: user.name ?? '',
      enabled: user.enabled,
      admin: user.admin,
      allowed_console_guids: user.admin ? [] : user.allowed_console_guids ?? [],
    })
    setAccessModalVisible(true)
  }

  const closeAccessModal = () => {
    setAccessModalVisible(false)
    setAccessModalTarget(null)
    accessForm.resetFields()
  }

  const handleAccessSubmit = async () => {
    try {
      const values = await accessForm.validateFields()
      const isEdit = Boolean(accessModalTarget)
      const admin = Boolean(values.admin)
      const body = {
        name: values.name || values.id,
        enabled: values.enabled ?? true,
        admin,
        allowed_console_guids: admin ? [] : values.allowed_console_guids ?? [],
      }
      const response = await apiFetch(isEdit ? `/access/users/${accessModalTarget?.id}` : '/access/users', {
        method: isEdit ? 'PATCH' : 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(isEdit ? body : { id: values.id, ...body, generate_api_key: true }),
      })
      if (!response.ok) {
        throw new Error(await response.text())
      }
      const payload = await response.json()
      setGeneratedApiKey(payload.api_key ?? null)
      await fetchAccessUsers()
      if (!payload.api_key) {
        closeAccessModal()
      }
      messageApi.success(isEdit ? 'User updated' : 'User created')
    } catch (err) {
      if (err && typeof err === 'object' && 'errorFields' in err) {
        return
      }
      messageApi.error(err instanceof Error ? err.message : 'Failed to save user')
    }
  }

  const handleResetAccessKey = async (user: AccessUser) => {
    try {
      const response = await apiFetch(`/access/users/${user.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reset_api_key: true }),
      })
      if (!response.ok) {
        throw new Error(await response.text())
      }
      const payload = await response.json()
      setGeneratedApiKey(payload.api_key ?? null)
      setAccessModalTarget(user)
      setAccessModalVisible(true)
      await fetchAccessUsers()
    } catch (err) {
      messageApi.error(err instanceof Error ? err.message : 'Failed to reset API key')
    }
  }

  const handlePreviewUser = async (user: AccessUser) => {
    try {
      const response = await apiFetch(`/access/users/${user.id}/preview-link`, {
        method: 'POST',
      })
      if (!response.ok) {
        throw new Error(await response.text())
      }
      const payload = await response.json()
      const previewUrl = payload.url || `/admin/?api_key=${encodeURIComponent(payload.api_key)}`
      window.open(previewUrl, '_blank', 'noopener,noreferrer')
    } catch (err) {
      messageApi.error(err instanceof Error ? err.message : 'Failed to open user preview')
    }
  }

  const handleToggleModuleIgnore = (guid: string | undefined, next: boolean, index: number) => {
    setModulesData((prev) => {
      const nextModules = [...prev]
      const target = { ...(nextModules[index] || {}) }
      if (guid && target.guid && target.guid !== guid) {
        return prev
      }
      target.ignore = next ? 'true' : undefined
      nextModules[index] = target as ModuleEntry
      return nextModules
    })
  }

  useEffect(() => {
    const selectionTyped = selection?.kind === 'collection' ? selection : null
    fetchProviderStatus(selectionTyped)
  }, [selection, fetchProviderStatus])

  useEffect(() => {
    if (selection?.kind === 'collection') {
      fetchProviderCoverage({
        brand: selection.brand,
        console: selection.console,
        guid: selection.data.libretro_guid,
      })
      return
    }
    if (selection?.kind === 'console') {
      const entries = getConsoleEntriesFromDataset(providerData, selection.brand, selection.console)
      const guid = entries.find((entry) => entry.libretro_guid)?.libretro_guid
      fetchProviderCoverage({
        brand: selection.brand,
        console: selection.console,
        guid,
      })
      return
    }
    setProviderCoverage(null)
  }, [selection, providerData, fetchProviderCoverage])

  useEffect(() => {
    if (selection?.kind === 'module') {
      fetchModuleReadiness({
        guid: selection.data.guid,
        name: selection.data.name,
      })
      return
    }
    if (selection?.kind === 'collection') {
      fetchModuleReadiness({
        guid: selection.data.libretro_guid,
      })
      return
    }
    if (selection?.kind === 'console') {
      const entries = getConsoleEntriesFromDataset(providerData, selection.brand, selection.console)
      const guid = entries.find((entry) => entry.libretro_guid)?.libretro_guid
      fetchModuleReadiness(guid ? { guid } : null)
      return
    }
    setModuleReadiness(null)
  }, [selection, providerData, fetchModuleReadiness])

  const getConsoleEntries = (brand: string, consoleName: string): ProviderEntry[] =>
    getConsoleEntriesFromDataset(providerData, brand, consoleName)

  const handleConsoleNavigate = (brand: string, consoleName: string) => {
    const key = `provider:${brand}:${consoleName}`
    setSelectedKeys([key])
    setSelection({ kind: 'console', brand, console: consoleName })
  }

  const handleProviderNavigate = (brand: string, consoleName: string, entry: ProviderEntry, index: number) => {
    const suffix = getCollectionKeySuffix(entry, index)
    const key = `provider:${brand}:${consoleName}:${suffix}`
    setSelectedKeys([key])
    setSelection({
      kind: 'collection',
      key,
      brand,
      console: consoleName,
      collectionLabel: getCollectionLabel(entry, suffix),
      data: entry,
      archiveId: entry.archive_id || suffix,
      nodeSuffix: suffix,
      entryIndex: index,
    })
    setProviderStatus(null)
  }

  const handleRomConsoleNavigate = (meta: RomSetMeta) => {
    const brand = meta.brand || 'Other'
    const slugOrGuid = meta.slug || meta.guid || meta.module || brand
    const key = `roms:${brand}:${slugOrGuid}`
    setSelectedKeys([key])
    setSelection({ kind: 'rom-console', meta })
  }

  const openCreateProviderModal = (brand?: string, consoleName?: string) => {
    setProviderModalMode('create')
    setProviderModalTarget({ brand, console: consoleName })
    form.setFieldsValue({
      brand: brand ?? '',
      console_name: consoleName ?? '',
      name: '',
      provider: '',
      archive_id: '',
      base_url: '',
      size: '',
      updated: '',
      libretro_guid: '',
      rom_extensions: '',
      ...PROVIDER_FILE_FIELDS.reduce(
        (acc, field) => ({
          ...acc,
          [field.key]: '',
        }),
        {},
      ),
    })
    setEditModalVisible(true)
  }

  const openEditModal = () => {
    if (!selectedProvider) {
      return
    }
    setProviderModalMode('edit')
    setProviderModalTarget({ brand: selectedProvider.brand, console: selectedProvider.console })
    const entry = selectedProvider.data
    form.setFieldsValue({
      brand: selectedProvider.brand,
      console_name: selectedProvider.console,
      name: entry.name ?? entry.provider ?? entry.archive_id ?? '',
      provider: entry.provider ?? '',
      archive_id: entry.archive_id ?? '',
      base_url: entry.base_url ?? '',
      size: entry.size ?? '',
      updated: entry.updated ?? '',
      libretro_guid: entry.libretro_guid ?? '',
      rom_extensions: entry.rom_extensions?.join(', ') ?? '',
      ...PROVIDER_FILE_FIELDS.reduce(
        (acc, field) => ({
          ...acc,
          [field.key]: entry.files?.[field.key] ?? '',
        }),
        {},
      ),
    })
    setEditModalVisible(true)
  }

  const handleEditCancel = () => {
    setEditModalVisible(false)
    form.resetFields()
    setProviderModalMode('edit')
    setProviderModalTarget({})
  }

  const handleEditSubmit = async () => {
    if (providerModalMode === 'edit' && !selectedProvider) {
      return
    }
    try {
      const values = await form.validateFields()
      const isCreate = providerModalMode === 'create'
      const editTarget = selectedProvider
      const targetBrand = (isCreate ? values.brand ?? providerModalTarget.brand : editTarget?.brand) ?? ''
      const targetConsole = (isCreate ? values.console_name ?? providerModalTarget.console : editTarget?.console) ?? ''
      const normalizedBrand = targetBrand.trim()
      const normalizedConsole = targetConsole.trim()
      const nameValue = (values.name as string | undefined)?.trim()
      if (!normalizedBrand || !normalizedConsole) {
        messageApi.error('Brand and console are required')
        return
      }
      if (!nameValue) {
        messageApi.error('Collection name is required')
        return
      }
      const filesMap = PROVIDER_FILE_FIELDS.reduce<Record<string, string>>((acc, field) => {
        const value = (values as Record<string, string | undefined>)[field.key]?.trim()
        if (value) {
          acc[field.key] = value
        }
        return acc
      }, {})
      const romExtensions = values.rom_extensions
        ? (values.rom_extensions as string)
            .split(',')
            .map((ext) => ext.trim())
            .filter(Boolean)
        : undefined
      const safeEditTarget = editTarget as ProviderSelection | null
      const baseEntry = !isCreate && safeEditTarget ? safeEditTarget.data : {}
      const updatedEntry: ProviderEntry = {
        ...baseEntry,
        name: nameValue,
        provider: values.provider || undefined,
        archive_id: values.archive_id || undefined,
        base_url: values.base_url || undefined,
        size: values.size || undefined,
        updated: values.updated || undefined,
        libretro_guid: values.libretro_guid || undefined,
        rom_extensions: romExtensions,
        files: Object.keys(filesMap).length ? filesMap : undefined,
      }
      const response = await apiFetch('/providers', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          brand: normalizedBrand,
          console: normalizedConsole,
          entry: updatedEntry,
          previous_archive_id: isCreate ? undefined : safeEditTarget?.archiveId,
        }),
      })
      if (!response.ok) {
        const errorBody = await response.text()
        throw new Error(errorBody || 'Failed to save provider')
      }
      const latest = await fetchProviders()
      if (latest) {
        const selectionData = buildProviderSelection(
          latest,
          normalizedBrand,
          normalizedConsole,
          updatedEntry.archive_id || safeEditTarget?.archiveId,
        )
        if (selectionData) {
          setSelectedKeys([selectionData.key])
          setSelection(selectionData)
        } else {
          setSelection(null)
        }
      }
      handleEditCancel()
      messageApi.success(isCreate ? 'Provider created' : 'Provider updated')
    } catch (err) {
      if (err && typeof err === 'object' && 'errorFields' in err) {
        return
      }
      const messageText = err instanceof Error ? err.message : 'Failed to save provider'
      messageApi.error(messageText)
    }
  }

  const handleDuplicate = async () => {
    if (!selectedProvider) {
      return
    }
    try {
      const brand = selectedProvider.brand
      const consoleName = selectedProvider.console
      const existingEntries = getConsoleEntriesFromDataset(providerData, brand, consoleName)
      const existingSuffixes = new Set(
        existingEntries.map((entry, idx) => getCollectionKeySuffix(entry, idx)),
      )
      const baseSuffix =
        selectedProvider.archiveId?.replace(/\s+/g, '-').toLowerCase() || `${selectedProvider.nodeSuffix}-copy`
      let suffix = `${baseSuffix}-copy`
      let counter = 1
      while (existingSuffixes.has(suffix)) {
        suffix = `${baseSuffix}-copy-${counter++}`
      }
      const duplicateEntry: ProviderEntry = {
        ...selectedProvider.data,
        archive_id: suffix,
        updated: dayjs().format('YYYY-MM-DD'),
      }
      const response = await apiFetch('/providers', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ brand, console: consoleName, entry: duplicateEntry }),
      })
      if (!response.ok) {
        const errorBody = await response.text()
        throw new Error(errorBody || 'Unable to duplicate provider')
      }
      const latest = await fetchProviders()
      if (latest) {
        const selectionData = buildProviderSelection(latest, brand, consoleName, suffix)
        if (selectionData) {
          setSelectedKeys([selectionData.key])
          setSelection(selectionData)
        }
      }
      messageApi.success('Provider duplicated')
    } catch (err) {
      messageApi.error(err instanceof Error ? err.message : 'Unable to duplicate provider')
    }
  }

  const handleDelete = () => {
    if (!selectedProvider) {
      return
    }
    const brand = selectedProvider.brand
    const consoleName = selectedProvider.console
    Modal.confirm({
      title: `Delete ${selectedProvider.collectionLabel}?`,
      content: 'This removes the entry from the dataset.',
      okText: 'Delete',
      okButtonProps: { danger: true },
      onOk: async () => {
        try {
          const response = await apiFetch('/providers', {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              brand,
              console: consoleName,
              archive_id: selectedProvider.archiveId,
            }),
          })
          if (!response.ok) {
            const errorBody = await response.text()
            throw new Error(errorBody || 'Unable to delete provider')
          }
          const latest = await fetchProviders()
          if (latest) {
            const remainingEntries = getConsoleEntriesFromDataset(latest, brand, consoleName)
            if (remainingEntries.length > 0) {
              const consoleKey = `provider:${brand}:${consoleName}`
              setSelectedKeys([consoleKey])
              setSelection({ kind: 'console', brand, console: consoleName })
            } else {
              const brandBlock = latest.providers?.console_root?.[brand]
              if (brandBlock && Object.keys(brandBlock).length > 0) {
                const brandKey = `provider:${brand}`
                setSelectedKeys([brandKey])
                setSelection({ kind: 'brand', brand })
              } else {
                setSelectedKeys(['providers-root'])
                setSelection({ kind: 'providers-root' })
              }
            }
          }
          messageApi.success('Provider deleted')
        } catch (err) {
          messageApi.error(err instanceof Error ? err.message : 'Unable to delete provider')
          throw err
        }
      },
    })
  }

  const handleProviderFetchAssets = async () => {
    if (!selectedProvider) {
      return
    }
    setProviderStatus(null)
    const target = selectedProvider
    setProviderFetchRunning(true)
    try {
      const response = await apiFetch('/providers/tasks/fetch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          brand: target.brand,
          console: target.console,
          provider_slug: target.archiveId,
        }),
      })
      if (!response.ok) {
        const errorBody = await response.text()
        throw new Error(errorBody || 'Failed to fetch provider assets')
      }
      const payload = await response.json()
      const summary = payload?.summary
      const summaryKeys = summary && typeof summary === 'object' ? Object.keys(summary) : []
      const summaryLabel = summaryKeys.length ? summaryKeys.join(', ') : 'assets'
      messageApi.success(`Fetched ${summaryLabel} for ${target.collectionLabel}`)
    } catch (err) {
      const messageText = err instanceof Error ? err.message : 'Failed to fetch provider assets'
      messageApi.error(messageText)
    } finally {
      setProviderFetchRunning(false)
    }
  }

  const handleProviderExport = async () => {
    if (!selectedProvider) {
      return
    }
    setProviderStatus(null)
    const target = selectedProvider
    setProviderExportRunning(true)
    try {
      const response = await apiFetch('/providers/tasks/export', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          brand: target.brand,
          console: target.console,
          provider_slug: target.archiveId,
        }),
      })
      if (!response.ok) {
        const errorBody = await response.text()
        throw new Error(errorBody || 'Failed to export ROM catalog')
      }
      const payload = await response.json()
      const count = typeof payload?.count === 'number' ? payload.count : payload?.summary?.count
      const destination = payload?.path || 'cache'
      messageApi.success(
        count
          ? `Exported ${count.toLocaleString()} ROMs to ${destination}`
          : `ROM catalog exported to ${destination}`,
      )
      fetchRomMetadata()
    } catch (err) {
      const messageText = err instanceof Error ? err.message : 'Failed to export ROM catalog'
      messageApi.error(messageText)
    } finally {
      setProviderExportRunning(false)
    }
  }

  const handleValidateProviders = async () => {
    setValidationRunning(true)
    try {
      const response = await apiFetch('/providers/tasks/validate', {
        method: 'POST',
      })
      if (!response.ok) {
        const errorBody = await response.text()
        throw new Error(errorBody || 'Validation request failed')
      }
      const payload = await response.json()
      if (payload?.valid) {
        messageApi.success('providers.json passed validation')
      } else {
        const issues = Array.isArray(payload?.issues) ? payload.issues : []
        if (!issues.length) {
          messageApi.error('providers.json failed validation')
        } else {
          Modal.error({
            title: 'providers.json validation errors',
            content: (
              <div>
                <Typography.Paragraph>
                  Found {issues.length} issue{issues.length === 1 ? '' : 's'}.
                </Typography.Paragraph>
                <ul className="validation-issues">
                  {issues.slice(0, 10).map(
                    (issue: { path?: (string | number)[]; message?: string }, idx: number) => {
                      const rawPath = Array.isArray(issue?.path) ? issue.path : []
                      const label = rawPath.length ? rawPath.join(' / ') : '<root>'
                      return (
                        <li key={idx}>
                          <Typography.Text>
                            {label}: {issue?.message || 'Invalid entry'}
                          </Typography.Text>
                        </li>
                      )
                    },
                  )}
                </ul>
                {issues.length > 10 && (
                  <Typography.Text type="secondary">
                    …and {issues.length - 10} additional issue{issues.length - 10 === 1 ? '' : 's'}.
                  </Typography.Text>
                )}
              </div>
            ),
            width: 520,
          })
          messageApi.error('providers.json has validation errors')
        }
      }
    } catch (err) {
      const messageText = err instanceof Error ? err.message : 'Validation request failed'
      messageApi.error(messageText)
    } finally {
      setValidationRunning(false)
    }
  }

  const handleExportCoverageRdb = async () => {
    if (!providerCoverage?.guid && !providerCoverage?.module?.name) {
      messageApi.error('No module GUID available for RDB export')
      return
    }
    setRdbExportRunning(true)
    try {
      const response = await apiFetch('/modules/tasks/export-rdb', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          guid: providerCoverage.guid,
          name: providerCoverage.module?.name,
        }),
      })
      if (!response.ok) {
        throw new Error(await response.text())
      }
      messageApi.success('RDB exported')
      await fetchRomMetadata()
      await fetchProviderCoverage({
        brand: providerCoverage.brand,
        console: providerCoverage.console,
        guid: providerCoverage.guid,
      })
    } catch (err) {
      messageApi.error(err instanceof Error ? err.message : 'RDB export failed')
    } finally {
      setRdbExportRunning(false)
    }
  }

  // fetchProviderStatus defined above; keep single definition

  const openModuleEditModal = () => {
    if (!selectedModule) {
      return
    }
    const entry = selectedModule.data
    moduleForm.setFieldsValue({
      name: entry.name ?? '',
      path: entry.path ?? '',
      guid: entry.guid ?? '',
      url: entry.url ?? '',
      branch: entry.branch ?? '',
      ignore: entry.ignore ?? '',
      shallow: entry.shallow ?? '',
    })
    setModuleModalVisible(true)
  }

  const handleModuleEditCancel = () => {
    setModuleModalVisible(false)
    moduleForm.resetFields()
  }

  const handleModuleEditSubmit = async () => {
    if (!selectedModule) {
      return
    }
    try {
      const values = await moduleForm.validateFields()
      const updatedEntry: ModuleEntry = {
        ...selectedModule.data,
        name: values.name || undefined,
        path: values.path || undefined,
        guid: values.guid || undefined,
        url: values.url || undefined,
        branch: values.branch || undefined,
        ignore: values.ignore || undefined,
        shallow: values.shallow || undefined,
      }
      setModulesData((prev) => {
        const next = [...prev]
        next[selectedModule.index] = updatedEntry
        return next
      })
      setSelection({
        kind: 'module',
        index: selectedModule.index,
        data: updatedEntry,
      })
      setSelectedKeys([getModuleNodeKey(updatedEntry, selectedModule.index)])
      handleModuleEditCancel()
      messageApi.success('Module updated (not yet saved to disk)')
    } catch (err) {
      if (err && typeof err === 'object' && 'errorFields' in err) {
        return
      }
      const messageText = err instanceof Error ? err.message : 'Failed to update module'
      messageApi.error(messageText)
    }
  }

  const handleModuleDuplicate = () => {
    if (!selectedModule) {
      return
    }
    const baseName = selectedModule.data.name || 'Module'
    const duplicate: ModuleEntry = {
      ...selectedModule.data,
      name: `${baseName} (copy)`,
      guid: selectedModule.data.guid ? `${selectedModule.data.guid}-copy` : undefined,
    }
    setModulesData((prev) => {
      const next = [...prev, duplicate]
      const newIndex = next.length - 1
      setSelection({
        kind: 'module',
        index: newIndex,
        data: duplicate,
      })
      setSelectedKeys([getModuleNodeKey(duplicate, newIndex)])
      return next
    })
    messageApi.success('Module duplicated (not yet saved to disk)')
  }

  const handleModuleDelete = () => {
    if (!selectedModule) {
      return
    }
    Modal.confirm({
      title: `Delete ${selectedModule.data.name ?? 'module'}?`,
      content: 'This only affects the current session. Persistence to disk will arrive later.',
      okText: 'Delete',
      okButtonProps: { danger: true },
      onOk: () => {
        setModulesData((prev) => {
          const next = prev.filter((_, idx) => idx !== selectedModule.index)
          return next
        })
        setSelection(null)
        setSelectedKeys([])
        messageApi.success('Module deleted (not yet saved to disk)')
      },
    })
  }

  const busy = loading || providerLoading

  const renderProviderCoverage = () => {
    if (providerCoverageLoading) {
      return <Spin />
    }
    if (!providerCoverage) {
      return null
    }
    const summary = providerCoverage.summary
    const providerColumns = [
      { title: 'Provider', dataIndex: 'label', key: 'label' },
      { title: 'Archive ID', dataIndex: 'archive_id', key: 'archive_id' },
      { title: 'Source ROMs', dataIndex: 'source_roms', key: 'source_roms', width: 120 },
      { title: 'Matched RDB', dataIndex: 'matched_entries', key: 'matched_entries', width: 120 },
      { title: 'Unique files', dataIndex: 'matched_unique_files', key: 'matched_unique_files', width: 120 },
      {
        title: 'Cache',
        key: 'cache',
        width: 260,
        render: (_: unknown, row: ProviderCoverage['providers'][number]) => (
          <Space size="small" wrap>
            {[
              ['metadata', 'DB'],
              ['listings', 'XML'],
              ['torrent', 'Torrent'],
              ['rom_json', 'Export'],
            ].map(([key, label]) => (
              <Tag key={key} color={row.status?.[key] ? 'green' : 'orange'}>
                {label}
              </Tag>
            ))}
          </Space>
        ),
      },
    ]
    const sampleColumns = [
      { title: 'ROM', dataIndex: 'name', key: 'name' },
      { title: 'Region', dataIndex: 'region', key: 'region', width: 120 },
      { title: 'MD5', dataIndex: 'md5', key: 'md5', width: 260 },
      { title: 'CRC32', dataIndex: 'crc32', key: 'crc32', width: 140 },
    ]

    return (
      <Card size="small" title="Provider Coverage">
        {!providerCoverage.ready && (
          <Alert
            type="warning"
            showIcon
            message={providerCoverage.missing === 'rdb' ? 'RDB export missing' : 'Coverage unavailable'}
            description={providerCoverage.message}
            action={
              providerCoverage.missing === 'rdb' ? (
                <Button size="small" onClick={handleExportCoverageRdb} loading={rdbExportRunning}>
                  Export RDB
                </Button>
              ) : undefined
            }
            className="app-alert"
          />
        )}
        <Flex gap="large" className="summary-stats" wrap>
          <Statistic title="RDB entries" value={summary.rdb_entries} />
          <Statistic title="Providers" value={summary.provider_count} />
          <Statistic title="Matched" value={summary.matched_entries} />
          <Statistic title="Unmatched" value={summary.unmatched_entries} />
          <Statistic title="Coverage" value={summary.coverage_percent} suffix="%" precision={2} />
          <Statistic title="Multi-provider" value={summary.multi_provider_entries} />
        </Flex>
        <Table
          size="small"
          pagination={false}
          rowKey="id"
          columns={providerColumns}
          dataSource={providerCoverage.providers}
        />
        {providerCoverage.unmatched_samples.length > 0 && (
          <div style={{ marginTop: 16 }}>
            <Typography.Title level={5}>Unmatched samples</Typography.Title>
            <Table
              size="small"
              pagination={false}
              rowKey={(row) => row.md5 || row.crc32 || row.name || 'rom'}
              columns={sampleColumns}
              dataSource={providerCoverage.unmatched_samples}
            />
          </div>
        )}
      </Card>
    )
  }

  const renderBrandDetail = (brandSelection: BrandSelection) => {
    const brandConsoles = consolesRoot[brandSelection.brand]
    if (!brandConsoles || Object.keys(brandConsoles).length === 0) {
      return <Empty description="No consoles configured for this brand" />
    }
    return (
      <List
        header={
          <Flex justify="space-between" align="center">
            <Typography.Title level={4} style={{ marginBottom: 0 }}>
              {brandSelection.brand}
            </Typography.Title>
            <Button type="primary" icon={<PlusOutlined />} onClick={() => openCreateProviderModal(brandSelection.brand)}>
              New provider
            </Button>
          </Flex>
        }
        dataSource={Object.entries(brandConsoles)}
        renderItem={([consoleName, entry]) => {
          const count = Array.isArray(entry) ? entry.length : 1
          const sampleEntry = Array.isArray(entry) ? entry[0] : entry
          const moduleName =
            sampleEntry?.libretro_guid && moduleByGuid[sampleEntry.libretro_guid]
              ? moduleByGuid[sampleEntry.libretro_guid].name
              : null
          return (
            <List.Item
              actions={[
                <Button size="small" onClick={() => handleConsoleNavigate(brandSelection.brand, consoleName)}>
                  View
                </Button>,
              ]}
            >
              <List.Item.Meta
                title={consoleName}
                description={
                  moduleName
                    ? `${moduleName} • ${count} collection${count === 1 ? '' : 's'}`
                    : `${count} collection${count === 1 ? '' : 's'}`
                }
              />
            </List.Item>
          )
        }}
      />
    )
  }

  const renderConsoleDetail = (consoleSelection: ConsoleSelection) => {
    const entries = getConsoleEntries(consoleSelection.brand, consoleSelection.console)
    const consoleGuid = entries.find((entry) => entry.libretro_guid)?.libretro_guid
    const consoleModule = consoleGuid ? moduleByGuid[consoleGuid]?.name : undefined
    if (!entries.length) {
      return (
        <Flex vertical gap="large">
          <ConsoleInfoCard
            brand={consoleSelection.brand}
            console={consoleSelection.console}
            guid={consoleGuid}
            module={consoleModule}
          />
          <Flex justify="space-between" align="center">
            <Typography.Title level={4} style={{ marginBottom: 0 }}>
              {consoleSelection.brand} · {consoleSelection.console}
            </Typography.Title>
            <Button
              type="primary"
              icon={<PlusOutlined />}
              onClick={() => openCreateProviderModal(consoleSelection.brand, consoleSelection.console)}
            >
              New provider
            </Button>
          </Flex>
          <ModuleReadinessCard readiness={moduleReadiness} loading={moduleReadinessLoading} />
          {renderProviderCoverage()}
          <Empty description="No providers configured for this console" />
        </Flex>
      )
    }
    return (
      <Flex vertical gap="large">
        <ConsoleInfoCard
          brand={consoleSelection.brand}
          console={consoleSelection.console}
          guid={consoleGuid}
          module={consoleModule}
        />
        <ModuleReadinessCard readiness={moduleReadiness} loading={moduleReadinessLoading} />
        {renderProviderCoverage()}
        <List
          header={
            <Flex justify="space-between" align="center">
              <Typography.Title level={4} style={{ marginBottom: 0 }}>
                {consoleSelection.brand} · {consoleSelection.console}
              </Typography.Title>
              <Button
                type="primary"
                icon={<PlusOutlined />}
                onClick={() => openCreateProviderModal(consoleSelection.brand, consoleSelection.console)}
              >
                New provider
              </Button>
            </Flex>
          }
          dataSource={entries}
          renderItem={(entry, index) => {
            const moduleName =
              entry.libretro_guid && moduleByGuid[entry.libretro_guid]
                ? moduleByGuid[entry.libretro_guid].name
                : undefined
            return (
              <List.Item
                actions={[
                  <Button
                    size="small"
                    onClick={() => handleProviderNavigate(consoleSelection.brand, consoleSelection.console, entry, index)}
                  >
                    Inspect
                  </Button>,
                ]}
              >
                <List.Item.Meta
                  title={entry.archive_id || entry.provider || `Collection ${index + 1}`}
                  description={
                    moduleName
                      ? `${moduleName} • ${entry.archive_id || entry.base_url || 'No archive id configured'}`
                      : entry.archive_id || entry.base_url || 'No archive id configured'
                  }
                />
                <Space size="small" wrap>
                  <Tag>{entry.provider || 'Unknown provider'}</Tag>
                  {entry.rom_extensions?.slice(0, 3).map((ext) => (
                    <Tag key={`${entry.archive_id ?? index}-${ext}`} color="blue">
                      {ext}
                    </Tag>
                  ))}
                </Space>
              </List.Item>
            )
          }}
        />
      </Flex>
    )
  }

  const renderRomBrandDetail = (brandSelection: RomBrandSelection) => {
    const consoles = romSets.filter((meta) => (meta.brand || 'Other') === brandSelection.brand)
    if (!consoles.length) {
      return <Empty description="No ROM exports for this brand" />
    }
    return (
      <List
        header={
          <Typography.Title level={4} style={{ marginBottom: 0 }}>
            {brandSelection.brand}
          </Typography.Title>
        }
        dataSource={consoles}
        renderItem={(meta) => (
          <List.Item
            actions={[
              <Button size="small" onClick={() => handleRomConsoleNavigate(meta)}>
                View ROMs
              </Button>,
            ]}
          >
            <List.Item.Meta
              title={meta.console || meta.module || meta.slug}
              description={`${meta.entry_count ?? 0} master ROMs`}
            />
            <Typography.Text type="secondary">{meta.guid || meta.slug}</Typography.Text>
          </List.Item>
        )}
      />
    )
  }

  const renderRomConsoleDetail = (romSelection: RomConsoleSelection) => {
    const meta = romSelection.meta
    const cacheKey = meta.slug || meta.guid || meta.module || 'roms'
    const entries = romEntriesCache[cacheKey]
    const loading = romEntriesLoading && !entries
    const tableData =
      entries?.map((entry, index) => ({
        key: entry.md5 || entry.sha1 || entry.rom_name || `${index}`,
        ...entry,
      })) ?? []
    const romColumns = [
      { title: 'Name', dataIndex: 'name', key: 'name' },
      { title: 'Region', dataIndex: 'region', key: 'region', width: 120 },
      {
        title: 'Size',
        dataIndex: 'size',
        key: 'size',
        width: 120,
        render: (value: number) => formatBytes(value),
      },
      { title: 'CRC', dataIndex: 'crc', key: 'crc' },
      { title: 'MD5', dataIndex: 'md5', key: 'md5' },
    ]

    return (
      <Flex vertical gap="large">
        <ConsoleInfoCard brand={meta.brand} console={meta.console} guid={meta.guid} module={meta.module} />
        <div>
          <Typography.Title level={4} style={{ marginBottom: 0 }}>
            {meta.console || meta.module || meta.slug}
          </Typography.Title>
          <Typography.Text type="secondary">
            {meta.brand || 'Unknown brand'} · {meta.guid || 'No GUID'}
          </Typography.Text>
        </div>

        <Descriptions bordered size="small" column={2} labelStyle={{ width: 180 }}>
          <Descriptions.Item label="Dataset role">
            {meta.dataset_role === 'master_rom_list' ? 'Master ROM list' : meta.dataset_role || '—'}
          </Descriptions.Item>
          <Descriptions.Item label="Source">
            {meta.source_label || meta.source_kind || '—'}
          </Descriptions.Item>
          <Descriptions.Item label="Entries">{meta.entry_count ?? entries?.length ?? '—'}</Descriptions.Item>
          <Descriptions.Item label="Fetched">{formatTimestamp(meta.fetched_at)}</Descriptions.Item>
          <Descriptions.Item label="Source URL" span={2}>
            {meta.source_url ? (
              <a href={meta.source_url} target="_blank" rel="noreferrer">
                {meta.source_url}
              </a>
            ) : (
              '—'
            )}
          </Descriptions.Item>
        </Descriptions>

        <Flex justify="space-between" align="center" wrap className="rom-toolbar">
          <Typography.Text strong>Master ROM List</Typography.Text>
          <Segmented
            value={romViewMode}
            onChange={(value) => setRomViewMode(value as 'list' | 'cards')}
            options={[
              { label: 'List', value: 'list' },
              { label: 'Cards', value: 'cards' },
            ]}
          />
        </Flex>

        {romError && (
          <Alert type="error" showIcon message="ROM error" description={romError} className="app-alert" />
        )}

        {loading ? (
          <Spin />
        ) : entries && entries.length ? (
          romViewMode === 'list' ? (
            <Table columns={romColumns} dataSource={tableData} pagination={{ pageSize: 15 }} size="middle" />
          ) : (
            <List
              grid={{ gutter: 16, xs: 1, sm: 2, md: 3, lg: 3, xl: 4 }}
              dataSource={entries}
              renderItem={(entry) => (
                <List.Item key={entry.md5 || entry.sha1 || entry.rom_name}>
                  <Card className="rom-card" hoverable cover={<div className="rom-card-art" />}>
                    <div className="rom-card-body">
                      <Typography.Title level={5} className="rom-card-title">
                        {entry.name || entry.rom_name || 'Untitled ROM'}
                      </Typography.Title>
                      <Typography.Text type="secondary">{entry.region || 'Unknown region'}</Typography.Text>
                      <Typography.Text>Size: {formatBytes(entry.size)}</Typography.Text>
                      <Typography.Text className="rom-card-hash" copyable>
                        CRC: {entry.crc || '—'}
                      </Typography.Text>
                      <Typography.Text className="rom-card-hash" copyable>
                        MD5: {entry.md5 || '—'}
                      </Typography.Text>
                      <Typography.Text className="rom-card-hash" copyable>
                        SHA1: {entry.sha1 || '—'}
                      </Typography.Text>
                    </div>
                  </Card>
                </List.Item>
              )}
            />
          )
        ) : (
          <Empty description="No ROM entries found in this dataset" />
        )}
      </Flex>
    )
  }

  const renderModulesDataset = () => (
    <ModulesList modules={modulesData} onToggleIgnore={handleToggleModuleIgnore} />
  )

  const renderProvidersOverview = () => (
    <Card className="summary-card">
      <Flex justify="space-between" align="center" wrap>
        <Typography.Title level={5} style={{ marginBottom: 0 }}>
          Providers overview
        </Typography.Title>
        <Space wrap>
          <Button
            icon={<SafetyCertificateOutlined />}
            onClick={handleValidateProviders}
            loading={validationRunning}
          >
            Validate
          </Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => openCreateProviderModal()}>
            New provider
          </Button>
        </Space>
      </Flex>
      <Flex gap="large" className="summary-stats">
        <Statistic title="Brands" value={providerStats.brands} />
        <Statistic title="Consoles" value={providerStats.consoleCount} />
        <Statistic title="Collections" value={providerStats.providerCount} />
      </Flex>
    </Card>
  )

  const renderAccessOverview = () => {
    const accessColumns = [
      {
        title: 'User',
        dataIndex: 'id',
        key: 'id',
        render: (_: string, user: AccessUser) => (
          <Space direction="vertical" size={0}>
            <Typography.Text strong>{user.name || user.id}</Typography.Text>
            <Typography.Text type="secondary">{user.id}</Typography.Text>
          </Space>
        ),
      },
      {
        title: 'Role',
        dataIndex: 'admin',
        key: 'admin',
        render: (admin: boolean) => <Tag color={admin ? 'gold' : 'blue'}>{admin ? 'Admin' : 'Client'}</Tag>,
      },
      {
        title: 'Status',
        dataIndex: 'enabled',
        key: 'enabled',
        render: (enabled: boolean) => <Tag color={enabled ? 'green' : 'red'}>{enabled ? 'Enabled' : 'Disabled'}</Tag>,
      },
      {
        title: 'Consoles',
        dataIndex: 'allowed_console_guids',
        key: 'allowed_console_guids',
        render: (_: string[], user: AccessUser) =>
          user.admin ? 'All consoles' : `${user.allowed_console_guids?.length ?? 0} assigned`,
      },
      {
        title: 'Actions',
        key: 'actions',
        render: (_: unknown, user: AccessUser) => (
          <Space wrap>
            <Button size="small" icon={<EyeOutlined />} onClick={() => handlePreviewUser(user)}>
              View as
            </Button>
            <Button size="small" onClick={() => openEditAccessModal(user)}>
              Edit
            </Button>
            <Button size="small" onClick={() => handleResetAccessKey(user)}>
              Reset key
            </Button>
          </Space>
        ),
      },
    ]

    return (
      <Card className="summary-card">
        <Flex justify="space-between" align="center" wrap>
          <Typography.Title level={5} style={{ marginBottom: 0 }}>
            Access management
          </Typography.Title>
          <Space wrap>
            <Button icon={<ReloadOutlined />} onClick={fetchAccessUsers} loading={accessLoading}>
              Refresh
            </Button>
            <Button type="primary" icon={<PlusOutlined />} onClick={openCreateAccessModal}>
              New user
            </Button>
          </Space>
        </Flex>
        {accessError && (
          <Alert type="error" showIcon message="Unable to load users" description={accessError} className="app-alert" />
        )}
        <Table
          rowKey="id"
          columns={accessColumns}
          dataSource={accessUsers}
          loading={accessLoading}
          pagination={false}
          size="middle"
        />
      </Card>
    )
  }

  if (!apiKey || (!currentUser && !authLoading)) {
    return (
      <ConfigProvider
        theme={{
          algorithm: theme.darkAlgorithm,
          token: {
            colorPrimary: '#f4b860',
            colorBgBase: '#020817',
            fontFamily: '"Inter", system-ui, -apple-system, BlinkMacSystemFont, sans-serif',
          },
        }}
      >
        {contextHolder}
        <Layout className="app-shell login-shell">
          <Card className="summary-card login-card">
            <Space direction="vertical" size="large" style={{ width: '100%' }}>
              <div>
                <Typography.Title level={3} style={{ marginBottom: 0 }}>
                  Admin login
                </Typography.Title>
                <Typography.Text type="secondary">Sign in with an administrator account.</Typography.Text>
              </div>
              {authError && (
                <Alert type="error" showIcon message="Login failed" description={authError} />
              )}
              <Form layout="vertical" form={loginForm} onFinish={handleLoginSubmit} initialValues={{ username: 'admin' }}>
                <Form.Item
                  label="Username"
                  name="username"
                  rules={[{ required: true, message: 'Username is required' }]}
                >
                  <Input placeholder="admin" autoFocus />
                </Form.Item>
                <Form.Item
                  label="Password"
                  name="password"
                  rules={[{ required: true, message: 'Password is required' }]}
                >
                  <Input.Password placeholder="Password" />
                </Form.Item>
                <Button type="primary" htmlType="submit" loading={authLoading} block>
                  Login
                </Button>
              </Form>
            </Space>
          </Card>
        </Layout>
      </ConfigProvider>
    )
  }

  if (!currentUser) {
    return (
      <ConfigProvider theme={{ algorithm: theme.darkAlgorithm }}>
        <Layout className="app-shell login-shell">
          <Spin />
        </Layout>
      </ConfigProvider>
    )
  }

  return (
    <ConfigProvider
      theme={{
        algorithm: theme.darkAlgorithm,
        token: {
          colorPrimary: '#f4b860',
          colorBgBase: '#020817',
          fontFamily: '"Inter", system-ui, -apple-system, BlinkMacSystemFont, sans-serif',
        },
      }}
    >
      {contextHolder}
      <Layout className="app-shell">
        <NavigationSider
          collapsed={navCollapsed}
          width={navigationWidth}
          logoUrl={logoUrl}
          searchQuery={searchQuery}
          onSearchChange={setSearchQuery}
          onSearchSubmit={(value) => setSearchQuery(value)}
          menuItems={navigationMenuItems}
          selectedKeys={selectedKeys}
          openKeys={openKeys}
          onOpenChange={handleMenuOpenChange}
          onSelect={handleMenuSelect}
          providerError={providerError}
          romMetaLoading={romMetaLoading}
        />

        <Layout className="main-panel">
          <TopHeader
            collapsed={navCollapsed}
            user={currentUser}
            onLogout={handleLogout}
            onToggle={() => setNavCollapsed((prev) => !prev)}
          />

          <Layout.Content className="app-content">
            {isModulesDataset ? (
              renderModulesDataset()
            ) : (
              <>
                {showHomeSummary && (
                  <>
                    <Flex justify="space-between" align="center" className="home-toolbar">
                      <Typography.Text type="secondary" className="app-tagline">
                        {headerTagline}
                      </Typography.Text>
                      <Button
                        type="primary"
                        icon={<ReloadOutlined />}
                        onClick={handleRefreshAll}
                        loading={busy}
                      >
                        Refresh
                      </Button>
                    </Flex>
                    <Typography.Paragraph className="intro">
                      Pick an item on the left tree to inspect its metadata. Start with <strong>Providers</strong> to
                      view archive URLs, torrent links, and configured ROM extensions.
                    </Typography.Paragraph>
                    <SummaryCards
                      datasets={DATASETS}
                      meta={meta}
                      loading={loading}
                      error={error}
                      onDownload={handleDownloadDataset}
                    />
                  </>
                )}

                {showProvidersOverview && renderProvidersOverview()}
                {showAccessOverview && renderAccessOverview()}

                <DetailPanel
                  selection={selection}
                  selectedProvider={selectedProvider}
                  selectedModule={selectedModule}
                  selectedRomConsole={selectedRomConsole}
                  selectedRomBrand={selectedRomBrand}
                  selectedConsole={selectedConsole}
                  selectedBrand={selectedBrand}
                  isRomContext={isRomContext}
                  moduleByGuid={moduleByGuid}
                  providerStatus={providerStatus}
                  moduleReadiness={moduleReadiness}
                  moduleReadinessLoading={moduleReadinessLoading}
                  providerFetchRunning={providerFetchRunning}
                  providerExportRunning={providerExportRunning}
                  onFetchAssets={handleProviderFetchAssets}
                  onExportRoms={handleProviderExport}
                  onEditProvider={openEditModal}
                  onDuplicateProvider={handleDuplicate}
                  onDeleteProvider={handleDelete}
                  onEditModule={openModuleEditModal}
                  onDuplicateModule={handleModuleDuplicate}
                  onDeleteModule={handleModuleDelete}
                  renderRomConsoleDetail={renderRomConsoleDetail}
                  renderRomBrandDetail={renderRomBrandDetail}
                  renderConsoleDetail={renderConsoleDetail}
                  renderBrandDetail={renderBrandDetail}
                  renderProviderCoverage={renderProviderCoverage}
                />
              </>
            )}
          </Layout.Content>

          <Layout.Footer className="app-footer">
            ROMs Manager backend · Ant Design prototype
          </Layout.Footer>
        </Layout>
      </Layout>
      <Modal
        title={`Edit ${selectedProvider?.collectionLabel ?? 'provider'}`}
        open={isEditModalVisible}
        okText="Save changes"
        onOk={handleEditSubmit}
        onCancel={handleEditCancel}
        destroyOnClose
        width={640}
      >
        <Form layout="vertical" form={form}>
          <Form.Item
            label="Brand"
            name="brand"
            rules={[{ required: providerModalMode === 'create', message: 'Brand is required' }]}
          >
            <Input placeholder="Atari" disabled={providerModalMode === 'edit'} />
          </Form.Item>
          <Form.Item
            label="Console"
            name="console_name"
            rules={[{ required: providerModalMode === 'create', message: 'Console is required' }]}
          >
            <Input placeholder="2600" disabled={providerModalMode === 'edit'} />
          </Form.Item>
          <Form.Item
            label="Collection name"
            name="name"
            rules={[{ required: true, message: 'Collection name is required' }]}
          >
            <Input placeholder="Sega Mega-CD (Redump Collection)" />
          </Form.Item>
          <Form.Item label="Provider label" name="provider">
            <Input placeholder="Internet Archive" />
          </Form.Item>
          <Form.Item label="Archive ID" name="archive_id">
            <Input placeholder="archive-id" />
          </Form.Item>
          <Form.Item label="Base URL" name="base_url">
            <Input placeholder="https://archive.org/download/..." />
          </Form.Item>
          <Form.Item label="Size" name="size">
            <Input placeholder="Optional human readable size" />
          </Form.Item>
          <Form.Item label="Updated" name="updated">
            <Input placeholder="YYYY-MM-DD" />
          </Form.Item>
          <Form.Item label="libretro GUID" name="libretro_guid">
            <Select
              showSearch
              allowClear
              placeholder="Select a module"
              options={moduleOptions}
              loading={modulesLoading}
              optionFilterProp="label"
            />
          </Form.Item>
          <Form.Item label="ROM extensions (comma separated)" name="rom_extensions">
            <Input placeholder=".cue, .iso, .bin" />
          </Form.Item>
          <Typography.Text strong>Archive files</Typography.Text>
          {PROVIDER_FILE_FIELDS.map((field) => (
            <Form.Item key={field.key} label={field.label} name={field.key}>
              <Input placeholder={`https://archive.org/download/.../${field.key}.ext`} />
            </Form.Item>
          ))}
        </Form>
      </Modal>
      <Modal
        title={`Edit ${selectedModule?.data.name ?? 'module'}`}
        open={isModuleModalVisible}
        okText="Save changes"
        onOk={handleModuleEditSubmit}
        onCancel={handleModuleEditCancel}
        destroyOnClose
        width={640}
      >
        <Form layout="vertical" form={moduleForm}>
          <Form.Item label="Module name" name="name">
            <Input placeholder="Console name" />
          </Form.Item>
          <Form.Item label="Path" name="path">
            <Input placeholder="Atari - 2600" />
          </Form.Item>
          <Form.Item label="GUID" name="guid">
            <Input placeholder="GUID assigned to this console" />
          </Form.Item>
          <Form.Item label="Git URL" name="url">
            <Input placeholder="https://github.com/libretro-thumbnails/..." />
          </Form.Item>
          <Form.Item label="Branch" name="branch">
            <Input placeholder="master" />
          </Form.Item>
          <Form.Item label="Ignore rule" name="ignore">
            <Input placeholder="dirty" />
          </Form.Item>
          <Form.Item label="Shallow clone" name="shallow">
            <Input placeholder="true / false" />
          </Form.Item>
        </Form>
      </Modal>
      <Modal
        title={accessModalTarget ? `Edit ${accessModalTarget.id}` : 'New user'}
        open={isAccessModalVisible}
        okText={generatedApiKey ? 'Done' : 'Save'}
        onOk={generatedApiKey ? closeAccessModal : handleAccessSubmit}
        onCancel={closeAccessModal}
        destroyOnClose
        width={960}
      >
        {generatedApiKey ? (
          <Alert
            type="success"
            showIcon
            message="API key generated"
            description={
              <Space direction="vertical">
                <Typography.Text>This key is shown once. Store it before closing.</Typography.Text>
                <Typography.Text copyable code>
                  {generatedApiKey}
                </Typography.Text>
              </Space>
            }
          />
        ) : (
          <Form layout="vertical" form={accessForm}>
            <Form.Item
              label="User ID"
              name="id"
              rules={[{ required: true, message: 'User ID is required' }]}
            >
              <Input placeholder="player-1" disabled={Boolean(accessModalTarget)} />
            </Form.Item>
            <Form.Item label="Name" name="name">
              <Input placeholder="Display name" />
            </Form.Item>
            <Form.Item label="Status" name="enabled">
              <Select
                options={[
                  { label: 'Enabled', value: true },
                  { label: 'Disabled', value: false },
                ]}
              />
            </Form.Item>
            <Form.Item label="Role" name="admin">
              <Select
                options={[
                  { label: 'Client', value: false },
                  { label: 'Admin', value: true },
                ]}
              />
            </Form.Item>
            <Form.Item
              noStyle
              shouldUpdate={(prev, next) =>
                prev.admin !== next.admin || prev.allowed_console_guids !== next.allowed_console_guids
              }
            >
              {({ getFieldValue }) =>
                getFieldValue('admin') ? (
                  <Alert
                    type="warning"
                    showIcon
                    message="Admin users can see and modify the full backend."
                    className="app-alert"
                  />
                ) : (
                  <Form.Item label="Assigned consoles" name="allowed_console_guids">
                    <Transfer
                      className="console-transfer"
                      dataSource={moduleOptions.map((option) => ({
                        key: option.value,
                        title: option.label,
                      }))}
                      targetKeys={getFieldValue('allowed_console_guids') ?? []}
                      onChange={(nextKeys) => accessForm.setFieldValue('allowed_console_guids', nextKeys)}
                      render={(item) => item.title}
                      showSearch
                      oneWay
                      listStyle={{ width: 390, height: 420 }}
                      titles={['Available consoles', 'Assigned consoles']}
                      disabled={modulesLoading}
                    />
                  </Form.Item>
                )
              }
            </Form.Item>
          </Form>
        )}
      </Modal>
    </ConfigProvider>
  )
}

export default App
