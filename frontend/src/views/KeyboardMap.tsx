// views/KeyboardMap.tsx — D3 SVG klaviatura s outlier vizualizací
// Changelog: 2025-04-14 v0.1 — initial

import React, { useEffect, useRef, useCallback } from 'react'
import * as d3 from 'd3'
import { useBankStore }       from '../store/bankStore'
import { useFitStore }        from '../store/fitStore'
import { useAnchorStore }     from '../store/anchorStore'
import { useCorrectionStore } from '../store/correctionStore'
import { useUiStore }         from '../store/uiStore'
import { midiApi }            from '../api/client'
import { midiToNoteName, outlierColor, noteKeyToMidiVel } from '../types'

// ---------------------------------------------------------------------------
// Piano layout helpers
// ---------------------------------------------------------------------------

const BLACK_KEYS = new Set([1, 3, 6, 8, 10])  // semitóny v oktávě

function isBlack(midi: number): boolean {
  return BLACK_KEYS.has((midi - 21) % 12)
}

// Počet bílých kláves od A0 do dané MIDI noty
function whiteIndex(midi: number): number {
  let count = 0
  for (let m = 21; m < midi; m++) {
    if (!isBlack(m)) count++
  }
  return count
}

const TOTAL_WHITE = Array.from({ length: 88 }, (_, i) => i + 21)
  .filter(m => !isBlack(m)).length   // = 52

// ---------------------------------------------------------------------------
// Konstanty rozměrů
// ---------------------------------------------------------------------------

const W_WHITE  = 12         // šířka bílé klávesy
const H_WHITE  = 68         // výška bílé klávesy
const W_BLACK  = 8          // šířka černé
const H_BLACK  = 44         // výška černé
const SCORE_H  = 40         // max výška outlier sloupce nad klávesou
const TOTAL_W  = TOTAL_WHITE * W_WHITE
const TOTAL_H  = SCORE_H + H_WHITE

// ---------------------------------------------------------------------------
// Komponenta
// ---------------------------------------------------------------------------

export const KeyboardMap: React.FC = () => {
  const svgRef        = useRef<SVGSVGElement>(null)
  const tooltipRef    = useRef<HTMLDivElement>(null)

  const bankState     = useBankStore(s => s.activeState())
  const noteKeys      = bankState?.note_keys ?? []

  const outlierScore  = useFitStore(s => s.outlierScore)

  const isAnchor      = useAnchorStore(s => s.isAnchor)
  const entryScore    = useAnchorStore(s => s.entryScore)

  const pending       = useCorrectionStore(s => s.pending)

  const selectedMidi  = useUiStore(s => s.selectedMidi)
  const selectedVel   = useUiStore(s => s.selectedVel)
  const selectNote    = useUiStore(s => s.selectNote)
  const openDialog    = useUiStore(s => s.openAnchorDialog)

  // Sestavení sady MIDI not přítomných v bance
  const presentMidis = React.useMemo(() => {
    const set = new Set<number>()
    noteKeys.forEach(k => { const [m] = noteKeyToMidiVel(k); set.add(m) })
    return set
  }, [noteKeys])

  // MIDI noty s pending korekcemi
  const correctedMidis = React.useMemo(() => {
    const set = new Set<number>()
    if (pending) pending.corrections.forEach(c => set.add(c.midi))
    return set
  }, [pending])

  // ---------------------------------------------------------------------------
  // D3 render
  // ---------------------------------------------------------------------------

  const render = useCallback(() => {
    const svg = d3.select(svgRef.current!)
    svg.selectAll('*').remove()

    const g = svg.append('g').attr('transform', `translate(0, ${SCORE_H})`)

    // --- Bílé klávesy ---
    for (let midi = 21; midi <= 108; midi++) {
      if (isBlack(midi)) continue
      const wx = whiteIndex(midi) * W_WHITE
      const score  = outlierScore(`m${String(midi).padStart(3,'0')}`)
      const anchor = isAnchor(midi)
      const sel    = selectedMidi === midi
      const present= presentMidis.has(midi)

      // Klávesa
      g.append('rect')
        .attr('x', wx).attr('y', 0)
        .attr('width', W_WHITE - 1).attr('height', H_WHITE)
        .attr('rx', 2)
        .attr('fill', sel ? 'var(--c-sel-solid)' : present ? '#D8D4CC' : '#3A3A3A')
        .attr('stroke', anchor ? 'var(--c-anchor)' : '#1a1a1a')
        .attr('stroke-width', anchor ? 2 : 0.5)
        .attr('class', 'key key--white')
        .attr('data-midi', midi)

      // Značka korekce (zelený trojúhelník dole)
      if (correctedMidis.has(midi)) {
        const cx = wx + (W_WHITE - 1) / 2
        const cy = H_WHITE - 2
        g.append('polygon')
          .attr('points', `${cx-3},${cy} ${cx+3},${cy} ${cx},${cy-5}`)
          .attr('fill', '#1D9E75')
          .attr('pointer-events', 'none')
      }

      // Outlier sloupec nad klávesou
      if (present && score > 0) {
        const barH = score * SCORE_H
        g.append('rect')
          .attr('x', wx + 1)
          .attr('y', -(barH))
          .attr('width', W_WHITE - 3).attr('height', barH)
          .attr('rx', 1)
          .attr('fill', outlierColor(score))
          .attr('opacity', 0.85)
          .attr('pointer-events', 'none')
      }

      // Šrafování pro chybějící noty
      if (!present) {
        const pattern = g.append('pattern')
          .attr('id', `hatch-${midi}`)
          .attr('patternUnits', 'userSpaceOnUse')
          .attr('width', 4).attr('height', 4)
        pattern.append('path')
          .attr('d', 'M-1,1 l2,-2 M0,4 l4,-4 M3,5 l2,-2')
          .attr('stroke', '#555').attr('stroke-width', 0.8)
        g.append('rect')
          .attr('x', wx).attr('y', 0)
          .attr('width', W_WHITE - 1).attr('height', H_WHITE)
          .attr('fill', `url(#hatch-${midi})`)
          .attr('pointer-events', 'none')
      }

    }

    // --- Černé klávesy (nahoře, přes bílé) ---
    for (let midi = 21; midi <= 108; midi++) {
      if (!isBlack(midi)) continue
      // Pozice: nalevo od bezprostředně následující bílé
      let nextWhite = midi + 1
      while (isBlack(nextWhite)) nextWhite++
      const wx = whiteIndex(nextWhite) * W_WHITE - W_BLACK / 2 - 1
      const score  = outlierScore(`m${String(midi).padStart(3,'0')}`)
      const anchor = isAnchor(midi)
      const sel    = selectedMidi === midi

      g.append('rect')
        .attr('x', wx).attr('y', 0)
        .attr('width', W_BLACK).attr('height', H_BLACK)
        .attr('rx', 1)
        .attr('fill', sel ? '#3A68AA' : '#181A1E')
        .attr('stroke', anchor ? 'var(--c-anchor)' : '#000')
        .attr('stroke-width', anchor ? 1.5 : 0.5)
        .attr('class', 'key key--black')
        .attr('data-midi', midi)

      if (score > 0) {
        const barH = score * SCORE_H
        g.append('rect')
          .attr('x', wx + 1)
          .attr('y', -(barH))
          .attr('width', W_BLACK - 2).attr('height', barH)
          .attr('rx', 1)
          .attr('fill', outlierColor(score))
          .attr('opacity', 0.9)
          .attr('pointer-events', 'none')
      }
    }

    // --- Interakce ---
    svg.selectAll<SVGRectElement, unknown>('.key')
      .on('click', function(event: MouseEvent) {
        const midi = Number(this.getAttribute('data-midi'))
        if (event.shiftKey) {
          openDialog(midi)
        } else {
          // MIDI note first — minimální latence
          const midiVel = Math.round(1 + (selectedVel / 7) * 126)
          midiApi.play(midi, midiVel).catch(() => {})
          selectNote(midi)
        }
      })
      .on('mouseover', function(event: MouseEvent) {
        const midi = Number(this.getAttribute('data-midi'))
        showTooltip(event, midi)
      })
      .on('mousemove', function(event: MouseEvent) {
        moveTooltip(event)
      })
      .on('mouseout', () => hideTooltip())

  }, [outlierScore, isAnchor, selectedMidi, selectedVel, presentMidis,
      correctedMidis, noteKeys, selectNote, openDialog])

  useEffect(() => { render() }, [render])

  // ---------------------------------------------------------------------------
  // Tooltip
  // ---------------------------------------------------------------------------

  function showTooltip(event: MouseEvent, midi: number) {
    const el = tooltipRef.current
    if (!el) return
    const score = outlierScore(`m${String(midi).padStart(3, '0')}`)
    const anchor = isAnchor(midi)
    const anchorScore = anchor ? entryScore(midi, -1) : -1
    el.innerHTML =
      `<span style="color:var(--t-muted)">MIDI</span> <b>${midi}</b>  ` +
      `<span style="color:var(--t-muted)">${midiToNoteName(midi)}</span>` +
      (score > 0 ? `\n<span style="color:${outlierColor(score)}">outlier ${score.toFixed(2)}</span>` : '') +
      (anchor ? `\n<span style="color:var(--c-anchor)">⚓ anchor  score ${anchorScore < 0 ? '—' : anchorScore.toFixed(0)}</span>` : '')
    el.style.display = 'block'
    moveTooltip(event)
  }

  function moveTooltip(event: MouseEvent) {
    const el = tooltipRef.current
    if (!el) return
    el.style.left = `${event.clientX + 12}px`
    el.style.top  = `${event.clientY - 8}px`
  }

  function hideTooltip() {
    const el = tooltipRef.current
    if (el) el.style.display = 'none'
  }

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div style={{ position: 'relative', width: '100%', overflowX: 'auto' }}>
      <svg
        ref={svgRef}
        width={TOTAL_W}
        height={TOTAL_H}
        style={{ display: 'block', cursor: 'pointer' }}
        viewBox={`0 0 ${TOTAL_W} ${TOTAL_H}`}
      />
      <div
        ref={tooltipRef}
        className="tooltip"
        style={{ display: 'none' }}
      />
    </div>
  )
}
