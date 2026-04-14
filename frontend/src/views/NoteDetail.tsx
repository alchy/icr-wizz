// views/NoteDetail.tsx
// Changelog: 2025-04-14 v0.1 — initial

import React, { useEffect, useRef, useState } from 'react'
import Plotly from 'plotly.js-dist-min'
import { useBankStore }       from '../store/bankStore'
import { useFitStore }        from '../store/fitStore'
import { useCorrectionStore } from '../store/correctionStore'
import { useUiStore }         from '../store/uiStore'
import { midiToNoteName, midiToF0 } from '../types'
import type { NoteParams, PartialParams, Correction } from '../types'

const VEL_LABELS = ['pp', 'p', 'mp', 'mf', 'mf+', 'f', 'ff-', 'ff']

// Correction overlay barvy — konzistentní přes celé UI
const C_CORR_FILL   = 'rgba(29, 158, 117, 0.35)'  // světle zelená translucent
const C_CORR_LINE   = '#1D9E75'                     // zelená plná
const C_CORR_BORDER = '#0a3020'                     // tmavě zelený okraj
const VEL_ALPHA  = [0.25, 0.35, 0.45, 0.6, 0.72, 0.82, 0.92, 1.0]

// Barva velocity vrstvy — tmavší pro pp, světlejší pro ff
function velColor(vel: number, alpha: number): string {
  const h = 195 + vel * 8
  return `hsla(${h}, 55%, ${40 + vel * 4}%, ${alpha})`
}

const LAYOUT_BASE: Partial<Plotly.Layout> = {
  paper_bgcolor: 'transparent',
  plot_bgcolor:  '#2A2D30',
  font:          { family: 'IBM Plex Mono', size: 10, color: '#9B9892' },
  margin:        { l: 48, r: 12, t: 28, b: 36 },
  showlegend:    false,
  xaxis: { gridcolor: '#3E4044', zerolinecolor: '#3E4044' },
  yaxis: { gridcolor: '#3E4044', zerolinecolor: '#3E4044' },
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
  const pending = useCorrectionStore(s => s.pending)

  const [notes, setNotes] = useState<NoteParams[]>([])
  const [activeK, setActiveK] = useState<number>(1)

  // Index korekcí pro vybranou notu: field → Correction
  // Preferujeme selectedVel, fallback na jakoukoli vel s max |delta|
  const selectedVel = useUiStore(s => s.selectedVel)
  const corrMap = React.useMemo(() => {
    const map = new Map<string, Correction>()
    if (!pending || selectedMidi === null) return map
    for (const c of pending.corrections) {
      if (c.midi !== selectedMidi) continue
      const existing = map.get(c.field)
      if (!existing) {
        map.set(c.field, c)
      } else if (c.vel === selectedVel && existing.vel !== selectedVel) {
        map.set(c.field, c)
      } else if (c.vel === selectedVel && existing.vel === selectedVel && Math.abs(c.delta_pct) > Math.abs(existing.delta_pct)) {
        map.set(c.field, c)
      }
    }
    return map
  }, [pending, selectedMidi, selectedVel])

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
      const isActive = note.vel === selectedVel
      const A0dB = note.partials.map(p =>
        20 * Math.log10(Math.max(p.A0 / (note.partials[0]?.A0 || 1), 1e-6))
      )
      return {
        type:  'bar',
        name:  `⬤ ${VEL_LABELS[note.vel]}${isActive ? ' ◄' : ''}`,
        x:     note.partials.map(p => p.k),
        y:     A0dB,
        marker:{ color: velColor(note.vel, isActive ? 1.0 : VEL_ALPHA[note.vel] * 0.4),
                 line: isActive ? { color: '#E8E6E0', width: 0.5 } : undefined },
        hovertemplate: `vel ${note.vel} k=%{x}: %{y:.1f} dB<extra></extra>`,
        opacity: isActive ? 1.0 : VEL_ALPHA[note.vel] * 0.5,
      }
    })

    // Correction overlay: trojúhelníky s výškou dle míry korekce
    const corrPartials: number[] = []
    if (corrMap.size > 0) {
      const corrK: number[] = [], corrY: number[] = [], corrText: string[] = []
      const corrSizes: number[] = [], corrColors: string[] = []
      for (const [field, c] of corrMap) {
        const m = field.match(/^(tau[12]|A0|a1|beat_hz)_k(\d+)$/)
        if (!m) continue
        const k = parseInt(m[2])
        if (corrPartials.includes(k)) continue
        corrPartials.push(k)
        corrK.push(k)
        // Y pozice = clamp delta do rozsahu grafu (-50 až 0)
        const absDelta = Math.min(Math.abs(c.delta_pct), 200)
        corrY.push(-absDelta / 4)  // -50 max, 0 min
        // Velikost markeru úměrná delta
        corrSizes.push(Math.max(5, Math.min(16, absDelta / 10)))
        // Barva: zelená pro malé, oranžová pro střední, červená pro velké
        corrColors.push(absDelta < 20 ? C_CORR_LINE : absDelta < 80 ? '#BA7517' : '#E24B4A')
        corrText.push(`k=${k}: ${c.field} ${c.original.toPrecision(4)}→${c.corrected.toPrecision(4)} (${c.delta_pct.toFixed(1)}%)`)
      }
      if (corrK.length > 0) {
        traces.push({
          type: 'scatter', mode: 'markers',
          x: corrK, y: corrY,
          marker: { color: corrColors, size: corrSizes, symbol: 'triangle-up',
                    line: { color: C_CORR_LINE, width: 1 } },
          hovertemplate: '%{text}<extra></extra>',
          text: corrText,
          name: '◇ korekce',
        })
      }
    }

    // Highlight aktivní parciál
    traces.push({
      type: 'scatter', mode: 'markers',
      x: [activeK], y: [0],
      marker: { color: 'var(--c-anchor)', size: 10, symbol: 'diamond' },
      hoverinfo: 'skip',
    })

    // B korekce anotace
    const bCorr = corrMap.get('B')
    const corrCount = corrMap.size
    let titleSuffix = ''
    if (bCorr) titleSuffix += `  B: ${bCorr.original.toExponential(2)}→${bCorr.corrected.toExponential(2)}`
    if (corrCount > 0) titleSuffix += `  [${corrCount} korekcí]`

    const layout: Partial<Plotly.Layout> = {
      ...LAYOUT_BASE,
      height: 160,
      barmode: 'overlay',
      title: { text: `k harmonik — ${selectedMidi !== null ? midiToNoteName(selectedMidi) : ''} MIDI ${selectedMidi}${titleSuffix}`,
               font: { size: 10, color: corrCount > 0 ? C_CORR_LINE : '#9B9892' } },
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
  }, [notes, activeK, selectedMidi, selectedVel, selectPartial, corrMap])

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
      const isActive = note.vel === selectedVel
      const env = t.map(ti => {
        const A = p.A0 * (p.a1 * Math.exp(-ti / p.tau1) + (1 - p.a1) * Math.exp(-ti / p.tau2))
        return 20 * Math.log10(Math.max(A / p.A0, 1e-6))
      })
      traces.push({
        type: 'scatter', mode: 'lines',
        x: t, y: env,
        line: { color: velColor(note.vel, isActive ? 1.0 : VEL_ALPHA[note.vel] * 0.4),
                width: isActive ? 2.5 : 0.8 },
        name: `${VEL_LABELS[note.vel]}${isActive ? ' ◄' : ''}`,
        hovertemplate: `${VEL_LABELS[note.vel]} %{x:.2f}s: %{y:.1f} dB<extra></extra>`,
        opacity: isActive ? 1.0 : 0.4,
      })
    })

    // Correction overlay: opravená decay obálka (světle zelená)
    const tau1Corr = corrMap.get(`tau1_k${activeK}`)
    const tau2Corr = corrMap.get(`tau2_k${activeK}`)
    if (tau1Corr || tau2Corr) {
      const refNote = notes.find(n => n.vel === selectedVel) || notes[0]
      const refP = refNote?.partials.find(pp => pp.k === activeK)
      if (refP) {
        const newTau1 = tau1Corr?.corrected ?? refP.tau1
        const newTau2 = tau2Corr?.corrected ?? refP.tau2
        const envCorr = t.map(ti => {
          const A = refP.A0 * (refP.a1 * Math.exp(-ti / newTau1) + (1 - refP.a1) * Math.exp(-ti / newTau2))
          return 20 * Math.log10(Math.max(A / refP.A0, 1e-6))
        })
        traces.push({
          type: 'scatter', mode: 'lines',
          x: t, y: envCorr,
          line: { color: C_CORR_LINE, width: 2.5, dash: 'dot' },
          name: '◇ opravená obálka',
          hovertemplate: 'corr %{x:.2f}s: %{y:.1f} dB<extra></extra>',
        })
      }
    }

    // Knee bod (τ1 přechod na τ2) pro forte
    const forte = notes.find(n => n.vel === 6) || notes[notes.length - 1]
    const fp = forte?.partials.find(pp => pp.k === activeK)
    if (fp && fp.a1 < 0.99) {
      traces.push({
        type: 'scatter', mode: 'lines',
        x: [fp.tau1, fp.tau1], y: [-60, 0],
        line: { color: 'var(--c-anchor)', width: 1, dash: 'dot' },
        hoverinfo: 'skip', name: '◇ knee',
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
  }, [notes, activeK, corrMap, selectedVel])

  // ---------------------------------------------------------------------------
  // Plot 3: Damping law
  // ---------------------------------------------------------------------------
  useEffect(() => {
    if (!dampingRef.current || notes.length === 0) return

    const activeNote = notes.find(n => n.vel === selectedVel) || notes.find(n => n.vel === 4) || notes[0]
    const fk2 = activeNote.partials.map(p => p.f_hz * p.f_hz)
    const invTau = activeNote.partials.map(p => 1 / Math.max(p.tau1, 0.001))
    const colors = activeNote.partials.map(p => p.fit_quality < 0.5 ? 'var(--c-outlier)' : 'var(--c-mid)')

    const traces: Plotly.Data[] = [
      {
        type: 'scatter', mode: 'markers',
        x: fk2, y: invTau,
        marker: { color: colors, size: 6 },
        hovertemplate: 'f²=%{x:.0f}  1/τ=%{y:.4f}<extra></extra>',
        name: '⬤ 1/τ1(k)',
      },
    ]

    // Correction overlay: opravené τ1 jako světle zelené body
    {
      const corrFk2: number[] = [], corrInvTau: number[] = [], corrText: string[] = []
      for (const [field, c] of corrMap) {
        const m = field.match(/^tau1_k(\d+)$/)
        if (!m) continue
        const k = parseInt(m[1])
        const p = activeNote.partials.find(pp => pp.k === k)
        if (!p) continue
        corrFk2.push(p.f_hz * p.f_hz)
        corrInvTau.push(1 / Math.max(c.corrected, 0.001))
        corrText.push(`k=${k}: τ1 ${c.original.toFixed(3)}→${c.corrected.toFixed(3)}s`)
      }
      if (corrFk2.length > 0) {
        traces.push({
          type: 'scatter', mode: 'markers',
          x: corrFk2, y: corrInvTau,
          marker: { color: C_CORR_FILL, size: 9, symbol: 'diamond', line: { color: C_CORR_LINE, width: 1 } },
          hovertemplate: '%{text}<extra></extra>',
          text: corrText,
          name: '◇ τ1 opraveno',
        })
      }
    }

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
        name: `◇ fit R=${dp.R.toFixed(3)} η=${dp.eta.toExponential(2)}`,
        hoverinfo: 'skip',
      })
    }

    const layout: Partial<Plotly.Layout> = {
      ...LAYOUT_BASE,
      height: 160,
      title: { text: `Damping law (${VEL_LABELS[selectedVel]} ◄)`, font: { size: 10 } },
      xaxis: { ...LAYOUT_BASE.xaxis, title: { text: 'f_k² [Hz²]', font: { size: 9 } } },
      yaxis: { ...LAYOUT_BASE.yaxis, title: { text: '1/τ₁', font: { size: 9 } } },
    }

    if ((dampingRef.current as any)._plotly) {
      Plotly.react(dampingRef.current, traces, layout, { responsive: true, displayModeBar: false })
    } else {
      Plotly.newPlot(dampingRef.current, traces, layout, { responsive: true, displayModeBar: false })
      ;(dampingRef.current as any)._plotly = true
    }
  }, [notes, details, selectedMidi, corrMap])

  // ---------------------------------------------------------------------------
  // Plot 4: Beating map (heatmapa k × vel)
  // ---------------------------------------------------------------------------
  useEffect(() => {
    if (!beatingRef.current || notes.length === 0) return

    const kList = notes[0]?.partials.map(p => p.k) ?? []
    const z: number[][] = notes.map(n =>
      kList.map(k => n.partials.find(p => p.k === k)?.beat_hz ?? 0)
    )

    const yLabels = notes.map(n => VEL_LABELS[n.vel])
    const traces: Plotly.Data[] = [{
      type: 'heatmap',
      z,
      x: kList,
      y: yLabels,
      colorscale: [[0, '#141618'], [0.01, '#1A2A3A'], [1, '#378ADD']],
      hovertemplate: 'k=%{x} vel=%{y}: beat=%{z:.3f} Hz<extra></extra>',
      showscale: false,
    }]

    // Zvýrazni řádek aktivní velocity
    const activeLabel = VEL_LABELS[selectedVel]
    if (yLabels.includes(activeLabel)) {
      traces.push({
        type: 'scatter', mode: 'lines',
        x: [kList[0], kList[kList.length - 1]],
        y: [activeLabel, activeLabel],
        line: { color: '#BA7517', width: 2 },
        hoverinfo: 'skip', showlegend: false,
      } as Plotly.Data)
    }

    const layout: Partial<Plotly.Layout> = {
      ...LAYOUT_BASE,
      height: 110,
      title: { text: `Beating map (beat_hz) — ${VEL_LABELS[selectedVel]} ◄`, font: { size: 10 } },
      xaxis: { ...LAYOUT_BASE.xaxis, title: { text: 'k', font: { size: 9 } } },
      yaxis: { ...LAYOUT_BASE.yaxis, title: { text: '', font: { size: 9 } } },
    }

    if ((beatingRef.current as any)._plotly) {
      Plotly.react(beatingRef.current, traces, layout, { responsive: true, displayModeBar: false })
    } else {
      Plotly.newPlot(beatingRef.current, traces, layout, { responsive: true, displayModeBar: false })
      ;(beatingRef.current as any)._plotly = true
    }
  }, [notes, selectedVel])

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
