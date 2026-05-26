import { useEffect, useState } from 'react'
import api from '../lib/api.js'

const BLUE = '#3B82F6'

export default function Trades() {
  const [trades, setTrades] = useState([])
  const [filter, setFilter] = useState('ALL')

  useEffect(() => {
    api.get('/trades?limit=100').then(r => setTrades(r.data.trades))
  }, [])

  const filtered = filter === 'ALL' ? trades : trades.filter(t => t.action.includes(filter))

  return (
    <div className="p-6 space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold" style={{ color:'var(--text-primary)' }}>Trade History</h1>
          <p className="text-sm mt-0.5" style={{ color:'var(--text-muted)' }}>{trades.length} records</p>
        </div>
        <div className="flex gap-2">
          {['ALL', 'BUY', 'SELL'].map(f => (
            <button key={f} onClick={() => setFilter(f)}
              className="px-3 py-1.5 rounded-lg text-xs font-medium transition-colors"
              style={filter === f ? { background:BLUE, color:'#fff' } : { background:'var(--bg-elevated)', color:'var(--text-sub)' }}>
              {f}
            </button>
          ))}
        </div>
      </div>

      <div className="rounded-xl overflow-hidden" style={{ background:'var(--bg-surface)', border:'1px solid var(--border)' }}>
        <table className="w-full text-sm">
          <thead>
            <tr style={{ borderBottom:'1px solid var(--border-row)' }}>
              {['Time', 'Pair', 'Action', 'Price', 'Units', 'Fee', 'Strategy', 'Result'].map(h => (
                <th key={h} className="text-left px-4 py-3 text-xs font-medium uppercase tracking-wider" style={{ color:'var(--text-muted)' }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {filtered.map(t => {
              const isBuyAdd = t.action.includes('BUY ADD')
              const isBuy    = t.action.includes('BUY') && !isBuyAdd
              const verdict  = t.verdict || ''
              const pnlMatch = verdict.match(/([+-]?\d+\.?\d*)%/)
              const pnl      = pnlMatch ? parseFloat(pnlMatch[1]) : null
              const isWin    = pnl !== null && pnl > 0
              // On buys, amount = shares. On sells, amount = pnl_usd
              const shares   = (isBuy || isBuyAdd) ? t.amount : null

              const chipStyle = isBuyAdd
                ? { background:'transparent', color:BLUE, border:`1.5px solid ${BLUE}` }
                : isBuy
                  ? { background:'rgba(59,130,246,0.15)', color:BLUE, border:'1px solid rgba(59,130,246,0.25)' }
                  : { background:'var(--bg-elevated)', color:'var(--text-sub)' }

              return (
                <tr key={t.id} className="transition-colors" style={{ borderBottom:'1px solid var(--border-row)' }}
                  onMouseEnter={e => e.currentTarget.style.background = 'var(--bg-elevated)'}
                  onMouseLeave={e => e.currentTarget.style.background = 'transparent'}>
                  <td className="px-4 py-3 text-xs whitespace-nowrap" style={{ color:'var(--text-sub)' }}>
                    {new Date(t.timestamp).toLocaleString()}
                  </td>
                  <td className="px-4 py-3 font-medium" style={{ color:'var(--text-primary)' }}>{t.symbol}</td>
                  <td className="px-4 py-3">
                    <span className="px-2 py-0.5 rounded-full text-xs font-medium" style={chipStyle}>
                      {t.action}
                    </span>
                  </td>
                  <td className="px-4 py-3" style={{ color:'var(--text-primary)' }}>
                    ${t.price?.toLocaleString(undefined, { maximumFractionDigits:2 })}
                  </td>
                  <td className="px-4 py-3 font-medium" style={{ color:'var(--text-primary)', fontFamily:"'JetBrains Mono', monospace" }}>
                    {shares != null && shares > 0
                      ? `${shares} sh`
                      : <span style={{ color:'var(--text-dim)' }}>—</span>
                    }
                  </td>
                  {/* Fee — added 2026-05-26. Oanda revenue is spread-based,
                      not commission. Shadow fills carry SHADOW_FEE_RATE *
                      position_size_usd from database.log_trade (~1bp/leg). */}
                  <td className="px-4 py-3 text-xs" style={{ color:'var(--text-sub)', fontFamily:"'JetBrains Mono', monospace" }}>
                    {t.fee_usd != null && t.fee_usd > 0
                      ? `$${t.fee_usd.toFixed(2)}`
                      : <span style={{ color:'var(--text-dim)' }}>—</span>
                    }
                  </td>
                  <td className="px-4 py-3 text-xs" style={{ color:'var(--text-sub)' }}>{t.strategy}</td>
                  <td className="px-4 py-3">
                    {pnl !== null ? (
                      <span style={{ fontWeight:600, color: isWin ? '#4ade80' : '#f87171' }}>
                        {pnl > 0 ? '+' : ''}{pnl.toFixed(1)}%
                      </span>
                    ) : <span style={{ color:'var(--text-dim)' }}>—</span>}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
