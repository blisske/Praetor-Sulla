/**
 * Minimal layout for unauthenticated auth-lifecycle pages:
 *   /signup, /forgot-password, /reset-password, /verify
 *
 * Renders the Foundation Ionic wordmark + Ionic column-capital mark
 * above a centered card. Ionic brand colors (blue) — not Doric emerald,
 * not Corinthian gold.
 */
import { Link } from 'react-router-dom'

const BLUE      = '#3B82F6'
const BLUE_LT   = '#93C5FD'
const BLUE_GLOW = 'rgba(59,130,246,0.35)'

function FoundationMark({ size = 52 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 54 54" fill="none" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <linearGradient id="ionicGradAuth" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%"   stopColor="#93C5FD" />
          <stop offset="55%"  stopColor="#3B82F6" />
          <stop offset="100%" stopColor="#1D4ED8" />
        </linearGradient>
        <filter id="glowAuth">
          <feGaussianBlur stdDeviation="1.2" result="blur" />
          <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
        </filter>
      </defs>
      <rect x="2"  y="3"  width="50" height="3"  fill="url(#ionicGradAuth)" filter="url(#glowAuth)" />
      <rect x="10" y="6"  width="34" height="12" fill="url(#ionicGradAuth)" filter="url(#glowAuth)" />
      <circle cx="10" cy="12" r="9" fill="url(#ionicGradAuth)" />
      <circle cx="10" cy="12" r="5" fill="none" stroke="#1D4ED8" strokeWidth="2" />
      <circle cx="10" cy="12" r="2" fill="url(#ionicGradAuth)" />
      <circle cx="44" cy="12" r="9" fill="url(#ionicGradAuth)" />
      <circle cx="44" cy="12" r="5" fill="none" stroke="#1D4ED8" strokeWidth="2" />
      <circle cx="44" cy="12" r="2" fill="url(#ionicGradAuth)" />
      <rect x="13" y="20" width="28" height="2.5" fill="#1D4ED8" opacity="0.65" />
      <rect x="19" y="24" width="16" height="3"  fill="#1D4ED8" />
      <rect x="17" y="27" width="20" height="23" fill="url(#ionicGradAuth)" />
      <line x1="20" y1="27" x2="20" y2="50" stroke="#1D4ED8" strokeWidth="1" opacity="0.55" />
      <line x1="24" y1="27" x2="24" y2="50" stroke="#1D4ED8" strokeWidth="1" opacity="0.55" />
      <line x1="27" y1="27" x2="27" y2="50" stroke="#1D4ED8" strokeWidth="1" opacity="0.55" />
      <line x1="30" y1="27" x2="30" y2="50" stroke="#1D4ED8" strokeWidth="1" opacity="0.55" />
      <line x1="34" y1="27" x2="34" y2="50" stroke="#1D4ED8" strokeWidth="1" opacity="0.55" />
    </svg>
  )
}

export default function AuthScaffold({ title, subtitle, children, footer }) {
  return (
    <div style={{
      minHeight: '100vh',
      display: 'flex', flexDirection: 'column',
      alignItems: 'center', justifyContent: 'center',
      background: 'linear-gradient(180deg, #0b0e11 0%, #11161c 100%)',
      fontFamily: "'SF Pro Display', 'Helvetica Neue', system-ui, sans-serif",
      padding: '2rem 1rem',
      color: '#e2e8f0',
    }}>
      <style>{`
        input:-webkit-autofill, input:-webkit-autofill:hover, input:-webkit-autofill:focus {
          -webkit-box-shadow: 0 0 0px 1000px #0d1117 inset !important;
          -webkit-text-fill-color: #e2e8f0 !important;
          caret-color: #e2e8f0;
          transition: background-color 9999s ease-in-out 0s;
        }
        .auth-input {
          width: 100%;
          background: #0d1117;
          border: 1px solid rgba(59,130,246,0.18);
          color: #e2e8f0;
          font-size: 0.92rem;
          padding: 0.7rem 0.85rem;
          border-radius: 0.3rem;
          outline: none;
          transition: border-color 0.15s, box-shadow 0.15s;
          font-family: inherit;
        }
        .auth-input::placeholder { color: rgba(148,163,184,0.42); }
        .auth-input:focus {
          border-color: rgba(59,130,246,0.55);
          box-shadow: 0 0 0 3px rgba(59,130,246,0.10);
        }
        .auth-btn {
          width: 100%;
          background: linear-gradient(180deg, ${BLUE} 0%, #1D4ED8 100%);
          color: #fff;
          border: none;
          font-size: 0.88rem;
          font-weight: 700;
          letter-spacing: 0.08em;
          text-transform: uppercase;
          padding: 0.78rem 1rem;
          border-radius: 0.3rem;
          cursor: pointer;
          transition: transform 0.1s, box-shadow 0.15s, opacity 0.15s;
        }
        .auth-btn:hover:not(:disabled) {
          transform: translateY(-1px);
          box-shadow: 0 6px 22px rgba(59,130,246,0.40);
        }
        .auth-btn:active:not(:disabled) { transform: translateY(0); }
        .auth-btn:disabled { opacity: 0.4; cursor: not-allowed; }
        .auth-link {
          color: ${BLUE_LT};
          text-decoration: none;
          font-weight: 600;
          transition: color 0.15s;
        }
        .auth-link:hover { color: #BFDBFE; }
      `}</style>

      <Link to="/" style={{ textDecoration: 'none', marginBottom: '1.5rem' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
          <FoundationMark size={56} />
          <div>
            <div style={{
              fontSize: '1.4rem', fontWeight: 800, letterSpacing: '0.22em',
              color: '#f1f5f9',
              textShadow: `0 0 24px ${BLUE_GLOW}`,
              lineHeight: 1,
            }}>
              FOUNDATION
            </div>
            <div style={{
              fontSize: '0.7rem', fontWeight: 600, letterSpacing: '0.18em',
              textTransform: 'uppercase',
              color: BLUE_LT,
              marginTop: '0.25rem',
            }}>
              Ionic · FX Majors
            </div>
          </div>
        </div>
      </Link>

      <div style={{
        width: '100%', maxWidth: 420,
        background: 'rgba(10,14,20,0.92)',
        backdropFilter: 'blur(20px)',
        border: '1px solid rgba(59,130,246,0.18)',
        borderTop: `1px solid rgba(59,130,246,0.42)`,
        borderRadius: '0.5rem',
        padding: '1.75rem',
        boxShadow: '0 20px 56px rgba(0,0,0,0.55)',
      }}>
        {title && (
          <h1 style={{
            fontSize: '1.2rem', fontWeight: 700, color: '#f1f5f9',
            margin: 0, marginBottom: subtitle ? '0.35rem' : '1.2rem',
            textAlign: 'center',
          }}>{title}</h1>
        )}
        {subtitle && (
          <p style={{
            fontSize: '0.82rem', color: 'rgba(203,213,225,0.7)',
            margin: 0, marginBottom: '1.2rem',
            textAlign: 'center', lineHeight: 1.5,
          }}>{subtitle}</p>
        )}
        {children}
      </div>

      {footer && (
        <div style={{
          marginTop: '1.25rem', fontSize: '0.82rem',
          color: 'rgba(148,163,184,0.7)', textAlign: 'center',
        }}>{footer}</div>
      )}
    </div>
  )
}
