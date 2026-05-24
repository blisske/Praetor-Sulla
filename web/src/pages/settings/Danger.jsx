import { useEffect, useState } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import api from '../../lib/api.js'
import { useAuth } from '../../lib/auth.jsx'
import SettingsCard from '../../components/SettingsCard.jsx'
import { Trash2, ShieldOff, AlertOctagon, Eye } from 'lucide-react'

/**
 * /settings/danger — terminal actions consolidated.
 *
 * Two things live here:
 *   1. Disconnect Oanda key (also in /settings/broker, mirrored here so
 *      users looking for "how do I sever everything" find it).
 *   2. Delete account — soft-delete (deleted_at on the users row); the
 *      provisioner picks up a teardown flag and stops/removes the user's
 *      engine container + soft-moves data to _deleted/ for 30 days.
 *
 * Each action requires confirmation. Account deletion specifically
 * requires the user to re-type their email exactly + provide their
 * current password (matches the server's DeleteAccountRequest contract).
 */
const inputStyle = {
  width: '100%',
  background: 'var(--bg-elevated)',
  border: '1px solid var(--border-input)',
  borderRadius: '0.4rem',
  padding: '0.6rem 0.8rem',
  color: 'var(--text-primary)',
  fontSize: '0.86rem',
  outline: 'none',
  fontFamily: 'inherit',
}

const labelStyle = {
  display: 'block',
  fontSize: '0.7rem',
  fontWeight: 600,
  letterSpacing: '0.1em',
  textTransform: 'uppercase',
  color: 'var(--text-sub)',
  marginBottom: '0.35rem',
}

const dangerBtnStyle = {
  background: 'linear-gradient(180deg, #dc2626 0%, #991b1b 100%)',
  color: '#fff',
  border: 'none',
  fontSize: '0.83rem',
  fontWeight: 700,
  letterSpacing: '0.08em',
  textTransform: 'uppercase',
  padding: '0.6rem 1.2rem',
  borderRadius: '0.35rem',
  cursor: 'pointer',
  transition: 'opacity 0.15s',
}

const ghostBtnStyle = {
  background: 'transparent',
  color: 'var(--text-muted)',
  border: '1px solid var(--border)',
  fontSize: '0.83rem',
  fontWeight: 600,
  padding: '0.55rem 1.1rem',
  borderRadius: '0.35rem',
  cursor: 'pointer',
}


function DisconnectOanda({ connected, onChanged }) {
  const [busy, setBusy] = useState(false)
  const [err, setErr]   = useState('')
  const [armed, setArmed] = useState(false)

  if (!connected) return null  // mirror — only show if there's something to disconnect

  const submit = async () => {
    setErr(''); setBusy(true)
    try {
      await api.delete('/byok/oanda')
      if (onChanged) onChanged()
      setArmed(false)
    } catch (e) {
      setErr(e?.response?.data?.detail || 'Could not disconnect.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <SettingsCard
      title="Disconnect Oanda"
      subtitle="Removes your encrypted broker key. Live trading is disabled; your bot reverts to shadow."
      tone="danger"
    >
      {err && (
        <div style={{
          background: 'rgba(239,68,68,0.09)', border: '1px solid rgba(239,68,68,0.22)',
          color: '#fca5a5', fontSize: '0.82rem', borderRadius: '0.3rem',
          padding: '0.55rem 0.75rem', marginBottom: '0.8rem',
        }}>
          {err}
        </div>
      )}
      {!armed ? (
        <button onClick={() => setArmed(true)} style={dangerBtnStyle}>
          <ShieldOff size={14} style={{ display: 'inline', marginRight: '0.4rem', marginBottom: 2 }} />
          Disconnect Oanda Key
        </button>
      ) : (
        <div style={{ display: 'flex', gap: '0.6rem', alignItems: 'center', flexWrap: 'wrap' }}>
          <span style={{ fontSize: '0.84rem', color: '#fca5a5' }}>Disconnect now?</span>
          <button onClick={submit} disabled={busy} style={{ ...dangerBtnStyle, opacity: busy ? 0.5 : 1 }}>
            {busy ? 'Disconnecting…' : 'Yes, disconnect'}
          </button>
          <button onClick={() => setArmed(false)} style={ghostBtnStyle} disabled={busy}>Cancel</button>
        </div>
      )}
    </SettingsCard>
  )
}


function DeleteAccount({ user }) {
  const navigate = useNavigate()
  const { logout } = useAuth()
  const [confirmEmail, setConfirmEmail] = useState('')
  const [password, setPassword]         = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr]   = useState('')
  const [armed, setArmed] = useState(false)

  const expectedEmail = user?.email || ''
  const emailMatches  = confirmEmail.trim().toLowerCase() === expectedEmail
  const canSubmit     = armed && emailMatches && password.length > 0

  const submit = async e => {
    e.preventDefault()
    if (!canSubmit) return
    setErr(''); setBusy(true)
    try {
      await api.delete('/auth/account', {
        data: {
          current_password:   password,
          confirmation_email: confirmEmail.trim().toLowerCase(),
        },
      })
      // Account deleted server-side. Drop the JWT and bounce to login
      // with a banner.
      logout()
      window.location.assign('/login?deleted=ok')
    } catch (e) {
      const s = e?.response?.status
      if (s === 401)       setErr('Password incorrect.')
      else if (s === 400)  setErr(e?.response?.data?.detail || 'Could not delete account.')
      else                  setErr(e?.response?.data?.detail || 'Something went wrong.')
      setBusy(false)
    }
  }

  return (
    <SettingsCard
      title="Delete account"
      subtitle="Stops your trading bot, removes your engine container, and soft-deletes your data. Recoverable for 30 days by contacting support; permanent after that."
      tone="danger"
    >
      {err && (
        <div style={{
          background: 'rgba(239,68,68,0.09)', border: '1px solid rgba(239,68,68,0.22)',
          color: '#fca5a5', fontSize: '0.82rem', borderRadius: '0.3rem',
          padding: '0.55rem 0.75rem', marginBottom: '0.8rem',
        }}>
          {err}
        </div>
      )}

      {!armed ? (
        <button onClick={() => setArmed(true)} style={dangerBtnStyle}>
          <Trash2 size={14} style={{ display: 'inline', marginRight: '0.4rem', marginBottom: 2 }} />
          I want to delete my account
        </button>
      ) : (
        <form onSubmit={submit} style={{ display: 'flex', flexDirection: 'column', gap: '0.85rem' }}>
          <div style={{
            display: 'flex', alignItems: 'flex-start', gap: '0.55rem',
            background: 'rgba(220,38,38,0.08)', border: '1px solid rgba(220,38,38,0.25)',
            borderRadius: '0.35rem', padding: '0.65rem 0.85rem',
          }}>
            <AlertOctagon size={16} style={{ color: '#fca5a5', flexShrink: 0, marginTop: 2 }} />
            <p style={{ fontSize: '0.82rem', color: 'var(--text-primary)', margin: 0, lineHeight: 1.55 }}>
              This stops live trading immediately and removes your bot.
              Any open positions on Oanda stay open on your side — Foundation
              just stops managing them.
            </p>
          </div>

          <div>
            <label style={labelStyle}>
              Re-type your email to confirm: <code style={{
                background: 'var(--bg-elevated)', padding: '0.05rem 0.3rem',
                borderRadius: '0.2rem', fontSize: '0.78rem', color: 'var(--text-primary)',
              }}>{expectedEmail}</code>
            </label>
            <input
              style={{
                ...inputStyle,
                borderColor: confirmEmail && !emailMatches ? '#dc2626' : 'var(--border-input)',
              }}
              type="email" autoComplete="off"
              value={confirmEmail} onChange={e => setConfirmEmail(e.target.value)}
              required
            />
          </div>
          <div>
            <label style={labelStyle}>Current password</label>
            <input style={inputStyle} type="password" autoComplete="current-password"
                   value={password} onChange={e => setPassword(e.target.value)} required />
          </div>

          <div style={{ display: 'flex', gap: '0.6rem' }}>
            <button type="submit" disabled={!canSubmit || busy} style={{
              ...dangerBtnStyle,
              opacity: (!canSubmit || busy) ? 0.4 : 1,
              cursor: (!canSubmit || busy) ? 'not-allowed' : 'pointer',
            }}>
              {busy ? 'Deleting…' : 'Permanently Delete Account'}
            </button>
            <button type="button" onClick={() => { setArmed(false); setConfirmEmail(''); setPassword(''); setErr('') }}
                    style={ghostBtnStyle} disabled={busy}>
              Cancel
            </button>
          </div>
        </form>
      )}
    </SettingsCard>
  )
}


export default function SettingsDanger() {
  const { user: authUser } = useAuth()
  const isDemo = !!authUser?.is_demo
  const [user, setUser]       = useState(null)
  const [broker, setBroker]   = useState(null)
  const [tick, setTick]       = useState(0)

  useEffect(() => {
    let cancelled = false
    Promise.all([
      api.get('/auth/me').then(r => r.data).catch(() => null),
      api.get('/byok/oanda').then(r => r.data).catch(() => null),
    ]).then(([u, b]) => {
      if (cancelled) return
      setUser(u); setBroker(b)
    })
    return () => { cancelled = true }
  }, [tick])

  // In demo mode, the entire Danger tab is hard-disabled.
  // The shared public demo user MUST NOT be deletable.
  if (isDemo) {
    return (
      <div>
        <div style={{
          display: 'flex', alignItems: 'flex-start', gap: '0.7rem',
          background: 'rgba(247,147,26,0.08)',
          border: '1px solid rgba(247,147,26,0.25)',
          borderRadius: '0.4rem',
          padding: '1rem 1.2rem',
        }}>
          <Eye size={18} style={{ color: '#F7931A', flexShrink: 0, marginTop: 2 }} />
          <div>
            <h2 style={{
              fontSize: '0.95rem', fontWeight: 700, color: 'var(--text-primary)',
              margin: 0, marginBottom: '0.4rem',
            }}>
              Danger zone is disabled in demo mode
            </h2>
            <p style={{ fontSize: '0.84rem', color: 'var(--text-muted)', margin: 0, lineHeight: 1.55 }}>
              The shared demo account can&apos;t be deleted or have its broker key disconnected.{' '}
              <Link to="/signup" style={{ color: '#c8922a', fontWeight: 600 }}>Create your own account</Link>{' '}
              to access the full settings surface.
            </p>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div>
      <div style={{
        background: 'rgba(220,38,38,0.06)',
        border: '1px solid rgba(220,38,38,0.18)',
        borderRadius: '0.5rem',
        padding: '0.9rem 1.1rem',
        marginBottom: '1.25rem',
        fontSize: '0.84rem',
        color: 'var(--text-muted)',
        lineHeight: 1.55,
      }}>
        These actions are <strong style={{ color: '#fca5a5' }}>permanent or near-permanent</strong>.{' '}
        Need help instead? <Link to="/settings/account" style={{ color: '#c8922a', fontWeight: 600 }}>
          Account settings
        </Link>{' '}covers password + email changes.
      </div>

      <FreezeAccount onChanged={() => setTick(t => t + 1)} />
      <DisconnectOanda connected={broker?.connected} onChanged={() => setTick(t => t + 1)} />
      <DeleteAccount user={user} />
    </div>
  )
}


function FreezeAccount({ onChanged }) {
  const [status, setStatus] = useState(null)
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr]   = useState('')
  const [armed, setArmed] = useState(false)
  const [done, setDone] = useState(false)

  useEffect(() => {
    let cancelled = false
    api.get('/user/freeze')
      .then(r => { if (!cancelled) setStatus(r.data) })
      .catch(() => { if (!cancelled) setStatus({ frozen: false, config_present: false }) })
    return () => { cancelled = true }
  }, [done])

  const submit = async () => {
    setErr(''); setBusy(true)
    try {
      await api.post('/user/freeze', { reason })
      setDone(true)
      if (onChanged) onChanged()
    } catch (e) {
      const s = e?.response?.status
      if (s === 503) setErr('Your trading workspace is still being set up.')
      else            setErr(e?.response?.data?.detail || 'Could not freeze the account.')
    } finally {
      setBusy(false)
      setArmed(false)
    }
  }

  // Already frozen — show the frozen state + how to unfreeze
  if (status?.frozen) {
    return (
      <SettingsCard
        title="Account frozen"
        subtitle="Your bot is still managing existing positions but will NOT take any new entries. Unfreezing requires operator review."
        tone="danger"
      >
        <div style={{
          background: 'rgba(245,158,11,0.08)',
          border: '1px solid rgba(245,158,11,0.25)',
          borderRadius: '0.35rem',
          padding: '0.7rem 0.9rem',
          fontSize: '0.85rem',
          color: 'var(--text-primary)',
          lineHeight: 1.6,
        }}>
          <div style={{ marginBottom: '0.4rem' }}>
            <strong style={{ color: '#fcd34d' }}>Frozen at:</strong>{' '}
            {status.frozen_at ? new Date(status.frozen_at).toLocaleString() : '—'}
          </div>
          {status.frozen_reason && (
            <div style={{ marginBottom: '0.4rem' }}>
              <strong style={{ color: '#fcd34d' }}>Your reason:</strong> {status.frozen_reason}
            </div>
          )}
          <div style={{ marginTop: '0.6rem', color: 'var(--text-muted)', fontSize: '0.82rem' }}>
            <strong>To unfreeze:</strong> email{' '}
            <a href="mailto:support@foundationbots.com" style={{ color: '#c8922a' }}>
              support@foundationbots.com
            </a>{' '}— an operator will work through the unfreeze with you.
            This friction is intentional (panic-trade prevention).
          </div>
        </div>
      </SettingsCard>
    )
  }

  // Just-frozen confirmation banner
  if (done) {
    return (
      <SettingsCard
        title="Account frozen"
        subtitle="Confirmation email sent. Your bot will block new trades on its next cycle (within ~60 seconds)."
        tone="danger"
      >
        <p style={{ fontSize: '0.85rem', color: 'var(--text-muted)', margin: 0 }}>
          To unfreeze, email <a href="mailto:support@foundationbots.com" style={{ color: '#c8922a' }}>
            support@foundationbots.com
          </a>.
        </p>
      </SettingsCard>
    )
  }

  // Default: offer the freeze button
  return (
    <SettingsCard
      title="Freeze account"
      subtitle="Immediately block all new trade entries. Existing positions keep being managed (trailing stops, take-profits, exits). Unfreezing is operator-only — there's no self-service un-freeze."
      tone="danger"
    >
      {err && (
        <div style={{
          background: 'rgba(239,68,68,0.09)', border: '1px solid rgba(239,68,68,0.22)',
          color: '#fca5a5', fontSize: '0.82rem', borderRadius: '0.3rem',
          padding: '0.55rem 0.75rem', marginBottom: '0.8rem',
        }}>
          {err}
        </div>
      )}
      {!armed ? (
        <button onClick={() => setArmed(true)} style={dangerBtnStyle}>
          Freeze My Account
        </button>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.7rem' }}>
          <div>
            <label style={labelStyle}>Optional: reason (helps operator unfreeze faster)</label>
            <input
              style={inputStyle} type="text" maxLength={200}
              value={reason} onChange={e => setReason(e.target.value)}
              placeholder="e.g., market panic, suspicious activity, going on vacation"
            />
          </div>
          <div style={{ display: 'flex', gap: '0.6rem' }}>
            <button onClick={submit} disabled={busy} style={{ ...dangerBtnStyle, opacity: busy ? 0.5 : 1 }}>
              {busy ? 'Freezing…' : 'Yes, freeze now'}
            </button>
            <button onClick={() => { setArmed(false); setReason('') }}
                    style={ghostBtnStyle} disabled={busy}>
              Cancel
            </button>
          </div>
        </div>
      )}
    </SettingsCard>
  )
}
