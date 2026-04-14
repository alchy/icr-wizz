// api/client.ts — REST + WebSocket wrapper pro Piano Soundbank Editor API
// Changelog:
//   2025-04-14 v0.1 — initial

import type {
  AnchorDatabase, AnchorEntry, AnchorListItem, AnchorSuggestion,
  BankListItem, BankStateResponse, CorrectionSet, CoverageReport,
  FitDetailsResponse, FitSummary, LoadResponse, NoteParams,
  WsMessage, WsResponse,
} from '../types'

const BASE = '/api'

// ---------------------------------------------------------------------------
// HTTP helpers
// ---------------------------------------------------------------------------

async function get<T>(path: string, params?: Record<string, string | number | boolean>): Promise<T> {
  const url = new URL(BASE + path, window.location.origin)
  if (params) {
    Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, String(v)))
  }
  const res = await fetch(url.toString())
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }))
    throw new ApiError(res.status, body.detail ?? res.statusText)
  }
  return res.json()
}

async function post<T>(path: string, body?: unknown, params?: Record<string, string>): Promise<T> {
  const url = new URL(BASE + path, window.location.origin)
  if (params) {
    Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, v))
  }
  const res = await fetch(url.toString(), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) {
    const b = await res.json().catch(() => ({ detail: res.statusText }))
    throw new ApiError(res.status, b.detail ?? res.statusText)
  }
  return res.json()
}

async function del<T>(path: string, params?: Record<string, string | number>): Promise<T> {
  const url = new URL(BASE + path, window.location.origin)
  if (params) {
    Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, String(v)))
  }
  const res = await fetch(url.toString(), { method: 'DELETE' })
  if (!res.ok) {
    const b = await res.json().catch(() => ({ detail: res.statusText }))
    throw new ApiError(res.status, b.detail ?? res.statusText)
  }
  return res.json()
}

async function patch<T>(path: string, body: unknown): Promise<T> {
  const url = new URL(BASE + path, window.location.origin)
  const res = await fetch(url.toString(), {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const b = await res.json().catch(() => ({ detail: res.statusText }))
    throw new ApiError(res.status, b.detail ?? res.statusText)
  }
  return res.json()
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
    this.name = 'ApiError'
  }
}

// ---------------------------------------------------------------------------
// Config API
// ---------------------------------------------------------------------------

export const configApi = {
  get: () => get<Record<string, unknown>>('/config'),
  patch: (updates: Record<string, unknown>) =>
    patch<Record<string, unknown>>('/config', updates),
}

// ---------------------------------------------------------------------------
// Bank API
// ---------------------------------------------------------------------------

export const bankApi = {
  list: (directory: string) =>
    get<BankListItem[]>('/bank/list', { directory }),

  load: (paths: string[]) =>
    post<LoadResponse>('/bank/load', { paths }),

  state: (bankPath: string) =>
    get<BankStateResponse>('/bank/state', { bank_path: bankPath }),

  note: (bankPath: string, noteKey: string) =>
    get<NoteParams>(`/bank/note/${noteKey}`, { bank_path: bankPath }),
}

// ---------------------------------------------------------------------------
// Fit API
// ---------------------------------------------------------------------------

export const fitApi = {
  run: (bankPath: string, anchorDbName?: string) =>
    post<FitSummary>('/fit', { anchor_db_name: anchorDbName ?? null, sigma_threshold: 2.5 },
      { bank_path: bankPath }),

  details: (bankPath: string, anchorDbName?: string) =>
    post<FitDetailsResponse>('/fit/details',
      { anchor_db_name: anchorDbName ?? null, sigma_threshold: 2.5 },
      { bank_path: bankPath }),
}

// ---------------------------------------------------------------------------
// Corrections API
// ---------------------------------------------------------------------------

export const correctionsApi = {
  propose: (bankPath: string, fitResult: object) =>
    post<CorrectionSet>('/corrections/propose', fitResult, { bank_path: bankPath }),

  apply: (bankPath: string, correctionSet: CorrectionSet, selectedFields?: string[]) =>
    post<{ output_path: string; corrections_applied: number; notes_affected: number }>(
      '/corrections/apply',
      { bank_path: bankPath, correction_set: correctionSet, selected_fields: selectedFields ?? null }
    ),
}

// ---------------------------------------------------------------------------
// Export API
// ---------------------------------------------------------------------------

export const exportApi = {
  bank: (sourcePath: string, outputPath: string, diffOnly = false, addMetadata = true) =>
    post<{ path: string; size_kb: number }>('/export', {
      source_path: sourcePath,
      output_path: outputPath,
      diff_only: diffOnly,
      add_metadata: addMetadata,
    }),

  diffReport: async (bankPath: string, correctionSet: CorrectionSet): Promise<void> => {
    const url = new URL(`${BASE}/export/diff-report`, window.location.origin)
    url.searchParams.set('bank_path', bankPath)
    const res = await fetch(url.toString(), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(correctionSet),
    })
    if (!res.ok) throw new ApiError(res.status, res.statusText)
    const blob = await res.blob()
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = 'corrections.csv'
    a.click()
  },
}

// ---------------------------------------------------------------------------
// Anchor API
// ---------------------------------------------------------------------------

export const anchorApi = {
  list: () =>
    get<AnchorListItem[]>('/anchors/list'),

  save: (db: AnchorDatabase, overwrite = true) =>
    post<{ path: string; name: string }>('/anchors/save', { db, overwrite }),

  load: (name: string) =>
    get<AnchorDatabase>(`/anchors/${name}`),

  delete: (name: string) =>
    del<{ deleted: boolean; name: string }>(`/anchors/${name}`),

  addEntry: (name: string, entry: Omit<AnchorEntry, 'timestamp'>) =>
    post<{ saved: boolean; entry_count: number }>(`/anchors/${name}/entry`, entry),

  removeEntry: (name: string, midi: number, vel: number) =>
    del<{ saved: boolean; entry_count: number }>(`/anchors/${name}/entry`, { midi, vel }),

  coverage: (name: string, bankPath?: string) =>
    get<CoverageReport>(`/anchors/${name}/coverage`,
      bankPath ? { bank_path: bankPath } : undefined),

  suggest: (name: string, bankPath: string, n = 15) =>
    get<{ suggestions: AnchorSuggestion[] }>(`/anchors/${name}/suggest`,
      { bank_path: bankPath, n }),
}

// ---------------------------------------------------------------------------
// MIDI API
// ---------------------------------------------------------------------------

export const midiApi = {
  status: () =>
    get<{ connected: boolean; port_name?: string }>('/midi/status'),

  ports: () =>
    get<{ ports: string[] }>('/midi/ports'),

  connect: (portName: string) =>
    post<{ connected: boolean; port: string }>('/midi/connect', { port_name: portName }),

  disconnect: () =>
    post<{ connected: boolean }>('/midi/disconnect'),

  play: (midi: number, velocity = 100, duration_s = 3.0) =>
    post<{ status: string }>('/midi/play', { midi, velocity, duration_s }),

  uploadBank: (bankPath: string) =>
    post<{ chunks_total: number; chunks_sent: number; bytes: number }>(
      '/midi/upload-bank', { bank_path: bankPath }),

  patch: (bankPath: string, midiRange?: [number, number], velRange?: [number, number]) =>
    post<{ total: number; success: number; failed: number; errors: string[] }>(
      '/midi/patch', { bank_path: bankPath, midi_range: midiRange, vel_range: velRange }
    ),
}

// ---------------------------------------------------------------------------
// WebSocket klient
// ---------------------------------------------------------------------------

type WsHandler = (resp: WsResponse) => void
type WsErrorHandler = (err: Event) => void

export class PreviewSocket {
  private ws: WebSocket | null = null
  private handlers: WsHandler[] = []
  private errorHandlers: WsErrorHandler[] = []
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private reconnectDelay = 1000
  private _connected = false
  private _openResolve: (() => void) | null = null
  private _lastInit: WsMessage | null = null

  get connected() { return this._connected }

  /** Připojí WS. Vrátí Promise která se vyřeší po otevření. */
  connect(): Promise<void> {
    return new Promise((resolve) => {
      this._openResolve = resolve
      const wsBase = window.location.origin.replace(/^http/, 'ws')
      this.ws = new WebSocket(`${wsBase}/api/ws/preview`)

      this.ws.onopen = () => {
        this._connected = true
        this.reconnectDelay = 1000
        this._openResolve?.()
        this._openResolve = null
        // Re-send init po reconnectu
        if (this._lastInit) {
          this.ws!.send(JSON.stringify(this._lastInit))
        }
      }

      this.ws.onmessage = (ev) => {
        try {
          const data = JSON.parse(ev.data) as WsResponse
          this.handlers.forEach(h => h(data))
        } catch { /* ignore parse error */ }
      }

      this.ws.onerror = (ev) => {
        this.errorHandlers.forEach(h => h(ev))
      }

      this.ws.onclose = () => {
        this._connected = false
        this.reconnectTimer = setTimeout(() => {
          this.reconnectDelay = Math.min(this.reconnectDelay * 1.5, 10000)
          this.connect()
        }, this.reconnectDelay)
      }
    })
  }

  disconnect() {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer)
    this.ws?.close()
    this._connected = false
  }

  send(msg: WsMessage) {
    if (msg.action === 'init') this._lastInit = msg
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(msg))
    }
  }

  onMessage(handler: WsHandler)      { this.handlers.push(handler) }
  onError(handler: WsErrorHandler)   { this.errorHandlers.push(handler) }

  removeHandler(handler: WsHandler) {
    this.handlers = this.handlers.filter(h => h !== handler)
  }
}

/** Singleton WebSocket instance pro celou aplikaci */
export const previewSocket = new PreviewSocket()
