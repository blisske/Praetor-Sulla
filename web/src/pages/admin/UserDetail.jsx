import { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import api from '../../lib/api.js'
import { ArrowLeft, RotateCw, Mail, ShieldCheck, ShieldAlert } from 'lucide-react'

/**
 * /admin/users/:id — single-user admin view.
 *
 * Loads two payloads:
 *   GET /api/admin/users/{id}              — user + broker_events (last 50)
 *   GET /api/admin/users/{id}/login-attempts — last 50 login attempts
 *
 * Plus an action: POST /api/admin/users/{id}/restart-engine — touch the
 * user's .restart_engine flag (mirrors the operator's own restart button
 * from the legacy dashboard, but admin-targeted at any user_id).
 */
export default function AdminUserDetail() {
  const { id } = useParams()
  const userId = parseInt(id, 10)
  const [user, setUser]         = useState(null)
  const [logins, setLogins]     = useState(null)
  const [err, setErr]           = useState('')
  const [restartMsg, setRestartMsg] = useState('')
  const [tick, setTick]         = useState(0)

  useEffect(() => {
    let cancelled = false
    setErr('')
    Promise.all([
      api.get(`/admin/users/${userId}`).then(r => r.data),
      api.get(`/admin/users/${userId}/login-attempts`).then(r => r.data),
    ]).then(([u, l]) => {
      if (cancelled) return
      setUser(u); setLogins(l.attempts)
    }).catch(e => {
      if (cancelled) return
      if (e?.response?.status === 404)      setErr('User not found.')
      else if (e?.response?.status === 403) setErr('Admin access required.')
      else                                   setErr('Could not load user.')
    })
    return () => { cancelled = true }
  }, [userId, tick])

  const restartEngine = async () => {
    setRestartMsg('')
    try {
      const { data } = await api.post(`/admin/users/${userId}/restart-engine`)
      setRestartMsg(data.ok ? '✓ Restart flag touched. Engine will exit cleanly on next cycle.' : data.detail || 'Nothing to restart.')
    } catch (e) {
      setRestartMsg(e?.response?.data?.detail || 'Restart failed.')
    }
  }

  if (err) {
    return (
      <div className="px-6 py-6 max-w-5xl">
        <BackLink />
        <div style={{
          background: 'rgba(239,68,68,0.09)', border: '1px solid rgba(239,68,68,0.22)',
          color: '#fca5a5', fontSize: '0.85rem', borderRadius: '0.3rem',
          padding: '0.7rem 0.9rem', marginTop: '1rem',
        }}>
          {err}
        </div>
      </div>
    )
  }

  if (!user) {
    return (
      <div className="px-6 py-6 max-w-5xl">
        <BackLink />
        <p style={{ fontSize: '0.85rem', color: 'var(--text-muted)', marginTop: '1rem' }}>
          Loading user…
        </p>
      </div>
    )
  }

  const hbAge = user.engine?.heartbeat_age_seconds
  const engineLive = hbAge != null && hbAge < 120

  return (
    <div className="px-6 py-6 max-w-5xl">
      <BackLink />

      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginTop: '1rem', marginBottom: '1.5rem' }}>
        <div>
          <h1 className="text-2xl font-bold text-[var(--text-primary)] tracking-tight">
            #{user.id} · {user.email}
            {user.is_admin && (
              <span style={{
                marginLeft: '0.6rem', fontSize: '0.65rem', color: '#3B82F6', fontWeight: 700, letterSpacing: '0.1em',
                background: 'rgba(59,130,246,0.12)', padding: '0.12rem 0.5rem', borderRadius: 4,
                verticalAlign: 'middle',
              }}>
                ADMIN
              </span>
            )}
          </h1>
          <p className="text-sm text-[var(--text-muted)] mt-1">
            {user.email_verified ? '✓ Verified' : '○ Unverified'} ·
            Joined {user.created_at ? new Date(user.created_at + 'Z').toLocaleDateString() : '—'} ·
            Last login: {user.last_login_at ? new Date(user.last_login_at + 'Z').toLocaleString() : 'never'}
          </p>
        </div>
        <button onClick={restartEngine} style={{
          background: 'linear-gradient(180deg, #3B82F6 0%, #1D4ED8 100%)',
          color: '#1a1a1a', border: 'none', padding: '0.55rem 1rem',
          fontSize: '0.82rem', fontWeight: 700, letterSpacing: '0.06em',
          textTransform: 'uppercase', borderRadius: '0.35rem', cursor: 'pointer',
          display: 'inline-flex', alignItems: 'center', gap: 6,
        }}>
          <RotateCw size={14} />
          Restart Engine
        </button>
      </div>

      {restartMsg && (
        <div style={{
          background: restartMsg.startsWith('✓') ? 'rgba(34,197,94,0.08)' : 'rgba(245,158,11,0.08)',
          border: `1px solid ${restartMsg.startsWith('✓') ? 'rgba(34,197,94,0.25)' : 'rgba(245,158,11,0.25)'}`,
          color: restartMsg.startsWith('✓') ? '#86efac' : '#fcd34d',
          fontSize: '0.82rem', borderRadius: '0.3rem',
          padding: '0.55rem 0.75rem', marginBottom: '1rem',
        }}>
          {restartMsg}
        </div>
      )}

      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
        gap: '0.85rem',
        marginBottom: '1.5rem',
      }}>
        <Tile label="Broker">
          {user.broker?.connected ? (
            <BrokerCard broker={user.broker} />
          ) : (
            <span style={{ fontSize: '0.84rem', color: 'var(--text-muted)' }}>Not connected</span>
          )}
        </Tile>
        <Tile label="Engine">
          <EngineCard engine={user.engine} live={engineLive} />
        </Tile>
        <Tile label="Account">
          <div style={{ fontSize: '0.82rem', color: 'var(--text-primary)' }}>
            {user.deleted_at ? (
              <span style={{ color: '#fca5a5' }}>
                Soft-deleted {new Date(user.deleted_at + 'Z').toLocaleDateString()}
              </span>
            ) : (
              'Active'
            )}
          </div>
        </Tile>
      </div>

      <EventsTable title="Broker key events" events={user.broker_events} />
      <LoginsTable logins={logins} />
    </div>
  )
}


function BackLink() {
  return (
    <Link to="/admin" style={{
      color: 'var(--text-muted)', textDecoration: 'none', fontSize: '0.84rem',
      display: 'inline-flex', alignItems: 'center', gap: 4,
    }}>
      <ArrowLeft size={14} /> All users
    </Link>
  )
}


function Tile({ label, children }) {
  return (
    <div style={{
      background: 'var(--bg-surface)',
      border: '1px solid var(--border)',
      borderRadius: '0.4rem',
      padding: '0.85rem 1rem',
    }}>
      <div style={{
        fontSize: '0.66rem', fontWeight: 700, letterSpacing: '0.12em',
        textTransform: 'uppercase', color: 'var(--text-sub)', marginBottom: '0.4rem',
      }}>
        {label}
      </div>
      {children}
    </div>
  )
}


function BrokerCard({ broker }) {
  const isTrade = broker.scope === 'trade'
  const color = isTrade ? '#22c55e' : '#f59e0b'
  const Icon  = isTrade ? ShieldCheck : ShieldAlert
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: '0.86rem', color }}>
      <Icon size={14} />
      <span style={{ fontWeight: 600 }}>{broker.scope}</span>
      {broker.last_error && (
        <span style={{ color: '#fca5a5', fontSize: '0.74rem', marginLeft: 6 }}>
          err: {broker.last_error.slice(0, 30)}…
        </span>
      )}
    </div>
  )
}


function EngineCard({ engine, live }) {
  if (!engine?.config_present) {
    return <span style={{ fontSize: '0.84rem', color: 'var(--text-muted)' }}>No workspace</span>
  }
  const color = live ? '#22c55e' : 'var(--text-muted)'
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: '0.84rem' }}>
      <span style={{ width: 8, height: 8, borderRadius: '50%', background: color }} />
      <span style={{ color }}>
        {engine.provision_pending ? 'provisioning…'
          : engine.teardown_pending ? 'tearing down…'
          : live ? 'live'
          : (engine.heartbeat ? `${formatAge(engine.heartbeat_age_seconds)} ago` : 'never')}
      </span>
    </div>
  )
}


function EventsTable({ title, events }) {
  if (!events || events.length === 0) {
    return (
      <div style={{ marginBottom: '1.5rem' }}>
        <SectionHeader>{title}</SectionHeader>
        <p style={{ fontSize: '0.82rem', color: 'var(--text-muted)' }}>No events recorded.</p>
      </div>
    )
  }
  return (
    <div style={{ marginBottom: '1.5rem' }}>
      <SectionHeader>{title} <span style={{ color: 'var(--text-muted)' }}>({events.length})</span></SectionHeader>
      <div style={{
        background: 'var(--bg-surface)', border: '1px solid var(--border)', borderRadius: '0.4rem', overflow: 'hidden',
      }}>
        <table style={{ width: '100%', fontSize: '0.82rem', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ background: 'var(--bg-elevated)' }}>
              <Th2>Time</Th2><Th2>Type</Th2><Th2>Broker</Th2><Th2>IP</Th2><Th2>Detail</Th2>
            </tr>
          </thead>
          <tbody>
            {events.map(e => (
              <tr key={e.id}>
                <Td2 dim>{e.timestamp ? new Date(e.timestamp + 'Z').toLocaleString() : '—'}</Td2>
                <Td2>{e.event_type}</Td2>
                <Td2 dim>{e.broker}</Td2>
                <Td2 dim>{e.ip || '—'}</Td2>
                <Td2 dim>{e.detail || '—'}</Td2>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}


function LoginsTable({ logins }) {
  if (!logins || logins.length === 0) {
    return (
      <div>
        <SectionHeader>Recent login attempts</SectionHeader>
        <p style={{ fontSize: '0.82rem', color: 'var(--text-muted)' }}>No login attempts recorded.</p>
      </div>
    )
  }
  return (
    <div>
      <SectionHeader>Recent login attempts <span style={{ color: 'var(--text-muted)' }}>({logins.length})</span></SectionHeader>
      <div style={{
        background: 'var(--bg-surface)', border: '1px solid var(--border)', borderRadius: '0.4rem', overflow: 'hidden',
      }}>
        <table style={{ width: '100%', fontSize: '0.82rem', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ background: 'var(--bg-elevated)' }}>
              <Th2>Time</Th2><Th2>Result</Th2><Th2>IP</Th2>
            </tr>
          </thead>
          <tbody>
            {logins.map(a => (
              <tr key={a.id}>
                <Td2 dim>{a.timestamp ? new Date(a.timestamp + 'Z').toLocaleString() : '—'}</Td2>
                <Td2>
                  <span style={{
                    fontSize: '0.74rem', fontWeight: 600, letterSpacing: '0.05em',
                    color: a.result === 'SUCCESS' ? '#22c55e' : '#fca5a5',
                  }}>
                    {a.result}
                  </span>
                </Td2>
                <Td2 dim>{a.ip}</Td2>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}


function SectionHeader({ children }) {
  return (
    <h2 style={{
      fontSize: '0.78rem', fontWeight: 700, letterSpacing: '0.12em',
      textTransform: 'uppercase', color: '#3B82F6',
      margin: 0, marginBottom: '0.7rem',
    }}>
      {children}
    </h2>
  )
}

function Th2({ children }) {
  return (
    <th style={{
      textAlign: 'left', padding: '0.5rem 0.8rem',
      fontSize: '0.66rem', fontWeight: 600, letterSpacing: '0.1em',
      textTransform: 'uppercase', color: 'var(--text-sub)',
      borderBottom: '1px solid var(--border)',
    }}>
      {children}
    </th>
  )
}

function Td2({ children, dim }) {
  return (
    <td style={{
      padding: '0.55rem 0.8rem',
      borderBottom: '1px solid var(--border)',
      color: dim ? 'var(--text-muted)' : 'var(--text-primary)',
      verticalAlign: 'middle',
    }}>
      {children}
    </td>
  )
}

function formatAge(sec) {
  if (sec == null) return '—'
  if (sec < 60)    return `${sec}s`
  if (sec < 3600)  return `${Math.floor(sec / 60)}m`
  if (sec < 86400) return `${Math.floor(sec / 3600)}h`
  return `${Math.floor(sec / 86400)}d`
}
