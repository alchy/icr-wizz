// views/FileSelector.tsx
// Changelog: 2025-04-14 v0.1 — initial

import React, { useState, useEffect } from 'react'
import { bankApi, configApi } from '../api/client'
import { anchorApi }    from '../api/client'
import { previewSocket } from '../api/client'
import { useBankStore } from '../store/bankStore'
import { useFitStore }  from '../store/fitStore'
import { useAnchorStore } from '../store/anchorStore'
import { useUiStore }   from '../store/uiStore'
import type { BankListItem, AnchorListItem } from '../types'

export const FileSelector: React.FC = () => {
  const [directory, setDirectory]     = useState('')
  const [listing, setListing]         = useState<BankListItem[]>([])
  const [listError, setListError]     = useState('')
  const [loading, setLoading]         = useState(false)
  const [anchorDbs, setAnchorDbs]     = useState<AnchorListItem[]>([])
  const [newDbName, setNewDbName]     = useState('')
  const [showNewDb, setShowNewDb]     = useState(false)

  const { tabs, activeTabPath, addTabs, setActiveTab, closeTab } = useBankStore()
  const { runFit, fetchDetails } = useFitStore()
  const { selectDb, createDb, active: activeDb, loadDatabases, databases } = useAnchorStore()
  const { setStatus } = useUiStore()

  // Načti config, WS, anchor DB, poslední banku — v pořadí
  useEffect(() => {
    let cancelled = false;
    (async () => {
      // 1. WS připojení
      await previewSocket.connect()
      if (cancelled) return

      // 2. Config
      let cfg: Record<string, unknown> = {}
      try { cfg = await configApi.get() } catch { /* */ }
      if (cancelled) return

      const dir = cfg.soundbank_directory
      if (typeof dir === 'string' && dir && !directory) {
        setDirectory(dir)
      }

      // 3. Anchor DB — načti seznam, pak vyber poslední
      await loadDatabases().catch(() => {})
      if (cancelled) return

      const lastAnchor = cfg.last_anchor_db
      if (typeof lastAnchor === 'string' && lastAnchor) {
        await selectDb(lastAnchor).catch(() => {})
      }
      if (cancelled) return

      // 4. Poslední banka
      const lastBank = cfg.last_bank_path
      if (typeof lastBank === 'string' && lastBank) {
        handleLoad(lastBank)
      }
    })()
    return () => { cancelled = true; previewSocket.disconnect() }
  }, [loadDatabases])

  // List souborů v adresáři + exported
  async function handleList() {
    if (!directory.trim()) return
    setListError(''); setLoading(true)
    try {
      const items = await bankApi.list(directory.trim())
      // Přidej exported banky
      try {
        const exported = await bankApi.list('exported')
        const tagged = exported.map(e => ({
          ...e,
          filename: `[exported] ${e.filename}`,
        }))
        setListing([...items, ...tagged])
      } catch {
        setListing(items)
      }
    } catch (e: any) {
      setListError(e.message)
    } finally {
      setLoading(false)
    }
  }

  // Načtení banky
  async function handleLoad(path: string) {
    setLoading(true); setStatus('Načítám banku…')
    try {
      const resp = await bankApi.load([path])
      const newTabs = resp.loaded.map(p => ({
        path: p,
        state: resp.states[p],
      }))
      addTabs(newTabs)
      if (newTabs[0]) {
        setActiveTab(newTabs[0].path)
        // Čti aktuální anchor DB ze store (ne ze stale closure)
        const currentAnchorName = useAnchorStore.getState().active?.name ?? undefined
        // Initial fit + details
        setStatus('Spouštím analýzu…')
        await runFit(newTabs[0].path, currentAnchorName)
        fetchDetails(newTabs[0].path, currentAnchorName)
        // WS init
        previewSocket.send({
          action: 'init',
          payload: {
            bank_path:      newTabs[0].path,
            anchor_db_name: currentAnchorName ?? null,
          },
        })
        setStatus(`Banka načtena: ${newTabs[0].state.instrument_name || newTabs[0].state.source_path.split('/').pop()}`)
      }
      if (resp.errors.length) {
        setStatus(`Varování: ${resp.errors[0]}`)
      }
    } catch (e: any) {
      setStatus(`Chyba: ${e.message}`)
    } finally {
      setLoading(false)
    }
  }

  async function handleCreateDb() {
    if (!newDbName.trim()) return
    await createDb(newDbName.trim())
    setNewDbName(''); setShowNewDb(false)
  }

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--sp-3)',
                  padding: '0 var(--sp-4)', height: '100%', overflow: 'hidden' }}>

      {/* Adresář input */}
      <div style={{ display: 'flex', gap: 'var(--sp-2)', flex: 1, minWidth: 0 }}>
        <input
          className="input"
          style={{ flex: 1 }}
          placeholder="/cesta/k/bance  nebo  C:\SoundBanks\…"
          value={directory}
          onChange={e => setDirectory(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && handleList()}
        />
        <button className="btn" onClick={handleList} disabled={loading}>
          {loading ? '…' : 'Hledat'}
        </button>
      </div>

      {/* Výsledky hledání — dropdown */}
      {listing.length > 0 && (
        <div style={{ position: 'relative' }}>
          <select
            className="select"
            defaultValue=""
            onChange={e => { if (e.target.value) handleLoad(e.target.value) }}
          >
            <option value="" disabled>Vyberte soubor…</option>
            {listing.map(item => (
              <option key={item.path} value={item.path}>
                {item.filename}  ({item.instrument_name || '?'}  {item.midi_range}  {item.note_count}n)
              </option>
            ))}
          </select>
        </div>
      )}

      {listError && (
        <span style={{ color: 'var(--c-outlier)', fontSize: 11 }}>{listError}</span>
      )}

      {/* Taby načtených bank */}
      {tabs.length > 0 && (
        <div style={{ display: 'flex', gap: 2, maxWidth: 360, overflow: 'hidden' }}>
          {tabs.map(tab => {
            const name = tab.state?.instrument_name
              || tab.path.split('/').pop()?.replace('.json', '') || tab.path
            const isActive = tab.path === activeTabPath
            return (
              <div
                key={tab.path}
                onClick={() => setActiveTab(tab.path)}
                style={{
                  display: 'flex', alignItems: 'center', gap: 4,
                  padding: '2px 8px',
                  background: isActive ? 'var(--bg-hover)' : 'transparent',
                  borderRadius: 'var(--r-sm)',
                  border: `1px solid ${isActive ? 'var(--bg-border)' : 'transparent'}`,
                  cursor: 'pointer', fontSize: 11, color: isActive ? 'var(--t-primary)' : 'var(--t-muted)',
                  maxWidth: 140, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                  flexShrink: 0,
                }}
                title={tab.path}
              >
                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{name}</span>
                <span
                  onClick={e => { e.stopPropagation(); closeTab(tab.path) }}
                  style={{ color: 'var(--t-muted)', marginLeft: 2, lineHeight: 1, flexShrink: 0 }}
                >×</span>
              </div>
            )
          })}
        </div>
      )}

      <div className="sep" />

      {/* Anchor DB selector */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--sp-2)', flexShrink: 0 }}>
        <span className="label">Anchor DB</span>
        {showNewDb ? (
          <div style={{ display: 'flex', gap: 4 }}>
            <input
              className="input" style={{ width: 140 }}
              placeholder="název databáze"
              value={newDbName}
              onChange={e => setNewDbName(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleCreateDb()}
              autoFocus
            />
            <button className="btn btn--accent" onClick={handleCreateDb}>✓</button>
            <button className="btn" onClick={() => setShowNewDb(false)}>✕</button>
          </div>
        ) : (
          <>
            <select
              className="select"
              value={activeDb?.name ?? ''}
              onChange={e => {
                if (e.target.value === '__new__') {
                  setShowNewDb(true)
                } else if (e.target.value) {
                  selectDb(e.target.value).then(() => {
                    configApi.patch({ last_anchor_db: e.target.value }).catch(() => {})
                  }).catch(() => {})
                }
              }}
            >
              <option value="">— žádná —</option>
              {databases.map(d => (
                <option key={d.name} value={d.name}>
                  {d.name} ({d.entry_count}⚓)
                </option>
              ))}
              <option value="__new__">+ Nová databáze…</option>
            </select>
            {activeDb && (
              <span className="tag" style={{ color: 'var(--c-anchor)' }}>
                ⚓ {activeDb.entries.length}
              </span>
            )}
          </>
        )}
      </div>
    </div>
  )
}
