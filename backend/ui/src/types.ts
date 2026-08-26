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
  [key: string]: unknown
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
