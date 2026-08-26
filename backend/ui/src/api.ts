export const ADMIN_TOKEN_STORAGE = 'roms_manager_admin_session_token'
export const PREVIEW_TOKEN_STORAGE = 'roms_manager_preview_token'
const LEGACY_TOKEN_STORAGE = 'roms_manager_auth_token'

export const isPreviewMode = () => new URLSearchParams(window.location.search).has('preview_user')

export const getAuthToken = () => {
  const params = new URLSearchParams(window.location.search)
  const queryKey = params.get('api_key')
  if (queryKey) {
    sessionStorage.setItem(PREVIEW_TOKEN_STORAGE, queryKey)
    localStorage.removeItem(LEGACY_TOKEN_STORAGE)
    params.delete('api_key')
    const nextQuery = params.toString()
    window.history.replaceState(
      {},
      '',
      `${window.location.pathname}${nextQuery ? `?${nextQuery}` : ''}${window.location.hash}`,
    )
    return queryKey
  }
  localStorage.removeItem(LEGACY_TOKEN_STORAGE)
  if (isPreviewMode()) {
    return sessionStorage.getItem(PREVIEW_TOKEN_STORAGE) || ''
  }
  sessionStorage.removeItem(PREVIEW_TOKEN_STORAGE)
  return localStorage.getItem(ADMIN_TOKEN_STORAGE) || ''
}

export const setAuthToken = (value: string) => {
  const trimmed = value.trim()
  sessionStorage.removeItem(PREVIEW_TOKEN_STORAGE)
  localStorage.removeItem(LEGACY_TOKEN_STORAGE)
  if (trimmed) {
    localStorage.setItem(ADMIN_TOKEN_STORAGE, trimmed)
  } else {
    localStorage.removeItem(ADMIN_TOKEN_STORAGE)
  }
}

export const authHeaders = (headers?: HeadersInit): HeadersInit => {
  const token = getAuthToken()
  if (!token) {
    return headers ?? {}
  }
  return {
    ...(headers ?? {}),
    Authorization: `Bearer ${token}`,
  }
}

export const apiFetch = (input: RequestInfo | URL, init: RequestInit = {}) =>
  fetch(input, {
    ...init,
    headers: authHeaders(init.headers),
  })
