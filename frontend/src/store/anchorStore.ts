// store/anchorStore.ts
// Changelog: 2025-04-14 v0.1 — initial

import { create } from 'zustand'
import type { AnchorDatabase, AnchorListItem, CoverageReport } from '../types'
import { anchorApi } from '../api/client'
import { previewSocket } from '../api/client'

interface AnchorStore {
  databases:  AnchorListItem[]
  active:     AnchorDatabase | null
  coverage:   CoverageReport | null

  loadDatabases:  () => Promise<void>
  selectDb:       (name: string) => Promise<void>
  createDb:       (name: string, description?: string) => Promise<void>
  deleteDb:       (name: string) => Promise<void>

  addEntry:    (midi: number, vel: number, score: number, note?: string) => Promise<void>
  removeEntry: (midi: number, vel: number) => Promise<void>

  refreshCoverage: (bankPath?: string) => Promise<void>

  /** Vrátí score pro (midi, vel) — -1 pokud záznam neexistuje */
  entryScore: (midi: number, vel: number) => number

  /** True pokud nota je anchor (jakákoliv velocity) */
  isAnchor: (midi: number) => boolean
}

export const useAnchorStore = create<AnchorStore>((set, get) => ({
  databases:  [],
  active:     null,
  coverage:   null,

  loadDatabases: async () => {
    const databases = await anchorApi.list()
    set({ databases })
  },

  selectDb: async (name) => {
    const db = await anchorApi.load(name)
    set({ active: db, coverage: null })
  },

  createDb: async (name, description) => {
    const db: AnchorDatabase = {
      name,
      description,
      created:  new Date().toISOString(),
      modified: new Date().toISOString(),
      entries:  [],
    }
    await anchorApi.save(db)
    set({ active: db })
    await get().loadDatabases()
  },

  deleteDb: async (name) => {
    await anchorApi.delete(name)
    set(s => ({
      databases: s.databases.filter(d => d.name !== name),
      active: s.active?.name === name ? null : s.active,
    }))
  },

  addEntry: async (midi, vel, score, note) => {
    const { active } = get()
    if (!active) return
    await anchorApi.addEntry(active.name, { midi, vel, score, note })
    const db = await anchorApi.load(active.name)
    set({ active: db })
    // Inkrementální refit přes WS
    previewSocket.send({
      action: 'update_anchor',
      payload: { midi, vel, score },
    })
  },

  removeEntry: async (midi, vel) => {
    const { active } = get()
    if (!active) return
    await anchorApi.removeEntry(active.name, midi, vel)
    const db = await anchorApi.load(active.name)
    set({ active: db })
    // Inkrementální refit přes WS
    previewSocket.send({
      action: 'update_anchor',
      payload: { midi, vel, score: 0 },
    })
  },

  refreshCoverage: async (bankPath) => {
    const { active } = get()
    if (!active) return
    const coverage = await anchorApi.coverage(active.name, bankPath)
    set({ coverage })
  },

  entryScore: (midi, vel) => {
    const { active } = get()
    if (!active) return -1
    const entry = active.entries.find(e => e.midi === midi && (e.vel === vel || e.vel === -1))
    return entry?.score ?? -1
  },

  isAnchor: (midi) => {
    const { active } = get()
    if (!active) return false
    return active.entries.some(e => e.midi === midi)
  },
}))
