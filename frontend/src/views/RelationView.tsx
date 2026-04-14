// views/RelationView.tsx — 4 analytické grafy (Plotly)
// Changelog: 2025-04-14 v0.1 — initial

import React, { useEffect, useRef } from 'react'
import Plotly from 'plotly.js-dist-min'
import { useBankStore } from '../store/bankStore'
import { useFitStore }  from '../store/fitStore'
import { useUiStore }   from '../store/uiStore'
import { midiToF0, midiToNoteName, noteKeyToMidiVel, outlierColor } from '../types'

const PLOTLY_LAYOUT_BASE = {
  paper_bgcolor: 'transparent',
  plot_bgcolor:  '#141618',
  font:          { family: 'IBM Plex Mono', size: 10, color: '#9B9892' },
  margin:        { l: 46, r: 12, t: 28, b: 36 },
  showlegend:    false,
  xaxis: { gridcolor: '#2A2D35', zerolinecolor: '#2A2D35' },
  yaxis: { gridcolor: '#2A2D35', zerolinecolor: '#2A2D35' },
}

export const RelationView: React.FC = () => {
  const divRef   = useRef<HTMLDivElement>(null)
  const bankState = useBankStore(s => s.activeState())
  const summary   = useFitStore(s => s.summary)
  const details   = useFitStore(s => s.details)
  const selectNote = useUiStore(s => s.selectNote)

  useEffect(() => {
    if (!divRef.current || !bankState) return

    const midiFrom = bankState.midi_range_from
    const midiTo   = bankState.midi_range_to
    const midis    = Array.from({ length: midiTo - midiFrom + 1 }, (_, i) => i + midiFrom)

    // -----------------------------------------------------------------------
    // Plot 1: B-curve (log B vs log f0)
    // -----------------------------------------------------------------------

    const bcTrace: Plotly.Data = {
      type: 'scatter', mode: 'markers',
      x: [], y: [], text: [],
      marker: { color: [], size: 7, symbol: [] as string[] },
      hovertemplate: '%{text}<extra></extra>',
      name: 'B extrahováno',
    }

    // Body z note_keys
    for (const midi of midis) {
      const f0  = midiToF0(midi)
      const key = `m${String(midi).padStart(3,'0')}`
      const score = summary?.outlier_scores[key] ?? 0
      ;(bcTrace.x as number[]).push(Math.log10(f0))
      // B je pro RelationView ilustrativní — bez dat z banky zobrazíme 0
      ;(bcTrace.y as number[]).push(0)
      ;(bcTrace.text as string[]).push(`${midiToNoteName(midi)} MIDI ${midi}\noutlier: ${score.toFixed(2)}`)
      ;(bcTrace.marker as Plotly.PlotMarker).color = [
        ...(bcTrace.marker as Plotly.PlotMarker).color as string[],
        outlierColor(score),
      ]
    }

    // Overlay: fitted B-curve
    const bCurveTraces: Plotly.Data[] = []
    if (summary?.b_curve) {
      const bc = summary.b_curve
      const xLine: number[] = []
      const yLine: number[] = []
      for (let midi = midiFrom; midi <= midiTo; midi += 1) {
        const lf0 = Math.log10(midiToF0(midi))
        const lbk = Math.log10(midiToF0(bc.break_midi))
        const lB  = lf0 < lbk
          ? bc.alpha_bass * lf0 + bc.beta_bass
          : bc.alpha_treble * lf0 + bc.beta_treble
        xLine.push(lf0)
        yLine.push(lB)
      }
      bCurveTraces.push({
        type: 'scatter', mode: 'lines',
        x: xLine, y: yLine,
        line: { color: '#534AB7', width: 1.5 },
        hoverinfo: 'skip', name: 'B-curve fit',
      })
    }

    // -----------------------------------------------------------------------
    // Plot 2: Residuály (bar chart)
    // -----------------------------------------------------------------------

    const resX: string[] = []
    const resY: number[] = []
    const resColors: string[] = []

    for (const midi of midis) {
      const pfx   = `m${String(midi).padStart(3,'0')}`
      const score = summary?.outlier_scores[pfx] ?? 0
      resX.push(midiToNoteName(midi))
      resY.push(score)
      resColors.push(outlierColor(score))
    }

    const resTrace: Plotly.Data = {
      type: 'bar',
      x: resX, y: resY,
      marker: { color: resColors },
      hovertemplate: '%{x}: %{y:.3f}<extra></extra>',
      name: 'outlier skóre',
    }

    // -----------------------------------------------------------------------
    // Plot 3: τ profil (damping per MIDI)
    // -----------------------------------------------------------------------

    const tauTraces: Plotly.Data[] = []
    if (details?.damping) {
      const tau1X: number[] = []
      const tau1Y: number[] = []
      const tau2X: number[] = []
      const tau2Y: number[] = []

      for (const midi of midis) {
        const dp = details.damping[midi]
        if (!dp) continue
        const f0 = midiToF0(midi)
        const tau1_pred = 1 / Math.max(dp.R + dp.eta * f0 * f0, 1e-9)
        const tau2_pred = tau1_pred * 10  // cluster medián placeholder
        tau1X.push(midi); tau1Y.push(tau1_pred)
        tau2X.push(midi); tau2Y.push(tau2_pred)
      }

      tauTraces.push(
        { type: 'scatter', mode: 'lines+markers',
          x: tau1X, y: tau1Y,
          line: { color: '#1D9E75', width: 1.5 },
          marker: { size: 4 },
          name: 'τ1', hovertemplate: 'MIDI %{x}: τ1=%{y:.2f}s<extra></extra>' },
        { type: 'scatter', mode: 'lines+markers',
          x: tau2X, y: tau2Y,
          line: { color: '#534AB7', width: 1.5, dash: 'dot' },
          marker: { size: 4 },
          name: 'τ2', hovertemplate: 'MIDI %{x}: τ2=%{y:.2f}s<extra></extra>' },
      )
    }

    // -----------------------------------------------------------------------
    // Plot 4: γ_k mean (velocity sensitivity overview)
    // -----------------------------------------------------------------------

    const gammaTraces: Plotly.Data[] = []
    if (details?.gamma_k) {
      const gX: number[] = []
      const gY: number[] = []
      for (const midi of midis) {
        const gk = details.gamma_k[midi]
        if (!gk || gk.length === 0) continue
        const mean = gk.reduce((a, b) => a + b, 0) / gk.length
        gX.push(midi); gY.push(mean)
      }
      gammaTraces.push({
        type: 'scatter', mode: 'lines+markers',
        x: gX, y: gY,
        line: { color: '#BA7517', width: 1.5 },
        marker: { size: 4 },
        name: 'γ_k průměr',
        hovertemplate: 'MIDI %{x}: γ_mean=%{y:.2f}<extra></extra>',
      })
    }

    // -----------------------------------------------------------------------
    // Plotly subplot grid 2×2
    // -----------------------------------------------------------------------

    const traces: Plotly.Data[] = [
      { ...bcTrace, xaxis: 'x1', yaxis: 'y1' },
      ...bCurveTraces.map(t => ({ ...t, xaxis: 'x1', yaxis: 'y1' })),
      { ...resTrace, xaxis: 'x2', yaxis: 'y2' },
      ...tauTraces.map(t => ({ ...t, xaxis: 'x3', yaxis: 'y3' })),
      ...gammaTraces.map(t => ({ ...t, xaxis: 'x4', yaxis: 'y4' })),
    ]

    const layout: Partial<Plotly.Layout> = {
      ...PLOTLY_LAYOUT_BASE,
      grid: { rows: 2, columns: 2, pattern: 'independent' },
      height: 400,
      annotations: [
        { text: 'B-curve (log B vs log f₀)',    xref: 'x1 domain', yref: 'y1 domain', x: 0, y: 1.08, showarrow: false, font: { size: 10, color: '#5C5A55' } },
        { text: 'Outlier skóre',                 xref: 'x2 domain', yref: 'y2 domain', x: 0, y: 1.08, showarrow: false, font: { size: 10, color: '#5C5A55' } },
        { text: 'τ profil (damping law)',         xref: 'x3 domain', yref: 'y3 domain', x: 0, y: 1.08, showarrow: false, font: { size: 10, color: '#5C5A55' } },
        { text: 'γ_k mean (vel sensitivity)',     xref: 'x4 domain', yref: 'y4 domain', x: 0, y: 1.08, showarrow: false, font: { size: 10, color: '#5C5A55' } },
      ] as Plotly.Annotations[],
      xaxis:  { ...PLOTLY_LAYOUT_BASE.xaxis, title: { text: 'log f₀', font: { size: 9 } } },
      yaxis:  { ...PLOTLY_LAYOUT_BASE.yaxis, title: { text: 'log B',  font: { size: 9 } } },
      xaxis2: { ...PLOTLY_LAYOUT_BASE.xaxis, title: { text: 'nota',   font: { size: 9 } } },
      yaxis2: { ...PLOTLY_LAYOUT_BASE.yaxis, title: { text: 'skóre',  font: { size: 9 } }, range: [0, 1] },
      xaxis3: { ...PLOTLY_LAYOUT_BASE.xaxis, title: { text: 'MIDI',   font: { size: 9 } } },
      yaxis3: { ...PLOTLY_LAYOUT_BASE.yaxis, title: { text: 'τ [s]',  font: { size: 9 } }, type: 'log' },
      xaxis4: { ...PLOTLY_LAYOUT_BASE.xaxis, title: { text: 'MIDI',   font: { size: 9 } } },
      yaxis4: { ...PLOTLY_LAYOUT_BASE.yaxis, title: { text: 'γ_k',    font: { size: 9 } } },
    }

    const config: Partial<Plotly.Config> = {
      responsive:      true,
      displayModeBar:  true,
      modeBarButtonsToRemove: ['lasso2d', 'toImage'],
    }

    if ((divRef.current as any)._plotly) {
      Plotly.react(divRef.current, traces, layout, config)
    } else {
      Plotly.newPlot(divRef.current, traces, layout, config)
      ;(divRef.current as any)._plotly = true
    }

    // Klik na bod B-curve → selectNote
    ;(divRef.current as any).on('plotly_click', (data: Plotly.PlotMouseEvent) => {
      if (data.points[0]?.curveNumber === 0) {
        const idx  = data.points[0].pointIndex
        const midi = midis[idx]
        if (midi) selectNote(midi)
      }
    })

  }, [bankState, summary, details, selectNote])

  return (
    <div
      ref={divRef}
      style={{ width: '100%', minHeight: 400 }}
    />
  )
}
