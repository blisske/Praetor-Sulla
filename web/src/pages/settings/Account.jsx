import { useEffect, useState } from 'react'
import api from '../../lib/api.js'
import SettingsCard from '../../components/SettingsCard.jsx'
import TwoFactorCard from '../../components/TwoFactorCard.jsx'
import { CheckCircle2, Mail } from 'lucide-react'
import { useAuth } from '../../lib/auth.jsx'

/**
 * /settings/account — change password + change email.
 *
 * Both call dedicated endpoints in the new SaaS auth router:
 *   POST /api/auth/change-password   {current_password, new_password}
 *   POST /api/auth/change-email      {current_password, new_email}
 *
 * On password change success: optimistic confirmation banner, no auto-
 * relogin needed (existing JWT stays valid; user gets a confirmation
 * email; opportunistic argon2 rehash happens server-side).
 *
 * On email change: server sends a verification email to the NEW address.
 * The email isn't applied to the account until the user clicks the link.
 * We surface "verification email sent — click the link to complete" on
 * success; the current email stays in place until verified.
 */
const inputStyle = {
  width: '100%',
  background: 'var(--bg-elevated)',
  border: '1px solid var(--border-input)',
  borderRadius: '0.4rem',
  padding: '0.6rem 0.8rem',
  color: 'var(--text-primary)',
  fontSize: '0.9rem',
  outline: 'none',
  fontFamily: 'inherit',
  transition: 'border-color 0.15s',
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

const btnStyle = {
  background: 'linear-gradient(180deg, #c8922a 0%, #a8761d 100%)',
  color: '#1a1a1a',
  border: 'none',
  fontSize: '0.83rem',
  fontWeight: 700,
  letterSpacing: '0.08em',
  textTransform: 'uppercase',
  padding: '0.6rem 1.2rem',
  borderRadius: '0.35rem',
  cursor: 'pointer',
  transition: 'transform 0.1s, box-shadow 0.15s, opacity 0.15s',
}


function MeBanner({ user }) {
  if (!user) return null
  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      gap: '0.85rem',
      padding: '0.85rem 1.1rem',
      background: 'var(--bg-elevated)',
      border: '1px solid var(--border)',
      borderRadius: '0.5rem',
      marginBottom: '1rem',
    }}>
      <Mail size={18} style={{ color: '#c8922a', flexShrink: 0 }} />
      <div style={{ flex: 1 }}>
        <div style={{ fontSize: '0.92rem', color: 'var(--text-primary)', fontWeight: 600 }}>
          {user.email}
        </div>
        <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '0.1rem' }}>
          {user.email_verified
            ? <span style={{ color: '#22c55e' }}>✓ Verified</span>
            : <span style={{ color: '#f59e0b' }}>○ Unverified — check your inbox</span>}
          {user.is_admin && <span style={{ marginLeft: '0.5rem', color: '#c8922a' }}>· Admin</span>}
        </div>
      </div>
    </div>
  )
}


function PasswordForm() {
  const { user } = useAuth()
  const isDemo = !!user?.is_demo
  const [current, setCurrent] = useState('')
  const [next, setNext]       = useState('')
  const [confirm, setConfirm] = useState('')
  const [busy, setBusy]       = useState(false)
  const [err, setErr]         = useState('')
  const [ok, setOk]           = useState(false)

  const submit = async e => {
    e.preventDefault()
    setErr(''); setOk(false)
    if (next.length < 12) { setErr('New password must be at least 12 characters.'); return }
    if (next !== confirm) { setErr('New passwords do not match.'); return }
    if (next === current) { setErr('New password must differ from the current one.'); return }
    setBusy(true)
    try {
      await api.post('/auth/change-password', {
        current_password: current, new_password: next,
      })
      setOk(true)
      setCurrent(''); setNext(''); setConfirm('')
    } catch (e) {
      const status = e?.response?.status
      if (status === 401)      setErr('Current password is incorrect.')
      else if (status === 400) setErr(e.response.data?.detail || 'Password change rejected.')
      else if (status === 429) setErr('Too many attempts. Please wait and try again.')
      else                     setErr('Something went wrong. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <SettingsCard
      title="Change password"
      subtitle="Minimum 12 characters. You'll get a confirmation email on success."
    >
      <form onSubmit={submit} style={{ display: 'flex', flexDirection: 'column', gap: '0.85rem' }}>
        {ok && (
          <div style={{
            background: 'rgba(34,197,94,0.08)', border: '1px solid rgba(34,197,94,0.25)',
            color: '#86efac', fontSize: '0.82rem', borderRadius: '0.3rem',
            padding: '0.6rem 0.8rem', display: 'flex', alignItems: 'center', gap: '0.5rem',
          }}>
            <CheckCircle2 size={15} />
            Password updated. A confirmation email has been sent.
          </div>
        )}
        {err && (
          <div style={{
            background: 'rgba(239,68,68,0.09)', border: '1px solid rgba(239,68,68,0.22)',
            color: '#fca5a5', fontSize: '0.82rem', borderRadius: '0.3rem',
            padding: '0.55rem 0.75rem',
          }}>
            {err}
          </div>
        )}
        <div>
          <label style={labelStyle}>Current password</label>
          <input style={inputStyle} type="password" autoComplete="current-password"
                 value={current} onChange={e => setCurrent(e.target.value)} required />
        </div>
        <div>
          <label style={labelStyle}>New password</label>
          <input style={inputStyle} type="password" autoComplete="new-password"
                 value={next} onChange={e => setNext(e.target.value)} required minLength={12} />
        </div>
        <div>
          <label style={labelStyle}>Confirm new password</label>
          <input style={inputStyle} type="password" autoComplete="new-password"
                 value={confirm} onChange={e => setConfirm(e.target.value)} required minLength={12} />
        </div>
        <div>
          <button
            type="submit"
            disabled={busy || isDemo}
            title={isDemo ? 'Disabled in demo mode' : undefined}
            style={{ ...btnStyle, opacity: (busy || isDemo) ? 0.4 : 1, cursor: isDemo ? 'not-allowed' : 'pointer' }}
          >
            {busy ? 'Updating…' : 'Update Password'}
          </button>
        </div>
      </form>
    </SettingsCard>
  )
}


function EmailForm({ user }) {
  const { user: authUser } = useAuth()
  const isDemo = !!authUser?.is_demo
  const [newEmail, setNewEmail] = useState('')
  const [pw, setPw]             = useState('')
  const [busy, setBusy]         = useState(false)
  const [err, setErr]           = useState('')
  const [ok, setOk]             = useState(false)

  const submit = async e => {
    e.preventDefault()
    setErr(''); setOk(false)
    const target = newEmail.trim().toLowerCase()
    if (!target.includes('@')) { setErr('Enter a valid email.'); return }
    if (user?.email === target) { setErr('New email must differ from your current address.'); return }
    setBusy(true)
    try {
      await api.post('/auth/change-email', {
        current_password: pw, new_email: target,
      })
      setOk(true)
      setNewEmail(''); setPw('')
    } catch (e) {
      const status = e?.response?.status
      if (status === 401)      setErr('Current password is incorrect.')
      else if (status === 409) setErr('That email is already in use.')
      else if (status === 422) setErr('Please enter a valid email address.')
      else if (status === 429) setErr('Too many attempts. Please wait and try again.')
      else                     setErr(e?.response?.data?.detail || 'Something went wrong.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <SettingsCard
      title="Change email"
      subtitle="We'll send a confirmation link to the new address. The change applies once you click it."
    >
      <form onSubmit={submit} style={{ display: 'flex', flexDirection: 'column', gap: '0.85rem' }}>
        {ok && (
          <div style={{
            background: 'rgba(34,197,94,0.08)', border: '1px solid rgba(34,197,94,0.25)',
            color: '#86efac', fontSize: '0.82rem', borderRadius: '0.3rem',
            padding: '0.6rem 0.8rem',
          }}>
            ✓ Verification email sent. Click the link to complete the change.
          </div>
        )}
        {err && (
          <div style={{
            background: 'rgba(239,68,68,0.09)', border: '1px solid rgba(239,68,68,0.22)',
            color: '#fca5a5', fontSize: '0.82rem', borderRadius: '0.3rem',
            padding: '0.55rem 0.75rem',
          }}>
            {err}
          </div>
        )}
        <div>
          <label style={labelStyle}>New email address</label>
          <input style={inputStyle} type="email" autoComplete="email"
                 value={newEmail} onChange={e => setNewEmail(e.target.value)} required />
        </div>
        <div>
          <label style={labelStyle}>Confirm with current password</label>
          <input style={inputStyle} type="password" autoComplete="current-password"
                 value={pw} onChange={e => setPw(e.target.value)} required />
        </div>
        <div>
          <button
            type="submit"
            disabled={busy || isDemo}
            title={isDemo ? 'Disabled in demo mode' : undefined}
            style={{ ...btnStyle, opacity: (busy || isDemo) ? 0.4 : 1, cursor: isDemo ? 'not-allowed' : 'pointer' }}
          >
            {busy ? 'Sending…' : 'Send Verification Link'}
          </button>
        </div>
      </form>
    </SettingsCard>
  )
}


export default function SettingsAccount() {
  const [user, setUser] = useState(null)
  const [loadErr, setLoadErr] = useState('')

  useEffect(() => {
    let cancelled = false
    api.get('/auth/me')
      .then(r => { if (!cancelled) setUser(r.data) })
      .catch(() => { if (!cancelled) setLoadErr('Could not load account details.') })
    return () => { cancelled = true }
  }, [])

  return (
    <div>
      {loadErr && (
        <div style={{
          background: 'rgba(239,68,68,0.09)', border: '1px solid rgba(239,68,68,0.22)',
          color: '#fca5a5', fontSize: '0.82rem', borderRadius: '0.3rem',
          padding: '0.55rem 0.75rem', marginBottom: '1rem',
        }}>
          {loadErr}
        </div>
      )}
      <MeBanner user={user} />
      <PasswordForm />
      <TwoFactorCard isDemo={!!user?.is_demo} />
      <EmailForm user={user} />
    </div>
  )
}
