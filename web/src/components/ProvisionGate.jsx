import { useEffect, useState } from 'react'
import api from '../lib/api.js'
import { useAuth } from '../lib/auth.jsx'

const EMERALD    = '#3B82F6'
const EMERALD_LT = '#93C5FD'

/**
 * ProvisionGate — wraps the authenticated dashboard.
 *
 * For multi-tenant users (user_id ≥ 3) on a bot they haven't enabled
 * yet, renders a friendly empty-state with an "Enable Ionic for your
 * account →" button. Click POSTs /api/user/provision → daemon spawns
 * the per-user engine within ~5 seconds. UI polls until ready=true,
 * then unmounts itself and the real dashboard renders.
 *
 * The operator (user_id=1) and demo (user_id=2) are
 * server-side `PRE_PROVISIONED` and the API always returns
 * ready=true for them — no empty-state, immediate normal dashboard.
 */
export default function ProvisionGate({ children }) {
  const { user } = useAuth()
  const [status, setStatus]   = useState(null)   // null = loading
  const [busy, setBusy]       = useState(false)
  const [err, setErr]         = useState('')
  const [tick, setTick]       = useState(0)
  // Wait-list: a non-null timestamp means we're polling for ready=true
  const [waitingSince, setWaitingSince] = useState(null)

  // Fetch status. Re-runs on tick increments (polling).
  useEffect(() => {
    if (!user) return
    let cancelled = false
    api.get('/user/provision')
      .then(r => { if (!cancelled) setStatus(r.data) })
      .catch(() => { if (!cancelled) setStatus({ ready: true /* fail-open: never block dashboard on a check failure */ }) })
    return () => { cancelled = true }
  }, [user?.id, tick])

  // Polling loop — fire while pending OR while waitingSince is set + not ready yet
  useEffect(() => {
    if (status?.ready) return
    if (!status?.pending && !waitingSince) return
    const id = setTimeout(() => setTick(t => t + 1), 3000)
    return () => clearTimeout(id)
  }, [status?.ready, status?.pending, waitingSince])

  const enable = async () => {
    setBusy(true); setErr('')
    try {
      const r = await api.post('/user/provision')
      setStatus(r.data)
      setWaitingSince(Date.now())
    } catch (e) {
      setErr(e?.response?.data?.detail || 'Could not start provisioning. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  // Loading / null user → render nothing (Protected route catches no-user case)
  if (!status) return null
  // Provisioned + engine alive → unmount the gate, render the real dashboard
  if (status.ready) return children

  // Empty state — user hasn't enabled this bot yet (or engine still starting)
  const isStarting = status.pending || (waitingSince && !status.ready)
  const waitedSec = waitingSince ? Math.floor((Date.now() - waitingSince) / 1000) : 0

  return (
    <div style={{
      display: 'flex', flexDirection: 'column',
      alignItems: 'center', justifyContent: 'center',
      padding: '4rem 1.5rem',
      minHeight: '60vh',
      fontFamily: "'Helvetica Neue', system-ui, sans-serif",
    }}>
      <div style={{
        maxWidth: 540, width: '100%',
        background: 'var(--bg-surface)',
        border: '1px solid var(--border)',
        borderLeft: `3px solid ${EMERALD}`,
        borderRadius: '0.5rem',
        padding: '2rem 2.25rem',
        textAlign: 'center',
      }}>
        {/* Header */}
        <div style={{
          fontSize: '0.7rem', fontWeight: 700, letterSpacing: '0.18em',
          textTransform: 'uppercase',
          color: EMERALD_LT,
          marginBottom: '0.75rem',
        }}>
          Foundation · Ionic
        </div>

        <h1 style={{
          fontSize: '1.4rem', fontWeight: 700,
          color: 'var(--text-primary)',
          margin: 0, marginBottom: '0.5rem',
          letterSpacing: '-0.01em',
        }}>
          {isStarting ? 'Enabling Ionic for your account…' : 'Enable Ionic for your account'}
        </h1>

        <p style={{
          fontSize: '0.92rem', color: 'var(--text-muted)',
          lineHeight: 1.6, margin: 0, marginBottom: '1.5rem',
        }}>
          {isStarting
            ? `Your trading workspace is starting up — usually takes about 10 seconds. ${waitedSec >= 30 ? `(${waitedSec}s elapsed — if this hangs much longer something probably failed; check with support.)` : ''}`
            : 'You signed up for Foundation but haven’t turned on Ionic (FX bot) yet. Click below and we’ll spin up your own paper-trading workspace.'}
        </p>

        {!isStarting && (
          <ul style={{
            textAlign: 'left',
            fontSize: '0.85rem', color: 'var(--text-primary)',
            margin: 0, marginBottom: '1.75rem',
            paddingLeft: '1.25rem',
            lineHeight: 1.65,
          }}>
            <li>Paper-trade with $100K simulated funds — no Oanda account required to start.</li>
            <li>Same Foundation login + 2FA you already set up.</li>
            <li>Connect your own Oanda key later if you want to switch to live trading.</li>
          </ul>
        )}

        {err && (
          <div style={{
            background: 'rgba(239,68,68,0.09)', border: '1px solid rgba(239,68,68,0.22)',
            color: '#fca5a5', fontSize: '0.85rem', borderRadius: '0.3rem',
            padding: '0.55rem 0.75rem', marginBottom: '1rem',
          }}>{err}</div>
        )}

        {isStarting ? (
          <div style={{
            display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '0.65rem',
            color: 'var(--text-muted)', fontSize: '0.85rem',
          }}>
            <span style={{
              display: 'inline-block', width: 14, height: 14,
              border: `2px solid ${EMERALD}`,
              borderTopColor: 'transparent',
              borderRadius: '50%',
              animation: 'pgspin 0.9s linear infinite',
            }} />
            <span>{status.detail || 'Working…'}</span>
            <style>{`@keyframes pgspin { to { transform: rotate(360deg); } }`}</style>
          </div>
        ) : (
          <button
            onClick={enable}
            disabled={busy}
            style={{
              background: `linear-gradient(180deg, ${EMERALD} 0%, #1D4ED8 100%)`,
              color: '#fff', border: 'none',
              padding: '0.85rem 1.6rem',
              fontSize: '0.88rem', fontWeight: 700,
              letterSpacing: '0.08em', textTransform: 'uppercase',
              borderRadius: '0.4rem',
              cursor: busy ? 'not-allowed' : 'pointer',
              opacity: busy ? 0.55 : 1,
              transition: 'transform 0.1s, box-shadow 0.15s',
              boxShadow: '0 4px 16px rgba(59,130,246,0.25)',
            }}
          >
            {busy ? 'Enabling…' : 'Enable Ionic →'}
          </button>
        )}

        <p style={{
          marginTop: '1.75rem', paddingTop: '1.25rem',
          borderTop: '1px solid var(--border)',
          fontSize: '0.75rem', color: 'var(--text-muted)',
          margin: 0, marginTop: '1.75rem',
          paddingTop: '1.25rem',
        }}>
          You can disconnect any time from Settings → Danger.
        </p>
      </div>
    </div>
  )
}
