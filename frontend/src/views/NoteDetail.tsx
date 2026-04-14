// views/NoteDetail.tsx
// Changelog: 2025-04-14 v0.1 — initial

import React, { useEffect, useRef, useState } from 'react'
import Plotly from 'plotly.js-dist-min'
import { useBankStore } from '../store/bankStore'
import { useFitStore }  from '../store/fitStore'
import { useUiStore }   from '../store/uiStore'
import { midiToNoteName, midiToF0 } from '../types'
import type { NoteParams, PartialParams } from '../types'

const VEL_LABELS = ['pp', 'p', 'mp', 'mf', 'mf+', 'f', 'ff-', 'ff']
const VEL_ALPHA  = [0.25, 0.35, 0.45, 0.6, 0.72, 0.82, 0.92, 1.0]

// Barva velocity vrstvy — tmavší pro pp, světlejší pro ff
function velColor(vel: number, alpha: number): string {
  const h = 195 + vel * 8
  return `hsla(${h}, 55%, ${40 + vel * 4}%, ${alpha})`
}

const LAYOUT_BASE: Partial<Plotly.Layout> = {
  paper_bgcolor: 'transparent',
  plot_bgcolor:  '#141618',
  font:          { family: 'IBM Plex Mono', size: 10, color: '#9B9892' },
  margin:        { l: 48, r: 12, t: 28, b: 36 },
  showlegend:    false,
  xaxis: { gridcolor: '#2A2D35', zerolinecolor: '#2A2D35' },
  yaxis: { gridcolor: '#2A2D35', zerolinecolor: '#2A2D35' },
}

// ---------------------------------------------------------------------------

export const NoteDetail: React.FC = () => {
  const spectrumRef = useRef<HTMLDivElement>(null)
  const decayRef    = useRef<HTMLDivElement>(null)
  const dampingRef  = useRef<HTMLDivElement>(null)
  const beatingRef  = useRef<HTMLDivElement>(null)

  const selectedMidi  = useUiStore(s => s.selectedMidi)
  const selectedK     = useUiStore(s => s.selectedK)
  const selectPartial = useUiStore(s => s.selectPartial)

  const bankState  = useBankStore(s => s.activeState())
  const fetchNote  = useBankStore(s => s.fetchNote)
  const getCached  = useBankStore(s => s.getCachedNote)

  const details = useFitStore(s => s.details)

  const [notes, setNotes] = useState<NoteParams[]>([])
  const [activeK, setActiveK] = useState<number>(1)

  // Načti všechny velocity vrstvy pro vybranou notu
  useEffect(() => {
    if (selectedMidi === null || !bankState) return
    const keys = bankState.note_keys.filter(k =>
      k.startsWith(`m${String(selectedMidi).padStart(3, '0')}_vel`)
    )
    Promise.all(keys.map(k => fetchNote(k))).then(results => {
      const loaded = results.filter(Boolean) as NoteParams[]
      setNotes(loaded.sort((a, b) => a.vel - b.vel))
    })
  }, [selectedMidi, bankState, fetchNote])

  useEffect(() => {
    if (selectedK !== null) setActiveK(selectedK)
  }, [selectedK])

  // ---------------------------------------------------------------------------
  // Plot 1: Harmonic spectrum
  // ---------------------------------------------------------------------------
  useEffect(() => {
    if (!spectrumRef.current || notes.length === 0) return
    const kMax = Math.max(...notes.map(n => n.partials.length))
    const traces: Plotly.Data[] = notes.map(note => {
      const A0dB = note.partials.map(p =>
        20 * Math.log10(Math.max(p.A0 / (note.partials[0]?.A0 || 1), 1e-6))
      )
      return {
        type:  'bar',
        name:  VEL_LABELS[note.vel],
        x:     note.partials.map(p => p.k),
        y:     A0dB,
        marker:{ color: velColor(note.vel, VEL_ALPHA[note.vel]) },
        hovertemplate: `vel ${note.vel} k=%{x}: %{y:.1f} dB<extra></extra>`,
        opacity: VEL_ALPHA[note.vel],
      }
    })

    // Highlight aktivní parciál
    traces.push({
      type: 'scatter', mode: 'markers',
      x: [activeK], y: [0],
      marker: { color: 'var(--c-anchor)', size: 10, symbol: 'diamond' },
      hoverinfo: 'skip',
    })

    const layout: Partial<Plotly.Layout> = {
      ...LAYOUT_BASE,
      height: 160,
      barmode: 'overlay',
      title: { text: `k harmonik — MIDI ${selectedMidi} ${selectedMidi !== null ? midiToNoteName(selectedMidi) : ''}`, font: { size: 10 } },
      xaxis: { ...LAYOUT_BASE.xaxis, title: { text: 'k', font: { size: 9 } }, dtick: 5 },
      yaxis: { ...LAYOUT_BASE.yaxis, title: { text: 'A0(k)/A0(1) [dB]', font: { size: 9 } } },
    }

    if ((spectrumRef.current as any)._plotly) {
      Plotly.react(spectrumRef.current, traces, layout, { responsive: true, displayModeBar: false })
    } else {
      Plotly.newPlot(spectrumRef.current, traces, layout, { responsive: true, displayModeBar: false })
      ;(spectrumRef.current as any)._plotly = true
    }

    ;(spectrumRef.current as any).on('plotly_click', (data: Plotly.PlotMouseEvent) => {
      const k = data.points[0]?.x as number
      if (k) { setActiveK(k); selectPartial(k) }
    })
  }, [notes, activeK, selectedMidi, selectPartial])

  // ---------------------------------------------------------------------------
  // Plot 2: Decay envelope
  // ---------------------------------------------------------------------------
  useEffect(() => {
    if (!decayRef.current || notes.length === 0) return

    const t = Array.from({ length: 200 }, (_, i) => i * 0.05)  // 0–10s
    const traces: Plotly.Data[] = []

    notes.forEach(note => {
      const p = note.partials.find(pp => pp.k === activeK)
      if (!p) return
      const env = t.map(ti => {
        const A = p.A0 * (p.a1 * Math.exp(-ti / p.tau1) + (1 - p.a1) * Math.exp(-ti / p.tau2))
        return 20 * Math.log10(Math.max(A / p.A0, 1e-6))
      })
      traces.push({
        type: 'scatter', mode: 'lines',
        x: t, y: env,
        line: { color: velColor(note.vel, VEL_ALPHA[note.vel]), width: 1.2 },
        name: VEL_LABELS[note.vel],
        hovertemplate: '%{x:.2f}s: %{y:.1f} dB<extra></extra>',
      })
    })

    // Knee bod (τ1 přechod na τ2) pro forte
    const forte = notes.find(n => n.vel === 6) || notes[notes.length - 1]
    const fp = forte?.partials.find(pp => pp.k === activeK)
    if (fp && fp.a1 < 0.99) {
      traces.push({
        type: 'scatter', mode: 'lines',
        x: [fp.tau1, fp.tau1], y: [-60, 0],
        line: { color: 'var(--c-anchor)', width: 1, dash: 'dot' },
        hoverinfo: 'skip', name: 'knee',
      })
    }

    const layout: Partial<Plotly.Layout> = {
      ...LAYOUT_BASE,
      height: 160,
      title: { text: `Decay envelope — k=${activeK}`, font: { size: 10 } },
      xaxis: { ...LAYOUT_BASE.xaxis, title: { text: 't [s]', font: { size: 9 } } },
      yaxis: { ...LAYOUT_BASE.yaxis, title: { text: 'dB', font: { size: 9 } }, range: [-60, 3] },
    }

    if ((decayRef.current as any)._plotly) {
      Plotly.react(decayRef.current, traces, layout, { responsive: true, displayModeBar: false })
    } else {
      Plotly.newPlot(decayRef.current, traces, layout, { responsive: true, displayModeBar: false })
      ;(decayRef.current as any)._plotly = true
    }
  }, [notes, activeK])

  // ---------------------------------------------------------------------------
  // Plot 3: Damping law
  // ---------------------------------------------------------------------------
  useEffect(() => {
    if (!dampingRef.current || notes.length === 0) return

    const mf = notes.find(n => n.vel === 4) || notes[0]
    const fk2 = mf.partials.map(p => p.f_hz * p.f_hz)
    const invTau = mf.partials.map(p => 1 / Math.max(p.tau1, 0.001))
    const colors = mf.partials.map(p => p.fit_quality < 0.5 ? 'var(--c-outlier)' : 'var(--c-mid)')

    const traces: Plotly.Data[] = [
      {
        type: 'scatter', mode: 'markers',
        x: fk2, y: invTau,
        marker: { color: colors, size: 6 },
        hovertemplate: 'f²=%{x:.0f}  1/τ=%{y:.4f}<extra></extra>',
        name: '1/τ1(k)',
      },
    ]

    // Fitted line z damping details
    if (selectedMidi !== null && details?.damping?.[selectedMidi]) {
      const dp = details.damping[selectedMidi]
      const xMax = Math.max(...fk2)
      const xLine = [0, xMax]
      const yLine = xLine.map(x => dp.R + dp.eta * x)
      traces.push({
        type: 'scatter', mode: 'lines',
        x: xLine, y: yLine,
        line: { color: '#534AB7', width: 1.5 },
        name: `R=${dp.R.toFixed(3)} η=${dp.eta.toExponential(2)}`,
        hoverinfo: 'skip',
      })
    }

    const layout: Partial<Plotly.Layout> = {
      ...LAYOUT_BASE,
      height: 160,
      title: { text: 'Damping law (vel mf)', font: { size: 10 } },
      xaxis: { ...LAYOUT_BASE.xaxis, title: { text: 'f_k² [Hz²]', font: { size: 9 } } },
      yaxis: { ...LAYOUT_BASE.yaxis, title: { text: '1/τ₁', font: { size: 9 } } },
    }

    if ((dampingRef.current as any)._plotly) {
      Plotly.react(dampingRef.current, traces, layout, { responsive: true, displayModeBar: false })
    } else {
      Plotly.newPlot(dampingRef.current, traces, layout, { responsive: true, displayModeBar: false })
      ;(dampingRef.current as any)._plotly = true
    }
  }, [notes, details, selectedMidi])

  // ---------------------------------------------------------------------------
  // Plot 4: Beating map (heatmapa k × vel)
  // ---------------------------------------------------------------------------
  useEffect(() => {
    if (!beatingRef.current || notes.length === 0) return

    const kList = notes[0]?.partials.map(p => p.k) ?? []
    const z: number[][] = notes.map(n =>
      kList.map(k => n.partials.find(p => p.k === k)?.beat_hz ?? 0)
    )

    const traces: Plotly.Data[] = [{
      type: 'heatmap',
      z,
      x: kList,
      y: notes.map(n => VEL_LABELS[n.vel]),
      colorscale: [[0, '#141618'], [0.01, '#1A2A3A'], [1, '#378ADD']],
      hovertemplate: 'k=%{x} vel=%{y}: beat=%{z:.3f} Hz<extra></extra>',
      showscale: false,
    }]

    const layout: Partial<Plotly.Layout> = {
      ...LAYOUT_BASE,
      height: 110,
      title: { text: 'Beating map (beat_hz)', font: { size: 10 } },
      xaxis: { ...LAYOUT_BASE.xaxis, title: { text: 'k', font: { size: 9 } } },
      yaxis: { ...LAYOUT_BASE.yaxis, title: { text: '', font: { size: 9 } } },
    }

    if ((beatingRef.current as any)._plotly) {
      Plotly.react(beatingRef.current, traces, layout, { responsive: true, displayModeBar: false })
    } else {
      Plotly.newPlot(beatingRef.current, traces, layout, { responsive: true, displayModeBar: false })
      ;(beatingRef.current as any)._plotly = true
    }
  }, [notes])

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  if (selectedMidi === null) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center',
                    height: '100%', color: 'var(--t-muted)', fontSize: 13 }}>
        Klikněte na klávesu pro detail noty
      </div>
    )
  }

  const forte = notes.find(n => n.vel === 6) || notes[0]

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-3)',
                  padding: 'var(--sp-3)', height: '100%', overflowY: 'auto' }}
         className="animate-in">

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 'var(--sp-3)' }}>
        <span style={{ fontFamily: 'var(--font-ui)', fontWeight: 700, fontSize: 18,
                       color: 'var(--t-primary)' }}>
          {midiToNoteName(selectedMidi)}
        </span>
        <span className="mono" style={{ fontSize: 12, color: 'var(--t-muted)' }}>
          MIDI {selectedMidi}
        </span>
        {forte && (
          <>
            <span className="tag">f₀ {forte.f0_hz.toFixed(2)} Hz</span>
            <span className="tag">B {forte.B.toExponential(3)}</span>
            <span className="tag">{forte.n_strings}× struna</span>
            <span className="tag">{forte.partials.length} parciálů</span>
          </>
        )}
        {/* Parciál selector */}
        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 6 }}>
          <span className="label">parciál k=</span>
          <select
            className="select"
            value={activeK}
            onChange={e => { const k = Number(e.target.value); setActiveK(k); selectPartial(k) }}
            style={{ width: 60 }}
          >
            {(forte?.partials ?? []).map(p => (
              <option key={p.k} value={p.k}>{p.k}</option>
            ))}
          </select>
        </div>
      </div>

      {/* Grafy */}
      <div ref={spectrumRef} style={{ width: '100%' }} />
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 'var(--sp-3)' }}>
        <div ref={decayRef}   style={{ width: '100%' }} />
        <div ref={dampingRef} style={{ width: '100%' }} />
      </div>
      <div ref={beatingRef} style={{ width: '100%' }} />
    </div>
  )
}
