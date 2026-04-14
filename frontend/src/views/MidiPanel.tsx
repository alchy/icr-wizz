// views/MidiPanel.tsx — MIDI port management + SysEx upload
// Changelog: 2025-04-14 v0.1 — initial
//            2025-04-14 v0.2 — config-based port, upload bank button

import React, { useEffect, useState } from 'react'
import { midiApi, configApi } from '../api/client'
import { useBankStore } from '../store/bankStore'
import { useUiStore }   from '../store/uiStore'

export const MidiPanel: React.FC = () => {
  const [ports, setPorts]               = useState<string[]>([])
  const [configPort, setConfigPort]     = useState<string>('')
  const [selectedPort, setSelectedPort] = useState('')
  const [uploading, setUploading]       = useState(false)
  const [log, setLog]                   = useState<string[]>([])

  const activePath = useBankStore(s => s.activePath())
  const setStatus  = useUiStore(s => s.setStatus)

  // Načti porty a aktuální port z configu
  useEffect(() => {
    midiApi.ports().then(r => setPorts(r.ports)).catch(() => {})
    configApi.get().then(cfg => {
      const port = cfg.midi_port
      if (typeof port === 'string' && port) {
        setConfigPort(port)
        setSelectedPort(port)
      }
    }).catch(() => {})
  }, [])

  function addLog(msg: string) {
    setLog(prev => [...prev.slice(-49), `${new Date().toLocaleTimeString('cs')} ${msg}`])
  }

  async function handlePortChange(port: string) {
    setSelectedPort(port)
    if (!port) return
    try {
      await configApi.patch({ midi_port: port })
      setConfigPort(port)
      addLog(`Port uložen: ${port} — restart backendu pro aktivaci`)
      setStatus(`MIDI port: ${port} (restart pro aktivaci)`)
    } catch (e: any) {
      addLog(`Chyba ukládání: ${e.message}`)
    }
  }

  async function handleUpload() {
    if (!activePath) return
    setUploading(true)
    addLog('SysEx SET_BANK upload…')
    setStatus('SysEx upload…')
    try {
      const r = await midiApi.uploadBank(activePath)
      const msg = `SET_BANK OK: ${r.chunks_sent}/${r.chunks_total} chunks (${Math.round(r.bytes / 1024)} kB)`
      addLog(msg)
      setStatus(msg)
    } catch (e: any) {
      addLog(`Chyba: ${e.message}`)
      setStatus(`SysEx chyba: ${e.message}`)
    } finally {
      setUploading(false)
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-4)',
                  padding: 'var(--sp-4)', height: '100%', overflowY: 'auto' }}
         className="animate-in">

      {/* Port selection */}
      <div className="panel">
        <div className="panel__header">
          <span className="panel__title">MIDI port</span>
          <span style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 6 }}>
            <span className={`status-dot status-dot--${configPort ? 'ok' : 'off'}`} />
            <span style={{ fontSize: 11, fontFamily: 'var(--font-mono)',
                           color: configPort ? 'var(--c-mid)' : 'var(--t-muted)' }}>
              {configPort || 'nenastaveno'}
            </span>
          </span>
        </div>
        <div className="panel__body" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-3)' }}>
          <div style={{ display: 'flex', gap: 'var(--sp-3)' }}>
            <select className="select" style={{ flex: 1 }}
                    value={selectedPort}
                    onChange={e => handlePortChange(e.target.value)}>
              <option value="">Vyberte MIDI port…</option>
              {ports.map(p => <option key={p} value={p}>{p}</option>)}
            </select>
          </div>
          <div style={{ fontSize: 11, color: 'var(--t-muted)' }}>
            Port se používá pro přehrávání not a SysEx upload.
            Změna portu se uloží do konfigurace — aktivuje se po restartu backendu.
          </div>
        </div>
      </div>

      {/* SysEx Upload */}
      <div className="panel">
        <div className="panel__header">
          <span className="panel__title">SysEx SET_BANK</span>
        </div>
        <div className="panel__body" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-3)' }}>
          <div style={{ fontSize: 12, color: 'var(--t-secondary)' }}>
            Odešle celou banku do ICR syntetizéru jako chunked SysEx (240B/chunk, 2ms delay).
          </div>
          {!activePath && (
            <div style={{ fontSize: 11, color: 'var(--c-warn)' }}>⚠ Není načtena žádná banka</div>
          )}
          <button
            className="btn btn--accent"
            onClick={handleUpload}
            disabled={!activePath || !configPort || uploading}
            style={{ alignSelf: 'flex-start' }}
          >
            {uploading ? 'Nahrávám…' : '⚡ SysEx → ICR'}
          </button>
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
