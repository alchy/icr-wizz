// store/correctionStore.ts
// Changelog: 2025-04-14 v0.1 — initial

import { create } from 'zustand'
import type { Correction, CorrectionSet } from '../types'
import { correctionsApi } from '../api/client'

interface CorrectionStore {
  pending:   CorrectionSet | null
  history:   CorrectionSet[]          // undo stack
  selected:  Set<string>              // "{midi}_{vel}_{field}" keys povolených korekcí

  propose:      (bankPath: string, fitResult: object) => Promise<void>
  toggleSelect: (c: Correction) => void
  selectAll:    () => void
  selectNone:   () => void

  apply:        (bankPath: string) => Promise<{ output_path: string }>
  undo:         () => void

  selectedCorrections: () => Correction[]
  isSelected:   (c: Correction) => boolean
}

function corrKey(c: Correction) { return `${c.midi}_${c.vel}_${c.field}` }

export const useCorrectionStore = create<CorrectionStore>((set, get) => ({
  pending:  null,
  history:  [],
  selected: new Set(),

  propose: async (bankPath, fitResult) => {
    const cs = await correctionsApi.propose(bankPath, fitResult)
    // Výchozí: všechny korekce jsou vybrány
    set({
      pending:  cs,
      selected: new Set(cs.corrections.map(corrKey)),
    })
  },

  toggleSelect: (c) => {
    const key = corrKey(c)
    set(s => {
      const sel = new Set(s.selected)
      sel.has(key) ? sel.delete(key) : sel.add(key)
      return { selected: sel }
    })
  },

  selectAll: () => {
    const { pending } = get()
    if (!pending) return
    set({ selected: new Set(pending.corrections.map(corrKey)) })
  },

  selectNone: () => set({ selected: new Set() }),

  apply: async (bankPath) => {
    const { pending, selected } = get()
    if (!pending) throw new Error('Nejsou žádné korekce k aplikaci')
    const selectedFields = [...selected]
      .map(k => k.split('_').slice(2).join('_'))   // extrahuj field část

    const result = await correctionsApi.apply(bankPath, pending, selectedFields)

    // Ulož do history (undo)
    set(s => ({
      history: [...s.history, pending],
      pending: null,
      selected: new Set(),
    }))
    return result
  },

  undo: () => {
    set(s => {
      if (s.history.length === 0) return s
      const history = [...s.history]
      const pending = history.pop()!
      return {
        history,
        pending,
        selected: new Set(pending.corrections.map(corrKey)),
      }
    })
  },

  selectedCorrections: () => {
    const { pending, selected } = get()
    if (!pending) return []
    return pending.corrections.filter(c => selected.has(corrKey(c)))
  },

  isSelected: (c) => get().selected.has(corrKey(c)),
}))
