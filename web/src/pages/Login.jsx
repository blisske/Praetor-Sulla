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
function FoundationMark({ size = 28 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 54 54" fill="none" style={{ flexShrink: 0 }}>
      <defs>
        <linearGradient id="ionicGradLogin" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%"   stopColor="#93C5FD" />
          <stop offset="55%"  stopColor="#3B82F6" />
          <stop offset="100%" stopColor="#1D4ED8" />
        </linearGradient>
      </defs>
      <rect x="2"  y="3"  width="50" height="3"  fill="url(#ionicGradLogin)" />
      <rect x="10" y="6"  width="34" height="12" fill="url(#ionicGradLogin)" />
      <circle cx="10" cy="12" r="9" fill="url(#ionicGradLogin)" />
      <circle cx="10" cy="12" r="5" fill="none" stroke="#1D4ED8" strokeWidth="2" />
      <circle cx="10" cy="12" r="2" fill="url(#ionicGradLogin)" />
      <circle cx="44" cy="12" r="9" fill="url(#ionicGradLogin)" />
      <circle cx="44" cy="12" r="5" fill="none" stroke="#1D4ED8" strokeWidth="2" />
      <circle cx="44" cy="12" r="2" fill="url(#ionicGradLogin)" />
      <rect x="13" y="20" width="28" height="2.5" fill="#1D4ED8" opacity="0.65" />
      <rect x="19" y="24" width="16" height="3"  fill="#1D4ED8" />
      <rect x="17" y="27" width="20" height="23" fill="url(#ionicGradLogin)" />
      <line x1="20" y1="27" x2="20" y2="50" stroke="#1D4ED8" strokeWidth="1" opacity="0.55" />
      <line x1="24" y1="27" x2="24" y2="50" stroke="#1D4ED8" strokeWidth="1" opacity="0.55" />
      <line x1="27" y1="27" x2="27" y2="50" stroke="#1D4ED8" strokeWidth="1" opacity="0.55" />
      <line x1="30" y1="27" x2="30" y2="50" stroke="#1D4ED8" strokeWidth="1" opacity="0.55" />
      <line x1="34" y1="27" x2="34" y2="50" stroke="#1D4ED8" strokeWidth="1" opacity="0.55" />
    </svg>
  )
}

// Multi-pair chart trace — seven smooth Bezier curves in graduated blues,
// one per FX major. Two prominent lines (EUR/USD, USD/JPY) get a soft
// glow + a "current price" dot at the right edge. Layered BEHIND the
// CurrencyGrid text labels so both render together: the labels name the
// pair, the curves show the brand-blue "tape" rolling underneath.
function MultiPairChartTrace() {
  const lines = [
    // Back layer — deepest blues, lowest opacity. The macro tape.
    { name: 'NZD/USD', d: 'M 0 38 C 18 36, 32 42, 50 39 S 78 33, 100 30',
      color: '#1E3A8A', width: 0.6, opacity: 0.30, prominent: false },
    { name: 'USD/CAD', d: 'M 0 72 C 16 68, 30 73, 48 70 S 76 78, 100 75',
      color: '#1E40AF', width: 0.7, opacity: 0.35, prominent: false },
    // Mid layer — deep but readable blues.
    { name: 'AUD/USD', d: 'M 0 56 C 14 60, 28 51, 44 58 S 70 65, 100 60',
      color: '#2563EB', width: 0.9, opacity: 0.50, prominent: false },
    { name: 'USD/CHF', d: 'M 0 28 C 18 30, 36 24, 54 28 S 80 35, 100 32',
      color: '#3B82F6', width: 0.9, opacity: 0.55, prominent: false },
    // Front layer — brand-blue mid-tone. The pairs in motion.
    { name: 'GBP/USD', d: 'M 0 64 C 14 58, 30 70, 48 62 S 76 50, 100 45',
      color: '#60A5FA', width: 1.1, opacity: 0.75, prominent: false },
    // Hero layer — the two prominent lines with glow + endpoint dots.
    { name: 'EUR/USD', d: 'M 0 48 C 18 44, 36 52, 56 44 S 82 38, 100 33',
      color: '#06B6D4', width: 1.5, opacity: 0.85, prominent: true },
    { name: 'USD/JPY', d: 'M 0 18 C 16 22, 30 16, 48 20 S 76 12, 100 18',
      color: '#3B82F6', width: 1.5, opacity: 0.90, prominent: true },
  ]
  const endpoints = {
    'NZD/USD': { x: 100, y: 30 }, 'USD/CAD': { x: 100, y: 75 },
    'AUD/USD': { x: 100, y: 60 }, 'USD/CHF': { x: 100, y: 32 },
    'GBP/USD': { x: 100, y: 45 }, 'EUR/USD': { x: 100, y: 33 },
    'USD/JPY': { x: 100, y: 18 },
  }
  return (
    <svg viewBox="0 0 100 100" preserveAspectRatio="none"
      style={{ position: 'absolute', inset: 0, width: '100%', height: '100%',
                pointerEvents: 'none' }}>
      <defs>
        <filter id="line-glow" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="0.8" result="blurred" />
          <feMerge>
            <feMergeNode in="blurred" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
      </defs>
      {lines.map((line, i) => {
        const ep = endpoints[line.name] || { x: 100, y: 50 }
        return (
          <g key={i} opacity={line.opacity}>
            <path
              d={line.d}
              stroke={line.color}
              strokeWidth={line.width}
              fill="none"
              strokeLinecap="round"
              strokeLinejoin="round"
              vectorEffect="non-scaling-stroke"
              filter={line.prominent ? 'url(#line-glow)' : undefined}
            />
            {line.prominent && (
              <>
                <circle cx={ep.x} cy={ep.y} r={1.2} fill={line.color}
                        filter="url(#line-glow)" />
                <circle cx={ep.x} cy={ep.y} r={0.5} fill="#fff" opacity={0.9} />
              </>
            )}
          </g>
        )
      })}
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
        {/* Chart-trace lines render BEHIND the text labels so the pair
            names sit on top of the curves. Order matters: this must come
            before <CurrencyGrid /> in the JSX tree. */}
        <MultiPairChartTrace />
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
