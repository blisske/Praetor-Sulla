import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../lib/auth.jsx'

const GOLD     = '#C8922A'
const GOLD_LT  = '#E8B84B'
const NAVY     = '#0b1526'

function PraetorMark({ size = 28 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 54 54" fill="none" style={{ flexShrink: 0 }}>
      <defs>
        <linearGradient id="pgL" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%"   stopColor={GOLD_LT} />
          <stop offset="55%"  stopColor={GOLD} />
          <stop offset="100%" stopColor="#6B4C10" />
        </linearGradient>
      </defs>
      <rect x="14" y="8" width="5" height="38" rx="2" fill="url(#pgL)" />
      <path d="M18 8 Q40 8 40 19 Q40 30 18 30"
        stroke="url(#pgL)" strokeWidth="4.5" fill="none" strokeLinecap="round" />
      <path d="M10 34 C14 28,20 24,28 18" stroke={GOLD_LT} strokeWidth="1.8"
        fill="none" strokeLinecap="round" opacity="0.9" />
      <path d="M8 38 C13 31,21 26,30 20" stroke={GOLD} strokeWidth="1.3"
        fill="none" strokeLinecap="round" opacity="0.6" />
    </svg>
  )
}

// Large decorative candlesticks for the right panel
function DramaticCandles() {
  const candles = [
    { x: 4,  bodyY: 55, bodyH: 30, wickT: 28, wickB: 90,  bull: false, op: 0.18 },
    { x: 14, bodyY: 30, bodyH: 45, wickT: 12, wickB: 85,  bull: true,  op: 0.28 },
    { x: 24, bodyY: 50, bodyH: 25, wickT: 38, wickB: 82,  bull: false, op: 0.22 },
    { x: 34, bodyY: 20, bodyH: 55, wickT:  5, wickB: 88,  bull: true,  op: 0.40 },
    { x: 44, bodyY: 40, bodyH: 35, wickT: 25, wickB: 92,  bull: false, op: 0.20 },
    { x: 54, bodyY: 15, bodyH: 65, wickT:  2, wickB: 90,  bull: true,  op: 0.55 },
    { x: 64, bodyY: 45, bodyH: 30, wickT: 30, wickB: 86,  bull: false, op: 0.25 },
    { x: 74, bodyY: 10, bodyH: 72, wickT:  0, wickB: 95,  bull: true,  op: 0.70 },
    { x: 84, bodyY: 35, bodyH: 40, wickT: 20, wickB: 88,  bull: false, op: 0.30 },
    { x: 94, bodyY:  5, bodyH: 80, wickT:  0, wickB: 98,  bull: true,  op: 0.85 },
  ]
  return (
    <svg viewBox="0 0 100 100" preserveAspectRatio="none"
      style={{ position: 'absolute', inset: 0, width: '100%', height: '100%' }}>
      {candles.map((c, i) => (
        <g key={i} opacity={c.op}>
          {/* Wick */}
          <line
            x1={c.x + 3.5} y1={c.wickT}
            x2={c.x + 3.5} y2={c.wickB}
            stroke={GOLD} strokeWidth="0.8" />
          {/* Body */}
          <rect
            x={c.x} y={c.bodyY} width={7} height={c.bodyH}
            fill={c.bull ? GOLD : 'none'}
            stroke={GOLD} strokeWidth="0.6"
            rx="0.3" />
        </g>
      ))}
    </svg>
  )
}

export default function Login() {
  const { login }   = useAuth()
  const navigate    = useNavigate()
  const [form, setForm]     = useState({ username: '', password: '' })
  const [error, setError]   = useState('')
  const [loading, setLoading] = useState(false)
  const [showPw, setShowPw] = useState(false)

  const submit = async e => {
    e.preventDefault()
    setLoading(true); setError('')
    try { await login(form.username, form.password); navigate('/') }
    catch { setError('Invalid credentials') }
    finally { setLoading(false) }
  }

  return (
    <div style={{
      display: 'flex', minHeight: '100vh',
      fontFamily: "'Helvetica Neue', Arial, sans-serif",
    }}>

      <style>{`
        .lf-input {
          width: 100%; box-sizing: border-box;
          background: #fff;
          border: 1.5px solid #e2e6ec;
          border-radius: 8px;
          padding: 13px 16px;
          color: #111827; font-size: 14px; outline: none;
          transition: border-color 0.2s, box-shadow 0.2s;
          font-family: inherit;
        }
        .lf-input::placeholder { color: #adb5bd; }
        .lf-input:focus {
          border-color: ${GOLD};
          box-shadow: 0 0 0 3px rgba(200,146,42,0.12);
        }
        .lf-btn {
          width: 100%;
          background: ${NAVY};
          border: none; border-radius: 8px;
          padding: 14px;
          color: #fff; font-size: 14px; font-weight: 600;
          letter-spacing: 0.04em; cursor: pointer;
          transition: background 0.2s, transform 0.1s;
          font-family: inherit;
          position: relative; overflow: hidden;
        }
        .lf-btn::after {
          content: '';
          position: absolute; inset: 0;
          background: linear-gradient(135deg, rgba(200,146,42,0.15) 0%, transparent 60%);
          pointer-events: none;
        }
        .lf-btn:hover:not(:disabled) {
          background: #14233e;
          transform: translateY(-1px);
        }
        .lf-btn:disabled { opacity: 0.55; cursor: not-allowed; }
        .lf-label {
          display: block;
          font-size: 12px; font-weight: 600;
          color: #6b7280; letter-spacing: 0.06em;
          text-transform: uppercase; margin-bottom: 7px;
        }
        .pw-eye {
          position: absolute; right: 14px; top: 50%;
          transform: translateY(-50%);
          background: none; border: none; cursor: pointer;
          color: #adb5bd; font-size: 15px; line-height: 1; padding: 0;
        }
        .pw-eye:hover { color: ${GOLD}; }
      `}</style>

      {/* ── LEFT: form panel ── */}
      <div style={{
        flex: '0 0 42%', display: 'flex', flexDirection: 'column',
        background: '#f8f9fb',
        padding: '0 56px',
        justifyContent: 'center',
      }}>

        {/* Wordmark */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '14px', marginBottom: '52px' }}>
          <PraetorMark size={44} />
          <div>
            <span style={{
              fontSize: '26px', fontWeight: 800, letterSpacing: '0.18em',
              color: NAVY, lineHeight: 1,
            }}>PRAETOR</span>
            <span style={{
              display: 'block', fontSize: '12px', letterSpacing: '0.16em',
              color: GOLD, marginTop: '4px',
            }}>SULLA · TRADFI</span>
          </div>
        </div>

        {/* Heading */}
        <div style={{ marginBottom: '36px' }}>
          <h1 style={{ fontSize: '28px', fontWeight: 700, color: '#111827',
            margin: '0 0 8px', letterSpacing: '-0.02em' }}>
            Welcome back
          </h1>
          <p style={{ fontSize: '14px', color: '#6b7280', margin: 0 }}>
            Sign in to your Sulla trading dashboard.
          </p>
        </div>

        {/* Form */}
        <form onSubmit={submit} style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>

          {error && (
            <div style={{
              background: '#fef2f2', border: '1.5px solid #fecaca',
              color: '#b91c1c', fontSize: '13px', borderRadius: '8px',
              padding: '11px 14px',
            }}>{error}</div>
          )}

          <div>
            <label className="lf-label">Username / Account ID</label>
            <input className="lf-input" type="text"
              value={form.username} placeholder="Enter account ID"
              onChange={e => setForm(f => ({ ...f, username: e.target.value }))}
              autoComplete="username" required />
          </div>

          <div>
            <label className="lf-label">Password</label>
            <div style={{ position: 'relative' }}>
              <input className="lf-input" type={showPw ? 'text' : 'password'}
                value={form.password} placeholder="Enter access key"
                onChange={e => setForm(f => ({ ...f, password: e.target.value }))}
                autoComplete="current-password" required
                style={{ paddingRight: '44px' }} />
              <button type="button" className="pw-eye"
                onClick={() => setShowPw(v => !v)}>
                {showPw ? '🙈' : '👁'}
              </button>
            </div>
          </div>

          <button type="submit" disabled={loading} className="lf-btn"
            style={{ marginTop: '4px' }}>
            {loading ? 'Authenticating…' : 'Sign In →'}
          </button>
        </form>

        {/* Footer */}
        <p style={{
          marginTop: '48px', fontSize: '11px', color: '#9ca3af',
          lineHeight: 1.7,
        }}>
          Personal autonomous trading system. All trading involves risk of loss.<br />
          Past performance does not guarantee future results.
        </p>
      </div>

      {/* ── RIGHT: branded visual panel ── */}
      <div style={{
        flex: 1, position: 'relative', overflow: 'hidden',
        background: `linear-gradient(160deg, #0f1e38 0%, ${NAVY} 40%, #060d18 100%)`,
      }}>

        {/* Ambient glow behind candles */}
        <div style={{
          position: 'absolute', bottom: '-10%', left: '0', right: '0',
          height: '70%',
          background: `radial-gradient(ellipse at 50% 100%, rgba(200,146,42,0.18) 0%, transparent 70%)`,
          pointerEvents: 'none',
        }} />

        {/* Dramatic candlesticks */}
        <DramaticCandles />

        {/* Top-left label */}
        <div style={{
          position: 'absolute', top: '36px', left: '40px',
          fontSize: '13px', fontWeight: 600, letterSpacing: '0.18em',
          color: 'rgba(200,146,42,0.70)', textTransform: 'uppercase',
        }}>
          Praetor
        </div>

        {/* Bottom text overlay */}
        <div style={{
          position: 'absolute', bottom: '52px', left: '40px', right: '40px',
        }}>
          <h2 style={{
            fontSize: '32px', fontWeight: 700, color: '#fff',
            margin: '0 0 12px', lineHeight: 1.2, letterSpacing: '-0.02em',
          }}>
            Autonomous FX<br />intelligence.
          </h2>
          <p style={{
            fontSize: '14px', color: 'rgba(255,255,255,0.55)',
            margin: 0, lineHeight: 1.6, maxWidth: '320px',
          }}>
            Multi-paradigm signal engine. AI consensus layer.
            Session-aware execution. Disciplined compounding.
          </p>

          {/* Gold accent line */}
          <div style={{
            marginTop: '24px', width: '48px', height: '3px',
            background: `linear-gradient(90deg, ${GOLD}, transparent)`,
            borderRadius: '2px',
          }} />
        </div>
      </div>

    </div>
  )
}
