// components/BankActions.tsx — Export + SysEx upload tlačítka

import React, { useState } from 'react'
import { useBankStore }       from '../store/bankStore'
import { useCorrectionStore } from '../store/correctionStore'
import { useUiStore }         from '../store/uiStore'
import { correctionsApi, exportApi, midiApi } from '../api/client'

export const BankActions: React.FC = () => {
  const bankState  = useBankStore(s => s.activeState())
  const activePath = useBankStore(s => s.activePath())
  const pending    = useCorrectionStore(s => s.pending)
  const setStatus  = useUiStore(s => s.setStatus)
  const [busy, setBusy] = useState(false)
  const [exportedPath, setExportedPath] = useState<string | null>(null)

  if (!activePath || !bankState) return null

  const stem = activePath.split('/').pop()?.replace('.json', '') ?? 'bank'
  const hasCorrections = pending && pending.corrections.length > 0

  async function handleExport() {
    if (!activePath) return
    setBusy(true)
    try {
      if (hasCorrections) {
        // Aplikuj korekce → export korigované banky
        setStatus(`Aplikuji ${pending!.corrections.length} korekcí + export…`)
        const res = await correctionsApi.apply(activePath, pending!)
        setExportedPath(res.output_path)
        setStatus(`Export OK: ${res.output_path} (${res.corrections_applied} korekcí, ${res.notes_affected} not)`)
      } else {
        // Export originální banky
        setStatus('Exportuji banku…')
        const outPath = `exported/${stem}-corrected.json`
        const res = await exportApi.bank(activePath, outPath, false, true)
        setExportedPath(res.path)
        setStatus(`Export OK: ${res.path} (${res.size_kb} kB)`)
      }
    } catch (e: any) {
      setStatus(`Export chyba: ${e.message}`)
    } finally {
      setBusy(false)
    }
  }

  async function handleUpload() {
    const pathToUpload = exportedPath || activePath
    if (!pathToUpload) return
    setBusy(true)
    setStatus('SysEx upload…')
    try {
      const res = await midiApi.uploadBank(pathToUpload)
      setStatus(`SysEx OK: ${res.chunks_sent}/${res.chunks_total} chunks (${Math.round(res.bytes / 1024)} kB)`)
    } catch (e: any) {
      setStatus(`SysEx chyba: ${e.message}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      gap: 4,
      userSelect: 'none',
    }}>
      <button className="btn" onClick={handleExport} disabled={busy}
              style={{ fontSize: 11 }}>
        {busy ? '…' : hasCorrections ? `Export (${pending!.corrections.length})` : 'Export JSON'}
      </button>
      <button className="btn btn--accent" onClick={handleUpload} disabled={busy}
              style={{ fontSize: 11 }}>
        {busy ? '…' : '⚡ SysEx → ICR'}
      </button>
      {exportedPath && (
        <span style={{ fontSize: 9, color: 'var(--t-muted)', maxWidth: 160,
                       overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
              title={exportedPath}>
          {exportedPath.split('/').pop()}
        </span>
      )}
    </div>
  )
}
