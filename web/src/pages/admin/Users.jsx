import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import api from '../../lib/api.js'
import { ShieldCheck, ShieldAlert, Circle, Trash2, ChevronRight } from 'lucide-react'

/**
 * /admin — operator user list.
 *
 * GET /api/admin/users returns full rows including per-user broker +
 * engine summary. We render a table with quick-glance status badges and
 * link each row to /admin/users/:id for the detail view.
 */
export default function AdminUsers() {
  const [users, setUsers]     = useState(null)
  const [showDeleted, setShowDeleted] = useState(false)
  const [err, setErr]         = useState('')

  useEffect(() => {
    let cancelled = false
    setErr('')
    api.get(`/admin/users${showDeleted ? '?include_deleted=true' : ''}`)
      .then(r => { if (!cancelled) setUsers(r.data) })
      .catch(e => {
        if (cancelled) return
        if (e?.response?.status === 403) setErr('Admin access required.')
        else setErr('Could not load users.')
      })
    return () => { cancelled = true }
  }, [showDeleted])

  return (
    <div className="px-6 py-6 max-w-6xl">
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '1.5rem' }}>
        <div>
          <h1 className="text-2xl font-bold text-[var(--text-primary)] tracking-tight">Users</h1>
          <p className="text-sm text-[var(--text-muted)] mt-1">
            All Foundation accounts and their broker + engine status.
          </p>
        </div>
        <label style={{
          display: 'flex', alignItems: 'center', gap: '0.5rem',
          fontSize: '0.82rem', color: 'var(--text-muted)', cursor: 'pointer',
        }}>
          <input
            type="checkbox"
            checked={showDeleted}
            onChange={e => setShowDeleted(e.target.checked)}
            style={{ accentColor: '#3B82F6' }}
          />
          Include soft-deleted
        </label>
      </div>

      {err && (
        <div style={{
          background: 'rgba(239,68,68,0.09)', border: '1px solid rgba(239,68,68,0.22)',
          color: '#fca5a5', fontSize: '0.82rem', borderRadius: '0.3rem',
          padding: '0.55rem 0.75rem', marginBottom: '1rem',
        }}>
          {err}
        </div>
      )}

      {!users && !err && (
        <p style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>Loading users…</p>
      )}

      {users && users.length === 0 && (
        <p style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>No users yet.</p>
      )}

      {users && users.length > 0 && (
        <div style={{
          background: 'var(--bg-surface)',
          border: '1px solid var(--border)',
          borderRadius: '0.5rem',
          overflow: 'hidden',
        }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.86rem' }}>
            <thead>
              <tr style={{ background: 'var(--bg-elevated)' }}>
                <Th>ID</Th>
                <Th>Email</Th>
                <Th>Role</Th>
                <Th>Broker</Th>
                <Th>Engine</Th>
                <Th>Created</Th>
                <Th />
              </tr>
            </thead>
            <tbody>
              {users.map(u => <UserRow key={u.id} user={u} />)}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}


function Th({ children }) {
  return (
    <th style={{
      textAlign: 'left',
      padding: '0.6rem 0.85rem',
      fontSize: '0.7rem',
      fontWeight: 600,
      letterSpacing: '0.1em',
      textTransform: 'uppercase',
      color: 'var(--text-sub)',
      borderBottom: '1px solid var(--border)',
    }}>
      {children}
    </th>
  )
}


function Td({ children, dim }) {
  return (
    <td style={{
      padding: '0.7rem 0.85rem',
      borderBottom: '1px solid var(--border)',
      color: dim ? 'var(--text-muted)' : 'var(--text-primary)',
      fontSize: '0.86rem',
      verticalAlign: 'middle',
    }}>
      {children}
    </td>
  )
}


function UserRow({ user }) {
  const deleted = !!user.deleted_at
  const hbAge = user.engine?.heartbeat_age_seconds
  const engineLive = hbAge != null && hbAge < 120

  return (
    <tr style={{ opacity: deleted ? 0.55 : 1 }}>
      <Td dim>#{user.id}</Td>
      <Td>
        {deleted && <Trash2 size={11} style={{ display: 'inline', marginRight: 4, color: '#fca5a5' }} />}
        {user.email}
        {!user.email_verified && (
          <span style={{
            marginLeft: '0.5rem', fontSize: '0.66rem', color: '#f59e0b',
            background: 'rgba(245,158,11,0.10)', padding: '0.05rem 0.4rem', borderRadius: 4,
          }}>
            unverified
          </span>
        )}
      </Td>
      <Td>
        {user.is_admin ? (
          <span style={{
            fontSize: '0.7rem', color: '#3B82F6', fontWeight: 700, letterSpacing: '0.06em',
            background: 'rgba(59,130,246,0.12)', padding: '0.1rem 0.45rem', borderRadius: 4,
          }}>
            ADMIN
          </span>
        ) : (
          <span style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>user</span>
        )}
      </Td>
      <Td>
        <BrokerBadge broker={user.broker} />
      </Td>
      <Td>
        <EngineBadge engine={user.engine} live={engineLive} />
      </Td>
      <Td dim>
        <span style={{ fontSize: '0.78rem' }}>
          {user.created_at ? new Date(user.created_at + 'Z').toLocaleDateString() : '—'}
        </span>
      </Td>
      <Td>
        <Link to={`/admin/users/${user.id}`} style={{
          color: '#3B82F6', textDecoration: 'none', fontSize: '0.82rem', fontWeight: 600,
          display: 'inline-flex', alignItems: 'center', gap: 2,
        }}>
          Detail <ChevronRight size={14} />
        </Link>
      </Td>
    </tr>
  )
}


function BrokerBadge({ broker }) {
  if (!broker?.connected) {
    return <span style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>—</span>
  }
  const isTrade = broker.scope === 'trade'
  const color = isTrade ? '#22c55e' : '#f59e0b'
  const Icon  = isTrade ? ShieldCheck : ShieldAlert
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, color, fontSize: '0.78rem', fontWeight: 600 }}>
      <Icon size={13} />
      {broker.scope}
      {broker.last_error && (
        <span style={{ color: '#dc2626', marginLeft: 4 }}>·err</span>
      )}
    </span>
  )
}


function EngineBadge({ engine, live }) {
  if (!engine?.config_present) {
    return (
      <span style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>
        <Circle size={9} style={{ display: 'inline', marginRight: 4 }} />
        no workspace
      </span>
    )
  }
  if (engine.provision_pending) {
    return <span style={{ fontSize: '0.78rem', color: '#f59e0b' }}>provisioning…</span>
  }
  if (engine.teardown_pending) {
    return <span style={{ fontSize: '0.78rem', color: '#fca5a5' }}>tearing down…</span>
  }
  const color = live ? '#22c55e' : 'var(--text-muted)'
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, color, fontSize: '0.78rem' }}>
      <span style={{
        width: 7, height: 7, borderRadius: '50%', background: color,
      }} />
      {live ? 'live' : (engine.heartbeat ? `${formatAge(engine.heartbeat_age_seconds)} ago` : 'never')}
    </span>
  )
}


function formatAge(sec) {
  if (sec == null) return '—'
  if (sec < 60)    return `${sec}s`
  if (sec < 3600)  return `${Math.floor(sec / 60)}m`
  if (sec < 86400) return `${Math.floor(sec / 3600)}h`
  return `${Math.floor(sec / 86400)}d`
}
