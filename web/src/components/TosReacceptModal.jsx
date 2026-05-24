import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import api from '../lib/api.js'

/**
 * TosReacceptModal — surfaces a blocking modal when the operator bumps
 * CURRENT_TOS_VERSION and the logged-in user's last-accepted version
 * doesn't match.
 *
 * Self-fetching: calls /api/auth/me on mount + after dismiss. The
 * tos_needs_reaccept field drives whether to render.
 *
 * Behavior:
 *   - Modal is blocking (can't dismiss without action) when the version
 *     differs. The user must either accept (POST /api/auth/accept-tos)
 *     OR sign out.
 *   - The full ToS body is fetched from /api/legal/tos and rendered
 *     scrollable inside the modal so the user can scan it before
 *     accepting.
 *   - Operator (you) is also subject to this — you'll see it once on
 *     next dashboard load because your account was seeded before any
 *     ToS existed (tos_version_accepted: null).
 *   - Demo users (is_demo=true) are exempted — we don't surface a
 *     legal acceptance to throwaway demo tokens.
 */
export default function TosReacceptModal({ user, isDemo, onAccepted, onSignOut }) {
  // Fetched lazily — null until needed
  const [me, setMe] = useState(null)
  const [docBody, setDocBody] = useState('')
  const [docVersion, setDocVersion] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  // Fetch /api/auth/me to learn the user's ToS state
  useEffect(() => {
    if (isDemo) return  // demos never see this modal
    if (!user) return
    let cancelled = false
    api.get('/auth/me')
      .then(r => { if (!cancelled) setMe(r.data) })
      .catch(() => {})
    return () => { cancelled = true }
  }, [user?.id, isDemo])

  // Once we know we need re-accept, fetch the doc body too
  useEffect(() => {
    if (!me?.tos_needs_reaccept) return
    let cancelled = false
    api.get('/legal/tos')
      .then(r => {
        if (cancelled) return
        setDocBody(r.data.body_markdown)
        setDocVersion(r.data.version)
      })
      .catch(() => {})
    return () => { cancelled = true }
  }, [me?.tos_needs_reaccept])

  if (isDemo) return null
  if (!me) return null
  if (!me.tos_needs_reaccept) return null

  const accept = async () => {
    setBusy(true); setErr('')
    try {
      await api.post('/auth/accept-tos', { version: me.tos_version_current })
      // Re-fetch /me to confirm + clear the modal
      const fresh = await api.get('/auth/me')
      setMe(fresh.data)
      if (onAccepted) onAccepted()
    } catch (e) {
      setErr(e?.response?.data?.detail || 'Could not record acceptance. Try again.')
    } finally {
      setBusy(false)
    }
  }

  const signOut = () => {
    if (onSignOut) onSignOut()
  }

  const isFirstAccept = !me.tos_version_accepted
  const headline = isFirstAccept
    ? 'Welcome — please accept our Terms before continuing'
    : 'Updated Terms of Service'
  const subhead = isFirstAccept
    ? 'Your account was created before our current Terms were finalized. Please review and accept them to continue using Foundation.'
    : `We've updated our Terms of Service. You last accepted version ${me.tos_version_accepted}; the current version is ${me.tos_version_current}. Review the latest terms below.`

  return (
    <div style={{
      position: 'fixed', inset: 0,
      background: 'rgba(0,0,0,0.85)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      zIndex: 9999, padding: '1rem',
    }}>
      <div style={{
        width: '100%', maxWidth: 720,
        maxHeight: '90vh',
        background: 'var(--bg-surface)',
        border: '1px solid var(--border)',
        borderTop: '3px solid #3B82F6',
        borderRadius: '0.5rem',
        display: 'flex', flexDirection: 'column',
        overflow: 'hidden',
      }}>
        <div style={{ padding: '1.5rem 1.75rem 0.5rem' }}>
          <h2 style={{
            fontSize: '1.25rem', fontWeight: 700,
            color: 'var(--text-primary)', margin: 0, marginBottom: '0.5rem',
          }}>
            {headline}
          </h2>
          <p style={{
            fontSize: '0.86rem', color: 'var(--text-muted)',
            margin: 0, marginBottom: '1rem', lineHeight: 1.55,
          }}>
            {subhead}
          </p>
        </div>

        <div style={{
          flex: 1, overflow: 'auto',
          padding: '0 1.75rem',
          background: 'var(--bg-elevated)',
          margin: '0 0 0.5rem',
          borderTop: '1px solid var(--border)',
          borderBottom: '1px solid var(--border)',
        }}>
          {docBody ? (
            <pre style={{
              fontFamily: 'inherit', whiteSpace: 'pre-wrap',
              fontSize: '0.78rem', color: 'var(--text-primary)',
              lineHeight: 1.55, margin: 0, padding: '1rem 0',
            }}>
              {docBody}
            </pre>
          ) : (
            <p style={{ fontSize: '0.82rem', color: 'var(--text-muted)', padding: '1rem 0' }}>
              Loading Terms…
            </p>
          )}
        </div>

        <div style={{ padding: '0.5rem 1.75rem 1.5rem' }}>
          {err && (
            <div style={{
              background: 'rgba(239,68,68,0.09)', border: '1px solid rgba(239,68,68,0.22)',
              color: '#fca5a5', fontSize: '0.82rem', borderRadius: '0.3rem',
              padding: '0.55rem 0.75rem', marginBottom: '0.75rem',
            }}>
              {err}
            </div>
          )}
          <p style={{
            fontSize: '0.78rem', color: 'var(--text-muted)',
            margin: 0, marginBottom: '0.9rem', lineHeight: 1.5,
          }}>
            Also available as a standalone page:{' '}
            <Link to="/terms" target="_blank" style={{ color: '#3B82F6', fontWeight: 600 }}>
              Open in new tab
            </Link>
            {' · '}
            <Link to="/privacy" target="_blank" style={{ color: '#3B82F6', fontWeight: 600 }}>
              Privacy Policy
            </Link>
          </p>
          <div style={{ display: 'flex', gap: '0.6rem', justifyContent: 'flex-end' }}>
            <button onClick={signOut} disabled={busy} style={{
              background: 'transparent', color: 'var(--text-muted)',
              border: '1px solid var(--border)', padding: '0.65rem 1.2rem',
              borderRadius: '0.35rem', fontSize: '0.83rem', fontWeight: 600,
              cursor: 'pointer', opacity: busy ? 0.5 : 1,
            }}>
              Sign out
            </button>
            <button onClick={accept} disabled={busy || !docBody} style={{
              background: 'linear-gradient(180deg, #3B82F6 0%, #1D4ED8 100%)',
              color: '#1a1a1a', border: 'none',
              padding: '0.65rem 1.4rem',
              fontSize: '0.85rem', fontWeight: 700,
              letterSpacing: '0.06em', textTransform: 'uppercase',
              borderRadius: '0.35rem', cursor: 'pointer',
              opacity: (busy || !docBody) ? 0.5 : 1,
            }}>
              {busy ? 'Recording…' : `Accept Version ${docVersion || ''}`.trim()}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
