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
  | HomeSelection
  | null
