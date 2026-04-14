// views/VelocityEditor.tsx — D3 γ_k spline editor
// Changelog: 2025-04-14 v0.1 — initial

import React, { useEffect, useRef, useState } from 'react'
import * as d3 from 'd3'
import { useBankStore } from '../store/bankStore'
import { useFitStore }  from '../store/fitStore'
import { useUiStore }   from '../store/uiStore'
import { previewSocket } from '../api/client'
import { midiToNoteName } from '../types'

const W = 520, H = 160
const M = { top: 16, right: 16, bottom: 36, left: 44 }
const IW = W - M.left - M.right
const IH = H - M.top - M.bottom

const VEL_LABELS = ['pp','p','mp','mf','mf+','f','ff-','ff']

export const VelocityEditor: React.FC = () => {
  const svgRef = useRef<SVGSVGElement>(null)

  const selectedMidi = useUiStore(s => s.selectedMidi)
  const selectedVel  = useUiStore(s => s.selectedVel)
  const details      = useFitStore(s => s.details)
  const bankState    = useBankStore(s => s.activeState())

  const [draggedGamma, setDraggedGamma] = useState<number[]>([])
  const [selectedK, setSelectedK]       = useState(1)
  const gammaRef = useRef<number[]>([])  // mutable ref pro drag (bez re-render)

  // Inicializace γ_k z fit details
  const originalGamma = selectedMidi !== null
    ? (details?.gamma_k?.[selectedMidi] ?? [])
    : []

  useEffect(() => {
    if (originalGamma.length > 0) {
      const copy = [...originalGamma]
      setDraggedGamma(copy)
      gammaRef.current = copy
    }
  }, [selectedMidi, details])

  // Sync ref při state změnách (reset tlačítko apod.)
  useEffect(() => {
    gammaRef.current = draggedGamma
  }, [draggedGamma])

  // ---------------------------------------------------------------------------
  // D3 γ_k spline editor — renderuje se jen při změně noty/k, ne při dragu
  // ---------------------------------------------------------------------------
  const renderKey = `${selectedMidi}_${selectedK}_${draggedGamma.length}`

  useEffect(() => {
    const svg = d3.select(svgRef.current!)
    svg.selectAll('*').remove()
    if (gammaRef.current.length === 0) return

    const gamma = gammaRef.current
    const kMax = gamma.length
    const xScale = d3.scaleLinear().domain([1, kMax]).range([0, IW])
    const yScale = d3.scaleLinear().domain([0, 4]).range([IH, 0])

    const g = svg.append('g').attr('transform', `translate(${M.left},${M.top})`)

    // Grid
    g.append('g').attr('class', 'grid')
      .attr('transform', `translate(0,${IH})`)
      .call(d3.axisBottom(xScale).ticks(10).tickSize(-IH))
      .selectAll('line').attr('stroke', '#2A2D35').attr('stroke-width', 0.5)
    g.append('g').attr('class', 'grid')
      .call(d3.axisLeft(yScale).ticks(5).tickSize(-IW))
      .selectAll('line').attr('stroke', '#2A2D35').attr('stroke-width', 0.5)

    // Referenční čára γ=1
    g.append('line')
      .attr('x1', 0).attr('x2', IW)
      .attr('y1', yScale(1)).attr('y2', yScale(1))
      .attr('stroke', '#2A2D35').attr('stroke-width', 1).attr('stroke-dasharray', '4,3')

    // Axes labels
    g.append('g').attr('transform', `translate(0,${IH})`)
      .call(d3.axisBottom(xScale).ticks(8))
      .selectAll('text').attr('fill', '#9B9892').attr('font-size', 9)
    g.append('g')
      .call(d3.axisLeft(yScale).ticks(5))
      .selectAll('text').attr('fill', '#9B9892').attr('font-size', 9)

    // Spline čára
    const line = d3.line<number>()
      .x((_, i) => xScale(i + 1))
      .y(d => yScale(d))
      .curve(d3.curveCatmullRom.alpha(0.5))

    const path = g.append('path')
      .datum(gamma)
      .attr('fill', 'none')
      .attr('stroke', '#BA7517')
      .attr('stroke-width', 2)
      .attr('d', line)

    // Highlight aktivní k
    g.append('line')
      .attr('x1', xScale(selectedK)).attr('x2', xScale(selectedK))
      .attr('y1', 0).attr('y2', IH)
      .attr('stroke', 'var(--c-anchor)').attr('stroke-width', 1)
      .attr('stroke-dasharray', '2,2').attr('opacity', 0.6)

    // Draggable uzlové body (každý 5. k pro přehlednost)
    const nodeKs = [1, ...Array.from({ length: Math.floor(kMax / 5) }, (_, i) => (i + 1) * 5), kMax]
      .filter(k => k <= kMax)

    nodeKs.forEach(k => {
      const ix = k - 1
      const node = g.append('circle')
        .attr('cx', xScale(k))
        .attr('cy', yScale(gamma[ix] ?? 1))
        .attr('r', 6)
        .attr('fill', '#BA7517')
        .attr('stroke', '#1a1a1a').attr('stroke-width', 1.5)
        .attr('cursor', 'ns-resize')

      node.call(
        d3.drag<SVGCircleElement, unknown>()
          .on('drag', function(event: d3.D3DragEvent<SVGCircleElement, unknown, unknown>) {
            // event.y je relativní k <g> díky D3 drag kontejneru
            const localY = event.y
            const val = Math.max(0.05, Math.min(4.0, yScale.invert(localY)))
            // Aktualizuj ref přímo (žádný re-render)
            gammaRef.current[ix] = val
            // Aktualizuj vizuál
            d3.select(this).attr('cy', yScale(val))
            path.datum([...gammaRef.current]).attr('d', line)
            // WS preview
            if (selectedMidi !== null) {
              previewSocket.send({
                action: 'drag_gamma_k',
                payload: { midi: selectedMidi, k, gamma: val },
              })
            }
          })
          .on('end', function() {
            // Po dokončení dragu sync state
            setDraggedGamma([...gammaRef.current])
          })
      )
    })

  }, [renderKey, selectedMidi])

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  if (selectedMidi === null) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center',
                    height: '100%', color: 'var(--t-muted)', fontSize: 13 }}>
        Vyberte notu a velocity vrstvu pro editaci γ_k
      </div>
    )
  }

  const attackTau = selectedMidi !== null && selectedVel !== null && details
    ? (() => {
        const alpha = details.attack_alpha[selectedMidi] ?? 0.3
        const tref  = details.attack_tref[selectedMidi] ?? 0.05
        const vNorm = (selectedVel + 1) / 8
        const vRef  = 5 / 8
        return Math.min(tref * Math.pow(vNorm / vRef, -alpha), 0.1)
      })()
    : null

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-4)',
                  padding: 'var(--sp-4)', height: '100%', overflowY: 'auto' }}
         className="animate-in">

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--sp-3)' }}>
        <span style={{ fontFamily: 'var(--font-ui)', fontWeight: 700, fontSize: 16 }}>
          {midiToNoteName(selectedMidi)}
        </span>
        <span className="mono" style={{ fontSize: 11, color: 'var(--t-muted)' }}>MIDI {selectedMidi}</span>
        {selectedVel !== null && (
          <span className="tag">{VEL_LABELS[selectedVel]} (vel {selectedVel})</span>
        )}
        <button className="btn" style={{ marginLeft: 'auto' }}
                onClick={() => setDraggedGamma([...originalGamma])}>
          Reset na fit
        </button>
      </div>

      {/* γ_k editor */}
      <div className="panel">
        <div className="panel__header">
          <span className="panel__title">γ_k — velocity exponent per harmonik</span>
          <span style={{ marginLeft: 'auto', fontSize: 10, color: 'var(--t-muted)' }}>
            táhněte uzlové body · γ &gt; 1 = citlivý na velocity · γ ≈ 0 = stabilní
          </span>
        </div>
        <div className="panel__body">
          <svg ref={svgRef} width={W} height={H}
               style={{ display: 'block', width: '100%', maxWidth: W }} />
          {/* Parciál selector */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--sp-2)', marginTop: 8 }}>
            <span className="label">Aktivní k</span>
            <input
              type="range" className="slider" style={{ flex: 1 }}
              min={1} max={draggedGamma.length || 60} value={selectedK}
              onChange={e => setSelectedK(Number(e.target.value))}
            />
            <span className="mono" style={{ minWidth: 24 }}>{selectedK}</span>
            <span className="mono" style={{ fontSize: 11, color: 'var(--c-anchor)' }}>
              γ = {(draggedGamma[selectedK - 1] ?? 1).toFixed(2)}
            </span>
          </div>
        </div>
      </div>

      {/* Attack panel */}
      <div className="panel">
        <div className="panel__header">
          <span className="panel__title">Attack — τ a šum</span>
        </div>
        <div className="panel__body" style={{ display: 'flex', gap: 'var(--sp-6)' }}>
          {selectedVel !== null && details && (
            <>
              <div>
                <div className="label" style={{ marginBottom: 4 }}>attack_tau</div>
                <div className="mono" style={{ fontSize: 18, fontWeight: 600,
                                               color: attackTau && attackTau >= 0.09 ? 'var(--c-warn)' : 'var(--t-primary)' }}>
                  {attackTau !== null ? `${(attackTau * 1000).toFixed(1)} ms` : '—'}
                </div>
                {attackTau !== null && attackTau >= 0.09 && (
                  <div style={{ fontSize: 10, color: 'var(--c-warn)', marginTop: 2 }}>
                    blíží se stropu 100 ms
                  </div>
                )}
              </div>
              <div>
                <div className="label" style={{ marginBottom: 4 }}>α (power-law)</div>
                <div className="mono" style={{ fontSize: 18, fontWeight: 600 }}>
                  {(details.attack_alpha[selectedMidi] ?? 0).toFixed(3)}
                </div>
              </div>
              <div>
                <div className="label" style={{ marginBottom: 4 }}>τ_ref (vel mf)</div>
                <div className="mono" style={{ fontSize: 18, fontWeight: 600 }}>
                  {((details.attack_tref[selectedMidi] ?? 0) * 1000).toFixed(1)} ms
                </div>
              </div>
            </>
          )}
        </div>
      </div>

      {/* Velocity cross-section — γ_k preview */}
      {draggedGamma.length > 0 && (
        <div className="panel">
          <div className="panel__header">
            <span className="panel__title">A0 vs velocity — k={selectedK}</span>
          </div>
          <div className="panel__body">
            <div style={{ display: 'flex', gap: 'var(--sp-2)', alignItems: 'flex-end', height: 60 }}>
              {Array.from({ length: 8 }, (_, vel) => {
                const gamma = draggedGamma[selectedK - 1] ?? 1
                const vNorm = (vel + 1) / 8
                const height = Math.pow(vNorm, gamma) * 100
                return (
                  <div key={vel} style={{ flex: 1, display: 'flex', flexDirection: 'column',
                                          alignItems: 'center', gap: 2 }}>
                    <div style={{
                      width: '100%',
                      height: `${height}%`,
                      background: vel === selectedVel ? 'var(--c-anchor)' : 'var(--c-mid)',
                      borderRadius: 2, opacity: 0.7 + vel * 0.04,
                      transition: 'height 100ms',
                    }} />
                    <span style={{ fontSize: 9, color: 'var(--t-muted)' }}>{VEL_LABELS[vel]}</span>
                  </div>
                )
              })}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
