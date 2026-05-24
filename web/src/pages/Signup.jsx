import { useState } from 'react'
import { Link } from 'react-router-dom'
import axios from 'axios'
import AuthScaffold from '../components/AuthScaffold.jsx'

/**
 * /signup — create account, accept ToS, store JWT, redirect to dashboard.
 *
 * Per AUTH_PLAN: 12-char minimum password, terms checkbox required, email
 * validated server-side via Pydantic EmailStr. On 409 (duplicate email),
 * surface a "sign in instead?" link inline.
 *
 * After successful POST /api/auth/signup, the response is {token, user};
 * we localStorage.setItem('token', ...) and force a hard navigate so the
 * AuthProvider re-reads the token from storage and the user lands on the
 * authenticated dashboard. The server has already enqueued a verification
 * email — Dashboard surfaces a banner until the user clicks it.
 */
export default function Signup() {
  const [form, setForm] = useState({
    email: '',
    password: '',
    accepted_terms: false,
    accepted_risk_acknowledgment: false,
  })
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  // Light password-strength check — purely UI feedback, not a gate
  const strength = (() => {
    if (!form.password) return null
    const len = form.password.length
    const variety = /[A-Z]/.test(form.password)
      + /[a-z]/.test(form.password)
      + /[0-9]/.test(form.password)
      + /[^A-Za-z0-9]/.test(form.password)
    if (len < 12) return { label: 'Too short', color: '#ef4444', pct: 20 }
    if (variety < 2) return { label: 'Weak',    color: '#f59e0b', pct: 45 }
    if (variety < 3) return { label: 'Medium',  color: '#eab308', pct: 70 }
    return { label: 'Strong', color: '#22c55e', pct: 100 }
  })()

  const submit = async e => {
    e.preventDefault()
    setError('')
    if (!form.accepted_terms) {
      setError('Please accept the terms to continue.')
      return
    }
    if (!form.accepted_risk_acknowledgment) {
      setError('Please acknowledge the trading + operator risk to continue.')
      return
    }
    if (form.password.length < 12) {
      setError('Password must be at least 12 characters.')
      return
    }
    setLoading(true)
    try {
      const { data } = await axios.post('/api/auth/signup', {
        email: form.email.trim().toLowerCase(),
        password: form.password,
        accepted_terms: true,
        accepted_risk_acknowledgment: true,
      })
      localStorage.setItem('token', data.token)
      window.location.assign('/?onboarding=true')
    } catch (e) {
      const status = e?.response?.status
      const detail = e?.response?.data?.detail || ''
      if (status === 409) {
        setError('An account with that email already exists.')
      } else if (status === 422) {
        setError('Check that the email is valid and password is at least 12 characters.')
      } else if (status === 429) {
        setError('Too many signup attempts from this network. Please wait and try again.')
      } else {
        setError(detail || 'Something went wrong. Please try again.')
      }
      setLoading(false)
    }
  }

  return (
    <AuthScaffold
      title="Create your account"
      subtitle="Foundation runs your trading algorithm against your own Oanda keys. You're in control."
      footer={<>Already have an account? <Link to="/login" className="auth-link">Sign in</Link></>}
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
            {error.includes('already exists') && (
              <> <Link to="/login" className="auth-link">Sign in instead</Link>.</>
            )}
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
            value={form.email}
            onChange={e => setForm(f => ({ ...f, email: e.target.value }))}
            required
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
            Password <span style={{ color: 'rgba(148,163,184,0.5)', textTransform: 'none', letterSpacing: 0 }}>(min 12 chars)</span>
          </label>
          <input
            className="auth-input"
            type="password"
            autoComplete="new-password"
            value={form.password}
            onChange={e => setForm(f => ({ ...f, password: e.target.value }))}
            required
            minLength={12}
          />
          {strength && (
            <div style={{ marginTop: '0.4rem', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <div style={{ flex: 1, height: 4, background: 'rgba(148,163,184,0.18)', borderRadius: 2 }}>
                <div style={{
                  width: `${strength.pct}%`,
                  height: '100%',
                  background: strength.color,
                  borderRadius: 2,
                  transition: 'width 0.2s, background 0.2s',
                }} />
              </div>
              <span style={{ fontSize: '0.7rem', color: strength.color, fontWeight: 600 }}>
                {strength.label}
              </span>
            </div>
          )}
        </div>

        <label style={{
          display: 'flex',
          alignItems: 'flex-start',
          gap: '0.5rem',
          fontSize: '0.78rem',
          color: 'rgba(203,213,225,0.78)',
          lineHeight: 1.5,
          cursor: 'pointer',
        }}>
          <input
            type="checkbox"
            checked={form.accepted_terms}
            onChange={e => setForm(f => ({ ...f, accepted_terms: e.target.checked }))}
            style={{ marginTop: 3, accentColor: '#3B82F6' }}
          />
          <span>
            I have read and agree to the{' '}
            <Link to="/terms" target="_blank" style={{ color: '#3B82F6', fontWeight: 600 }}>
              Terms of Service
            </Link>{' '}and{' '}
            <Link to="/privacy" target="_blank" style={{ color: '#3B82F6', fontWeight: 600 }}>
              Privacy Policy
            </Link>. I understand Foundation is personal trading software and not
            financial advice. Trading involves risk of loss.
          </span>
        </label>

        <label style={{
          display: 'flex',
          alignItems: 'flex-start',
          gap: '0.5rem',
          fontSize: '0.78rem',
          color: 'rgba(203,213,225,0.78)',
          lineHeight: 1.5,
          cursor: 'pointer',
        }}>
          <input
            type="checkbox"
            checked={form.accepted_risk_acknowledgment}
            onChange={e => setForm(f => ({ ...f, accepted_risk_acknowledgment: e.target.checked }))}
            style={{ marginTop: 3, accentColor: '#3B82F6' }}
          />
          <span>
            I understand Foundation is <strong>experimental software</strong> run by a
            sole operator who has technical ability to read my encrypted broker keys.
            I will not connect a Oanda key with withdraw permission, and I take
            full responsibility for my trading outcomes.
          </span>
        </label>

        <button type="submit" disabled={loading} className="auth-btn">
          {loading ? 'Creating account…' : 'Create Account'}
        </button>
      </form>
    </AuthScaffold>
  )
}
