// views/ExtractPanel.tsx — extrakce WAV bank → JSON parametry
// Spouští icr-engine pipeline, zobrazuje live progress, umožní load výsledku.

import React, { useEffect, useRef, useState } from 'react'
import { extractApi, bankApi, midiApi, configApi } from '../api/client'
import type { ExtractJob } from '../api/client'
import { useBankStore } from '../store/bankStore'
import { useUiStore }   from '../store/uiStore'

interface JobState extends ExtractJob {}

export const ExtractPanel: React.FC = () => {
  // Config
  const [bankDir, setBankDir]       = useState('')
  const [srTag, setSrTag]           = useState('f48')
  const [skipEq, setSkipEq]         = useState(false)
  const [skipIr, setSkipIr]         = useState(false)
  const [skipPanCal, setSkipPanCal] = useState(false)

  // Jobs
  const [jobs, setJobs]       = useState<JobState[]>([])
  const [starting, setStarting] = useState(false)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const setStatus = useUiStore(s => s.setStatus)
  const { loadBank } = useBankStore()

  // Load defaults from config
  useEffect(() => {
    configApi.get().then(cfg => {
      const ext = (cfg as any).extract
      if (ext?.default_bank_dir) setBankDir(ext.default_bank_dir)
      if (ext?.default_sr_tag) setSrTag(ext.default_sr_tag)
    }).catch(() => {})
  }, [])

  // Poll status every 2s when there are running jobs
  useEffect(() => {
    function poll() {
      extractApi.status().then(r => {
        if (r.jobs) setJobs(r.jobs)
        // Update StatusBar with latest running job
        const running = r.jobs?.find(j => j.status === 'running')
        if (running) {
          setStatus(`Extract ${running.bank_name}: Step ${running.step}/${running.step_total} — ${running.step_label} (${running.elapsed_s}s)`)
        }
      }).catch(() => {})
    }
    poll()
    pollRef.current = setInterval(poll, 2000)
    return () => { if (pollRef.current) clearInterval(pollRef.current) }
  }, [setStatus])

  async function handleStart() {
    if (!bankDir.trim()) return
    setStarting(true)
    setStatus(`Spouštím extrakci ${bankDir}…`)
    try {
      const r = await extractApi.start(bankDir.trim(), srTag, { skipEq, skipIr, skipPanCal })
      setStatus(`Extrakce spuštěna: ${r.job_id}`)
      // Save last used bank_dir to config
      configApi.patch({ extract: { default_bank_dir: bankDir.trim(), default_sr_tag: srTag } }).catch(() => {})
    } catch (e: any) {
      setStatus(`Chyba: ${e.message}`)
    } finally {
      setStarting(false)
    }
  }

  async function handleCancel(jobId: string) {
    try {
      await extractApi.cancel(jobId)
      setStatus('Extrakce zrušena')
    } catch (e: any) {
      setStatus(`Chyba: ${e.message}`)
    }
  }

  async function handleLoad(path: string) {
    try {
      setStatus(`Načítám ${path}…`)
      await loadBank(path)
      setStatus(`Banka načtena: ${path}`)
      useUiStore.getState().setPanelView('relation')
    } catch (e: any) {
      setStatus(`Chyba: ${e.message}`)
    }
  }

  async function handleSysEx(path: string) {
    setStatus('SysEx upload…')
    try {
      const r = await midiApi.uploadBank(path)
      setStatus(`SysEx OK: ${r.chunks_sent}/${r.chunks_total} chunks`)
    } catch (e: any) {
      setStatus(`SysEx chyba: ${e.message}`)
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-4)',
                  padding: 'var(--sp-4)', height: '100%', overflowY: 'auto' }}
         className="animate-in">

      {/* Config panel */}
      <div className="panel">
        <div className="panel__header">
          <span className="panel__title">Extrakce WAV banky</span>
        </div>
        <div className="panel__body" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-3)' }}>
          <div style={{ display: 'flex', gap: 'var(--sp-3)', alignItems: 'center' }}>
            <span className="label" style={{ minWidth: 60 }}>WAV dir</span>
            <input className="input" style={{ flex: 1 }} placeholder="/path/to/wav-bank"
                   value={bankDir} onChange={e => setBankDir(e.target.value)} />
          </div>
          <div style={{ display: 'flex', gap: 'var(--sp-4)', alignItems: 'center', flexWrap: 'wrap' }}>
            <div style={{ display: 'flex', gap: 'var(--sp-2)', alignItems: 'center' }}>
              <span className="label">SR</span>
              <select className="select" value={srTag} onChange={e => setSrTag(e.target.value)}>
                <option value="f48">48 kHz</option>
                <option value="f44">44.1 kHz</option>
              </select>
            </div>
            <label style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 11, cursor: 'pointer' }}>
              <input type="checkbox" className="check" checked={skipEq} onChange={e => setSkipEq(e.target.checked)} />
              Skip EQ
            </label>
            <label style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 11, cursor: 'pointer' }}>
              <input type="checkbox" className="check" checked={skipIr} onChange={e => setSkipIr(e.target.checked)} />
              Skip IR
            </label>
            <label style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 11, cursor: 'pointer' }}>
              <input type="checkbox" className="check" checked={skipPanCal} onChange={e => setSkipPanCal(e.target.checked)} />
              Skip Pan Cal
            </label>
            <button className="btn btn--accent" onClick={handleStart}
                    disabled={!bankDir.trim() || starting}
                    style={{ marginLeft: 'auto' }}>
              {starting ? 'Spouštím…' : '▶ Spustit extrakci'}
            </button>
          </div>
        </div>
      </div>

      {/* Jobs */}
      {jobs.length === 0 && (
        <div style={{ textAlign: 'center', color: 'var(--t-muted)', fontSize: 12, padding: 'var(--sp-4)' }}>
          Žádné extrakce. Zadejte cestu k WAV bance a spusťte.
        </div>
      )}

      {jobs.map(job => (
        <div key={job.job_id} className="panel">
          <div className="panel__header">
            <span className="panel__title">{job.bank_name}</span>
            <span style={{ marginLeft: 8, fontSize: 10, color: 'var(--t-muted)', fontFamily: 'var(--font-mono)' }}>
              {job.job_id}
            </span>
            <span style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 6 }}>
              <span className={`status-dot status-dot--${
                job.status === 'running' ? 'warn' :
                job.status === 'done' ? 'ok' :
                job.status === 'error' ? 'crit' : 'off'
              }`} />
              <span style={{ fontSize: 11, color: 'var(--t-secondary)' }}>
                {job.status === 'running' ? `${job.step}/${job.step_total}` : job.status}
              </span>
              <span style={{ fontSize: 10, color: 'var(--t-muted)', fontFamily: 'var(--font-mono)' }}>
                {job.elapsed_s}s
              </span>
            </span>
          </div>
          <div className="panel__body" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-2)' }}>

            {/* Progress bar */}
            {job.status === 'running' && (
              <div style={{ position: 'relative', height: 6, background: 'var(--bg-border)', borderRadius: 3 }}>
                <div style={{
                  position: 'absolute', top: 0, left: 0, height: '100%', borderRadius: 3,
                  width: `${Math.round((job.step / job.step_total) * 100)}%`,
                  background: 'var(--c-anchor)',
                  transition: 'width 300ms',
                }} />
              </div>
            )}

            {/* Step label */}
            <div style={{ fontSize: 12, color: 'var(--t-primary)' }}>
              {job.step_label}
            </div>

            {/* Error */}
            {job.error && (
              <div style={{ fontSize: 11, color: 'var(--c-crit)', fontFamily: 'var(--font-mono)' }}>
                {job.error}
              </div>
            )}

            {/* Actions */}
            <div style={{ display: 'flex', gap: 'var(--sp-2)', flexWrap: 'wrap' }}>
              {job.status === 'running' && (
                <button className="btn" onClick={() => handleCancel(job.job_id)}
                        style={{ fontSize: 11 }}>
                  ✕ Zrušit
                </button>
              )}
              {job.status === 'done' && job.output_paths.map((p, i) => (
                <React.Fragment key={i}>
                  <button className="btn btn--accent" onClick={() => handleLoad(p)}
                          style={{ fontSize: 11 }}>
                    Načíst: {p.split('/').pop()}
                  </button>
                  <button className="btn" onClick={() => handleSysEx(p)}
                          style={{ fontSize: 11 }}>
                    ⚡ SysEx
                  </button>
                </React.Fragment>
              ))}
            </div>

            {/* Log tail */}
            <details style={{ fontSize: 10 }}>
              <summary style={{ cursor: 'pointer', color: 'var(--t-muted)', userSelect: 'none' }}>
                Log ({job.log_tail.length} řádků)
              </summary>
              <div style={{
                maxHeight: 200, overflowY: 'auto', padding: 'var(--sp-2)',
                background: 'var(--bg-base)', borderRadius: 'var(--r-sm)',
                fontFamily: 'var(--font-mono)', fontSize: 10, marginTop: 4,
              }}>
                {job.log_tail.map((line, i) => (
                  <div key={i} style={{
                    color: /error|traceback|failed/i.test(line) ? 'var(--c-crit)' :
                           /step \d+\/\d+/i.test(line) ? 'var(--c-anchor)' :
                           'var(--t-secondary)',
                    padding: '1px 0',
                  }}>
                    {line}
                  </div>
                ))}
              </div>
            </details>
          </div>
        </div>
      ))}
    </div>
  )
}
