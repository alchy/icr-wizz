// utils/useKeyboardNav.ts — hook pro navigaci klávesnicí
// Changelog: 2025-04-14 v0.1 — initial

import { useEffect, useCallback } from 'react'
import { useBankStore }      from '../store/bankStore'
import { useUiStore }        from '../store/uiStore'
import { useCorrectionStore} from '../store/correctionStore'
import { useAnchorStore }    from '../store/anchorStore'

/**
 * Globální klávesové zkratky.
 * Voláno jednou v App.tsx.
 */
export function useKeyboardNav() {
  const bankState   = useBankStore(s => s.activeState())
  const activePath  = useBankStore(s => s.activePath())
  const {
    selectedMidi, selectNote, setPanelView,
    openAnchorDialog, closeAnchorDialog,
  } = useUiStore()
  const undo        = useCorrectionStore(s => s.undo)
  const { active: anchorDb } = useAnchorStore()

  const handle = useCallback((e: KeyboardEvent) => {
    const tag = (e.target as HTMLElement)?.tagName
    const inInput = ['INPUT', 'TEXTAREA', 'SELECT'].includes(tag)

    // Escape — zavři dialogy
    if (e.key === 'Escape') {
      closeAnchorDialog()
      return
    }

    // Ctrl/Cmd zkratky
    if (e.ctrlKey || e.metaKey) {
      switch (e.key.toLowerCase()) {
        case 'z':
          e.preventDefault()
          undo()
          return
        case 'e':
          e.preventDefault()
          setPanelView('diff_preview')
          return
        case 's':
          e.preventDefault()
          // TODO: Ctrl+S → uložit anchor DB
          return
      }
    }

    if (inInput) return

    // Navigace šipkami
    if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
      e.preventDefault()
      if (!bankState || selectedMidi === null) return
      const midis = uniqueMidis(bankState.note_keys)
      const idx   = midis.indexOf(selectedMidi)
      if (idx === -1) return
      const next = e.key === 'ArrowRight'
        ? midis[Math.min(idx + 1, midis.length - 1)]
        : midis[Math.max(idx - 1, 0)]
      if (next !== undefined) selectNote(next)
      return
    }

    // Space — přepnutí RelationView ↔ NoteDetail
    if (e.key === ' ') {
      e.preventDefault()
      useUiStore.setState(s =>
        s.panelView === 'relation'
          ? { panelView: 'note_detail' }
          : { panelView: 'relation' }
      )
      return
    }
  }, [bankState, selectedMidi, selectNote, undo, setPanelView,
      closeAnchorDialog])

  useEffect(() => {
    window.addEventListener('keydown', handle)
    return () => window.removeEventListener('keydown', handle)
  }, [handle])
}

function uniqueMidis(noteKeys: string[]): number[] {
  const set = new Set<number>()
  noteKeys.forEach(k => {
    const m = k.match(/m(\d+)_vel/)
    if (m) set.add(Number(m[1]))
  })
  return [...set].sort((a, b) => a - b)
}
