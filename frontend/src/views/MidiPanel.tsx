// views/MidiPanel.tsx
// Changelog: 2025-04-14 v0.1 — initial

import React, { useEffect, useState } from 'react'
import { midiApi } from '../api/client'
import { useBankStore } from '../store/bankStore'

export const MidiPanel: React.FC = () => {
  const [connected, setConnected] = useState(false)
  const [portName, setPortName]   = useState<string | null>(null)
  const [ports, setPorts]         = useState<string[]>([])
  const [selectedPort, setSelectedPort] = useState('')
  const [patching, setPatching]   = useState(false)
  const [patchResult, setPatchResult] = useState<string | null>(null)
  const [log, setLog]             = useState<string[]>([])

  const activePath = useBankStore(s => s.activePath())

  useEffect(() => {
    midiApi.status().then(s => {
      setConnected(s.connected)
      setPortName(s.port_name ?? null)
    }).catch(() => {})
    midiApi.ports().then(r => setPorts(r.ports)).catch(() => {})
  }, [])

  function addLog(msg: string) {
    setLog(prev => [...prev.slice(-49), `${new Date().toLocaleTimeString('cs')} ${msg}`])
  }

  async function handleConnect() {
    if (!selectedPort) return
    try {
      const r = await midiApi.connect(selectedPort)
      setConnected(r.connected); setPortName(r.port)
      addLog(`Připojeno: ${r.port}`)
    } catch (e: any) {
      addLog(`Chyba: ${e.message}`)
    }
  }

  async function handleDisconnect() {
    await midiApi.disconnect()
    setConnected(false); setPortName(null)
    addLog('Odpojeno')
  }

  async function handlePatch() {
    if (!activePath) return
    setPatching(true); setPatchResult(null)
    addLog('Spouštím SysEx patch…')
    try {
      const r = await midiApi.patch(activePath)
      const msg = `Hotovo: ${r.success}/${r.total} not  ${r.failed > 0 ? `(${r.failed} chyb)` : ''}`
      setPatchResult(msg); addLog(msg)
    } catch (e: any) {
      setPatchResult(`Chyba: ${e.message}`); addLog(`Chyba: ${e.message}`)
    } finally {
      setPatching(false)
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-4)',
                  padding: 'var(--sp-4)', height: '100%', overflowY: 'auto' }}
         className="animate-in">

      {/* Status */}
      <div className="panel">
        <div className="panel__header">
          <span className="panel__title">MIDI připojení</span>
          <span style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 6 }}>
            <span className={`status-dot status-dot--${connected ? 'ok' : 'off'}`} />
            <span style={{ fontSize: 11, fontFamily: 'var(--font-mono)',
                           color: connected ? 'var(--c-mid)' : 'var(--t-muted)' }}>
              {connected ? portName : 'nepřipojeno'}
            </span>
          </span>
        </div>
        <div className="panel__body" style={{ display: 'flex', gap: 'var(--sp-3)' }}>
          <select className="select" style={{ flex: 1 }}
                  value={selectedPort}
                  onChange={e => setSelectedPort(e.target.value)}
                  disabled={connected}>
            <option value="">Vyberte MIDI port…</option>
            {ports.map(p => <option key={p} value={p}>{p}</option>)}
            {ports.length === 0 && <option disabled>Žádné porty</option>}
          </select>
          {connected ? (
            <button className="btn btn--danger" onClick={handleDisconnect}>Odpojit</button>
          ) : (
            <button className="btn btn--accent" onClick={handleConnect}
                    disabled={!selectedPort}>Připojit</button>
          )}
        </div>
      </div>

      {/* Patch */}
      <div className="panel">
        <div className="panel__header">
          <span className="panel__title">SysEx Patch</span>
        </div>
        <div className="panel__body" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-3)' }}>
          <div style={{ fontSize: 12, color: 'var(--t-secondary)' }}>
            Odešle SysEx patche pro všechny noty aktivní banky do připojeného syntetizéru.
          </div>
          {!activePath && (
            <div style={{ fontSize: 11, color: 'var(--c-warn)' }}>⚠ Není načtena žádná banka</div>
          )}
          {patchResult && (
            <div style={{ fontSize: 11, fontFamily: 'var(--font-mono)',
                          color: patchResult.includes('Chyba') ? 'var(--c-outlier)' : 'var(--c-mid)' }}>
              {patchResult}
            </div>
          )}
          <button
            className="btn btn--accent"
            onClick={handlePatch}
            disabled={!connected || !activePath || patching}
            style={{ alignSelf: 'flex-start' }}
          >
            {patching ? 'Patchuji…' : 'Odeslat SysEx patch'}
          </button>
          <div style={{ fontSize: 11, color: 'var(--t-muted)',
                        background: 'var(--bg-base)', borderRadius: 'var(--r-sm)',
                        padding: 'var(--sp-2) var(--sp-3)',
                        border: '1px solid var(--bg-border)' }}>
            SysEx specifikace cílového syntetizéru bude doplněna v samostatné dokumentaci.
          </div>
        </div>
      </div>

      {/* Log */}
      <div className="panel" style={{ flex: 1 }}>
        <div className="panel__header">
          <span className="panel__title">Log</span>
          <button className="btn" style={{ marginLeft: 'auto', padding: '1px 6px', fontSize: 10 }}
                  onClick={() => setLog([])}>
            Vymazat
          </button>
        </div>
        <div style={{ padding: 'var(--sp-2) var(--sp-3)', overflowY: 'auto',
                      maxHeight: 240, fontFamily: 'var(--font-mono)', fontSize: 11 }}>
          {log.length === 0 && (
            <span style={{ color: 'var(--t-muted)' }}>Žádné události</span>
          )}
          {[...log].reverse().map((line, i) => (
            <div key={i} style={{ color: 'var(--t-secondary)', padding: '1px 0',
                                  borderBottom: '1px solid var(--bg-border)' }}>
              {line}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
