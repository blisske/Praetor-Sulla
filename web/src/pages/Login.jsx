import { useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { useAuth } from '../lib/auth.jsx'

const BLUE      = '#3B82F6'
const BLUE_LT   = '#93C5FD'
const NAVY      = '#0b1526'

// Ionic column capital — abacus + connecting band + paired volutes
// (scrolls) on either side + egg-and-dart band + fluted shaft.
// Ionic is the "scholar's" order — graceful, balanced, intermediate
// between Doric's plainness and Corinthian's ornament. Matches FX:
// macro-driven, deliberate, less volatile than crypto.
// Ionic column capital — the signature feature is the pair of spiral
// volutes (ammonite scrolls) that sit on the abacus. The previous
// rendering used two large concentric circles flanking the shaft, which
// at small sizes read as "robot eyes" not "Ionic capital." This version
// places proper spiral paths ON the abacus, centered closer to the
// column shaft so the whole mark reads as one shape.
function FoundationMark({ size = 28 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 54 54" fill="none" style={{ flexShrink: 0 }} xmlns="http://www.w3.org/2000/svg">
      <defs>
        <linearGradient id="ionicGradLogin" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%"   stopColor="#93C5FD" />
          <stop offset="55%"  stopColor="#3B82F6" />
          <stop offset="100%" stopColor="#1D4ED8" />
        </linearGradient>
      </defs>

      {/* Abacus — flat slab on top */}
      <rect x="4" y="3" width="46" height="3" fill="url(#ionicGradLogin)" />

      {/* Echinus — slight downward arc beneath the abacus (the
          "egg-and-dart" decoration zone in a classical Ionic capital) */}
      <path d="M 8 6 Q 27 11 46 6 L 46 7 Q 27 12 8 7 Z" fill="url(#ionicGradLogin)" opacity="0.85" />

      {/* Left volute — spiral that scrolls outward.
          Center at (12, 14), 3 nested arcs of decreasing radius. */}
      <circle cx="12" cy="14" r="6" fill="url(#ionicGradLogin)" />
      <path d="M 12 9 a 5 5 0 1 1 -0.1 0 M 12 11 a 3 3 0 1 0 0.1 0 M 13 14 a 1.2 1.2 0 1 1 -0.1 0"
            stroke="#1D4ED8" strokeWidth="1.3" fill="none" strokeLinecap="round" />
      <circle cx="13" cy="14" r="0.9" fill="#1D4ED8" />

      {/* Right volute — mirror of left, scrolls outward to the right.
          Center at (42, 14). */}
      <circle cx="42" cy="14" r="6" fill="url(#ionicGradLogin)" />
      <path d="M 42 9 a 5 5 0 1 0 0.1 0 M 42 11 a 3 3 0 1 1 -0.1 0 M 41 14 a 1.2 1.2 0 1 0 0.1 0"
            stroke="#1D4ED8" strokeWidth="1.3" fill="none" strokeLinecap="round" />
      <circle cx="41" cy="14" r="0.9" fill="#1D4ED8" />

      {/* Cushion between volutes (the actual capital body) */}
      <rect x="18" y="11" width="18" height="8" fill="url(#ionicGradLogin)" />

      {/* Necking — thin ring separating capital from shaft */}
      <rect x="16" y="19" width="22" height="2" fill="#1D4ED8" />

      {/* Column shaft — centered, fluted */}
      <rect x="17" y="21" width="20" height="29" fill="url(#ionicGradLogin)" />
      <line x1="20" y1="21" x2="20" y2="50" stroke="#1D4ED8" strokeWidth="0.8" opacity="0.55" />
      <line x1="24" y1="21" x2="24" y2="50" stroke="#1D4ED8" strokeWidth="0.8" opacity="0.55" />
      <line x1="27" y1="21" x2="27" y2="50" stroke="#1D4ED8" strokeWidth="0.8" opacity="0.55" />
      <line x1="30" y1="21" x2="30" y2="50" stroke="#1D4ED8" strokeWidth="0.8" opacity="0.55" />
      <line x1="34" y1="21" x2="34" y2="50" stroke="#1D4ED8" strokeWidth="0.8" opacity="0.55" />
    </svg>
  )
}

// Decorative currency tickers in the right panel — FX vibe (vs Doric's
// candlesticks for equities, Corinthian's coin pile for crypto).
function CurrencyGrid() {
  const pairs = [
    { x: 8,  y: 18, label: 'EUR/USD', op: 0.85 },
    { x: 60, y: 15, label: 'GBP/JPY', op: 0.55 },
    { x: 28, y: 38, label: 'AUD/NZD', op: 0.70 },
    { x: 70, y: 42, label: 'USD/CHF', op: 0.30 },
    { x: 12, y: 58, label: 'USD/JPY', op: 0.45 },
    { x: 52, y: 65, label: 'EUR/GBP', op: 0.65 },
    { x: 24, y: 78, label: 'USD/CAD', op: 0.40 },
    { x: 68, y: 84, label: 'EUR/CHF', op: 0.55 },
  ]
  return (
    <svg viewBox="0 0 100 100" preserveAspectRatio="none"
      style={{ position: 'absolute', inset: 0, width: '100%', height: '100%' }}>
      {pairs.map((p, i) => (
        <text key={i} x={p.x} y={p.y}
          fill={BLUE} opacity={p.op}
          fontSize="3.5" fontFamily="ui-monospace, Menlo, monospace"
          fontWeight="600" letterSpacing="0.05em">
          {p.label}
        </text>
      ))}
      <g stroke={BLUE} strokeWidth="0.15" opacity="0.18">
        <line x1="0" y1="25"  x2="100" y2="25" />
        <line x1="0" y1="50"  x2="100" y2="50" />
        <line x1="0" y1="75"  x2="100" y2="75" />
        <line x1="25"  y1="0" x2="25"  y2="100" />
        <line x1="50"  y1="0" x2="50"  y2="100" />
        <line x1="75"  y1="0" x2="75"  y2="100" />
      </g>
    </svg>
  )
}

export default function Login() {
  const { login, completeTotpLogin } = useAuth()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const resetOk   = searchParams.get('reset')   === 'ok'
  const deletedOk = searchParams.get('deleted') === 'ok'

  const [form, setForm]     = useState({ email: '', password: '' })
  const [error, setError]   = useState('')
  const [loading, setLoading] = useState(false)
  const [showPw, setShowPw] = useState(false)
  const [partialToken, setPartialToken]       = useState(null)
  const [codesRemaining, setCodesRemaining]   = useState(null)
  const [totpCode, setTotpCode]               = useState('')

  const submit = async e => {
    e.preventDefault()
    setLoading(true); setError('')
    try {
      const result = await login(form.email, form.password)
      if (result.totp_required) {
        setPartialToken(result.partial_token)
        setCodesRemaining(result.recovery_codes_remaining)
        setLoading(false)
        return
      }
      navigate('/')
    } catch (e) {
      const detail = e?.response?.data?.detail
      setError(detail || 'Invalid email or password.')
      setLoading(false)
    }
  }

  const submitTotp = async e => {
    e.preventDefault()
    setLoading(true); setError('')
    try {
      await completeTotpLogin(partialToken, totpCode.trim())
      navigate('/')
    } catch (e) {
      setError(e?.response?.data?.detail || 'Code did not verify.')
      setLoading(false)
    }
  }

  const cancelTotp = () => {
    setPartialToken(null); setTotpCode(''); setError(''); setLoading(false)
  }

  return (
    <div style={{ display: 'flex', minHeight: '100vh',
                  fontFamily: "'Helvetica Neue', Arial, sans-serif" }}>

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
          border-color: ${BLUE};
          box-shadow: 0 0 0 3px rgba(59,130,246,0.12);
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
          background: linear-gradient(135deg, rgba(59,130,246,0.18) 0%, transparent 60%);
          pointer-events: none;
        }
        .lf-btn:hover:not(:disabled) { background: #14233e; transform: translateY(-1px); }
        .lf-btn:disabled { opacity: 0.55; cursor: not-allowed; }
        .lf-label {
          display: block;
          font-size: 12px; font-weight: 600;
          color: #6b7280; letter-spacing: 0.06em;
          text-transform: uppercase; margin-bottom: 7px;
        }
        .lf-link { color: ${BLUE}; text-decoration: none; font-weight: 600; }
        .lf-link:hover { text-decoration: underline; }
        .pw-eye {
          position: absolute; right: 14px; top: 50%;
          transform: translateY(-50%);
          background: none; border: none; cursor: pointer;
          color: #adb5bd; font-size: 15px; line-height: 1; padding: 0;
        }
        .pw-eye:hover { color: ${BLUE}; }
      `}</style>

      <div style={{
        flex: '0 0 42%', display: 'flex', flexDirection: 'column',
        background: '#f8f9fb',
        padding: '0 56px',
        justifyContent: 'center',
      }}>

        <div style={{ display: 'flex', alignItems: 'center', gap: '14px', marginBottom: '52px' }}>
          <FoundationMark size={44} />
          <div>
            <span style={{
              fontSize: '26px', fontWeight: 800, letterSpacing: '0.18em',
              color: NAVY, lineHeight: 1,
            }}>FOUNDATION</span>
            <span style={{
              display: 'block', fontSize: '12px', letterSpacing: '0.16em',
              color: BLUE, marginTop: '4px',
            }}>IONIC · FX MAJORS</span>
          </div>
        </div>

        <div style={{ marginBottom: '36px' }}>
          <h1 style={{ fontSize: '28px', fontWeight: 700, color: '#111827',
                       margin: '0 0 8px', letterSpacing: '-0.02em' }}>
            {partialToken ? 'Enter your code' : 'Welcome back'}
          </h1>
          <p style={{ fontSize: '14px', color: '#6b7280', margin: 0 }}>
            {partialToken
              ? 'Enter the 6-digit code from your authenticator app, or a recovery code.'
              : 'Sign in to your Ionic trading dashboard.'}
          </p>
        </div>

        <form onSubmit={partialToken ? submitTotp : submit}
              style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>

          {resetOk && (
            <div style={{ background: '#eff6ff', border: '1.5px solid #bfdbfe',
                          color: '#1d4ed8', fontSize: '13px', borderRadius: '8px',
                          padding: '11px 14px' }}>
              Password updated. Sign in with your new password.
            </div>
          )}
          {deletedOk && (
            <div style={{ background: '#f3f4f6', border: '1.5px solid #d1d5db',
                          color: '#374151', fontSize: '13px', borderRadius: '8px',
                          padding: '11px 14px' }}>
              Your account has been deleted. Thanks for trying Foundation.
            </div>
          )}
          {error && (
            <div style={{ background: '#fef2f2', border: '1.5px solid #fecaca',
                          color: '#b91c1c', fontSize: '13px', borderRadius: '8px',
                          padding: '11px 14px' }}>{error}</div>
          )}

          {partialToken ? (
            <>
              {codesRemaining != null && codesRemaining <= 2 && (
                <div style={{ background: '#fffbeb', border: '1.5px solid #fde68a',
                              color: '#92400e', fontSize: '12px', borderRadius: '8px',
                              padding: '9px 12px' }}>
                  {codesRemaining} recovery code{codesRemaining === 1 ? '' : 's'} left —
                  regenerate after signing in.
                </div>
              )}
              <div>
                <label className="lf-label">2FA code</label>
                <input className="lf-input" type="text"
                  inputMode="numeric" autoComplete="one-time-code" autoFocus
                  value={totpCode} placeholder="123456 or recovery code"
                  onChange={e => setTotpCode(e.target.value)} required
                  style={{ fontFamily: 'ui-monospace, Menlo, monospace',
                           letterSpacing: '0.18em', textAlign: 'center', fontSize: '15px' }} />
              </div>
              <button type="submit" disabled={loading} className="lf-btn"
                style={{ marginTop: '4px' }}>
                {loading ? 'Verifying…' : 'Verify + Sign In →'}
              </button>
              <button type="button" onClick={cancelTotp} disabled={loading}
                style={{ background: 'none', border: 'none', color: '#6b7280',
                         fontSize: '12px', cursor: 'pointer', marginTop: '-8px' }}>
                ← Back to sign-in
              </button>
            </>
          ) : (
            <>
              <div>
                <label className="lf-label">Email</label>
                <input className="lf-input" type="email"
                  value={form.email} placeholder="you@example.com"
                  onChange={e => setForm(f => ({ ...f, email: e.target.value }))}
                  autoComplete="email" required />
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

              <div style={{
                display: 'flex', justifyContent: 'space-between',
                fontSize: '13px', color: '#6b7280', marginTop: '4px',
              }}>
                <Link to="/forgot-password" className="lf-link">Forgot password?</Link>
                <span>
                  No account?{' '}
                  <Link to="/signup" className="lf-link">Create one</Link>
                </span>
              </div>

              <div style={{
                marginTop: '12px', paddingTop: '16px',
                borderTop: '1px solid #e5e7eb',
                fontSize: '13px', textAlign: 'center', color: '#6b7280',
              }}>
                <Link to="/demo" style={{ color: BLUE, textDecoration: 'none' }}>
                  Just looking?{' '}
                  <span style={{ fontWeight: 600 }}>Try the public demo →</span>
                </Link>
              </div>
            </>
          )}
        </form>

        <p style={{
          marginTop: '48px', fontSize: '11px', color: '#9ca3af',
          lineHeight: 1.7,
        }}>
          Personal autonomous trading system. All trading involves risk of loss.<br />
          Past performance does not guarantee future results.
        </p>
      </div>

      <div style={{
        flex: 1, position: 'relative', overflow: 'hidden',
        background: `linear-gradient(160deg, #0f1e38 0%, ${NAVY} 40%, #060d18 100%)`,
      }}>
        <div style={{
          position: 'absolute', bottom: '-10%', left: '0', right: '0',
          height: '70%',
          background: `radial-gradient(ellipse at 50% 100%, rgba(59,130,246,0.18) 0%, transparent 70%)`,
          pointerEvents: 'none',
        }} />
        <CurrencyGrid />
        <div style={{
          position: 'absolute', top: '36px', left: '40px',
          fontSize: '13px', fontWeight: 600, letterSpacing: '0.18em',
          color: 'rgba(59,130,246,0.70)', textTransform: 'uppercase',
        }}>
          Foundation
        </div>
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
            Seven majors. 24/5 macro tape. Multi-paradigm signal engine
            with AI consensus. Patient compounding, no leverage.
          </p>
          <div style={{
            marginTop: '24px', width: '48px', height: '3px',
            background: `linear-gradient(90deg, ${BLUE}, transparent)`,
            borderRadius: '2px',
          }} />
        </div>
      </div>
    </div>
  )
}
