import { useState } from 'react'
import { Link, useSearchParams, useNavigate } from 'react-router-dom'
import axios from 'axios'
import AuthScaffold from '../components/AuthScaffold.jsx'

/**
 * /reset-password?token=... — set a new password using a token from the
 * password-reset email.
 *
 * Flow:
 *  1. Read token from URL query.
 *  2. User enters new password + confirm.
 *  3. POST {token, new_password} to /api/auth/reset-password.
 *  4. On success: redirect to /login with a success banner.
 *  5. On 400/410 (invalid/expired token): show "This link has expired or
 *     been used" + link back to /forgot-password.
 *
 * We DON'T probe the token validity on mount — the simpler "try to
 * consume it, handle the error" pattern is fine and avoids a second
 * endpoint just for "is this token still valid?"
 */
export default function ResetPassword() {
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const token = searchParams.get('token') || ''

  const [pw, setPw] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [tokenInvalid, setTokenInvalid] = useState(!token)

  const submit = async e => {
    e.preventDefault()
    setError('')
    if (pw.length < 12) {
      setError('Password must be at least 12 characters.')
      return
    }
    if (pw !== confirm) {
      setError('Passwords do not match.')
      return
    }
    setLoading(true)
    try {
      await axios.post('/api/auth/reset-password', {
        token,
        new_password: pw,
      })
      // Navigate to login with a banner-passing query param. Login page
      // can read `?reset=ok` and surface success copy.
      navigate('/login?reset=ok', { replace: true })
    } catch (e) {
      const status = e?.response?.status
      if (status === 400 || status === 410 || status === 404) {
        setTokenInvalid(true)
      } else if (status === 429) {
        setError('Too many attempts. Please wait and try again.')
      } else {
        setError(e?.response?.data?.detail || 'Something went wrong. Please try again.')
      }
    } finally {
      setLoading(false)
    }
  }

  if (tokenInvalid) {
    return (
      <AuthScaffold
        title="Link expired"
        subtitle="This reset link has expired or already been used."
        footer={<Link to="/forgot-password" className="auth-link">Request a new reset link</Link>}
      >
        <p style={{
          fontSize: '0.85rem',
          color: 'rgba(203,213,225,0.78)',
          lineHeight: 1.6,
          margin: 0,
          textAlign: 'center',
        }}>
          Password reset links are good for 1 hour and can only be used once.
        </p>
      </AuthScaffold>
    )
  }

  return (
    <AuthScaffold
      title="Set a new password"
      subtitle="Choose a strong one — at least 12 characters."
      footer={<Link to="/login" className="auth-link">← Back to sign in</Link>}
    >
      <form onSubmit={submit} style={{ display: 'flex', flexDirection: 'column', gap: '0.9rem' }}>
        {error && (
          <div style={{
            background: 'rgba(239,68,68,0.09)',
            border: '1px solid rgba(239,68,68,0.22)',
            color: '#fca5a5',
            fontSize: '0.8rem',
            borderRadius: '0.3rem',
            padding: '0.55rem 0.75rem',
          }}>
            {error}
          </div>
        )}

        <div>
          <label style={{
            fontSize: '0.72rem',
            fontWeight: 600,
            letterSpacing: '0.08em',
            textTransform: 'uppercase',
            color: 'rgba(59,130,246,0.85)',
            display: 'block',
            marginBottom: '0.35rem',
          }}>
            New password
          </label>
          <input
            className="auth-input"
            type="password"
            autoComplete="new-password"
            value={pw}
            onChange={e => setPw(e.target.value)}
            required
            minLength={12}
            autoFocus
          />
        </div>

        <div>
          <label style={{
            fontSize: '0.72rem',
            fontWeight: 600,
            letterSpacing: '0.08em',
            textTransform: 'uppercase',
            color: 'rgba(59,130,246,0.85)',
            display: 'block',
            marginBottom: '0.35rem',
          }}>
            Confirm new password
          </label>
          <input
            className="auth-input"
            type="password"
            autoComplete="new-password"
            value={confirm}
            onChange={e => setConfirm(e.target.value)}
            required
            minLength={12}
          />
        </div>

        <button type="submit" disabled={loading} className="auth-btn">
          {loading ? 'Updating…' : 'Update Password'}
        </button>
      </form>
    </AuthScaffold>
  )
}
