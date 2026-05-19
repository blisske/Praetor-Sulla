import { useEffect, useState } from 'react'
import api from '../lib/api.js'
import HelpTip from '../components/HelpTip.jsx'

const BLUE = "#3B82F6"

const MASTER_TIP = (
  "Sulla tunes its own parameters from live shadow performance. " +
  "Lifecycle: (1) gate — a symbol needs 10+ closed shadow trades before any proposal can fire. " +
  "(2) candidate selection — the tuner picks one parameter and one bounded candidate value per cycle. " +
  "(3) shadow validation — the candidate must accumulate 10 more closed trades while running in shadow. " +
  "(4) promotion — only if Profit Factor improves ≥5% vs. baseline does the change write back to Config.yaml. " +
  "Otherwise it's rejected and the baseline stays."
)

const STATUS_TIPS = {
  SHADOW_PENDING: "Validating: the candidate value is running in shadow alongside the baseline, accumulating the 10 closed-trade window required before promotion or rejection.",
  PROMOTED_LIVE:  "Promoted: this candidate beat the baseline on Profit Factor by ≥5%. Config.yaml has been updated and the new value is now live.",
  REJECTED:       "Rejected: validation completed but the candidate did not clear the ≥5% Profit Factor improvement threshold. Baseline remains in force.",
  PENDING:        "Awaiting validation start — proposal logged but the shadow window has not yet begun accumulating closed trades.",
}

const MIN_TRADES = 10  // per (symbol, paradigm); mirrors tuning.min_trades_to_tune

export default function Tuning() {
  const [data, setData] = useState({ log: [], snapshots: [], progress: [] })

  useEffect(() => {
    api.get('/tuning').then(r => setData(r.data))
  }, [])

  const statusColor = s => {
    if (s === 'PROMOTED_LIVE')   return '#4ade80'
    if (s === 'REJECTED')        return '#f87171'
    if (s === 'SHADOW_PENDING')  return BLUE
    return 'var(--text-sub)'
  }

  return (
    <div className="p-6 space-y-6">
      <div>
        <h1 className="text-xl font-bold" style={{ color:'var(--text-primary)', display:'inline-flex', alignItems:'center' }}>
          Self-Tuning Monitor
          <HelpTip position="bottom" width={340} text={MASTER_TIP} />
        </h1>
        <p className="text-sm mt-0.5" style={{ color:'var(--text-muted)' }}>Autonomous parameter optimization log</p>
      </div>

      {/* Active validations */}
      {data.snapshots.length > 0 && (
        <div className="rounded-xl p-5" style={{ background:'var(--bg-surface)', border:'1px solid var(--border)' }}>
          <h2 className="text-sm font-medium mb-4" style={{ color:'var(--text-sub)' }}>Active Shadow Validations</h2>
          <table className="w-full text-sm">
            <thead>
              <tr style={{ borderBottom:'1px solid var(--border-row)' }}>
                {['Symbol','Parameter','Candidate','Baseline','Progress'].map(h => (
                  <th key={h} className="text-left px-3 py-2 text-xs uppercase tracking-wider" style={{ color:'var(--text-muted)' }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.snapshots.map(s => (
                <tr key={s.id} style={{ borderBottom:'1px solid var(--border-row)' }}>
                  <td className="px-3 py-2.5 font-medium" style={{ color:'var(--text-primary)' }}>{s.symbol}</td>
                  <td className="px-3 py-2.5 text-xs" style={{ color:'var(--text-sub)' }}>{s.parameter}</td>
                  <td className="px-3 py-2.5" style={{ color:BLUE, fontWeight:600 }}>{s.candidate_value}</td>
                  <td className="px-3 py-2.5" style={{ color:'var(--text-sub)' }}>{s.baseline_value}</td>
                  <td className="px-3 py-2.5">
                    <div className="flex items-center gap-2">
                      <div className="flex-1 h-1.5 rounded-full overflow-hidden" style={{ background:'var(--bg-elevated)', minWidth:60 }}>
                        <div className="h-full rounded-full" style={{ width:`${Math.min(100,(s.shadow_trades_completed/s.shadow_trades_required)*100)}%`, background:BLUE }} />
                      </div>
                      <span className="text-xs" style={{ color:'var(--text-muted)' }}>{s.shadow_trades_completed}/{s.shadow_trades_required}</span>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Progress to threshold — per (symbol, paradigm) */}
      <div className="rounded-xl p-5" style={{ background:'var(--bg-surface)', border:'1px solid var(--border)' }}>
        <div className="flex items-center gap-2 mb-4">
          <h2 className="text-sm font-medium" style={{ color:'var(--text-sub)' }}>Progress to Tuning Threshold</h2>
          <HelpTip text={`Each row shows closed shadow trades for a (symbol × paradigm) combination. The tuner only runs on paradigms with at least ${MIN_TRADES} closes. Below threshold are accumulating; at threshold or above are evaluated each cycle.`} />
        </div>
        {(!data.progress || data.progress.length === 0) ? (
          <div className="p-6 text-center text-sm" style={{ color:'var(--text-dim)' }}>
            No closed shadow trades yet
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr style={{ borderBottom:'1px solid var(--border-row)' }}>
                {['Pair','Paradigm','Closes','Progress'].map(h => (
                  <th key={h} className="text-left px-3 py-2 text-xs uppercase tracking-wider" style={{ color:'var(--text-muted)' }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.progress.map((p, i) => {
                const pct = Math.min(100, (p.closed_count / MIN_TRADES) * 100)
                const eligible = p.closed_count >= MIN_TRADES
                return (
                  <tr key={`${p.symbol}-${p.strategy}-${i}`} style={{ borderBottom:'1px solid var(--border-row)' }}>
                    <td className="px-3 py-2.5 font-medium" style={{ color:'var(--text-primary)' }}>{p.symbol}</td>
                    <td className="px-3 py-2.5 text-xs" style={{ color:'var(--text-sub)' }}>{p.strategy}</td>
                    <td className="px-3 py-2.5" style={{ color: eligible ? BLUE : 'var(--text-sub)', fontWeight: eligible ? 600 : 400 }}>
                      {p.closed_count}/{MIN_TRADES}
                    </td>
                    <td className="px-3 py-2.5">
                      <div className="h-1.5 rounded-full overflow-hidden" style={{ background:'var(--bg-elevated)' }}>
                        <div className="h-full rounded-full" style={{ width:`${pct}%`, background: eligible ? BLUE : '#f59e0b' }} />
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* Tuning log */}
      <div className="rounded-xl overflow-hidden" style={{ background:'var(--bg-surface)', border:'1px solid var(--border)' }}>
        <div className="px-5 py-3" style={{ borderBottom:'1px solid var(--border-row)' }}>
          <h2 className="text-sm font-medium" style={{ color:'var(--text-sub)' }}>Tuning History</h2>
        </div>
        {data.log.length === 0 ? (
          <div className="p-8 text-center" style={{ color:'var(--text-dim)' }}>
            No tuning events yet — engine needs {10}+ closed shadow trades per (symbol × paradigm) to begin
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr style={{ borderBottom:'1px solid var(--border-row)' }}>
                {['Time','Symbol','Parameter','Old','New','Metric','Status'].map(h => (
                  <th key={h} className="text-left px-4 py-2.5 text-xs uppercase tracking-wider" style={{ color:'var(--text-muted)' }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.log.map(r => (
                <tr key={r.id} style={{ borderBottom:'1px solid var(--border-row)' }}
                  onMouseEnter={e => e.currentTarget.style.background = 'var(--bg-elevated)'}
                  onMouseLeave={e => e.currentTarget.style.background = 'transparent'}>
                  <td className="px-4 py-3 text-xs whitespace-nowrap" style={{ color:'var(--text-sub)' }}>{new Date(r.timestamp).toLocaleString()}</td>
                  <td className="px-4 py-3 font-medium" style={{ color:'var(--text-primary)' }}>{r.symbol}</td>
                  <td className="px-4 py-3 text-xs" style={{ color:'var(--text-sub)' }}>{r.parameter}</td>
                  <td className="px-4 py-3" style={{ color:'var(--text-muted)' }}>{r.old_value}</td>
                  <td className="px-4 py-3 font-medium" style={{ color:BLUE }}>{r.new_value}</td>
                  <td className="px-4 py-3 text-xs" style={{ color:'var(--text-sub)' }}>{r.metric_name}: {r.metric_value?.toFixed(3)}</td>
                  <td className="px-4 py-3">
                    <span style={{ fontSize:11, fontWeight:600, color:statusColor(r.status), display:'inline-flex', alignItems:'center' }}>
                      {r.status}
                      {STATUS_TIPS[r.status] && <HelpTip width={260} text={STATUS_TIPS[r.status]} />}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
