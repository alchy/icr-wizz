// views/ParamSpace3DView.tsx — 3D vizualizace parametrového prostoru
// Každá nota = bod v 3D prostoru definovaném vybranými parametry
// Barva = anchor (zlatá) / outlier (červená) / normální (šedá)
// Před/po korekci jako dva sety bodů

import React, { useEffect, useRef, useState } from 'react'
import Plotly from 'plotly.js-dist-min'
import { useBankStore }       from '../store/bankStore'
import { useCorrectionStore } from '../store/correctionStore'
import { useAnchorStore }     from '../store/anchorStore'
import { useFitStore }        from '../store/fitStore'
import { useUiStore }         from '../store/uiStore'
import { midiToNoteName }     from '../types'

const AXIS_OPTIONS = [
  { key: 'B',                  label: 'B (inharmonicita)', log: true },
  { key: 'rms_gain',           label: 'rms_gain',          log: true },
  { key: 'attack_tau',         label: 'attack_tau',        log: false },
  { key: 'A_noise',            label: 'A_noise',           log: false },
  { key: 'noise_centroid_hz',  label: 'noise_centroid',    log: false },
  { key: 'A0_k1',              label: 'A0 k1',             log: true },
  { key: 'tau1_k1',            label: 'τ1 k1',             log: true },
  { key: 'tau2_k1',            label: 'τ2 k1',             log: true },
  { key: 'a1_k1',              label: 'a1 k1',             log: false },
  { key: 'beat_hz_k1',         label: 'beat k1',           log: false },
  { key: 'A0_k2',              label: 'A0 k2',             log: true },
  { key: 'tau1_k2',            label: 'τ1 k2',             log: true },
  { key: 'A0_k5',              label: 'A0 k5',             log: true },
  { key: 'tau1_k5',            label: 'τ1 k5',             log: true },
  { key: 'midi',               label: 'MIDI nota',         log: false },
]

function getNoteParam(note: any, key: string): number | null {
  if (key === 'midi') return note.midi
  // Skalární
  if (['B', 'rms_gain', 'attack_tau', 'A_noise', 'noise_centroid_hz'].includes(key)) {
    return note[key] ?? null
  }
  // Per parciál: "A0_k1" → partials[0].A0
  const m = key.match(/^(\w+)_k(\d+)$/)
  if (m) {
    const field = m[1]
    const k = parseInt(m[2])
    const p = note.partials?.find((pp: any) => pp.k === k)
    return p ? (p[field] ?? null) : null
  }
  return null
}

export const ParamSpace3DView: React.FC = () => {
  const plotRef = useRef<HTMLDivElement>(null)

  const bankState   = useBankStore(s => s.activeState())
  const fetchNote   = useBankStore(s => s.fetchNote)
  const pending     = useCorrectionStore(s => s.pending)
  const isAnchor    = useAnchorStore(s => s.isAnchor)
  const outlierScore = useFitStore(s => s.outlierScore)
  const selectedVel = useUiStore(s => s.selectedVel)
  const selectNote  = useUiStore(s => s.selectNote)

  const [axisX, setAxisX] = useState('midi')
  const [axisY, setAxisY] = useState('tau1_k1')
  const [axisZ, setAxisZ] = useState('A0_k1')
  const [notes, setNotes] = useState<any[]>([])
  const [loading, setLoading] = useState(false)

  // Načti noty pro vybranou velocity
  useEffect(() => {
    if (!bankState) return
    setLoading(true)
    const keys = bankState.note_keys.filter(k =>
      k.endsWith(`_vel${selectedVel}`)
    )
    Promise.all(keys.map(k => fetchNote(k))).then(results => {
      setNotes(results.filter(Boolean))
      setLoading(false)
    })
  }, [bankState, selectedVel, fetchNote])

  // Correction index
  const corrMap = React.useMemo(() => {
    const map = new Map<string, Map<string, number>>()  // "midi_vel" → { field → corrected }
    if (!pending) return map
    for (const c of pending.corrections) {
      const key = `${c.midi}_${c.vel}`
      if (!map.has(key)) map.set(key, new Map())
      map.get(key)!.set(c.field, c.corrected)
    }
    return map
  }, [pending])

  // 3D plot
  useEffect(() => {
    if (!plotRef.current || notes.length === 0) return

    const getVal = (note: any, axis: string, corrected: boolean): number | null => {
      if (corrected) {
        const ck = `${note.midi}_${note.vel}`
        const corrs = corrMap.get(ck)
        if (corrs?.has(axis)) return corrs.get(axis)!
      }
      return getNoteParam(note, axis)
    }

    const axXOpt = AXIS_OPTIONS.find(a => a.key === axisX)
    const axYOpt = AXIS_OPTIONS.find(a => a.key === axisY)
    const axZOpt = AXIS_OPTIONS.find(a => a.key === axisZ)

    // Originální body
    const origX: number[] = [], origY: number[] = [], origZ: number[] = []
    const origColor: string[] = [], origText: string[] = [], origSize: number[] = []

    // Opravené body
    const corrX: number[] = [], corrY: number[] = [], corrZ: number[] = []
    const corrText: string[] = []

    // Spojnice orig → corr
    const lineX: (number|null)[] = [], lineY: (number|null)[] = [], lineZ: (number|null)[] = []

    for (const note of notes) {
      const x = getVal(note, axisX, false)
      const y = getVal(note, axisY, false)
      const z = getVal(note, axisZ, false)
      if (x === null || y === null || z === null) continue

      const midi = note.midi
      const anchor = isAnchor(midi)
      const score = outlierScore(`m${String(midi).padStart(3, '0')}`)
      const name = midiToNoteName(midi)

      origX.push(x); origY.push(y); origZ.push(z)
      origColor.push(anchor ? '#BA7517' : score > 0.5 ? '#E24B4A' : '#888780')
      origSize.push(anchor ? 7 : 4)
      origText.push(`${name} MIDI ${midi}\n${axisX}=${x.toPrecision(4)}\n${axisY}=${y.toPrecision(4)}\n${axisZ}=${z.toPrecision(4)}`)

      // Opravené
      const cx = getVal(note, axisX, true)
      const cy = getVal(note, axisY, true)
      const cz = getVal(note, axisZ, true)
      if (cx !== null && cy !== null && cz !== null &&
          (cx !== x || cy !== y || cz !== z)) {
        corrX.push(cx); corrY.push(cy); corrZ.push(cz)
        corrText.push(`${name} MIDI ${midi} (opraveno)\n${axisX}=${cx.toPrecision(4)}\n${axisY}=${cy.toPrecision(4)}\n${axisZ}=${cz.toPrecision(4)}`)

        // Spojnice
        lineX.push(x, cx, null)
        lineY.push(y, cy, null)
        lineZ.push(z, cz, null)
      }
    }

    const traces: Plotly.Data[] = [
      // Originální body
      {
        type: 'scatter3d', mode: 'markers',
        x: origX, y: origY, z: origZ,
        text: origText,
        marker: { color: origColor, size: origSize, opacity: 0.7 },
        hovertemplate: '%{text}<extra>⬤ originál</extra>',
        name: '⬤ originál',
      },
    ]

    // Opravené body
    if (corrX.length > 0) {
      traces.push({
        type: 'scatter3d', mode: 'markers',
        x: corrX, y: corrY, z: corrZ,
        text: corrText,
        marker: { color: 'rgba(29, 158, 117, 0.6)', size: 5, symbol: 'diamond' },
        hovertemplate: '%{text}<extra>◇ opraveno</extra>',
        name: '◇ opraveno',
      })

      // Spojnice orig → corr
      traces.push({
        type: 'scatter3d', mode: 'lines',
        x: lineX as number[], y: lineY as number[], z: lineZ as number[],
        line: { color: 'rgba(29, 158, 117, 0.25)', width: 1 },
        hoverinfo: 'skip',
        name: 'korekce vektor',
        connectgaps: false,
      } as Plotly.Data)
    }

    const layout: Partial<Plotly.Layout> = {
      paper_bgcolor: 'transparent',
      font: { family: 'IBM Plex Mono', size: 10, color: '#9B9892' },
      margin: { l: 0, r: 0, t: 30, b: 0 },
      showlegend: true,
      legend: { x: 0, y: 1, font: { size: 10 } },
      scene: {
        xaxis: { title: { text: axXOpt?.label ?? axisX }, type: axXOpt?.log ? 'log' : undefined,
                 gridcolor: '#3E4044', backgroundcolor: '#2A2D30' },
        yaxis: { title: { text: axYOpt?.label ?? axisY }, type: axYOpt?.log ? 'log' : undefined,
                 gridcolor: '#3E4044', backgroundcolor: '#2A2D30' },
        zaxis: { title: { text: axZOpt?.label ?? axisZ }, type: axZOpt?.log ? 'log' : undefined,
                 gridcolor: '#3E4044', backgroundcolor: '#2A2D30' },
        bgcolor: '#2A2D30',
      },
      height: 500,
    }

    if ((plotRef.current as any)._plotly) {
      Plotly.react(plotRef.current, traces, layout, { responsive: true })
    } else {
      Plotly.newPlot(plotRef.current, traces, layout, { responsive: true })
      ;(plotRef.current as any)._plotly = true
    }

    ;(plotRef.current as any).on('plotly_click', (data: Plotly.PlotMouseEvent) => {
      const text = data.points[0]?.text as string
      const m = text?.match(/MIDI (\d+)/)
      if (m) selectNote(parseInt(m[1]))
    })
  }, [notes, corrMap, axisX, axisY, axisZ, isAnchor, outlierScore, selectNote])

  // -----------------------------------------------------------------------

  if (!bankState) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center',
                    height: '100%', color: 'var(--t-muted)', fontSize: 13 }}>
        Načtěte banku
      </div>
    )
  }

  function AxisSelect({ value, onChange, label }: {
    value: string; onChange: (v: string) => void; label: string
  }) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
        <span className="label">{label}</span>
        <select className="select" style={{ fontSize: 11 }}
                value={value} onChange={e => onChange(e.target.value)}>
          {AXIS_OPTIONS.map(o => (
            <option key={o.key} value={o.key}>{o.label}</option>
          ))}
        </select>
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-3)',
                  padding: 'var(--sp-3)', height: '100%', overflow: 'hidden' }}
         className="animate-in">

      {/* Axis selectors */}
      <div style={{ display: 'flex', gap: 'var(--sp-4)', alignItems: 'center', flexWrap: 'wrap' }}>
        <AxisSelect label="X" value={axisX} onChange={setAxisX} />
        <AxisSelect label="Y" value={axisY} onChange={setAxisY} />
        <AxisSelect label="Z" value={axisZ} onChange={setAxisZ} />
        <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--t-muted)' }}>
          {loading ? 'Načítám…' : `${notes.length} not · vel ${selectedVel}`}
          {pending ? ` · ${pending.corrections.length} korekcí` : ''}
        </span>
      </div>

      {/* 3D plot */}
      <div ref={plotRef} style={{ flex: 1, minHeight: 400 }} />
    </div>
  )
}
