import { useEffect, useState } from 'react'
import { Link, useSearchParams, useNavigate } from 'react-router-dom'
import axios from 'axios'
import AuthScaffold from '../components/AuthScaffold.jsx'

/**
 * /verify?token=... — auto-fires email verification on mount.
 *
 * Three outcomes the user might see:
 *   - "Verifying your email…"     (in-flight)
 *   - "Your email is verified. Redirecting to your dashboard…" + 3s delay → /
 *   - "This link expired or has already been used."
 *
 * The verify-email endpoint is public — no auth required, just a valid
 * single-use token from the welcome email. After a successful verify,
 * the user may or may not be logged in (token in localStorage); we
 * redirect to `/` either way and let RequireAuth send them to login if
 * they're not authenticated.
 */
export default function VerifyEmail() {
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const token = searchParams.get('token') || ''

  // 'pending' | 'success' | 'expired' | 'error'
  const [status, setStatus] = useState(token ? 'pending' : 'expired')
  const [errMsg, setErrMsg] = useState('')

  useEffect(() => {
    if (!token) return
    let cancelled = false
    ;(async () => {
      try {
        await axios.post('/api/auth/verify-email', { token })
        if (cancelled) return
        setStatus('success')
        setTimeout(() => {
          if (!cancelled) navigate('/', { replace: true })
        }, 3000)
      } catch (e) {
        if (cancelled) return
        const s = e?.response?.status
        if (s === 400 || s === 410 || s === 404) {
          setStatus('expired')
        } else {
          setStatus('error')
          setErrMsg(e?.response?.data?.detail || 'Verification could not be completed.')
        }
      }
    })()
    return () => { cancelled = true }
  }, [token, navigate])

  if (status === 'pending') {
    return (
      <AuthScaffold title="Verifying your email…">
        <div style={{
          display: 'flex',
          justifyContent: 'center',
          padding: '0.5rem',
        }}>
          <div style={{
            width: 28,
            height: 28,
            border: '3px solid rgba(200,146,42,0.18)',
            borderTopColor: '#c8922a',
            borderRadius: '50%',
            animation: 'spin 0.9s linear infinite',
          }} />
          <style>{`
            @keyframes spin { to { transform: rotate(360deg); } }
          `}</style>
        </div>
      </AuthScaffold>
    )
  }

  if (status === 'success') {
    return (
      <AuthScaffold
        title="Email verified"
        subtitle="Redirecting you to the dashboard…"
      >
        <p style={{
          textAlign: 'center',
          fontSize: '0.85rem',
          color: 'rgba(203,213,225,0.78)',
          margin: 0,
        }}>
          You can <Link to="/" className="auth-link">go now</Link> if you don&apos;t want to wait.
        </p>
      </AuthScaffold>
    )
  }

  if (status === 'expired') {
    return (
      <AuthScaffold
        title="Link expired"
        subtitle="This verification link is no longer valid."
        footer={<Link to="/login" className="auth-link">Sign in</Link>}
      >
        <p style={{
          fontSize: '0.85rem',
          color: 'rgba(203,213,225,0.78)',
          lineHeight: 1.6,
          margin: 0,
          textAlign: 'center',
        }}>
          Verification links are good for 24 hours. Sign in, and we&apos;ll
          let you request a new one from the dashboard.
        </p>
      </AuthScaffold>
    )
  }

  // status === 'error'
  return (
    <AuthScaffold
      title="Something went wrong"
      footer={<Link to="/login" className="auth-link">Back to sign in</Link>}
    >
      <p style={{
        fontSize: '0.85rem',
        color: '#fca5a5',
        margin: 0,
        textAlign: 'center',
      }}>
        {errMsg}
      </p>
    </AuthScaffold>
  )
}
