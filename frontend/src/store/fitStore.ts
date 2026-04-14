// store/fitStore.ts
// Changelog: 2025-04-14 v0.1 — initial

import { create } from 'zustand'
import type { FitDetailsResponse, FitSummary, WsResponse } from '../types'
import { fitApi } from '../api/client'
import { previewSocket } from '../api/client'

interface FitStore {
  summary:    FitSummary | null
  details:    FitDetailsResponse | null
  loading:    boolean
  wsConnected: boolean

  runFit:     (bankPath: string, anchorDbName?: string) => Promise<void>
  fetchDetails:(bankPath: string, anchorDbName?: string) => Promise<void>

  applyWsResponse: (resp: WsResponse) => void
  setWsConnected:  (v: boolean) => void

  outlierScore:    (notePrefix: string) => number
  outlierScoreVel: (noteKey: string) => number
}

export const useFitStore = create<FitStore>((set, get) => ({
  summary:     null,
  details:     null,
  loading:     false,
  wsConnected: false,

  runFit: async (bankPath, anchorDbName) => {
    set({ loading: true })
    try {
      const summary = await fitApi.run(bankPath, anchorDbName)
      set({ summary, loading: false })
    } catch (e) {
      set({ loading: false })
      throw e
    }
  },

  fetchDetails: async (bankPath, anchorDbName) => {
    try {
      const details = await fitApi.details(bankPath, anchorDbName)
      set({ details })
    } catch { /* details jsou optional */ }
  },

  applyWsResponse: (resp) => {
    if (resp.error) return
    set(s => ({
      summary: s.summary ? {
        ...s.summary,
        outlier_scores:         resp.outlier_scores,
        outlier_scores_per_vel: resp.outlier_scores_per_vel,
      } : null,
    }))
  },

  setWsConnected: (v) => set({ wsConnected: v }),

  outlierScore: (notePrefix) =>
    get().summary?.outlier_scores[notePrefix] ?? 0,

  outlierScoreVel: (noteKey) =>
    get().summary?.outlier_scores_per_vel[noteKey] ?? 0,
}))

// ---------------------------------------------------------------------------
// Inicializace WS spojení
// ---------------------------------------------------------------------------
previewSocket.onMessage((resp) => {
  useFitStore.getState().applyWsResponse(resp)
})

// Sledování stavu WS připojení
const _origConnect = previewSocket.connect.bind(previewSocket)
// Periodická kontrola stavu (WebSocket.onopen/onclose jsou v PreviewSocket)
setInterval(() => {
  useFitStore.getState().setWsConnected(previewSocket.connected)
}, 2000)
