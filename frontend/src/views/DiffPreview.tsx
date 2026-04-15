// views/DiffPreview.tsx
// Changelog:
//   2025-04-14 v0.1 — initial
//   2025-04-14 v0.2 — export do ./exported/, SysEx patch tlacitko, MIDI stav

import React, { useEffect, useRef, useState } from 'react'
import Plotly from 'plotly.js-dist-min'
import { useCorrectionStore } from '../store/correctionStore'
import { useBankStore }       from '../store/bankStore'
import { useFitStore }        from '../store/fitStore'
import { useUiStore }         from '../store/uiStore'
import { exportApi, midiApi, correctionsApi } from '../api/client'
import { useAnchorStore }    from '../store/anchorStore'
import { midiToNoteName }     from '../types'
import type { Correction }    from '../types'

const SOURCE_LABELS: Record<string, string> = {
  b_curve_fit:    'B-curve',
  damping_law:    'Damping',
  anchor_interp:  'Anchor',
  spectral_shape: 'Spectral',
  velocity_model: 'Velocity',
  manual:         'Ruční',
}

function deltaColor(delta: number): string {
  const abs = Math.abs(delta)
  if (abs < 5)  return 'var(--c-fit)'
  if (abs < 20) return 'var(--c-anchor)'
  return 'var(--c-outlier)'
}

export const DiffPreview: React.FC = () => {
  const histRef = useRef<HTMLDivElement>(null)

  const {
    pending, selectedCorrections, toggleSelect, selectAll, selectNone,
    isSelected, apply,
  } = useCorrectionStore()

  const activePath  = useBankStore(s => s.activePath())
  const summary     = useFitStore(s => s.summary)
  const { runFit }  = useFitStore()
  const { selectNote, setStatus } = useUiStore()

  const [filterSource, setFilterSource] = useState<string>('all')
  const [minDelta, setMinDelta]          = useState(0)
  const [diffOnly, setDiffOnly]          = useState(false)
  const [addMeta, setAddMeta]            = useState(true)
  const [applying, setApplying]          = useState(false)
  const [exportedPath, setExportedPath]  = useState<string | null>(null)

  // MIDI stav
  const [midiConnected, setMidiConnected] = useState(false)
  const [midiPort,      setMidiPort]      = useState<string>('')
  const [patching,      setPatching]      = useState(false)
  const [patchResult,   setPatchResult]   = useState<
    { ok: true; success: number; total: number } |
    { ok: false; error: string } | null
  >(null)

  // Kontrola MIDI stavu při mountu
  useEffect(() => {
    midiApi.status().then(s => {
      setMidiConnected(s.connected)
      setMidiPort(s.port_name ?? '')
    }).catch(() => {})
  }, [])

  // Histogram delta %
  useEffect(() => {
    if (!histRef.current || !pending) return
    const deltas = pending.corrections.map(c => c.delta_pct)
    const traces: Plotly.Data[] = [{
      type: 'histogram',
      x: deltas,
      // @ts-ignore
      nbinsx: 20,
      marker: { color: deltas.map(d => deltaColor(d)) },
      hovertemplate: '%{x:.1f}%: %{y} korekcí<extra></extra>',
    }]
    const layout: Partial<Plotly.Layout> = {
      paper_bgcolor: 'transparent',
      plot_bgcolor:  '#2A2D30',
      font: { family: 'IBM Plex Mono', size: 10, color: '#9B9892' },
      margin: { l: 40, r: 8, t: 16, b: 30 },
      height: 90,
      xaxis: { title: { text: 'delta %', font: { size: 9 } }, gridcolor: '#3E4044' },
      yaxis: { title: { text: 'N', font: { size: 9 } }, gridcolor: '#3E4044' },
      shapes: [
        { type: 'line', x0: 5,  x1: 5,  y0: 0, y1: 1, yref: 'paper', line: { color: 'var(--c-anchor)', width: 1, dash: 'dot' } },
        { type: 'line', x0: 20, x1: 20, y0: 0, y1: 1, yref: 'paper', line: { color: 'var(--c-outlier)', width: 1, dash: 'dot' } },
      ],
    }
    if ((histRef.current as any)._plotly) {
      Plotly.react(histRef.current, traces, layout, { displayModeBar: false })
    } else {
      Plotly.newPlot(histRef.current, traces, layout, { displayModeBar: false })
      ;(histRef.current as any)._plotly = true
    }
  }, [pending])

  const { propose } = useCorrectionStore()
  const activeAnchor = useAnchorStore(s => s.active)
  const [tensionVal, setTensionVal] = useState(0.5)
  const [rbfKernel, setRbfKernel] = useState('thin_plate_spline')
  const EPSILON_FREE = new Set(['cubic', 'linear', 'thin_plate_spline', 'quintic'])
  const needsEpsilon = !EPSILON_FREE.has(rbfKernel)
  // slider 0.1–1.0 → epsilon 0.5–5.0
  const sliderToEpsilon = (v: number) => 0.5 + (v - 0.1) * (4.5 / 0.9)

  async function handlePropose() {
    if (!activePath || !summary) return
    setApplying(true)
    setStatus('Navrhuji korekce…')
    try {
      await propose(activePath, summary)
      setStatus('Korekce navrženy')
    } catch (e: any) {
      setStatus(`Chyba: ${e.message}`)
    } finally {
      setApplying(false)
    }
  }

  async function handleTension() {
    if (!activePath) return
    setApplying(true)
    setStatus(`Tension manifold (${tensionVal})…`)
    try {
      const cs = await correctionsApi.tension(
        activePath, activeAnchor?.name, tensionVal)
      useCorrectionStore.setState({
        pending: cs,
        selected: new Set(cs.corrections.map(
          (c: any) => `${c.midi}_${c.vel}_${c.field}`)),
      })
      setStatus(`Tension: ${cs.corrections.length} korekcí`)
    } catch (e: any) {
      setStatus(`Chyba: ${e.message}`)
    } finally {
      setApplying(false)
    }
  }

  async function handlePCA() {
    if (!activePath) return
    setApplying(true)
    setStatus(`PCA manifold (${tensionVal})…`)
    try {
      const cs = await correctionsApi.pca(
        activePath, activeAnchor?.name, tensionVal)
      useCorrectionStore.setState({
        pending: cs,
        selected: new Set(cs.corrections.map(
          (c: any) => `${c.midi}_${c.vel}_${c.field}`)),
      })
      setStatus(`PCA: ${cs.corrections.length} korekcí`)
    } catch (e: any) {
      setStatus(`Chyba: ${e.message}`)
    } finally {
      setApplying(false)
    }
  }

  async function handleRBF() {
    if (!activePath) return
    setApplying(true)
    const epsilon = needsEpsilon ? sliderToEpsilon(tensionVal) : undefined
    const tension = needsEpsilon ? 0.5 : tensionVal
    setStatus(`RBF ${rbfKernel} (${needsEpsilon ? 'ε=' + epsilon!.toFixed(1) : 't=' + tensionVal})…`)
    try {
      const cs = await correctionsApi.rbf(
        activePath, activeAnchor?.name, tension, rbfKernel, 0.0, epsilon)
      useCorrectionStore.setState({
        pending: cs,
        selected: new Set(cs.corrections.map(
          (c: any) => `${c.midi}_${c.vel}_${c.field}`)),
      })
      setStatus(`RBF: ${cs.corrections.length} korekcí`)
    } catch (e: any) {
      setStatus(`Chyba: ${e.message}`)
    } finally {
      setApplying(false)
    }
  }

  if (!pending) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center',
                    justifyContent: 'center', height: '100%', gap: 'var(--sp-4)',
                    color: 'var(--t-muted)' }}>
        <span style={{ fontSize: 13 }}>Nejsou žádné korekce k zobrazení.</span>
        <div style={{ display: 'flex', gap: 'var(--sp-3)', alignItems: 'center' }}>
          <button className="btn btn--accent" onClick={handlePropose}
                  disabled={!activePath || !summary || applying}>
            {applying ? '…' : 'Fit korekce'}
          </button>
          <button className="btn btn--accent" onClick={handleTension}
                  disabled={!activePath || !activeAnchor || applying}
                  style={{ background: 'var(--c-bass)' }}>
            {applying ? '…' : 'Tension'}
          </button>
          <button className="btn btn--accent" onClick={handlePCA}
                  disabled={!activePath || !activeAnchor || applying}
                  style={{ background: 'var(--c-fit)' }}>
            {applying ? '…' : 'PCA'}
          </button>
          <button className="btn btn--accent" onClick={handleRBF}
                  disabled={!activePath || !activeAnchor || applying}
                  style={{ background: 'var(--c-mid)' }}>
            {applying ? '…' : 'RBF'}
          </button>
          <select className="select" value={rbfKernel}
                  onChange={e => setRbfKernel(e.target.value)}
                  style={{ fontSize: 10, padding: '2px 4px' }}>
            <option value="thin_plate_spline">thin plate</option>
            <option value="multiquadric">multiquadric</option>
            <option value="cubic">cubic</option>
            <option value="gaussian">gaussian</option>
            <option value="linear">linear</option>
          </select>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--sp-2)', fontSize: 11 }}>
          <span className="label">{needsEpsilon ? 'epsilon' : 'tension'}</span>
          <input type="range" className="slider" style={{ width: 120 }}
                 min={0.1} max={1.0} step={0.1} value={tensionVal}
                 onChange={e => setTensionVal(Number(e.target.value))} />
          <span className="mono">{needsEpsilon ? sliderToEpsilon(tensionVal).toFixed(1) : tensionVal.toFixed(1)}</span>
        </div>
      </div>
    )
  }

  // Filtrování
  const visible = pending.corrections.filter(c => {
    if (filterSource !== 'all' && c.source !== filterSource) return false
    if (Math.abs(c.delta_pct) < minDelta) return false
    return true
  }).sort((a, b) => Math.abs(b.delta_pct) - Math.abs(a.delta_pct))

  const selected = selectedCorrections()
  const affected = new Set(visible.filter(c => isSelected(c)).map(c => `${c.midi}_${c.vel}`)).size

  async function handleApply() {
    if (!activePath) return
    setApplying(true)
    try {
      await apply(activePath)
      setStatus(`Korekce aplikovány`)
      await runFit(activePath)
    } catch (e: any) {
      setStatus(`Chyba při aplikaci: ${e.message}`)
    } finally {
      setApplying(false)
    }
  }

  function correctionSuffix(): string {
    const desc = pending?.description ?? ''
    if (desc.startsWith('RBF surface:')) {
      const m = desc.match(/kernel=(\w+)/)
      return m ? `rbf-${m[1]}` : 'rbf'
    }
    if (desc.startsWith('PCA manifold:')) return 'pca'
    if (desc.startsWith('tension manifold:')) return 'tension'
    return 'fit'
  }

  async function handleExport() {
    if (!activePath) return
    const stem = activePath.split(/[/\\]/).pop()?.replace('.json', '') ?? 'bank'
    const suffix = correctionSuffix()
    const outName = `./exported/${stem}-${suffix}.json`
    try {
      const res = await exportApi.bank(activePath, outName, diffOnly, addMeta)
      setExportedPath(res.path.split(/[/\\]/).pop() ?? res.path)
      setStatus(`Exportováno → exported/${stem}-${suffix}.json`)
    } catch (e: any) {
      setStatus(`Export selhal: ${e.message}`)
    }
  }

  async function handlePatch() {
    if (!activePath || !exportedPath) return
    setPatching(true); setPatchResult(null)
    try {
      // Patchujeme exportovanou banku (./exported/...)
      const exportPath = `./exported/${exportedPath}`
      const r = await midiApi.patch(exportPath)
      setPatchResult({ ok: true, success: r.success, total: r.total })
      setStatus(`SysEx patch: ${r.success}/${r.total} not`)
    } catch (e: any) {
      setPatchResult({ ok: false, error: e.message })
      setStatus(`SysEx patch selhal: ${e.message}`)
    } finally {
      setPatching(false)
    }
  }

  async function handleCsvReport() {
    if (!activePath || !pending) return
    await exportApi.diffReport(activePath, pending)
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-3)',
                  padding: 'var(--sp-4)', height: '100%', overflowY: 'auto' }}
         className="animate-in">

      {/* Summary */}
      <div style={{ display: 'flex', gap: 'var(--sp-3)', alignItems: 'center',
                    padding: 'var(--sp-3) var(--sp-4)',
                    background: 'var(--bg-card)', borderRadius: 'var(--r-md)',
                    border: '1px solid var(--bg-border)' }}>
        <div style={{ textAlign: 'center', minWidth: 56 }}>
          <div className="mono" style={{ fontSize: 22, fontWeight: 600, color: 'var(--t-primary)' }}>
            {pending.corrections.length}
          </div>
          <div className="label">korekcí</div>
        </div>
        <div style={{ textAlign: 'center', minWidth: 56 }}>
          <div className="mono" style={{ fontSize: 22, fontWeight: 600, color: 'var(--c-mid)' }}>
            {affected}
          </div>
          <div className="label">not</div>
        </div>
        <div style={{ textAlign: 'center', minWidth: 56 }}>
          <div className="mono" style={{ fontSize: 22, fontWeight: 600, color: deltaColor(
            Math.max(...pending.corrections.map(c => Math.abs(c.delta_pct)), 0)
          )}}>
            {Math.max(...pending.corrections.map(c => Math.abs(c.delta_pct)), 0).toFixed(1)}%
          </div>
          <div className="label">max Δ</div>
        </div>
        <div ref={histRef} style={{ flex: 1, minWidth: 0 }} />
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 'var(--sp-2)', flexShrink: 0, alignItems: 'center' }}>
          <span className="label">{needsEpsilon ? 'ε' : 'tension'}</span>
          <input type="range" className="slider" style={{ width: 80 }}
                 min={0.1} max={1.0} step={0.1} value={tensionVal}
                 onChange={e => setTensionVal(Number(e.target.value))} />
          <span className="mono" style={{ fontSize: 10 }}>{needsEpsilon ? sliderToEpsilon(tensionVal).toFixed(1) : tensionVal.toFixed(1)}</span>
          <button className="btn" onClick={handleTension}
                  disabled={!activePath || !activeAnchor || applying}
                  style={{ background: 'var(--c-bass)', color: '#fff' }}>
            {applying ? '…' : '⊗ Tension'}
          </button>
          <button className="btn" onClick={handlePCA}
                  disabled={!activePath || !activeAnchor || applying}
                  style={{ background: 'var(--c-fit)', color: '#fff' }}>
            {applying ? '…' : '◎ PCA'}
          </button>
          <button className="btn" onClick={handleRBF}
                  disabled={!activePath || !activeAnchor || applying}
                  style={{ background: 'var(--c-mid)', color: '#fff' }}>
            {applying ? '…' : '◈ RBF'}
          </button>
          <select className="select" value={rbfKernel}
                  onChange={e => setRbfKernel(e.target.value)}
                  style={{ fontSize: 9, padding: '1px 3px' }}>
            <option value="thin_plate_spline">thin plate</option>
            <option value="multiquadric">multiquadric</option>
            <option value="cubic">cubic</option>
            <option value="gaussian">gaussian</option>
            <option value="linear">linear</option>
          </select>
          <button className="btn" onClick={handlePropose}
                  disabled={!activePath || !summary || applying}>
            {applying ? '…' : '↻ Fit'}
          </button>
        </div>
      </div>

      {pending.corrections.length === 0 && (
        <div style={{ padding: 'var(--sp-4)', textAlign: 'center',
                      color: 'var(--t-muted)', fontSize: 13 }}>
          Žádné korekce nebyly navrženy — parametry banky jsou v rámci tolerancí fitu.
        </div>
      )}

      {/* Filtry */}
      <div style={{ display: 'flex', gap: 'var(--sp-3)', alignItems: 'center', flexWrap: 'wrap' }}>
        <span className="label">Zdroj</span>
        <select className="select" value={filterSource}
                onChange={e => setFilterSource(e.target.value)}>
          <option value="all">Všechny</option>
          {Object.entries(SOURCE_LABELS).map(([k, v]) => (
            <option key={k} value={k}>{v}</option>
          ))}
        </select>
        <span className="label">Min Δ%</span>
        <input type="number" className="input" style={{ width: 60 }}
               value={minDelta} min={0} max={100} step={1}
               onChange={e => setMinDelta(Number(e.target.value))} />
        <button className="btn" onClick={selectAll}>Vybrat vše</button>
        <button className="btn" onClick={selectNone}>Odebrat vše</button>
        <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--t-muted)' }}>
          {selected.length} / {visible.length} vybráno
        </span>
      </div>

      {/* Tabulka */}
      <div style={{ flex: 1, overflowY: 'auto', border: '1px solid var(--bg-border)',
                    borderRadius: 'var(--r-md)' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
          <thead style={{ position: 'sticky', top: 0, zIndex: 1 }}>
            <tr style={{ background: 'var(--bg-card)', borderBottom: '1px solid var(--bg-border)' }}>
              <th style={{ padding: '5px 8px', width: 24 }}/>
              {['Nota','Vel','Parametr','Originál','Korekce','Zdroj','Δ %'].map(h => (
                <th key={h} style={{ padding: '5px 8px', textAlign: 'left',
                                      color: 'var(--t-muted)', fontWeight: 500, fontSize: 10 }}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visible.slice(0, 500).map((c, i) => {
              const sel = isSelected(c)
              return (
                <tr
                  key={i}
                  onClick={() => selectNote(c.midi)}
                  style={{
                    borderBottom: '1px solid var(--bg-border)',
                    background: sel
                      ? (i % 2 === 0 ? 'var(--bg-card)' : '#1c1e24')
                      : 'transparent',
                    opacity: sel ? 1 : 0.45,
                    cursor: 'pointer',
                  }}
                >
                  <td style={{ padding: '4px 8px' }}>
                    <input
                      type="checkbox" className="check"
                      checked={sel}
                      onChange={() => toggleSelect(c)}
                      onClick={e => e.stopPropagation()}
                    />
                  </td>
                  <td style={{ padding: '4px 8px' }}>
                    <span className="mono" style={{ fontWeight: 600 }}>
                      {midiToNoteName(c.midi)}
                    </span>
                    <span style={{ color: 'var(--t-muted)', marginLeft: 4, fontSize: 10 }}>
                      {c.midi}
                    </span>
                  </td>
                  <td style={{ padding: '4px 8px', color: 'var(--t-secondary)' }}>
                    {['pp','p','mp','mf','mf+','f','ff-','ff'][c.vel] ?? c.vel}
                  </td>
                  <td style={{ padding: '4px 8px' }}>
                    <code style={{ fontSize: 11, color: 'var(--t-mono)',
                                   background: 'var(--bg-input)', padding: '1px 4px',
                                   borderRadius: 2 }}>
                      {c.field}
                    </code>
                  </td>
                  <td style={{ padding: '4px 8px', fontFamily: 'var(--font-mono)', fontSize: 11,
                                color: 'var(--t-muted)' }}>
                    {c.original.toPrecision(5)}
                  </td>
                  <td style={{ padding: '4px 8px', fontFamily: 'var(--font-mono)', fontSize: 11,
                                color: 'var(--t-primary)' }}>
                    {c.corrected.toPrecision(5)}
                  </td>
                  <td style={{ padding: '4px 8px' }}>
                    <span className="tag" style={{ fontSize: 10 }}>
                      {SOURCE_LABELS[c.source] ?? c.source}
                    </span>
                  </td>
                  <td style={{ padding: '4px 8px', fontFamily: 'var(--font-mono)', fontSize: 12,
                                fontWeight: 600, color: deltaColor(c.delta_pct) }}>
                    {c.delta_pct > 0 ? '+' : ''}{c.delta_pct.toFixed(1)}%
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
        {visible.length > 500 && (
          <div style={{ padding: '8px 12px', textAlign: 'center', fontSize: 11,
                        color: 'var(--t-muted)', borderTop: '1px solid var(--bg-border)' }}>
            Zobrazeno 500 / {visible.length} korekcí (seřazeno podle |Δ%|)
          </div>
        )}
      </div>

      {/* Export & Patch */}
      <div style={{
        background: 'var(--bg-card)', borderRadius: 'var(--r-md)',
        border: '1px solid var(--bg-border)',
        overflow: 'hidden',
      }}>

        {/* Export do souboru */}
        <div style={{
          padding: 'var(--sp-3) var(--sp-4)',
          borderBottom: '1px solid var(--bg-border)',
          display: 'flex', alignItems: 'center', gap: 'var(--sp-4)', flexWrap: 'wrap',
        }}>
          <span className="label" style={{ minWidth: 60 }}>Export</span>
          <label style={{ display:'flex', alignItems:'center', gap:6, fontSize:12, cursor:'pointer' }}>
            <input type="checkbox" className="check" checked={diffOnly}
                   onChange={e => setDiffOnly(e.target.checked)} />
            Pouze změněné noty
          </label>
          <label style={{ display:'flex', alignItems:'center', gap:6, fontSize:12, cursor:'pointer' }}>
            <input type="checkbox" className="check" checked={addMeta}
                   onChange={e => setAddMeta(e.target.checked)} />
            Metadata editoru
          </label>
          <div style={{ marginLeft:'auto', display:'flex', gap:'var(--sp-2)', alignItems:'center' }}>
            <span style={{ fontSize:11, color:'var(--t-muted)' }}>→ ./exported/</span>
            <button className="btn" onClick={handleCsvReport}>CSV</button>
            <button className="btn" onClick={handleExport} disabled={applying}>
              Export JSON
            </button>
            <button
              className="btn btn--accent"
              onClick={handleApply}
              disabled={applying || selected.length === 0}
            >
              {applying ? 'Aplikuji…' : `Aplikovat ${selected.length}`}
            </button>
          </div>
        </div>

        {/* SysEx Patch */}
        <div style={{
          padding: 'var(--sp-3) var(--sp-4)',
          display: 'flex', alignItems: 'center', gap: 'var(--sp-4)', flexWrap: 'wrap',
        }}>
          <span className="label" style={{ minWidth: 60 }}>SysEx</span>
          <div style={{ display:'flex', alignItems:'center', gap:6 }}>
            <span className={`status-dot status-dot--${midiConnected ? 'ok' : 'off'}`} />
            <span style={{ fontSize:11, fontFamily:'var(--font-mono)',
                           color: midiConnected ? 'var(--c-mid)' : 'var(--t-muted)' }}>
              {midiConnected ? midiPort : 'nepřipojeno'}
            </span>
          </div>
          {!midiConnected && (
            <span style={{ fontSize:11, color:'var(--t-muted)' }}>
              Připojte MIDI v panelu MIDI
            </span>
          )}
          <div style={{ marginLeft:'auto', display:'flex', gap:'var(--sp-2)', alignItems:'center' }}>
            {patchResult && (
              <span style={{
                fontSize:11, fontFamily:'var(--font-mono)',
                color: patchResult.ok ? 'var(--c-mid)' : 'var(--c-outlier)',
              }}>
                {patchResult.ok
                  ? `✓ ${patchResult.success}/${patchResult.total} not`
                  : `✗ ${patchResult.error}`}
              </span>
            )}
            <button
              className="btn"
              onClick={handlePatch}
              disabled={patching || !midiConnected || !exportedPath}
              title={!exportedPath ? 'Nejprve exportujte banku' : !midiConnected ? 'MIDI není připojeno' : 'Patch syntetizér'}
            >
              {patching ? 'Patchuji…' : '⚡ SysEx patch'}
            </button>
          </div>
        </div>

        {/* Feedback */}
        {exportedPath && (
          <div style={{
            padding: '4px var(--sp-4)',
            borderTop: '1px solid var(--bg-border)',
            fontSize:11, fontFamily:'var(--font-mono)', color:'var(--c-mid)',
            background:'#0d2218',
          }}>
            ✓ Exportováno: {exportedPath}
          </div>
        )}
      </div>
    </div>
  )
}
