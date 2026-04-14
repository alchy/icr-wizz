// components/VelocitySelector.tsx — globální volba velocity vrstvy (8 kroužků)

import React from 'react'
import { useUiStore } from '../store/uiStore'

const VEL_LABELS = ['pp', 'p', 'mp', 'mf', 'mf+', 'f', 'ff-', 'ff']

export const VelocitySelector: React.FC = () => {
  const selectedVel = useUiStore(s => s.selectedVel)
  const setVelocity = useUiStore(s => s.setVelocity)

  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      gap: 6,
      userSelect: 'none',
    }}>
      <span style={{
        fontSize: 10,
        fontWeight: 600,
        letterSpacing: '0.1em',
        textTransform: 'uppercase',
        color: 'var(--t-muted)',
        marginRight: 2,
      }}>vel</span>
      {Array.from({ length: 8 }, (_, vel) => {
        const active = vel === selectedVel
        // Tmavší = nižší velocity, světlejší = vyšší
        const lightness = 20 + vel * 10  // 20% → 90%
        return (
          <button
            key={vel}
            onClick={() => setVelocity(vel)}
            title={`${VEL_LABELS[vel]} (vel ${vel})`}
            style={{
              width: 18,
              height: 18,
              borderRadius: '50%',
              background: `hsl(0, 0%, ${lightness}%)`,
              border: active ? '2px solid var(--c-anchor)' : '2px solid transparent',
              cursor: 'pointer',
              padding: 0,
              transition: 'border-color var(--dur-fast)',
              flexShrink: 0,
            }}
          />
        )
      })}
      <span style={{
        fontFamily: 'var(--font-mono)',
        fontSize: 11,
        color: 'var(--t-secondary)',
        minWidth: 20,
        textAlign: 'center',
      }}>{VEL_LABELS[selectedVel]}</span>
    </div>
  )
}
