import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useAuth } from '../lib/auth.jsx'

const GOLD     = '#C8922A'
const GOLD_LT  = '#E8B84B'
const NAVY     = '#0b1526'
const BLUE     = '#3B82F6'   // Sulla brand accent
const BLUE_LT  = '#60A5FA'

// Ionic column capital — same glyph as the sidebar, scaled for the login wordmark.
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

// Currency-symbol pattern overlay for the right panel.
// Scattered glyphs of the 7 majors at varying size + opacity + rotation,
// rendered as background ambience behind the candles. Distinct from Anton's
// coins / Tiberius's abstract bg — this is Sulla's "what am I logging into" tell.
function CurrencyGlyphs() {
  // Each entry: {x, y, glyph, size, opacity, rotate}.
  // Glyphs cover the 7 majors: € £ ¥ $ for the Unicode-friendly ones,
  // 3-letter codes for AUD / NZD / CHF / CAD which don't have single glyphs.
  // Coordinates are SVG viewBox 0-100 percent; preserveAspectRatio=none lets
  // them stretch to fit the right rail at any aspect.
  const glyphs = [
    { x:  8, y:  18, t: '€',    s: 14, o: 0.10, r: -8 },
    { x: 30, y:  12, t: '£',    s: 11, o: 0.08, r:  4 },
    { x: 58, y:  20, t: '¥',    s: 16, o: 0.13, r: -3 },
    { x: 82, y:  16, t: '$',    s: 12, o: 0.09, r:  6 },
    { x: 18, y:  44, t: 'AUD',  s:  5, o: 0.08, r: -2 },
    { x: 46, y:  48, t: '€',    s:  9, o: 0.07, r:  2 },
    { x: 70, y:  42, t: 'CHF',  s:  5, o: 0.09, r:  3 },
    { x: 90, y:  50, t: '£',    s: 10, o: 0.07, r: -5 },
    { x: 12, y:  74, t: '¥',    s: 12, o: 0.08, r:  4 },
    { x: 40, y:  78, t: 'NZD',  s:  4, o: 0.07, r: -3 },
    { x: 62, y:  72, t: '$',    s: 14, o: 0.10, r:  2 },
    { x: 86, y:  82, t: 'CAD',  s:  5, o: 0.08, r: -4 },
    { x: 26, y:  90, t: '€',    s:  8, o: 0.06, r:  3 },
    { x: 54, y:  92, t: '£',    s:  9, o: 0.07, r: -2 },
  ]
  return (
    <svg viewBox="0 0 100 100" preserveAspectRatio="none"
      style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', pointerEvents: 'none' }}>
      {glyphs.map((g, i) => (
        <text key={i}
          x={g.x} y={g.y}
          fill={BLUE_LT}
          opacity={g.o}
          fontSize={g.s}
          fontFamily="'Times New Roman', Georgia, serif"
          fontWeight="700"
          transform={`rotate(${g.r} ${g.x} ${g.y})`}
          textAnchor="middle"
          dominantBaseline="middle">
          {g.t}
        </text>
      ))}
    </svg>
  )
}

// Multi-pair chart trace — seven smooth Bezier curves in graduated blues,
// one per major. Replaces the gold candlestick decoration from earlier; the
// candle motif appears on Anton's login too and was making the three bots'
// login pages bleed into one another visually. This component is unique to
// Sulla and reads as "this is the multi-asset FX system" at a glance.
//
// Each line is a cubic-bezier path traversing x=0 to x=100 in the SVG
// viewBox. We hand-tune Y values per line so the curves feel like distinct
// price-action shapes (some trending, some choppy, some sideways) rather
// than uniform sine waves. Stroke width / opacity / color form a depth
// hierarchy: front lines pop, back lines recede.
function MultiPairChartTrace() {
  // Color palette graduated from background to foreground. The two lines
  // tagged `prominent: true` get a glow effect and a "current price" dot.
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
    // EUR/USD: the most-traded pair, soft cyan-blue (matches the brand
    // accent + the Guide's CYAN paradigm color), gentle uptrend.
    { name: 'EUR/USD', d: 'M 0 48 C 18 44, 36 52, 56 44 S 82 38, 100 33',
      color: '#06B6D4', width: 1.5, opacity: 0.85, prominent: true },

    // USD/JPY: the carry-trade staple, electric blue, slight downtrend.
    { name: 'USD/JPY', d: 'M 0 18 C 16 22, 30 16, 48 20 S 76 12, 100 18',
      color: '#3B82F6', width: 1.5, opacity: 0.90, prominent: true },
  ]

  return (
    <svg viewBox="0 0 100 100" preserveAspectRatio="none"
      style={{ position: 'absolute', inset: 0, width: '100%', height: '100%',
                pointerEvents: 'none' }}>
      <defs>
        {/* Soft glow filter for the two hero lines */}
        <filter id="line-glow" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="0.8" result="blurred" />
          <feMerge>
            <feMergeNode in="blurred" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
      </defs>

      {lines.map((line, i) => {
        // Endpoint dot — the last (x, y) point in the Bezier path. We could
        // parse the path but it's easier to hand-stamp here so the dot
        // sits exactly where the line terminates.
        const endpoints = {
          'NZD/USD': { x: 100, y: 30 },
          'USD/CAD': { x: 100, y: 75 },
          'AUD/USD': { x: 100, y: 60 },
          'USD/CHF': { x: 100, y: 32 },
          'GBP/USD': { x: 100, y: 45 },
          'EUR/USD': { x: 100, y: 33 },
          'USD/JPY': { x: 100, y: 18 },
        }
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
            {/* "Current price" dot at the right edge for the hero lines.
                Tiny glowing pip indicating "live data, this is moving." */}
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
          <FoundationMark size={44} />
          <div>
            <span style={{
              fontSize: '26px', fontWeight: 800, letterSpacing: '0.18em',
              color: NAVY, lineHeight: 1,
            }}>FOUNDATION</span>
            <span style={{
              display: 'block', fontSize: '12px', letterSpacing: '0.16em',
              color: BLUE, marginTop: '4px',
            }}>IONIC · FX</span>
          </div>
        </div>

        {/* Heading */}
        <div style={{ marginBottom: '36px' }}>
          <h1 style={{ fontSize: '28px', fontWeight: 700, color: '#111827',
            margin: '0 0 8px', letterSpacing: '-0.02em' }}>
            Welcome back
          </h1>
          <p style={{ fontSize: '14px', color: '#6b7280', margin: 0 }}>
            Sign in to your Ionic trading dashboard.
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

        {/* Public demo escape hatch — mirrors Doric + Corinthian login pages.
            /demo route was wired in App.jsx + DemoMode.jsx exists, but
            until 2026-05-26 there was no LINK to it anywhere, so visitors
            hit the login wall with no way to explore. */}
        <div style={{
          marginTop: '24px', paddingTop: '20px',
          borderTop: '1px solid #1e293b',
          fontSize: '13px', textAlign: 'center', color: '#94a3b8',
        }}>
          <Link to="/demo" style={{ color: BLUE, textDecoration: 'none' }}>
            Just looking?{' '}
            <span style={{ fontWeight: 600 }}>Try the public demo →</span>
          </Link>
        </div>

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
        background: `linear-gradient(160deg, #0b1830 0%, ${NAVY} 45%, #060d18 100%)`,
      }}>

        {/* Currency-symbol pattern (Sulla's distinct FX-trading tell) */}
        <CurrencyGlyphs />

        {/* Ambient blue glow behind candles — Sulla brand wash */}
        <div style={{
          position: 'absolute', bottom: '-10%', left: '0', right: '0',
          height: '70%',
          background: `radial-gradient(ellipse at 50% 100%, rgba(59,130,246,0.20) 0%, transparent 70%)`,
          pointerEvents: 'none',
        }} />

        {/* Multi-pair chart trace — seven smooth Bezier curves in graduated
            blues, one per major. Replaces Anton's gold-candle motif so the
            Sulla login reads as distinctly FX, not a TradFi recolor. */}
        <MultiPairChartTrace />

        {/* Top-left label */}
        <div style={{
          position: 'absolute', top: '36px', left: '40px',
          fontSize: '13px', fontWeight: 600, letterSpacing: '0.18em',
          color: 'rgba(200,146,42,0.70)', textTransform: 'uppercase',
        }}>
          Foundation
        </div>

        {/* Top-right Sulla mark — distinguishes from Anton/Tiberius without
            forcing the visitor to read the bottom-left tagline */}
        <div style={{
          position: 'absolute', top: '34px', right: '40px',
          display: 'flex', alignItems: 'center', gap: '10px',
        }}>
          <span style={{
            fontSize: '11px', fontWeight: 700, letterSpacing: '0.22em',
            color: BLUE_LT, textTransform: 'uppercase',
          }}>
            Ionic · FX
          </span>
          <span style={{
            width: '24px', height: '2px',
            background: `linear-gradient(90deg, ${BLUE}, transparent)`,
          }} />
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
            margin: 0, lineHeight: 1.6, maxWidth: '340px',
          }}>
            Seven major pairs. 24/5 global execution.
            Multi-paradigm signals, AI consensus, disciplined compounding.
          </p>

          {/* Blue accent line (Sulla brand) */}
          <div style={{
            marginTop: '24px', width: '64px', height: '3px',
            background: `linear-gradient(90deg, ${BLUE}, ${BLUE_LT}33 60%, transparent)`,
            borderRadius: '2px',
          }} />
        </div>
      </div>

    </div>
  )
}
