// store/uiStore.ts
// Changelog: 2025-04-14 v0.1 — initial

import { create } from 'zustand'
import type { PanelView } from '../types'

interface UiStore {
  panelView:      PanelView
  selectedMidi:   number | null      // aktuálně vybraná nota
  selectedVel:    number             // globální velocity vrstva (0–7)
  selectedK:      number | null      // aktuálně vybraný parciál
  anchorDialogMidi: number | null    // shift+klik anchor dialog

  setPanelView:    (v: PanelView) => void
  selectNote:      (midi: number) => void
  setVelocity:     (vel: number) => void
  selectPartial:   (k: number) => void
  openAnchorDialog:(midi: number) => void
  closeAnchorDialog:() => void

  statusMessage:  string
  setStatus:      (msg: string) => void
}

export const useUiStore = create<UiStore>((set) => ({
  panelView:       'relation',
  selectedMidi:    null,
  selectedVel:     4,
  selectedK:       null,
  anchorDialogMidi:null,
  statusMessage:   'Vyberte banku pro začátek',

  setPanelView:  (v) => set({ panelView: v }),

  selectNote: (midi) => set({
    selectedMidi:  midi,
    selectedK:     null,
    panelView:     'note_detail',
  }),

  setVelocity: (vel) => set({ selectedVel: vel }),

  selectPartial: (k) => set({ selectedK: k }),

  openAnchorDialog:  (midi) => set({ anchorDialogMidi: midi }),
  closeAnchorDialog: ()     => set({ anchorDialogMidi: null }),

  setStatus: (msg) => set({ statusMessage: msg }),
}))
