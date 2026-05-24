import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import api from '../../lib/api.js'
import { ArrowLeft, RefreshCw, CheckCircle2, XCircle, Clock } from 'lucide-react'

/**
 * /admin/provisioner — daemon health + audit log tail.
 *
 * Polls every 10s while the page is open. Surfaces:
 *   - Whether the audit log file is present (proxy for "daemon has run at
 *     least once"; the daemon writes the log)
 *   - Pending flag files in the queue dir (i.e., the daemon hasn't picked
 *     them up yet — should drain within 5s normally)
 *   - The last N audit log entries with provision/teardown actions and
 *     their ok/error status
 */
export default function AdminProvisioner() {
  const [data, setData]     = useState(null)
  const [err, setErr]       = useState('')
  const [tick, setTick]     = useState(0)
  const [tail, setTail]     = useState(50)

  useEffect(() => {
    let cancelled = false
    setErr('')
    api.get(`/admin/provisioner?tail=${tail}`)
      .then(r => { if (!cancelled) setData(r.data) })
      .catch(e => {
        if (cancelled) return
        if (e?.response?.status === 403) setErr('Admin access required.')
        else                              setErr('Could not load provisioner status.')
      })
    return () => { cancelled = true }
  }, [tick, tail])

  // Auto-refresh every 10s
  useEffect(() => {
    const id = setInterval(() => setTick(t => t + 1), 10000)
    return () => clearInterval(id)
  }, [])

  return (
    <div className="px-6 py-6 max-w-5xl">
      <Link to="/admin" style={{
        color: 'var(--text-muted)', textDecoration: 'none', fontSize: '0.84rem',
        display: 'inline-flex', alignItems: 'center', gap: 4,
      }}>
        <ArrowLeft size={14} /> Admin
      </Link>

      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginTop: '1rem', marginBottom: '1.5rem' }}>
        <div>
          <h1 className="text-2xl font-bold text-[var(--text-primary)] tracking-tight">
            Provisioner
          </h1>
          <p className="text-sm text-[var(--text-muted)] mt-1">
            Host-side daemon that spins up / tears down per-user engine containers.
          </p>
        </div>
        <button onClick={() => setTick(t => t + 1)} style={{
          background: 'transparent', color: 'var(--text-muted)',
          border: '1px solid var(--border)', padding: '0.5rem 0.9rem',
          fontSize: '0.82rem', fontWeight: 600, borderRadius: '0.35rem', cursor: 'pointer',
          display: 'inline-flex', alignItems: 'center', gap: 6,
        }}>
          <RefreshCw size={13} />
          Refresh
        </button>
      </div>

      {err && (
        <div style={{
          background: 'rgba(239,68,68,0.09)', border: '1px solid rgba(239,68,68,0.22)',
          color: '#fca5a5', fontSize: '0.85rem', borderRadius: '0.3rem',
          padding: '0.7rem 0.9rem',
        }}>
          {err}
        </div>
      )}

      {!data && !err && (
        <p style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>Loading…</p>
      )}

      {data && (
        <>
          {/* Top-row tiles */}
          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
            gap: '0.85rem',
            marginBottom: '1.5rem',
          }}>
            <Tile label="Audit log">
              {data.audit_log_present ? (
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: '0.86rem', color: '#22c55e' }}>
                  <CheckCircle2 size={14} /> Present
                </div>
              ) : (
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: '0.86rem', color: 'var(--text-muted)' }}>
                  <XCircle size={14} /> Not yet written
                </div>
              )}
              <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: 4, fontFamily: 'ui-monospace, monospace' }}>
                {data.audit_log_path}
              </div>
            </Tile>

            <Tile label="Recent audit lines">
              <div style={{ fontSize: '1.6rem', fontWeight: 700, color: 'var(--text-primary)' }}>
                {data.recent?.length || 0}
              </div>
              <div style={{ fontSize: '0.74rem', color: 'var(--text-muted)' }}>
                most recent {tail}
              </div>
            </Tile>

            <Tile label="Queue pending">
              <div style={{
                fontSize: '1.6rem', fontWeight: 700,
                color: (data.queue_pending?.length || 0) > 0 ? '#f59e0b' : 'var(--text-primary)',
              }}>
                {data.queue_pending?.length || 0}
              </div>
              <div style={{ fontSize: '0.74rem', color: 'var(--text-muted)' }}>
                unresolved flags
              </div>
            </Tile>
          </div>

          {/* Pending queue */}
          {data.queue_pending?.length > 0 && (
            <div style={{ marginBottom: '1.5rem' }}>
              <SectionHeader>
                <Clock size={13} style={{ display: 'inline', marginRight: 6 }} />
                Pending queue
              </SectionHeader>
              <div style={{
                background: 'var(--bg-surface)', border: '1px solid var(--border)',
                borderRadius: '0.4rem', padding: '0.7rem 1rem',
                fontFamily: 'ui-monospace, "SF Mono", monospace', fontSize: '0.82rem',
                color: '#fcd34d',
              }}>
                {data.queue_pending.join('  ·  ')}
              </div>
              <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '0.4rem' }}>
                Daemon should pick these up within 5 seconds. If they persist, check{' '}
                <code>systemctl status foundation-provisioner</code> on the host.
              </p>
            </div>
          )}

          {/* Audit table */}
          <div>
            <SectionHeader>Audit log (most recent {data.recent?.length || 0})</SectionHeader>
            {data.recent?.length === 0 ? (
              <p style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>
                No records yet. The daemon writes here on every provision / teardown.
              </p>
            ) : (
              <div style={{
                background: 'var(--bg-surface)', border: '1px solid var(--border)',
                borderRadius: '0.4rem', overflow: 'hidden',
              }}>
                <table style={{ width: '100%', fontSize: '0.82rem', borderCollapse: 'collapse' }}>
                  <thead>
                    <tr style={{ background: 'var(--bg-elevated)' }}>
                      <Th>Time</Th><Th>Action</Th><Th>User</Th><Th>Status</Th><Th>Detail</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {[...data.recent].reverse().map((r, i) => (
                      <tr key={i}>
                        <Td dim>{r.ts ? new Date(r.ts).toLocaleString() : '—'}</Td>
                        <Td><code style={{ fontSize: '0.78rem' }}>{r.action}</code></Td>
                        <Td><Link to={`/admin/users/${r.user_id}`} style={{ color: '#c8922a', textDecoration: 'none' }}>#{r.user_id}</Link></Td>
                        <Td>
                          {r.ok ? (
                            <span style={{ color: '#22c55e', fontSize: '0.78rem', fontWeight: 600 }}>OK</span>
                          ) : (
                            <span style={{ color: '#fca5a5', fontSize: '0.78rem', fontWeight: 600 }}>FAIL</span>
                          )}
                        </Td>
                        <Td dim style={{ fontFamily: 'ui-monospace, monospace', fontSize: '0.74rem' }}>
                          {r.detail || '—'}
                        </Td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  )
}


function Tile({ label, children }) {
  return (
    <div style={{
      background: 'var(--bg-surface)', border: '1px solid var(--border)',
      borderRadius: '0.4rem', padding: '0.85rem 1rem',
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


function SectionHeader({ children }) {
  return (
    <h2 style={{
      fontSize: '0.78rem', fontWeight: 700, letterSpacing: '0.12em',
      textTransform: 'uppercase', color: '#c8922a',
      margin: 0, marginBottom: '0.7rem',
    }}>
      {children}
    </h2>
  )
}


function Th({ children }) {
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


function Td({ children, dim, style }) {
  return (
    <td style={{
      padding: '0.55rem 0.8rem',
      borderBottom: '1px solid var(--border)',
      color: dim ? 'var(--text-muted)' : 'var(--text-primary)',
      verticalAlign: 'middle',
      ...style,
    }}>
      {children}
    </td>
  )
}
