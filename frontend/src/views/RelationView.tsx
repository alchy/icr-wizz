// views/RelationView.tsx — 4 analytické grafy (Plotly) s overlay korekcí
// Changelog: 2025-04-14 v0.1 — initial
//            2025-04-14 v0.2 — correction overlay (B-curve, outlier, tau, gamma)

import React, { useEffect, useRef } from 'react'
import Plotly from 'plotly.js-dist-min'
import { useBankStore }       from '../store/bankStore'
import { useFitStore }        from '../store/fitStore'
import { useCorrectionStore } from '../store/correctionStore'
import { useUiStore }         from '../store/uiStore'
import { midiToF0, midiToNoteName, outlierColor } from '../types'
import type { Correction } from '../types'

const PLOTLY_LAYOUT_BASE = {
  paper_bgcolor: 'transparent',
  plot_bgcolor:  '#141618',
  font:          { family: 'IBM Plex Mono', size: 10, color: '#9B9892' },
  margin:        { l: 46, r: 12, t: 28, b: 36 },
  showlegend:    false,
  xaxis: { gridcolor: '#2A2D35', zerolinecolor: '#2A2D35' },
  yaxis: { gridcolor: '#2A2D35', zerolinecolor: '#2A2D35' },
}

// Correction overlay barvy — konzistentní přes celé UI
const C_CORR_FILL   = 'rgba(29, 158, 117, 0.35)'  // světle zelená translucent
const C_CORR_LINE   = '#1D9E75'                     // zelená plná
const C_ORIGINAL    = '#E24B4A'                     // červená pro originální outliery

export const RelationView: React.FC = () => {
  const divRef    = useRef<HTMLDivElement>(null)
  const bankState = useBankStore(s => s.activeState())
  const summary   = useFitStore(s => s.summary)
  const details   = useFitStore(s => s.details)
  const pending   = useCorrectionStore(s => s.pending)
  const selectNote = useUiStore(s => s.selectNote)

  useEffect(() => {
    if (!divRef.current || !bankState) return

    const midiFrom = bankState.midi_range_from
    const midiTo   = bankState.midi_range_to
    const midis    = Array.from({ length: midiTo - midiFrom + 1 }, (_, i) => i + midiFrom)

    // Index korekcí per (midi, field) — bereme max delta přes velocity
    const corrByMidi: Map<number, Map<string, Correction>> = new Map()
    if (pending) {
      for (const c of pending.corrections) {
        if (!corrByMidi.has(c.midi)) corrByMidi.set(c.midi, new Map())
        const fields = corrByMidi.get(c.midi)!
        const existing = fields.get(c.field)
        if (!existing || Math.abs(c.delta_pct) > Math.abs(existing.delta_pct)) {
          fields.set(c.field, c)
        }
      }
    }
    const hasCorrFor = (midi: number, field: string) => corrByMidi.get(midi)?.get(field)
    const correctedMidis = new Set(corrByMidi.keys())

    // -----------------------------------------------------------------------
    // Plot 1: B-curve (log B vs log f0)
    // -----------------------------------------------------------------------

    // Originální B body
    const bcOrigX: number[] = [], bcOrigY: number[] = [], bcOrigText: string[] = []
    const bcOrigColor: string[] = [], bcOrigSymbol: string[] = []
    // Opravené B body (overlay)
    const bcCorrX: number[] = [], bcCorrY: number[] = [], bcCorrText: string[] = []

    for (const midi of midis) {
      const f0  = midiToF0(midi)
      const lf0 = Math.log10(f0)
      const pfx = `m${String(midi).padStart(3,'0')}`
      const score = summary?.outlier_scores[pfx] ?? 0

      const bCorr = hasCorrFor(midi, 'B')
      if (bCorr) {
        // Originál jako červený bod
        const origB = bCorr.original
        if (origB > 0) {
          bcOrigX.push(lf0); bcOrigY.push(Math.log10(origB))
          bcOrigText.push(`${midiToNoteName(midi)} MIDI ${midi}\nB orig: ${origB.toExponential(3)}\nΔ: ${bCorr.delta_pct.toFixed(1)}%`)
          bcOrigColor.push(C_ORIGINAL)
          bcOrigSymbol.push('x')
        }
        // Opravený jako zelený bod
        const corrB = bCorr.corrected
        if (corrB > 0) {
          bcCorrX.push(lf0); bcCorrY.push(Math.log10(corrB))
          bcCorrText.push(`${midiToNoteName(midi)} MIDI ${midi}\nB corr: ${corrB.toExponential(3)}\nΔ: ${bCorr.delta_pct.toFixed(1)}%`)
        }
      } else {
        // Normální bod
        bcOrigX.push(lf0); bcOrigY.push(0)  // B není přímo v summary
        bcOrigText.push(`${midiToNoteName(midi)} MIDI ${midi}\noutlier: ${score.toFixed(2)}`)
        bcOrigColor.push(outlierColor(score))
        bcOrigSymbol.push('circle')
      }
    }

    const bcOrigTrace: Plotly.Data = {
      type: 'scatter', mode: 'markers',
      x: bcOrigX, y: bcOrigY, text: bcOrigText,
      marker: { color: bcOrigColor, size: 7, symbol: bcOrigSymbol },
      hovertemplate: '%{text}<extra></extra>',
      name: 'B originál',
    }

    const bcCorrTrace: Plotly.Data = {
      type: 'scatter', mode: 'markers',
      x: bcCorrX, y: bcCorrY, text: bcCorrText,
      marker: { color: C_CORR_FILL, size: 9, symbol: 'diamond', line: { color: C_CORR_LINE, width: 1 } },
      hovertemplate: '%{text}<extra></extra>',
      name: 'B opraveno',
    }

    // B-curve fit line
    const bCurveTraces: Plotly.Data[] = []
    if (summary?.b_curve) {
      const bc = summary.b_curve
      const xLine: number[] = [], yLine: number[] = []
      for (let midi = midiFrom; midi <= midiTo; midi++) {
        const lf0 = Math.log10(midiToF0(midi))
        const lbk = Math.log10(midiToF0(bc.break_midi))
        const lB  = lf0 < lbk
          ? bc.alpha_bass * lf0 + bc.beta_bass
          : bc.alpha_treble * lf0 + bc.beta_treble
        xLine.push(lf0); yLine.push(lB)
      }
      bCurveTraces.push({
        type: 'scatter', mode: 'lines',
        x: xLine, y: yLine,
        line: { color: '#534AB7', width: 1.5 },
        hoverinfo: 'skip', name: 'B-curve fit',
      })
    }

    // -----------------------------------------------------------------------
    // Plot 2: Outlier skóre + korekce overlay
    // -----------------------------------------------------------------------

    const resX: string[] = [], resY: number[] = [], resColors: string[] = []
    // Overlay: noty s korekcí dostanou druhou řadu
    const corrResX: string[] = [], corrResY: number[] = []

    for (const midi of midis) {
      const pfx   = `m${String(midi).padStart(3,'0')}`
      const score = summary?.outlier_scores[pfx] ?? 0
      const name  = midiToNoteName(midi)
      resX.push(name); resY.push(score)
      resColors.push(correctedMidis.has(midi) ? C_ORIGINAL : outlierColor(score))

      if (correctedMidis.has(midi)) {
        corrResX.push(name)
        // Po korekci odhadujeme nižší score — vizuální indikátor
        corrResY.push(Math.max(0, score * 0.3))
      }
    }

    const resTrace: Plotly.Data = {
      type: 'bar', x: resX, y: resY,
      marker: { color: resColors },
      hovertemplate: '%{x}: %{y:.3f}<extra></extra>',
      name: 'outlier skóre (originál)',
    }

    const corrResTrace: Plotly.Data = {
      type: 'bar', x: corrResX, y: corrResY,
      marker: { color: C_CORR_LINE, opacity: 0.7 },
      hovertemplate: '%{x}: ~%{y:.3f} (po korekci)<extra></extra>',
      name: 'odhad po korekci',
    }

    // -----------------------------------------------------------------------
    // Plot 3: τ profil + korekce overlay
    // -----------------------------------------------------------------------

    const tauTraces: Plotly.Data[] = []
    if (details?.damping) {
      const tau1X: number[] = [], tau1Y: number[] = []
      const tau2X: number[] = [], tau2Y: number[] = []

      for (const midi of midis) {
        const dp = details.damping[midi]
        if (!dp) continue
        const f0 = midiToF0(midi)
        const tau1_pred = 1 / Math.max(dp.R + dp.eta * f0 * f0, 1e-9)
        const tau2_pred = tau1_pred * 10
        tau1X.push(midi); tau1Y.push(tau1_pred)
        tau2X.push(midi); tau2Y.push(tau2_pred)
      }

      tauTraces.push(
        { type: 'scatter', mode: 'lines+markers',
          x: tau1X, y: tau1Y,
          line: { color: '#1D9E75', width: 1.5 }, marker: { size: 4 },
          name: 'τ1', hovertemplate: 'MIDI %{x}: τ1=%{y:.2f}s<extra></extra>' },
        { type: 'scatter', mode: 'lines+markers',
          x: tau2X, y: tau2Y,
          line: { color: '#534AB7', width: 1.5, dash: 'dot' }, marker: { size: 4 },
          name: 'τ2', hovertemplate: 'MIDI %{x}: τ2=%{y:.2f}s<extra></extra>' },
      )
    }

    // τ korekce overlay
    const tauCorrTraces: Plotly.Data[] = []
    if (pending) {
      const tauCorrX: number[] = [], tauOrigY: number[] = [], tauCorrY: number[] = []
      for (const c of pending.corrections) {
        if (!c.field.startsWith('tau1_')) continue
        tauCorrX.push(c.midi)
        tauOrigY.push(c.original)
        tauCorrY.push(c.corrected)
      }
      if (tauCorrX.length > 0) {
        tauCorrTraces.push(
          { type: 'scatter', mode: 'markers',
            x: tauCorrX, y: tauOrigY,
            marker: { color: C_ORIGINAL, size: 8, symbol: 'x' },
            name: 'τ1 orig (outlier)', hovertemplate: 'MIDI %{x}: τ1 orig=%{y:.3f}s<extra></extra>' },
          { type: 'scatter', mode: 'markers',
            x: tauCorrX, y: tauCorrY,
            marker: { color: C_CORR_FILL, size: 9, symbol: 'diamond', line: { color: C_CORR_LINE, width: 1 } },
            name: 'τ1 opraveno', hovertemplate: 'MIDI %{x}: τ1 corr=%{y:.3f}s<extra></extra>' },
        )
      }
    }

    // -----------------------------------------------------------------------
    // Plot 4: γ_k mean
    // -----------------------------------------------------------------------

    const gammaTraces: Plotly.Data[] = []
    if (details?.gamma_k) {
      const gX: number[] = [], gY: number[] = []
      for (const midi of midis) {
        const gk = details.gamma_k[midi]
        if (!gk || gk.length === 0) continue
        const mean = gk.reduce((a, b) => a + b, 0) / gk.length
        gX.push(midi); gY.push(mean)
      }
      gammaTraces.push({
        type: 'scatter', mode: 'lines+markers',
        x: gX, y: gY,
        line: { color: '#BA7517', width: 1.5 }, marker: { size: 4 },
        name: 'γ_k průměr',
        hovertemplate: 'MIDI %{x}: γ_mean=%{y:.2f}<extra></extra>',
      })
    }

    // -----------------------------------------------------------------------
    // Plotly subplot grid 2×2
    // -----------------------------------------------------------------------

    const hasCorr = pending && pending.corrections.length > 0

    const traces: Plotly.Data[] = [
      { ...bcOrigTrace, xaxis: 'x1', yaxis: 'y1' },
      ...(bcCorrX.length > 0 ? [{ ...bcCorrTrace, xaxis: 'x1', yaxis: 'y1' }] : []),
      ...bCurveTraces.map(t => ({ ...t, xaxis: 'x1', yaxis: 'y1' })),
      { ...resTrace, xaxis: 'x2', yaxis: 'y2' },
      ...(corrResX.length > 0 ? [{ ...corrResTrace, xaxis: 'x2', yaxis: 'y2' }] : []),
      ...tauTraces.map(t => ({ ...t, xaxis: 'x3', yaxis: 'y3' })),
      ...tauCorrTraces.map(t => ({ ...t, xaxis: 'x3', yaxis: 'y3' })),
      ...gammaTraces.map(t => ({ ...t, xaxis: 'x4', yaxis: 'y4' })),
    ]

    const corrLabel = hasCorr ? ` — ${pending!.corrections.length} korekcí` : ''

    const layout: Partial<Plotly.Layout> = {
      ...PLOTLY_LAYOUT_BASE,
      grid: { rows: 2, columns: 2, pattern: 'independent' },
      height: 400,
      annotations: [
        { text: `B-curve (log B vs log f₀)${corrLabel}`, xref: 'x1 domain', yref: 'y1 domain', x: 0, y: 1.08, showarrow: false, font: { size: 10, color: hasCorr ? C_CORR_LINE : '#5C5A55' } },
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

    // Klik na bod → selectNote
    ;(divRef.current as any).on('plotly_click', (data: Plotly.PlotMouseEvent) => {
      if (data.points[0]?.curveNumber <= 1) {
        const idx  = data.points[0].pointIndex
        const midi = midis[idx]
        if (midi) selectNote(midi)
      }
    })

  }, [bankState, summary, details, pending, selectNote])

  return (
    <div
      ref={divRef}
      style={{ width: '100%', minHeight: 400 }}
    />
  )
}
