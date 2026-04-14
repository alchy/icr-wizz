// components/PanelNav.tsx — navigace pravého panelu
// Changelog: 2025-04-14 v0.1 — initial

import React from 'react'
import { useUiStore }        from '../store/uiStore'
import { useCorrectionStore} from '../store/correctionStore'
import { useFitStore }       from '../store/fitStore'
import { useBankStore }      from '../store/bankStore'
import { useAnchorStore }    from '../store/anchorStore'
import type { PanelView }    from '../types'

interface NavItem {
  id:    PanelView
  label: string
  badge?: () => string | number | null
}

export const PanelNav: React.FC = () => {
  const { panelView, setPanelView } = useUiStore()
  const pending     = useCorrectionStore(s => s.pending)
  const activePath  = useBankStore(s => s.activePath())
  const summary     = useFitStore(s => s.summary)
  const { propose } = useCorrectionStore()
  const { runFit }  = useFitStore()
  const { active: anchorDb } = useAnchorStore()

  const items: NavItem[] = [
    { id: 'relation',        label: 'Vztahy' },
    { id: 'note_detail',     label: 'Nota',  badge: () => null },
    { id: 'velocity_editor', label: 'Velocity' },
    { id: 'anchor_panel',    label: 'Anchor',  badge: () => anchorDb?.entries.length ?? null },
    { id: 'diff_preview',    label: 'Korekce', badge: () => pending?.corrections.length ?? null },
    { id: 'param_space',     label: 'Prostor' },
    { id: 'param_space_3d', label: '3D' },
    { id: 'midi_panel',      label: 'MIDI' },
  ]

  async function handleProposeAndSwitch() {
    if (!activePath || !summary) return
    await propose(activePath, summary)
    setPanelView('diff_preview')
  }

  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 2,
      padding: '0 var(--sp-2)',
      borderBottom: '1px solid var(--bg-border)',
      background: 'var(--bg-card)',
      height: 34, flexShrink: 0,
    }}>
      {items.map(item => {
        const badge = item.badge?.()
        const active = panelView === item.id

        const handleClick = async () => {
          if (item.id === 'diff_preview' && !pending) {
            await handleProposeAndSwitch()
          } else {
            setPanelView(item.id)
          }
        }

        return (
          <button
            key={item.id}
            onClick={handleClick}
            style={{
              display: 'flex', alignItems: 'center', gap: 5,
              padding: '4px 10px',
              border: 'none',
              borderBottom: active ? '2px solid var(--c-anchor)' : '2px solid transparent',
              borderRadius: 0,
              background: active ? 'var(--bg-hover)' : 'transparent',
              color: active ? 'var(--t-primary)' : 'var(--t-muted)',
              fontFamily: 'var(--font-ui)',
              fontSize: 12,
              fontWeight: active ? 600 : 400,
              cursor: 'pointer',
              transition: 'color 120ms, background 120ms',
              whiteSpace: 'nowrap',
            }}
          >
            {item.label}
            {badge !== null && badge !== undefined && (
              <span style={{
                display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                minWidth: 16, height: 16, padding: '0 4px',
                background: active ? 'var(--c-anchor)' : 'var(--bg-border)',
                borderRadius: 8,
                fontSize: 10, fontFamily: 'var(--font-mono)',
                color: active ? '#000' : 'var(--t-secondary)',
              }}>
                {badge}
              </span>
            )}
          </button>
        )
      })}
    </div>
  )
}
