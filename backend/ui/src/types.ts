export type DatasetKey = 'modules' | 'providers'

export type DatasetMeta = {
  target: DatasetKey
  version?: string
  count?: number
}

export type DatasetCard = {
  key: DatasetKey
  title: string
  description: string
  endpoint: string
  accent: string
}

export type ModuleEntry = {
  name?: string
  guid?: string
  path?: string
  url?: string
  branch?: string
  ignore?: string
  shallow?: string
}

export type RomEntry = {
  name?: string
  description?: string
  region?: string
  rom_name?: string
  size?: number
  crc?: string
  md5?: string
  sha1?: string
  thumbnail_url?: string
  thumbnail_category?: string
  game_title?: string
  variant_tags?: string[]
  variant_label?: string
  variant_count?: number
  variant_index?: number
  artwork?: Record<string, {
    category?: string
    url?: string
    path?: string
    sha?: string
  }>
  [key: string]: unknown
}

export type RomEntriesPage = {
  entries: RomEntry[]
  total: number
  catalog_total?: number
  limit: number
  offset: number
}

export type RomEntryFilters = {
  q?: string
  availability?: string
  region?: string
  format?: string
  sort?: string
}

export type RomSetMeta = {
  slug: string
  module?: string
  brand?: string
  console?: string
  guid?: string
  dataset_role?: string
  source_kind?: string
  source_label?: string
  entry_count?: number
  rdb_entry_count?: number
  provider_only_count?: number
  catalog_total?: number
  coverage?: Record<string, unknown>
  fetched_at?: string
  source_url?: string
}

export type ProviderEntry = {
  name?: string
  provider?: string
  archive_id?: string
  base_url?: string
  files?: Record<string, string>
  rom_extensions?: string[]
  size?: string
  updated?: string
  libretro_guid?: string
  [key: string]: unknown
}

export type ProviderStatus = {
  metadata?: boolean
  listings?: boolean
  torrent?: boolean
  rom_json?: boolean
  [key: string]: unknown
}

export type ProviderCoverage = {
  brand: string
  console: string
  guid?: string
  module?: ModuleEntry
  ready: boolean
  missing?: string
  message?: string
  rdb_path?: string
  summary: {
    rdb_entries: number
    provider_count: number
    matched_entries: number
    unmatched_entries: number
    multi_provider_entries: number
    coverage_percent: number
  }
  providers: Array<{
    id: string
    label?: string
    archive_id?: string
    provider?: string
    source_roms: number
    matched_entries: number
    matched_unique_files: number
    status?: Record<string, boolean>
  }>
  unmatched_samples: Array<{
    name?: string
    region?: string
    md5?: string
    crc32?: string
  }>
}

export type ModuleReadiness = {
  module: ModuleEntry
  guid?: string
  name?: string
  brand?: string
  console?: string
  score: 'ready' | 'partial' | 'needs_work' | string
  summary: {
    ready: boolean
    label: string
  }
  checks: Record<string, {
    state: 'ok' | 'partial' | 'missing' | string
    label: string
    coverage_percent?: number
  }>
  providers: ProviderEntry[]
  core_metadata: Array<Record<string, unknown>>
  bios_metadata: Array<Record<string, unknown>>
  coverage?: ProviderCoverage | null
}

export type ProvidersResponse = {
  target: 'providers'
  version?: string
  providers?: {
    fetched_at?: string
    console_root?: Record<string, Record<string, ProviderEntry | ProviderEntry[]>>
  }
}

export type DashboardDatasetInfo = {
  version?: string | null
  count: number
  size?: number
}

export type DashboardConsole = {
  guid?: string
  module: string
  brand?: string | null
  console?: string | null
  category: string
  status: string
  completion: number
  coverage_percent: number
  entry_count: number
  provider_linked_entries: number
  provider_count: number
  core_count: number
  required_bios_count: number
  bios_with_sources: number
  strategy_types: string[]
  thumbnail_indexed_titles: number
  gaps: string[]
  next_action: string
  validated_at?: string | null
  notes?: string | null
}

export type DashboardAlert = {
  severity: 'critical' | 'warning' | 'info' | 'success' | string
  title: string
  message: string
  action?: string
}

export type DashboardRomDataset = {
  slug: string
  module?: string | null
  brand?: string | null
  console?: string | null
  guid?: string | null
  entry_count: number
  provider_linked_entries: number
  downloadable_entries: number
  inline_artwork_entries: number
  coverage_percent: number
  known_size: number
  thumbnail_indexed_titles: number
  thumbnail_images: number
  thumbnail_categories: string[]
  has_thumbnail_index: boolean
}

export type DashboardResponse = {
  generated_at: string
  scope: 'admin' | 'client' | string
  datasets: {
    modules: DashboardDatasetInfo
    providers: DashboardDatasetInfo
    roms: DashboardDatasetInfo
    cache: DashboardDatasetInfo
  }
  readiness: {
    total: number
    average_completion: number
    buckets: Record<string, number>
    categories: Record<string, number>
    statuses: Record<string, number>
    ready_for_assignment: number
  }
  providers: {
    brands: number
    consoles: number
    total: number
    with_cache: number
    missing_cache: number
    with_providers: number
    without_providers: number
  }
  roms: {
    datasets: number
    entries: number
    provider_linked_entries: number
    downloadable_entries: number
    inline_artwork_entries: number
    thumbnail_indexed_titles: number
    thumbnail_images: number
    thumbnail_indexes: number
    known_size: number
    coverage_percent: number
    thumbnail_index_percent: number
    largest_datasets: DashboardRomDataset[]
    missing_thumbnail_indexes: DashboardRomDataset[]
  }
  users: {
    visible: boolean
    current_user?: AccessUser
    total?: number
    enabled?: number
    admins?: number
    clients?: number
    zero_access?: number
    zero_access_users?: string[]
    assigned_total?: number
    assigned_ready?: number
    assigned_at_risk?: number
    risky_users?: Array<{ id: string; at_risk: number }>
    assigned_console_count?: number
  }
  runtime: {
    cores: number
    bios_files: number
    bios_with_sources: number
    bios_without_sources: number
    mapped_consoles: number
    missing_core_metadata: number
    special_strategy_consoles: Array<{
      module: string
      guid?: string | null
      strategy_types: string[]
    }>
  }
  alerts: DashboardAlert[]
  work_queue: DashboardConsole[]
  other_work_queue: DashboardConsole[]
  ready_consoles: DashboardConsole[]
}

export type ProviderSelection = {
  kind: 'collection'
  key: string
  brand: string
  console: string
  collectionLabel: string
  data: ProviderEntry
  archiveId: string
  nodeSuffix: string
  entryIndex: number
}

export type BrandSelection = {
  kind: 'brand'
  brand: string
}

export type ConsoleSelection = {
  kind: 'console'
  brand: string
  console: string
}

export type DatasetSelection = {
  kind: 'dataset'
  dataset: DatasetKey
}

export type ModuleSelection = {
  kind: 'module'
  index: number
  data: ModuleEntry
}

export type RomBrandSelection = {
  kind: 'rom-brand'
  brand: string
}

export type RomConsoleSelection = {
  kind: 'rom-console'
  meta: RomSetMeta
}

export type HomeSelection = {
  kind: 'home'
}

export type AccessSelection = {
  kind: 'access-root'
}

export type AccessUser = {
  id: string
  name?: string
  enabled: boolean
  admin: boolean
  allowed_console_guids: string[]
  has_api_key: boolean
}

export type ProvidersHomeSelection = {
  kind: 'providers-root'
}

export type Selection =
  | ProviderSelection
  | BrandSelection
  | ConsoleSelection
  | ModuleSelection
  | RomBrandSelection
  | RomConsoleSelection
  | DatasetSelection
  | ProvidersHomeSelection
  | AccessSelection
  | HomeSelection
  | null
