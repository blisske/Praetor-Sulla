import { useEffect, useState } from 'react'
import { Navigate, Link } from 'react-router-dom'
import axios from 'axios'
import AuthScaffold from '../components/AuthScaffold.jsx'

/**
 * /demo — public-no-auth entry point.
 *
 * On mount we POST /api/demo/login (no credentials required, rate-limited
 * server-side), receive a short-lived JWT with `is_demo: true` in claims,
 * store it in localStorage, then hard-navigate to / so the AuthProvider
 * picks up the token + the Dashboard renders the demo data.
 *
 * Once the redirect fires this component never re-renders. The "still
 * fetching" / "error" states below cover the brief in-flight window plus
 * the failure path when the backend doesn't have a demo user seeded yet
 * (returns 503 — operator hasn't run the migration script).
 */
export default function DemoMode() {
  const [status, setStatus] = useState('loading')   // 'loading' | 'error' | 'rate-limited' | 'unavailable'
  const [error, setError]   = useState('')

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        // Use bare axios so we don't accidentally send a stale Bearer
        // from a previous session — the demo login is unauthenticated.
        const { data } = await axios.post('/api/demo/login')
        if (cancelled) return
        localStorage.setItem('token', data.token)
        // Hard-navigate so AuthProvider re-reads from localStorage and
        // RequireAuth no longer bounces us to /login.
        window.location.assign('/?demo=true')
      } catch (e) {
        if (cancelled) return
        const s = e?.response?.status
        if (s === 503) {
          setStatus('unavailable')
          setError(e?.response?.data?.detail || 'Demo mode is unavailable.')
        } else if (s === 429) {
          setStatus('rate-limited')
          setError('Too many demo logins from this network. Try again in an hour.')
        } else {
          setStatus('error')
          setError(e?.response?.data?.detail || 'Could not start demo. Please try again.')
        }
      }
    })()
    return () => { cancelled = true }
  }, [])

  if (status === 'loading') {
    return (
      <AuthScaffold title="Loading demo…">
        <div style={{ display: 'flex', justifyContent: 'center', padding: '0.5rem' }}>
          <div style={{
            width: 28, height: 28,
            border: '3px solid rgba(59,130,246,0.18)',
            borderTopColor: '#3B82F6',
            borderRadius: '50%',
            animation: 'spin 0.9s linear infinite',
          }} />
          <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
        </div>
      </AuthScaffold>
    )
  }

  return (
    <AuthScaffold
      title={status === 'unavailable' ? 'Demo unavailable' : 'Could not start demo'}
      subtitle={error}
      footer={<>
        <Link to="/login" className="auth-link">Sign in</Link>
        {' · '}
        <Link to="/signup" className="auth-link">Create an account</Link>
      </>}
    >
      <p style={{
        fontSize: '0.84rem', color: 'rgba(203,213,225,0.78)',
        lineHeight: 1.6, margin: 0, textAlign: 'center',
      }}>
        {status === 'rate-limited'
          ? 'The demo gets a lot of traffic. Sign up for a free account to skip the line — it takes about 10 seconds.'
          : 'In the meantime, you can create a free account in about 10 seconds.'}
      </p>
    </AuthScaffold>
  )
}
