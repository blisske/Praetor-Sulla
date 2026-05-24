import { useState } from 'react'
import { Link } from 'react-router-dom'
import axios from 'axios'
import AuthScaffold from '../components/AuthScaffold.jsx'

/**
 * /forgot-password — anti-enumeration request flow.
 *
 * POSTs {email} to /api/auth/request-password-reset. The endpoint ALWAYS
 * returns 200 regardless of whether the email exists. We display the
 * confirmation message after submit no matter what — never reveal which
 * emails have accounts.
 *
 * If a reset link is actually delivered, the user follows it to
 * /reset-password?token=... where they pick a new password.
 */
export default function ForgotPassword() {
  const [email, setEmail] = useState('')
  const [submitted, setSubmitted] = useState(false)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const submit = async e => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      await axios.post('/api/auth/request-password-reset', {
        email: email.trim().toLowerCase(),
      })
      setSubmitted(true)
    } catch (e) {
      const status = e?.response?.status
      if (status === 429) {
        setError('Too many requests from this network. Please wait a few minutes.')
      } else {
        // Note: anti-enumeration means the server SHOULDN'T return errors
        // for unknown emails — but any actual error (rate limit, server
        // outage) we surface generically.
        setError('Something went wrong. Please try again in a moment.')
      }
    } finally {
      setLoading(false)
    }
  }

  if (submitted) {
    return (
      <AuthScaffold
        title="Check your inbox"
        footer={<Link to="/login" className="auth-link">← Back to sign in</Link>}
      >
        <p style={{
          fontSize: '0.88rem',
          color: 'rgba(203,213,225,0.85)',
          lineHeight: 1.6,
          margin: 0,
        }}>
          If an account exists for <strong>{email}</strong>, we&apos;ve sent
          a password reset link. Check your inbox (and your spam folder).
          The link expires in 1 hour.
        </p>
      </AuthScaffold>
    )
  }

  return (
    <AuthScaffold
      title="Reset your password"
      subtitle="We'll email you a link to set a new password."
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
            Email address
          </label>
          <input
            className="auth-input"
            type="email"
            autoComplete="email"
            placeholder="you@example.com"
            value={email}
            onChange={e => setEmail(e.target.value)}
            required
            autoFocus
          />
        </div>

        <button type="submit" disabled={loading} className="auth-btn">
          {loading ? 'Sending…' : 'Send Reset Link'}
        </button>
      </form>
    </AuthScaffold>
  )
}
