import { Link } from 'react-router-dom'
import { Eye } from 'lucide-react'
import { useAuth } from '../lib/auth.jsx'

/**
 * Banner that surfaces at the top of authenticated pages when the user
 * is in demo mode (JWT carries is_demo=true).
 *
 * Renders nothing for regular users — zero footprint for paid/operator
 * traffic.
 */
export default function DemoModeBanner() {
  const { user, logout } = useAuth()
  if (!user?.is_demo) return null

  const exit = () => {
    logout()
    window.location.assign('/login')
  }

  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      gap: '0.85rem',
      background: 'linear-gradient(135deg, rgba(247,147,26,0.10) 0%, rgba(247,147,26,0.04) 100%)',
      border: '1px solid rgba(247,147,26,0.28)',
      borderLeft: '3px solid #F7931A',
      borderRadius: '0.4rem',
      padding: '0.7rem 1rem',
      marginBottom: '1rem',
      flexWrap: 'wrap',
    }}>
      <Eye size={16} style={{ color: '#F7931A', flexShrink: 0 }} />
      <div style={{ flex: 1, minWidth: 0, fontSize: '0.85rem', color: 'var(--text-primary)' }}>
        <strong>Demo mode.</strong>{' '}
        <span style={{ color: 'var(--text-muted)' }}>
          You&apos;re viewing read-only paper-trading data. Write actions are disabled.
        </span>
      </div>
      <div style={{ display: 'flex', gap: '0.5rem', flexShrink: 0 }}>
        <Link to="/signup" style={{
          background: 'linear-gradient(180deg, #3B82F6 0%, #1D4ED8 100%)',
          color: '#1a1a1a',
          textDecoration: 'none',
          padding: '0.35rem 0.9rem',
          fontSize: '0.78rem',
          fontWeight: 700,
          letterSpacing: '0.06em',
          textTransform: 'uppercase',
          borderRadius: '0.3rem',
        }}>
          Create account
        </Link>
        <button onClick={exit} style={{
          background: 'transparent',
          color: 'var(--text-muted)',
          border: '1px solid var(--border)',
          padding: '0.32rem 0.85rem',
          fontSize: '0.78rem',
          fontWeight: 600,
          borderRadius: '0.3rem',
          cursor: 'pointer',
        }}>
          Exit demo
        </button>
      </div>
    </div>
  )
}
