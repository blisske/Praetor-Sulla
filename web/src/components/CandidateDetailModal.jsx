// Shared across the Praetor swarm (Anton/Tiberius/Ionic). Identical contents
// per bot — drift detector enforces parity. Renders the inspect + reject
// surface for a single tuning candidate.
//
// Color choices: GREEN (#10B981) for "good" / candidate / positive P&L,
// RED (#f87171) for "bad" / reject / negative P&L. These are semantic, not
// brand colors — the bots' brand accents stay on their own dashboards.

import { useEffect, useState } from 'react'
import api from '../lib/api.js'
import HelpTip from './HelpTip.jsx'

const GREEN = '#10B981'
const RED   = '#f87171'

export default function CandidateDetailModal({ logId, onClose, onRejected }) {
  const [detail, setDetail] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [confirming, setConfirming] = useState(false)
  const [reason, setReason] = useState('')
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    setLoading(true)
    api.get(`/tuning/candidate/${logId}`)
      .then(r => { setDetail(r.data); setLoading(false) })
      .catch(e => { setError(e?.response?.data?.detail || e.message); setLoading(false) })
  }, [logId])

  useEffect(() => {
    const onKey = e => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  async function doReject() {
    setSubmitting(true)
    try {
      await api.post(`/tuning/candidate/${logId}/reject`, { reason: reason || null })
      onRejected()
    } catch (e) {
      setError(e?.response?.data?.detail || e.message)
      setSubmitting(false)
    }
  }

  const log  = detail?.log
  const snap = detail?.snapshot
  const isActive = snap && snap.status === 'VALIDATING'

  return (
    <div style={{
      position:'fixed', inset:0, background:'rgba(0,0,0,0.6)',
      display:'flex', alignItems:'center', justifyContent:'center', zIndex:50, padding:16,
    }} onClick={onClose}>
      <div onClick={e => e.stopPropagation()} style={{
        background:'var(--bg-surface)', border:'1px solid var(--border)', borderRadius:12,
        width:'100%', maxWidth:760, maxHeight:'90vh', overflow:'auto', padding:24,
      }}>
        {loading && <div style={{ color:'var(--text-muted)' }}>Loading…</div>}
        {error && !loading && <div style={{ color:RED }}>Error: {error}</div>}

        {detail && log && (
          <>
            <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start', marginBottom:16 }}>
              <div>
                <h2 style={{ fontSize:18, fontWeight:700, color:'var(--text-primary)' }}>
                  {log.symbol} · <span style={{ color:'var(--text-sub)', fontWeight:500 }}>{log.parameter}</span>
                </h2>
                <div style={{ fontSize:12, color:'var(--text-muted)', marginTop:4 }}>
                  Proposed {new Date(log.timestamp).toLocaleString()}
                  {detail.strategy_filter && <> · paradigm <strong>{detail.strategy_filter}</strong></>}
                </div>
              </div>
              <button onClick={onClose} style={{
                background:'transparent', border:'none', color:'var(--text-muted)',
                fontSize:20, cursor:'pointer', padding:'0 8px',
              }}>×</button>
            </div>

            <div style={{ display:'grid', gridTemplateColumns:'repeat(4, 1fr)', gap:12, marginBottom:20 }}>
              <Stat label="Baseline"  value={log.old_value} />
              <Stat label="Candidate" value={log.new_value} color={GREEN} />
              <Stat label={log.metric_name || 'metric'} value={log.metric_value?.toFixed(3)} />
              <Stat label="Status"    value={log.status} color={log.status === 'REJECTED' ? RED : log.status === 'PROMOTED_LIVE' ? '#4ade80' : GREEN} />
            </div>

            {snap && (
              <div style={{ fontSize:12, color:'var(--text-muted)', marginBottom:16 }}>
                Validation: <strong style={{ color:'var(--text-sub)' }}>
                  {snap.shadow_trades_completed} / {snap.shadow_trades_required}
                </strong> closes accumulated since proposal
              </div>
            )}

            {log.rejection_reason && (
              <div style={{
                background:'var(--bg-elevated)', border:`1px solid ${RED}`,
                borderRadius:6, padding:12, marginBottom:16, fontSize:13, color:'var(--text-sub)',
              }}>
                <strong style={{ color:RED }}>Operator rejection reason:</strong> {log.rejection_reason}
              </div>
            )}

            <TradeTable
              title={`Driving trades (${detail.driving_trades.length})`}
              hint="The closed shadow trades the tuner scored when proposing this change. Limited to the same paradigm if the parameter is paradigm-specific."
              trades={detail.driving_trades}
            />

            {detail.recent_trades.length > 0 && (
              <TradeTable
                title={`Trades since proposal (${detail.recent_trades.length}, counting toward validation)`}
                hint="Closed trades AFTER the proposal was made. These accumulate toward the shadow validation window; promotion or auto-rejection decides once the count reaches the required threshold."
                trades={detail.recent_trades}
              />
            )}

            {isActive && !confirming && (
              <div style={{ marginTop:24, display:'flex', justifyContent:'flex-end', gap:8 }}>
                <button onClick={onClose} style={btnStyle()}>Close</button>
                <button onClick={() => setConfirming(true)} style={btnStyle('danger')}>Reject candidate…</button>
              </div>
            )}

            {isActive && confirming && (
              <div style={{ marginTop:24, padding:16, background:'var(--bg-elevated)', borderRadius:8, border:`1px solid ${RED}` }}>
                <div style={{ fontSize:13, color:'var(--text-sub)', marginBottom:8 }}>
                  Rejecting kills this proposal. The cooling-off window (~48h) prevents the
                  tuner from re-proposing the same (symbol, parameter) immediately.
                </div>
                <textarea
                  value={reason}
                  onChange={e => setReason(e.target.value)}
                  placeholder="Optional: why are you rejecting? (e.g. 'tightens stop too much in this regime')"
                  rows={3}
                  style={{
                    width:'100%', background:'var(--bg-surface)', color:'var(--text-primary)',
                    border:'1px solid var(--border)', borderRadius:6, padding:8, fontSize:13,
                    resize:'vertical',
                  }}
                />
                <div style={{ display:'flex', justifyContent:'flex-end', gap:8, marginTop:12 }}>
                  <button onClick={() => { setConfirming(false); setReason('') }}
                          disabled={submitting} style={btnStyle()}>Cancel</button>
                  <button onClick={doReject} disabled={submitting}
                          style={btnStyle('danger')}>
                    {submitting ? 'Rejecting…' : 'Confirm reject'}
                  </button>
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}

function Stat({ label, value, color }) {
  return (
    <div style={{ background:'var(--bg-elevated)', borderRadius:6, padding:12 }}>
      <div style={{ fontSize:10, textTransform:'uppercase', letterSpacing:'0.05em', color:'var(--text-muted)' }}>{label}</div>
      <div style={{ fontSize:16, fontWeight:600, marginTop:4, color: color || 'var(--text-primary)' }}>{String(value ?? '—')}</div>
    </div>
  )
}

function TradeTable({ title, hint, trades }) {
  return (
    <div style={{ marginTop:20 }}>
      <div style={{ display:'flex', alignItems:'center', gap:6, marginBottom:8 }}>
        <h3 style={{ fontSize:13, fontWeight:600, color:'var(--text-sub)' }}>{title}</h3>
        {hint && <HelpTip text={hint} />}
      </div>
      {trades.length === 0 ? (
        <div style={{ fontSize:12, color:'var(--text-muted)', padding:8 }}>—</div>
      ) : (
        <table className="w-full text-xs">
          <thead>
            <tr style={{ borderBottom:'1px solid var(--border-row)' }}>
              {['#','Time','Strategy','P&L %','P&L $','Verdict'].map(h => (
                <th key={h} className="text-left px-2 py-1.5" style={{ color:'var(--text-muted)', fontWeight:500 }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {trades.map(t => (
              <tr key={t.id} style={{ borderBottom:'1px solid var(--border-row)' }}>
                <td className="px-2 py-1.5" style={{ color:'var(--text-muted)' }}>{t.id}</td>
                <td className="px-2 py-1.5 whitespace-nowrap" style={{ color:'var(--text-sub)' }}>
                  {t.timestamp ? new Date(t.timestamp).toLocaleString() : '—'}
                </td>
                <td className="px-2 py-1.5" style={{ color:'var(--text-sub)' }}>{t.strategy || '—'}</td>
                <td className="px-2 py-1.5" style={{ color: (t.pnl_pct ?? 0) >= 0 ? GREEN : RED, fontWeight:500 }}>
                  {t.pnl_pct == null ? '—' : `${t.pnl_pct >= 0 ? '+' : ''}${t.pnl_pct.toFixed(2)}%`}
                </td>
                <td className="px-2 py-1.5" style={{ color: (t.pnl_usd ?? 0) >= 0 ? GREEN : RED }}>
                  {t.pnl_usd == null ? '—' : `${t.pnl_usd >= 0 ? '+$' : '-$'}${Math.abs(t.pnl_usd).toFixed(2)}`}
                </td>
                <td className="px-2 py-1.5" style={{ color:'var(--text-muted)' }} title={t.verdict || ''}>
                  {(t.verdict || '').slice(0, 40)}{(t.verdict || '').length > 40 ? '…' : ''}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

function btnStyle(variant) {
  const base = {
    padding:'8px 14px', borderRadius:6, fontSize:13, fontWeight:500,
    cursor:'pointer', border:'1px solid var(--border)',
    background:'var(--bg-elevated)', color:'var(--text-sub)',
  }
  if (variant === 'danger') return { ...base, background:RED, borderColor:RED, color:'white' }
  return base
}
