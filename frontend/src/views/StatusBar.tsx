// views/StatusBar.tsx
// Changelog: 2025-04-14 v0.1 — initial

import React from 'react'
import { useFitStore }    from '../store/fitStore'
import { useAnchorStore } from '../store/anchorStore'
import { useBankStore }   from '../store/bankStore'
import { useUiStore }     from '../store/uiStore'
import { previewSocket }  from '../api/client'

export const StatusBar: React.FC = () => {
  const summary     = useFitStore(s => s.summary)
  const loading     = useFitStore(s => s.loading)
  const wsConnected = useFitStore(s => s.wsConnected)
  const activeDb    = useAnchorStore(s => s.active)
  const bankState   = useBankStore(s => s.activeState())
  const statusMsg   = useUiStore(s => s.statusMessage)

  const outlierCount = summary
    ? Object.values(summary.outlier_scores).filter(s => s > 0.5).length
    : 0
  const anchorCount  = activeDb?.entries.length ?? 0
  const noteCount    = bankState?.note_count ?? 0
  const fitQuality   = summary ? (1 - (outlierCount / Math.max(noteCount / 8, 1))).toFixed(2) : '—'

  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 'var(--sp-4)',
      padding: '0 var(--sp-4)',
      height: '100%',
      borderTop: '1px solid var(--bg-border)',
      fontSize: 11, fontFamily: 'var(--font-mono)',
      color: 'var(--t-muted)',
      overflow: 'hidden',
    }}>
      {/* Status zpráva */}
      <span style={{ flex: 1, minWidth: 0, overflow: 'hidden',
                     textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                     color: 'var(--t-secondary)' }}>
        {loading ? (
          <span style={{ color: 'var(--c-anchor)' }}>⟳ Analyzuji…</span>
        ) : statusMsg}
      </span>

      {/* Fit quality */}
      {summary && (
        <span style={{ flexShrink: 0 }}>
          fit {fitQuality}
        </span>
      )}

      <span className="sep" style={{ width: 1, height: 14 }} />

      {/* Outliery */}
      <span style={{ flexShrink: 0, color: outlierCount > 0 ? 'var(--c-warn)' : 'var(--t-muted)' }}>
        outl: {outlierCount}
      </span>

      <span className="sep" style={{ width: 1, height: 14 }} />

      {/* Anchor count */}
      <span style={{ flexShrink: 0, color: anchorCount > 0 ? 'var(--c-anchor)' : 'var(--t-muted)' }}>
        ⚓ {anchorCount}/{noteCount > 0 ? Math.floor(noteCount / 8) : '—'}
      </span>

      <span className="sep" style={{ width: 1, height: 14 }} />

      {/* WS status */}
      <span style={{ display: 'flex', alignItems: 'center', gap: 5, flexShrink: 0 }}>
        <span
          className="status-dot"
          style={{ background: previewSocket.connected ? 'var(--c-mid)' : 'var(--c-outlier)',
                   boxShadow: previewSocket.connected ? '0 0 5px var(--c-mid)' : 'none' }}
        />
        {previewSocket.connected ? 'live' : 'offline'}
      </span>

      <span className="sep" style={{ width: 1, height: 14 }} />

      {/* Keyboard hints */}
      <span style={{ flexShrink: 0, color: 'var(--t-muted)', fontSize: 10 }}>
        ←→ nota · Shift+klik anchor · Ctrl+E export · Ctrl+Z undo
      </span>
    </div>
  )
}
