// App.tsx — hlavní layout Piano Soundbank Editoru
// Changelog: 2025-04-14 v0.1 — initial
//            2025-04-14 v0.2 — useKeyboardNav hook, čistší layout

import React from 'react'
import { FileSelector }    from './views/FileSelector'
import { KeyboardMap }     from './views/KeyboardMap'
import { RelationView }    from './views/RelationView'
import { NoteDetail }      from './views/NoteDetail'
import { VelocityEditor }  from './views/VelocityEditor'
import { AnchorPanel }     from './views/AnchorPanel'
import { DiffPreview }     from './views/DiffPreview'
import { MidiPanel }       from './views/MidiPanel'
import { ExtractPanel }    from './views/ExtractPanel'
import { ParamSpaceView }  from './views/ParamSpaceView'
import { ParamSpace3DView }from './views/ParamSpace3DView'
import { PanelNav }        from './components/PanelNav'
import { VelocitySelector }from './components/VelocitySelector'
import { FitHeatmap }      from './components/FitHeatmap'
import { BankActions }     from './components/BankActions'
import { StatusBar }       from './views/StatusBar'
import { useBankStore }    from './store/bankStore'
import { useUiStore }      from './store/uiStore'
import { useKeyboardNav }  from './utils/useKeyboardNav'

const HEADER_H = 46
const STATUS_H = 26
const KB_H     = 152
const NAV_H    = 34

export default function App() {
  // Aktivuje globální klávesové zkratky
  useKeyboardNav()

  const { panelView }  = useUiStore()
  const bankState      = useBankStore(s => s.activeState())

  function renderPanel() {
    switch (panelView) {
      case 'extract':         return <ExtractPanel />
      case 'relation':        return <RelationView />
      case 'note_detail':     return <NoteDetail />
      case 'velocity_editor': return <VelocityEditor />
      case 'anchor_panel':    return <AnchorPanel />
      case 'diff_preview':    return <DiffPreview />
      case 'param_space':     return <ParamSpaceView />
      case 'param_space_3d': return <ParamSpace3DView />
      case 'midi_panel':      return <MidiPanel />
    }
  }

  return (
    <div style={{
      display:              'grid',
      gridTemplateRows:     `${HEADER_H}px ${KB_H}px 1fr ${STATUS_H}px`,
      gridTemplateColumns:  '1fr',
      width:  '100vw',
      height: '100vh',
      overflow: 'hidden',
      background: 'var(--bg-base)',
    }}>

      {/* Header */}
      <header style={{
        gridRow: 1,
        borderBottom: '1px solid var(--bg-border)',
        background:   'var(--bg-panel)',
        display: 'flex', alignItems: 'center', overflow: 'hidden',
      }}>
        <div style={{
          padding:     '0 var(--sp-5)',
          fontFamily:  'var(--font-ui)',
          fontWeight:  700,
          fontSize:    13,
          letterSpacing: '0.06em',
          color:       'var(--t-primary)',
          borderRight: '1px solid var(--bg-border)',
          height:      '100%',
          display:     'flex', alignItems: 'center',
          flexShrink:  0,
          gap: 8,
          userSelect:  'none',
        }}>
          <span style={{ color: 'var(--c-anchor)', fontSize: 16 }}>◈</span>
          <span>PSE</span>
        </div>
        <div style={{ flex: 1, minWidth: 0, height: '100%' }}>
          <FileSelector />
        </div>
      </header>

      {/* KeyboardMap + FitHeatmap + Velocity selector */}
      <section style={{
        gridRow:     2,
        background:  'var(--bg-panel)',
        borderBottom:'1px solid var(--bg-border)',
        padding:     'var(--sp-3) var(--sp-4)',
        overflowX:   'auto',
        overflowY:   'hidden',
      }}>
        {bankState ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--sp-4)', height: '100%' }}>
            {/* Klávesnice vlevo */}
            <div style={{ flexShrink: 0 }}>
              <KeyboardMap />
            </div>
            {/* Heatmap uprostřed — vyplní volný prostor */}
            <div style={{
              flex: 1,
              minWidth: 0,
              borderLeft: '1px solid var(--bg-border)',
              borderRight: '1px solid var(--bg-border)',
              paddingLeft: 'var(--sp-3)',
              paddingRight: 'var(--sp-3)',
              display: 'flex',
              alignItems: 'center',
            }}>
              <FitHeatmap />
            </div>
            {/* Velocity selektor + anchor + akce vpravo */}
            <div style={{
              flexShrink: 0,
              display: 'flex',
              alignItems: 'flex-start',
              gap: 'var(--sp-4)',
              paddingTop: 'var(--sp-1)',
            }}>
              <VelocitySelector />
              <div style={{ borderLeft: '1px solid var(--bg-border)', paddingLeft: 'var(--sp-3)' }}>
                <BankActions />
              </div>
            </div>
          </div>
        ) : (
          <div style={{
            height: '100%', width: '100%', display: 'flex',
            alignItems: 'center', justifyContent: 'center',
            color: 'var(--t-muted)', fontSize: 13, letterSpacing: '0.05em',
          }}>
            Načtěte banku pro zobrazení klaviatury
          </div>
        )}
      </section>

      {/* Hlavní panel */}
      <main style={{
        gridRow:       3,
        display:       'flex',
        flexDirection: 'column',
        overflow:      'hidden',
        background:    'var(--bg-panel)',
      }}>
        <PanelNav />
        <div style={{ flex: 1, overflow: 'hidden', position: 'relative' }}>
          {renderPanel()}
        </div>
      </main>

      {/* Status bar */}
      <footer style={{ gridRow: 4, background: 'var(--bg-base)' }}>
        <StatusBar />
      </footer>
    </div>
  )
}
