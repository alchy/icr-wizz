// store/bankStore.ts
// Changelog: 2025-04-14 v0.1 — initial

import { create } from 'zustand'
import type { BankStateResponse, NoteParams } from '../types'
import { bankApi } from '../api/client'

interface BankTab {
  path: string
  state: BankStateResponse
  error?: string
}

interface BankStore {
  // Načtené soubory jako taby
  tabs: BankTab[]
  activeTabPath: string | null

  // Cache NoteParams (lazy načítané)
  noteCache: Record<string, NoteParams>   // "path::m060_vel4" → NoteParams

  // Akce
  addTabs:       (tabs: BankTab[]) => void
  setActiveTab:  (path: string) => void
  closeTab:      (path: string) => void

  activeState:   () => BankStateResponse | null
  activePath:    () => string | null

  fetchNote:     (noteKey: string) => Promise<NoteParams | null>
  getCachedNote: (noteKey: string) => NoteParams | null
  clearNoteCache:() => void
}

export const useBankStore = create<BankStore>((set, get) => ({
  tabs:          [],
  activeTabPath: null,
  noteCache:     {},

  addTabs: (newTabs) => {
    set(s => {
      const existingPaths = new Set(s.tabs.map(t => t.path))
      const merged = [
        ...s.tabs,
        ...newTabs.filter(t => !existingPaths.has(t.path)),
      ]
      return {
        tabs: merged,
        activeTabPath: s.activeTabPath ?? (merged[0]?.path ?? null),
      }
    })
  },

  setActiveTab: (path) => set({ activeTabPath: path }),

  closeTab: (path) => {
    set(s => {
      const tabs = s.tabs.filter(t => t.path !== path)
      const active = s.activeTabPath === path
        ? (tabs[0]?.path ?? null)
        : s.activeTabPath
      // Vyčisti cache pro tento soubor
      const noteCache = Object.fromEntries(
        Object.entries(s.noteCache).filter(([k]) => !k.startsWith(path + '::'))
      )
      return { tabs, activeTabPath: active, noteCache }
    })
  },

  activeState: () => {
    const { tabs, activeTabPath } = get()
    return tabs.find(t => t.path === activeTabPath)?.state ?? null
  },

  activePath: () => get().activeTabPath,

  fetchNote: async (noteKey) => {
    const path = get().activeTabPath
    if (!path) return null
    const cacheKey = `${path}::${noteKey}`
    const cached = get().noteCache[cacheKey]
    if (cached) return cached
    try {
      const note = await bankApi.note(path, noteKey)
      set(s => ({ noteCache: { ...s.noteCache, [cacheKey]: note } }))
      return note
    } catch {
      return null
    }
  },

  getCachedNote: (noteKey) => {
    const path = get().activeTabPath
    if (!path) return null
    return get().noteCache[`${path}::${noteKey}`] ?? null
  },

  clearNoteCache: () => set({ noteCache: {} }),
}))
