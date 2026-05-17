import { useState } from 'react'
import { HelpCircle } from 'lucide-react'

export default function HelpTip({ text, position = 'top', width = 220 }) {
  const [show, setShow] = useState(false)
  const placement = position === 'bottom'
    ? { top: '130%' }
    : { bottom: '130%' }
  return (
    <span style={{ position: 'relative', display: 'inline-flex', alignItems: 'center', marginLeft: 4 }}>
      <HelpCircle
        size={11}
        style={{ color: 'var(--text-dim)', cursor: 'pointer', flexShrink: 0 }}
        onMouseEnter={() => setShow(true)}
        onMouseLeave={() => setShow(false)}
      />
      {show && (
        <span style={{
          position: 'absolute', ...placement, left: '50%', transform: 'translateX(-50%)',
          background: 'var(--bg-elevated)', border: '1px solid var(--border)',
          color: 'var(--text-sub)', fontSize: 11, lineHeight: 1.5,
          padding: '6px 10px', borderRadius: 6, width, zIndex: 100,
          whiteSpace: 'normal', pointerEvents: 'none',
          boxShadow: '0 4px 16px rgba(0,0,0,0.4)',
        }}>
          {text}
        </span>
      )}
    </span>
  )
}
