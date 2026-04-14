// views/AnchorPanel.tsx
// Changelog:
//   2025-04-14 v0.1 — initial (dialog-based)
//   2025-04-14 v0.2 — formulářový workflow: nota + MIDI vel 0-127 -> layer 0-7

import React, { useEffect, useRef, useState } from 'react'
import { useAnchorStore } from '../store/anchorStore'
import { useBankStore }   from '../store/bankStore'
import { useFitStore }    from '../store/fitStore'
import { useUiStore }     from '../store/uiStore'
import { previewSocket, anchorApi } from '../api/client'
import { midiToNoteName, midiToF0 } from '../types'
import {
  midiVelToLayer, layerToMidiVelRange,
  VEL_LAYER_LABELS,
} from '../utils/format'
import type { AnchorEntry, AnchorSuggestion } from '../types'

// ---------------------------------------------------------------------------
// Barvy vrstev (tmavá -> svetla)
// ---------------------------------------------------------------------------
const VEL_LAYER_COLORS = [
  '#2A3A5A','#2A4A6A','#2A5A7A','#2A6A6A',
  '#2A6A5A','#2A7A4A','#2A8A3A','#1D9E75',
] as const

function scoreLabel(s: number): string {
  if (s === 0) return 'ignorovat'
  if (s <= 2)  return 'velmi nizka'
  if (s <= 4)  return 'nizka'
  if (s <= 6)  return 'prumerna'
  if (s <= 8)  return 'dobra'
  return 'referencni'
}

function scoreCategory(s: number): 'low' | 'mid' | 'high' {
  return s <= 3 ? 'low' : s <= 6 ? 'mid' : 'high'
}

function parseNoteInput(raw: string): number | null {
  const s = raw.trim().toLowerCase()
  if (!s) return null
  const numMatch = s.match(/^m?(\d{1,3})$/)
  if (numMatch) {
    const n = parseInt(numMatch[1])
    return (n >= 21 && n <= 108) ? n : null
  }
  const noteMatch = s.match(/^([a-g])(#|b|s)?(-?\d)$/)
  if (!noteMatch) return null
  const base = ({ c:0,d:2,e:4,f:5,g:7,a:9,b:11 } as Record<string,number>)[noteMatch[1]]
  if (base === undefined) return null
  const acc  = noteMatch[2]==='#'||noteMatch[2]==='s' ? 1 : noteMatch[2]==='b' ? -1 : 0
  const midi = (parseInt(noteMatch[3])+1)*12 + base + acc
  return (midi >= 21 && midi <= 108) ? midi : null
}

// ---------------------------------------------------------------------------
export const AnchorPanel: React.FC = () => {
  const { active, coverage, databases, addEntry, removeEntry, refreshCoverage,
          selectDb, createDb, deleteDb, loadDatabases } = useAnchorStore()
  const activePath = useBankStore(s => s.activePath())
  const { runFit } = useFitStore()
  const { setStatus } = useUiStore()

  const [noteInput,    setNoteInput]    = useState('')
  const [parsedMidi,   setParsedMidi]   = useState<number | null>(null)
  const [parseError,   setParseError]   = useState('')

  // Velocity: vstup jako MIDI 0-127 nebo vsechny (-1)
  const [velMode,      setVelMode]      = useState<'all' | 'range'>('all')
  const [velMidiInput, setVelMidiInput] = useState('64')
  const [velMidi,      setVelMidi]      = useState(64)
  const [velLayer,     setVelLayer]     = useState(4)

  const [score,        setScore]        = useState(7)
  const [noteRemark,   setNoteRemark]   = useState('')
  const [submitting,   setSubmitting]   = useState(false)
  const [lastAdded,    setLastAdded]    = useState<string | null>(null)
  const [suggestions,  setSuggestions]  = useState<AnchorSuggestion[]>([])
  const [showSuggest,  setShowSuggest]  = useState(false)
  const [confirmDel,   setConfirmDel]   = useState(false)
  const [newDbName,    setNewDbName]    = useState('')
  const [showNewDb,    setShowNewDb]    = useState(false)

  const noteRef = useRef<HTMLInputElement>(null)
  const velRef  = useRef<HTMLInputElement>(null)

  useEffect(() => { loadDatabases().catch(() => {}) }, [loadDatabases])
  useEffect(() => {
    if (active && activePath) refreshCoverage(activePath)
  }, [active, activePath, refreshCoverage])

  function handleVelInput(raw: string) {
    setVelMidiInput(raw)
    const n = parseInt(raw)
    if (!isNaN(n) && n >= 0 && n <= 127) {
      setVelMidi(n); setVelLayer(midiVelToLayer(n))
    }
  }

  function handleVelSlider(n: number) {
    setVelMidi(n); setVelMidiInput(String(n)); setVelLayer(midiVelToLayer(n))
  }

  function handleNoteInput(val: string) {
    setNoteInput(val)
    if (!val.trim()) { setParsedMidi(null); setParseError(''); return }
    const midi = parseNoteInput(val)
    if (midi !== null) { setParsedMidi(midi); setParseError('') }
    else { setParsedMidi(null); setParseError('Zadejte nazev noty (C4, F#3) nebo MIDI cislo (60)') }
  }

  const bankVel = velMode === 'all' ? -1 : velLayer

  async function handleSubmit() {
    if (!active)             { setStatus('Vyberte anchor databazi'); return }
    if (parsedMidi === null) { setStatus('Zadejte platnou notu'); return }
    setSubmitting(true)
    try {
      await addEntry(parsedMidi, bankVel, score, noteRemark || undefined)
      const velDesc = velMode === 'all'
        ? 'vsechny vel'
        : `layer ${velLayer} (${VEL_LAYER_LABELS[velLayer]}) · MIDI ${velMidi}`
      const msg = `${midiToNoteName(parsedMidi)} (MIDI ${parsedMidi}) · ${velDesc} · score ${score}`
      setLastAdded(msg); setStatus('Pridano: ' + msg)
      if (activePath) {
        previewSocket.send({ action: 'update_anchor',
          payload: { midi: parsedMidi, vel: bankVel, score, anchor_db_name: active.name } })
        await runFit(activePath, active.name)
      }
      setNoteInput(''); setParsedMidi(null); setNoteRemark('')
      noteRef.current?.focus()
    } catch (e: any) {
      setStatus('Chyba: ' + e.message)
    } finally {
      setSubmitting(false)
    }
  }

  async function handleRemove(e: AnchorEntry) {
    await removeEntry(e.midi, e.vel)
    if (activePath) await runFit(activePath, active?.name)
  }

  async function handleSuggest() {
    if (!active || !activePath) return
    const res = await anchorApi.suggest(active.name, activePath, 15)
    setSuggestions(res.suggestions); setShowSuggest(true)
  }

  return (
    <div style={{ display:'flex', flexDirection:'column', gap:'var(--sp-4)',
                  padding:'var(--sp-4)', height:'100%', overflowY:'auto' }}
         className="animate-in">

      {/* 1. Databaze */}
      <div className="panel">
        <div className="panel__header">
          <span className="panel__title">① Databaze</span>
          <div style={{ marginLeft:'auto', display:'flex', gap:'var(--sp-2)' }}>
            {active && <>
              <button className="btn" onClick={handleSuggest}>Navrhnout</button>
              <button className="btn btn--danger"
                onClick={() => confirmDel
                  ? deleteDb(active.name).then(() => { setConfirmDel(false); loadDatabases() })
                  : setConfirmDel(true)}>
                {confirmDel ? 'Opravdu?' : 'Smazat'}
              </button>
            </>}
          </div>
        </div>
        <div className="panel__body" style={{ display:'flex', gap:'var(--sp-3)', alignItems:'center' }}>
          {showNewDb ? (
            <>
              <input className="input" style={{ flex:1 }} placeholder="nazev databaze"
                     value={newDbName} onChange={e => setNewDbName(e.target.value)}
                     onKeyDown={e => { if (e.key==='Enter' && newDbName.trim())
                       createDb(newDbName.trim()).then(() => { setNewDbName(''); setShowNewDb(false); loadDatabases() }) }}
                     autoFocus />
              <button className="btn btn--accent"
                onClick={() => newDbName.trim() && createDb(newDbName.trim()).then(() => { setNewDbName(''); setShowNewDb(false); loadDatabases() })}>
                Vytvorit
              </button>
              <button className="btn" onClick={() => setShowNewDb(false)}>Zrusit</button>
            </>
          ) : (
            <>
              <select className="select" style={{ flex:1 }} value={active?.name ?? ''}
                      onChange={async e => e.target.value==='__new__' ? setShowNewDb(true)
                                          : e.target.value && await selectDb(e.target.value)}>
                <option value="">— vyberte databazi —</option>
                {databases.map(d => <option key={d.name} value={d.name}>{d.name} ({d.entry_count})</option>)}
                <option value="__new__">+ Nova...</option>
              </select>
              {active && <span style={{ fontSize:11, color:'var(--t-muted)' }}>{active.entries.length} zaznamu</span>}
            </>
          )}
        </div>
      </div>

      {/* 2. Pridat zaznam */}
      <div className="panel">
        <div className="panel__header">
          <span className="panel__title">② Pridat anchor</span>
          {!active && <span style={{ marginLeft:8, fontSize:11, color:'var(--c-warn)' }}>Vyberte databazi</span>}
        </div>
        <div className="panel__body" style={{ display:'flex', flexDirection:'column', gap:'var(--sp-4)' }}>

          {/* Nota */}
          <div>
            <div className="label" style={{ marginBottom:6 }}>Nota</div>
            <div style={{ display:'flex', gap:'var(--sp-2)', alignItems:'center', flexWrap:'wrap' }}>
              <input
                ref={noteRef}
                className="input"
                style={{ width:160,
                  borderColor: parseError ? 'var(--c-outlier)'
                              : parsedMidi !== null ? 'var(--c-mid)' : undefined }}
                placeholder="C4, F#3, 60, m060..."
                value={noteInput}
                onChange={e => handleNoteInput(e.target.value)}
                onKeyDown={e => { if (e.key==='Enter') { e.preventDefault(); velRef.current?.focus() } }}
                disabled={!active} autoFocus
              />
              {parsedMidi !== null && (
                <div style={{ display:'flex', alignItems:'center', gap:'var(--sp-2)',
                              padding:'4px 10px', background:'var(--bg-card)',
                              border:'1px solid var(--c-mid)', borderRadius:'var(--r-sm)' }}>
                  <span style={{ fontFamily:'var(--font-ui)', fontWeight:700, fontSize:15 }}>
                    {midiToNoteName(parsedMidi)}
                  </span>
                  <span className="mono" style={{ fontSize:11, color:'var(--t-muted)' }}>MIDI {parsedMidi}</span>
                  <span className="mono" style={{ fontSize:11, color:'var(--t-muted)' }}>{midiToF0(parsedMidi).toFixed(1)} Hz</span>
                </div>
              )}
            </div>
            {parseError && <div style={{ fontSize:11, color:'var(--c-outlier)', marginTop:4 }}>{parseError}</div>}
          </div>

          {/* Velocity */}
          <div>
            <div className="label" style={{ marginBottom:8 }}>Velocity</div>
            <div style={{ display:'flex', gap:4, marginBottom:10 }}>
              <button className={`btn ${velMode==='all' ? 'btn--accent' : ''}`}
                      onClick={() => setVelMode('all')} disabled={!active}>
                * vsechny
              </button>
              <button className={`btn ${velMode==='range' ? 'btn--accent' : ''}`}
                      onClick={() => { setVelMode('range'); setTimeout(() => velRef.current?.focus(), 50) }}
                      disabled={!active}>
                konkretni
              </button>
            </div>

            {velMode === 'range' && (
              <div style={{ display:'flex', flexDirection:'column', gap:'var(--sp-3)' }}>
                <div style={{ display:'flex', gap:'var(--sp-3)', alignItems:'center', flexWrap:'wrap' }}>
                  <div style={{ display:'flex', alignItems:'center', gap:'var(--sp-2)' }}>
                    <span className="label" style={{ whiteSpace:'nowrap' }}>MIDI vel</span>
                    <input
                      ref={velRef}
                      className="input"
                      style={{ width:64, textAlign:'center' }}
                      type="number" min={0} max={127} step={1}
                      value={velMidiInput}
                      onChange={e => handleVelInput(e.target.value)}
                      onKeyDown={e => e.key==='Enter' && parsedMidi!==null && handleSubmit()}
                      disabled={!active}
                    />
                    <span style={{ fontSize:11, color:'var(--t-muted)' }}>0–127</span>
                  </div>
                  <div style={{ display:'flex', alignItems:'center', gap:'var(--sp-3)',
                                padding:'5px 12px', background:'var(--bg-card)',
                                border:'1px solid var(--bg-border)', borderRadius:'var(--r-sm)',
                                flex:1, minWidth:200 }}>
                    <span style={{ fontSize:10, color:'var(--t-muted)', whiteSpace:'nowrap' }}>vrstva</span>
                    <span style={{ fontFamily:'var(--font-mono)', fontWeight:700, fontSize:18,
                                   color: VEL_LAYER_COLORS[velLayer] }}>
                      {velLayer}
                    </span>
                    <span style={{ fontFamily:'var(--font-ui)', fontWeight:600, fontSize:14, color:'var(--t-primary)' }}>
                      {VEL_LAYER_LABELS[velLayer]}
                    </span>
                    <span style={{ fontSize:11, color:'var(--t-muted)', marginLeft:'auto', fontFamily:'var(--font-mono)' }}>
                      MIDI {layerToMidiVelRange(velLayer)[0]}–{layerToMidiVelRange(velLayer)[1]}
                    </span>
                  </div>
                </div>
                <div>
                  <input type="range" className="slider" min={0} max={127} step={1} value={velMidi}
                         onChange={e => handleVelSlider(Number(e.target.value))} disabled={!active} style={{ width:'100%' }} />
                  <div style={{ display:'flex', marginTop:4, fontSize:9, color:'var(--t-muted)', fontFamily:'var(--font-mono)' }}>
                    {(VEL_LAYER_LABELS as readonly string[]).map((l, i) => (
                      <div key={i} style={{ flex:1, textAlign:'center',
                                            color: i===velLayer ? 'var(--c-anchor)' : 'var(--t-muted)',
                                            fontWeight: i===velLayer ? 700 : 400,
                                            borderLeft: i===0 ? 'none' : '1px solid var(--bg-border)',
                                            paddingTop:2, cursor:'pointer', userSelect:'none' }}
                           onClick={() => { const m=i*16+8; handleVelSlider(m); setVelMidiInput(String(m)) }}>
                        {l}
                      </div>
                    ))}
                  </div>
                  <div style={{ display:'flex', fontSize:8, color:'var(--t-muted)', fontFamily:'var(--font-mono)' }}>
                    {[0,16,32,48,64,80,96,112].map(n => (
                      <div key={n} style={{ flex:1, textAlign:'center' }}>{n}</div>
                    ))}
                  </div>
                </div>
              </div>
            )}
            {velMode === 'all' && (
              <div style={{ fontSize:11, color:'var(--t-muted)' }}>
                Zaznam bude platit pro vsechny velocity vrstvy noty (0–7).
              </div>
            )}
          </div>

          {/* Score */}
          <div>
            <div style={{ display:'flex', justifyContent:'space-between', marginBottom:6 }}>
              <span className="label">Score</span>
              <span style={{ fontFamily:'var(--font-mono)', fontSize:14, fontWeight:700,
                             color: score>=7 ? 'var(--c-mid)' : score>=4 ? 'var(--c-anchor)' : 'var(--c-outlier)' }}>
                {score} — <span style={{ fontWeight:400, fontSize:11 }}>{scoreLabel(score)}</span>
              </span>
            </div>
            <input type="range" className="slider" min={0} max={9} step={1} value={score}
                   onChange={e => setScore(Number(e.target.value))} disabled={!active} />
            <div style={{ display:'flex', justifyContent:'space-between', fontSize:10, color:'var(--t-muted)', marginTop:3 }}>
              <span>0 ignorovat</span><span>3</span><span>6 prumerna</span><span>9 referencni</span>
            </div>
          </div>

          {/* Poznamka */}
          <div>
            <div className="label" style={{ marginBottom:6 }}>Poznamka (nepovinne)</div>
            <input className="input" placeholder="cisty decay, nizky SNR..."
                   value={noteRemark} onChange={e => setNoteRemark(e.target.value)}
                   onKeyDown={e => e.key==='Enter' && parsedMidi!==null && handleSubmit()}
                   maxLength={80} disabled={!active} />
          </div>

          <button className="btn btn--accent" onClick={handleSubmit}
                  disabled={!active || parsedMidi===null || submitting}
                  style={{ alignSelf:'flex-start', padding:'7px 20px', fontSize:13 }}>
            {submitting ? 'Ukladam...' : 'Pridat anchor'}
          </button>

          {lastAdded && (
            <div style={{ fontSize:11, color:'var(--c-mid)', padding:'4px 8px',
                          background:'#0d2218', borderRadius:'var(--r-sm)', border:'1px solid #1a4030' }}>
              {lastAdded}
            </div>
          )}
        </div>
      </div>

      {/* Coverage */}
      {coverage && (
        <div className="panel">
          <div className="panel__header">
            <span className="panel__title">Pokryti</span>
            <span className="tag" style={{ marginLeft:'auto', color: coverage.ok ? 'var(--c-mid)' : 'var(--c-outlier)' }}>
              {coverage.ok ? 'OK' : 'Nedostatecne'}
            </span>
          </div>
          <div className="panel__body">
            <div style={{ display:'grid', gridTemplateColumns:'repeat(5,1fr)', gap:'var(--sp-2)', marginBottom:'var(--sp-3)' }}>
              {([['Bass',coverage.bass,3],['Stred',coverage.mid,6],['Vysky',coverage.treble,3],['pp',coverage.vel_low,2],['ff',coverage.vel_high,2]] as [string,number,number][]).map(([l,c,m]) => (
                <div key={l} style={{ textAlign:'center' }}>
                  <div style={{ fontFamily:'var(--font-mono)', fontSize:18, fontWeight:700,
                                color: c>=m ? 'var(--c-mid)' : 'var(--c-outlier)' }}>{c}</div>
                  <div className="label">{l} /{m}</div>
                </div>
              ))}
            </div>
            {coverage.warnings.map((w,i) => <div key={i} style={{ fontSize:11, color:'var(--c-anchor)' }}>{w}</div>)}
          </div>
        </div>
      )}

      {/* Suggestions */}
      {showSuggest && suggestions.length > 0 && (
        <div className="panel">
          <div className="panel__header">
            <span className="panel__title">Navrhovane noty</span>
            <button className="btn" style={{ marginLeft:'auto' }} onClick={() => setShowSuggest(false)}>X</button>
          </div>
          <div style={{ maxHeight:200, overflowY:'auto' }}>
            {suggestions.map((s,i) => (
              <div key={i} style={{ display:'flex', alignItems:'center', gap:'var(--sp-3)',
                                    padding:'6px var(--sp-4)', borderBottom:'1px solid var(--bg-border)' }}>
                <span className="mono" style={{ minWidth:36, fontWeight:600 }}>{midiToNoteName(s.midi)}</span>
                <span style={{ color:'var(--t-muted)', fontSize:11, minWidth:36 }}>
                  {s.vel===-1 ? 'all' : VEL_LAYER_LABELS[s.vel]}
                </span>
                <span className="tag">{s.region}</span>
                <span style={{ color:'var(--t-muted)', fontSize:11, flex:1 }}>{s.reason}</span>
                <button className="btn" onClick={() => {
                  setNoteInput(String(s.midi)); handleNoteInput(String(s.midi))
                  if (s.vel === -1) { setVelMode('all') }
                  else { setVelMode('range'); const m=s.vel*16+8; setVelMidi(m); setVelMidiInput(String(m)); setVelLayer(s.vel) }
                }}>pouzit</button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Zaznamy */}
      {active && active.entries.length > 0 && (
        <div className="panel">
          <div className="panel__header">
            <span className="panel__title">Zaznamy ({active.entries.length})</span>
          </div>
          <div style={{ overflowY:'auto', maxHeight:320 }}>
            <table style={{ width:'100%', borderCollapse:'collapse', fontSize:12 }}>
              <thead>
                <tr style={{ borderBottom:'1px solid var(--bg-border)' }}>
                  {['Nota','Vrstva','MIDI vel','Score','Poznamka',''].map(h => (
                    <th key={h} style={{ padding:'4px 8px', textAlign:'left',
                                         color:'var(--t-muted)', fontWeight:500, fontSize:10 }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {[...active.entries].sort((a,b) => a.midi-b.midi||a.vel-b.vel).map((e,i) => {
                  const [vLo, vHi] = e.vel >= 0 ? layerToMidiVelRange(e.vel) : [0, 127]
                  return (
                    <tr key={i} style={{ borderBottom:'1px solid var(--bg-border)',
                                         background: i%2===0 ? 'transparent' : 'var(--bg-card)' }}>
                      <td style={{ padding:'5px 8px' }}>
                        <span className="mono" style={{ fontWeight:600 }}>{midiToNoteName(e.midi)}</span>
                        <span style={{ color:'var(--t-muted)', marginLeft:5, fontSize:10 }}>{e.midi}</span>
                      </td>
                      <td style={{ padding:'5px 8px', fontFamily:'var(--font-mono)', fontSize:12 }}>
                        {e.vel===-1
                          ? <span style={{ color:'var(--c-anchor)' }}>* all</span>
                          : <span style={{ color: VEL_LAYER_COLORS[e.vel] }}>
                              {e.vel} {VEL_LAYER_LABELS[e.vel]}
                            </span>}
                      </td>
                      <td style={{ padding:'5px 8px', fontFamily:'var(--font-mono)', fontSize:11, color:'var(--t-muted)' }}>
                        {e.vel===-1 ? '0-127' : `${vLo}-${vHi}`}
                      </td>
                      <td style={{ padding:'5px 8px' }}>
                        <span className="score-badge" data-score={scoreCategory(e.score)}>{e.score.toFixed(0)}</span>
                      </td>
                      <td style={{ padding:'5px 8px', color:'var(--t-muted)', fontSize:11,
                                   maxWidth:140, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>
                        {e.note||'—'}
                      </td>
                      <td style={{ padding:'5px 8px' }}>
                        <button className="btn btn--danger" style={{ padding:'1px 6px', fontSize:10 }}
                                onClick={() => handleRemove(e)}>X</button>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
