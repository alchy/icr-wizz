// views/ParamSpaceView.tsx — vizualizace parametrového prostoru před/po korekci
// Changelog: 2025-04-14 v0.1 — initial

import React, { useEffect, useRef, useState } from 'react'
import Plotly from 'plotly.js-dist-min'
import { useBankStore }       from '../store/bankStore'
import { useCorrectionStore } from '../store/correctionStore'
import { useUiStore }         from '../store/uiStore'
import { midiToNoteName }     from '../types'
import type { Correction }    from '../types'

const LAYOUT_BASE: Partial<Plotly.Layout> = {
  paper_bgcolor: 'transparent',
  plot_bgcolor:  '#2A2D30',
  font:          { family: 'IBM Plex Mono', size: 10, color: '#9B9892' },
  margin:        { l: 120, r: 20, t: 40, b: 50 },
  showlegend:    false,
  xaxis: { gridcolor: '#3E4044', zerolinecolor: '#3E4044' },
  yaxis: { gridcolor: '#3E4044', zerolinecolor: '#3E4044' },
}

// Parametrové skupiny pro přehlednost
const PARAM_GROUPS = [
  { label: 'Skalární',  params: ['B', 'rms_gain', 'attack_tau', 'A_noise', 'noise_centroid_hz'] },
  { label: 'Parciál k1', params: ['A0_k1', 'tau1_k1', 'tau2_k1', 'a1_k1', 'beat_hz_k1'] },
  { label: 'Parciál k2', params: ['A0_k2', 'tau1_k2', 'tau2_k2', 'a1_k2', 'beat_hz_k2'] },
  { label: 'Parciál k3', params: ['A0_k3', 'tau1_k3', 'tau2_k3', 'a1_k3', 'beat_hz_k3'] },
  { label: 'Parciál k5', params: ['A0_k5', 'tau1_k5', 'tau2_k5', 'a1_k5', 'beat_hz_k5'] },
  { label: 'Parciál k10', params: ['A0_k10', 'tau1_k10', 'tau2_k10'] },
]

type ViewMode = 'heatmap' | 'scatter_B' | 'scatter_tau' | 'scatter_A0'

export const ParamSpaceView: React.FC = () => {
  const heatmapRef = useRef<HTMLDivElement>(null)
  const scatterRef = useRef<HTMLDivElement>(null)

  const bankState = useBankStore(s => s.activeState())
  const pending   = useCorrectionStore(s => s.pending)
  const selectNote = useUiStore(s => s.selectNote)
  const selectedVel = useUiStore(s => s.selectedVel)

  const [viewMode, setViewMode] = useState<ViewMode>('heatmap')
  const [selectedGroup, setSelectedGroup] = useState(0)

  // Index korekcí: "midi_vel_field" → Correction
  const corrIndex = React.useMemo(() => {
    const map = new Map<string, Correction>()
    if (!pending) return map
    for (const c of pending.corrections) {
      map.set(`${c.midi}_${c.vel}_${c.field}`, c)
    }
    return map
  }, [pending])

  // Per-MIDI agregované delta
  const midiDeltas = React.useMemo(() => {
    const map = new Map<number, { count: number; meanDelta: number; maxDelta: number }>()
    if (!pending) return map
    for (const c of pending.corrections) {
      const prev = map.get(c.midi) || { count: 0, meanDelta: 0, maxDelta: 0 }
      prev.count++
      prev.meanDelta += Math.abs(c.delta_pct)
      prev.maxDelta = Math.max(prev.maxDelta, Math.abs(c.delta_pct))
      map.set(c.midi, prev)
    }
    for (const [midi, v] of map) {
      v.meanDelta /= v.count
    }
    return map
  }, [pending])

  // -----------------------------------------------------------------------
  // Heatmap: MIDI × parametr, barva = delta %
  // -----------------------------------------------------------------------
  useEffect(() => {
    if (!heatmapRef.current || !bankState || viewMode !== 'heatmap') return

    const midiFrom = bankState.midi_range_from
    const midiTo   = bankState.midi_range_to
    const midis    = Array.from({ length: midiTo - midiFrom + 1 }, (_, i) => i + midiFrom)
    const params   = PARAM_GROUPS[selectedGroup].params

    // Heatmap data: rows = params, cols = MIDI
    const z: number[][] = []
    const hoverText: string[][] = []

    for (const param of params) {
      const row: number[] = []
      const textRow: string[] = []
      for (const midi of midis) {
        const key = `${midi}_${selectedVel}_${param}`
        const c = corrIndex.get(key)
        if (c) {
          row.push(c.delta_pct)
          textRow.push(`${midiToNoteName(midi)} MIDI ${midi}\n${param}: ${c.original.toPrecision(4)} → ${c.corrected.toPrecision(4)}\nΔ ${c.delta_pct.toFixed(1)}%`)
        } else {
          row.push(0)
          textRow.push(`${midiToNoteName(midi)} MIDI ${midi}\n${param}: beze změny`)
        }
      }
      z.push(row)
      hoverText.push(textRow)
    }

    const traces: Plotly.Data[] = [{
      type: 'heatmap',
      z,
      x: midis.map(m => `${midiToNoteName(m)}`),
      y: params,
      hovertext: hoverText as any,
      hovertemplate: '%{hovertext}<extra></extra>',
      colorscale: [
        [0, '#0C447C'],     // záporná delta (snížení)
        [0.35, '#1D9E75'],  // mírně záporná
        [0.5, '#2A2D30'],   // beze změny
        [0.65, '#BA7517'],  // mírně kladná
        [1, '#E24B4A'],     // velká kladná delta
      ],
      zmin: -100,
      zmax: 100,
      showscale: true,
      colorbar: { title: { text: 'Δ %', font: { size: 10 } }, len: 0.5 },
    }]

    const layout: Partial<Plotly.Layout> = {
      ...LAYOUT_BASE,
      height: Math.max(200, params.length * 30 + 100),
      title: { text: `Parametrový prostor — ${PARAM_GROUPS[selectedGroup].label} (vel ${selectedVel})`,
               font: { size: 11 } },
      xaxis: { ...LAYOUT_BASE.xaxis, title: { text: 'MIDI nota', font: { size: 9 } },
               tickangle: -45, dtick: 4 },
      yaxis: { ...LAYOUT_BASE.yaxis, automargin: true },
    }

    if ((heatmapRef.current as any)._plotly) {
      Plotly.react(heatmapRef.current, traces, layout, { responsive: true })
    } else {
      Plotly.newPlot(heatmapRef.current, traces, layout, { responsive: true })
      ;(heatmapRef.current as any)._plotly = true
    }
  }, [bankState, corrIndex, selectedVel, viewMode, selectedGroup])

  // -----------------------------------------------------------------------
  // Scatter: před vs po pro vybraný parametr
  // -----------------------------------------------------------------------
  useEffect(() => {
    if (!scatterRef.current || !pending || viewMode === 'heatmap') return

    const paramFilter = viewMode === 'scatter_B' ? 'B'
      : viewMode === 'scatter_tau' ? 'tau1_k1'
      : 'A0_k1'

    const corrs = pending.corrections.filter(c =>
      c.field === paramFilter && c.vel === selectedVel
    ).sort((a, b) => a.midi - b.midi)

    if (corrs.length === 0) {
      Plotly.purge(scatterRef.current)
      return
    }

    const traces: Plotly.Data[] = [
      // Originál
      {
        type: 'scatter', mode: 'lines+markers',
        x: corrs.map(c => c.midi),
        y: corrs.map(c => c.original),
        name: `⬤ ${paramFilter} originál`,
        line: { color: '#E24B4A', width: 1 },
        marker: { size: 4 },
        hovertemplate: 'MIDI %{x}: %{y:.4f}<extra>originál</extra>',
      },
      // Opraveno
      {
        type: 'scatter', mode: 'lines+markers',
        x: corrs.map(c => c.midi),
        y: corrs.map(c => c.corrected),
        name: `◇ ${paramFilter} opraveno`,
        line: { color: 'rgba(29, 158, 117, 0.7)', width: 2 },
        marker: { size: 5, symbol: 'diamond' },
        hovertemplate: 'MIDI %{x}: %{y:.4f}<extra>opraveno</extra>',
      },
    ]

    const layout: Partial<Plotly.Layout> = {
      ...LAYOUT_BASE,
      height: 300,
      showlegend: true,
      legend: { x: 0, y: 1.15, orientation: 'h', font: { size: 10 } },
      title: { text: `${paramFilter} — před vs. po (vel ${selectedVel})`,
               font: { size: 11 } },
      xaxis: { ...LAYOUT_BASE.xaxis, title: { text: 'MIDI', font: { size: 9 } } },
      yaxis: { ...LAYOUT_BASE.yaxis, title: { text: paramFilter, font: { size: 9 } },
               type: paramFilter === 'B' ? 'log' : undefined },
    }

    if ((scatterRef.current as any)._plotly) {
      Plotly.react(scatterRef.current, traces, layout, { responsive: true })
    } else {
      Plotly.newPlot(scatterRef.current, traces, layout, { responsive: true })
      ;(scatterRef.current as any)._plotly = true
    }

    ;(scatterRef.current as any).on('plotly_click', (data: Plotly.PlotMouseEvent) => {
      const midi = data.points[0]?.x as number
      if (midi) selectNote(midi)
    })
  }, [pending, selectedVel, viewMode, selectNote])

  // -----------------------------------------------------------------------
  // Overview bar: per-MIDI korekční intenzita
  // -----------------------------------------------------------------------
  const overviewRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!overviewRef.current || !bankState || !pending) return

    const midiFrom = bankState.midi_range_from
    const midiTo   = bankState.midi_range_to
    const midis    = Array.from({ length: midiTo - midiFrom + 1 }, (_, i) => i + midiFrom)

    const traces: Plotly.Data[] = [{
      type: 'bar',
      x: midis.map(m => midiToNoteName(m)),
      y: midis.map(m => midiDeltas.get(m)?.meanDelta ?? 0),
      marker: {
        color: midis.map(m => {
          const d = midiDeltas.get(m)?.meanDelta ?? 0
          if (d < 5) return '#1D9E75'
          if (d < 20) return '#BA7517'
          return '#E24B4A'
        }),
      },
      hovertemplate: '%{x}: mean Δ %{y:.1f}%<extra></extra>',
    }]

    const layout: Partial<Plotly.Layout> = {
      ...LAYOUT_BASE,
      height: 120,
      margin: { l: 40, r: 10, t: 10, b: 30 },
      xaxis: { ...LAYOUT_BASE.xaxis, tickangle: -45, dtick: 4, tickfont: { size: 8 } },
      yaxis: { ...LAYOUT_BASE.yaxis, title: { text: 'mean Δ%', font: { size: 8 } } },
    }

    if ((overviewRef.current as any)._plotly) {
      Plotly.react(overviewRef.current, traces, layout, { responsive: true })
    } else {
      Plotly.newPlot(overviewRef.current, traces, layout, { responsive: true })
      ;(overviewRef.current as any)._plotly = true
    }
  }, [bankState, midiDeltas, pending])

  // -----------------------------------------------------------------------
  // Render
  // -----------------------------------------------------------------------

  if (!pending || pending.corrections.length === 0) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center',
                    height: '100%', color: 'var(--t-muted)', fontSize: 13 }}>
        Navrhněte korekce pro vizualizaci parametrového prostoru
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-3)',
                  padding: 'var(--sp-3)', height: '100%', overflowY: 'auto' }}
         className="animate-in">

      {/* Controls */}
      <div style={{ display: 'flex', gap: 'var(--sp-3)', alignItems: 'center', flexWrap: 'wrap' }}>
        <span className="label">Pohled</span>
        {([
          ['heatmap', 'Heatmap Δ%'],
          ['scatter_B', 'B křivka'],
          ['scatter_tau', 'τ1 k1'],
          ['scatter_A0', 'A0 k1'],
        ] as [ViewMode, string][]).map(([id, label]) => (
          <button key={id} className={`btn ${viewMode === id ? 'btn--accent' : ''}`}
                  onClick={() => setViewMode(id)} style={{ fontSize: 11 }}>
            {label}
          </button>
        ))}

        {viewMode === 'heatmap' && (
          <>
            <span className="sep" />
            <span className="label">Skupina</span>
            {PARAM_GROUPS.map((g, i) => (
              <button key={i} className={`btn ${selectedGroup === i ? 'btn--accent' : ''}`}
                      onClick={() => setSelectedGroup(i)} style={{ fontSize: 11 }}>
                {g.label}
              </button>
            ))}
          </>
        )}

        <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--t-muted)' }}>
          {pending.corrections.length} korekcí · {midiDeltas.size} not
        </span>
      </div>

      {/* Overview: per-MIDI korekční intenzita */}
      <div ref={overviewRef} style={{ width: '100%' }} />

      {/* Main viz */}
      {viewMode === 'heatmap' ? (
        <div ref={heatmapRef} style={{ width: '100%' }} />
      ) : (
        <div ref={scatterRef} style={{ width: '100%' }} />
      )}
    </div>
  )
}
