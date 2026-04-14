// components/VelocitySelector.tsx — velocity volba + anchor score shortcut

import React from 'react'
import { useUiStore }     from '../store/uiStore'
import { useAnchorStore } from '../store/anchorStore'
import { midiApi }        from '../api/client'

const VEL_LABELS = ['pp', 'p', 'mp', 'mf', 'mf+', 'f', 'ff-', 'ff']

function scoreBg(score: number): string {
  if (score <= 3) return '#2a1515'
  if (score <= 6) return '#2a2010'
  return '#102218'
}
function scoreFg(score: number): string {
  if (score <= 3) return 'var(--c-outlier)'
  if (score <= 6) return 'var(--c-anchor)'
  return 'var(--c-mid)'
}

export const VelocitySelector: React.FC = () => {
  const selectedVel  = useUiStore(s => s.selectedVel)
  const selectedMidi = useUiStore(s => s.selectedMidi)
  const setVelocity  = useUiStore(s => s.setVelocity)

  const addEntry    = useAnchorStore(s => s.addEntry)
  const removeEntry = useAnchorStore(s => s.removeEntry)
  const entryScore  = useAnchorStore(s => s.entryScore)
  const active      = useAnchorStore(s => s.active)

  const currentScore = selectedMidi !== null
    ? entryScore(selectedMidi, selectedVel)
    : -1

  function handleScore(score: number) {
    if (selectedMidi === null || !active) return
    if (score === 0) {
      removeEntry(selectedMidi, selectedVel)
    } else {
      addEntry(selectedMidi, selectedVel, score)
    }
  }

  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'flex-start',
      gap: 10,
      userSelect: 'none',
    }}>
      {/* Velocity kroužky */}
      <div>
        <span style={{
          fontSize: 10, fontWeight: 600, letterSpacing: '0.1em',
          textTransform: 'uppercase', color: 'var(--t-muted)',
          display: 'block', marginBottom: 4,
        }}>vel</span>
        <div style={{ display: 'flex', gap: 5, alignItems: 'center' }}>
          {Array.from({ length: 8 }, (_, vel) => {
            const isActive = vel === selectedVel
            const lightness = 20 + vel * 10
            return (
              <button
                key={vel}
                onClick={() => {
                  setVelocity(vel)
                  if (selectedMidi !== null) {
                    const midiVel = Math.round(1 + (vel / 7) * 126)
                    midiApi.play(selectedMidi, midiVel).catch(() => {})
                  }
                }}
                title={`${VEL_LABELS[vel]} (vel ${vel})`}
                style={{
                  width: 18, height: 18, borderRadius: '50%',
                  background: `hsl(0, 0%, ${lightness}%)`,
                  border: isActive ? '2px solid var(--c-anchor)' : '2px solid transparent',
                  cursor: 'pointer', padding: 0,
                  transition: 'border-color var(--dur-fast)',
                  flexShrink: 0,
                }}
              />
            )
          })}
          <span style={{
            fontFamily: 'var(--font-mono)', fontSize: 11,
            color: 'var(--t-secondary)', minWidth: 20, textAlign: 'center',
          }}>{VEL_LABELS[selectedVel]}</span>
        </div>
      </div>

      {/* Anchor score matice */}
      <div>
        <span style={{
          fontSize: 10, fontWeight: 600, letterSpacing: '0.1em',
          textTransform: 'uppercase', color: 'var(--t-muted)',
          display: 'block', marginBottom: 4,
        }}>anchor</span>
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(5, 1fr)',
          gap: 3,
          opacity: (selectedMidi === null || !active) ? 0.3 : 1,
          pointerEvents: (selectedMidi === null || !active) ? 'none' : 'auto',
        }}>
          {Array.from({ length: 10 }, (_, score) => {
            const isActive = currentScore === score || (score === 0 && currentScore === -1)
            return (
              <button
                key={score}
                onClick={() => handleScore(score)}
                title={score === 0 ? 'Odebrat anchor' : `Anchor score ${score}`}
                style={{
                  width: 22, height: 22, borderRadius: 3,
                  background: isActive ? scoreBg(score) : 'var(--bg-card)',
                  border: isActive
                    ? `1.5px solid ${scoreFg(score)}`
                    : '1px solid var(--bg-border)',
                  color: isActive ? scoreFg(score) : 'var(--t-muted)',
                  fontFamily: 'var(--font-mono)', fontSize: 11, fontWeight: 600,
                  cursor: 'pointer', padding: 0,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  transition: 'all var(--dur-fast)',
                }}
              >
                {score}
              </button>
            )
          })}
        </div>
      </div>
    </div>
  )
}
