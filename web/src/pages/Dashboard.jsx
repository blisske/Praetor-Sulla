import { useEffect, useState } from 'react'
import api from '../lib/api.js'
import { TrendingUp, TrendingDown, Activity, DollarSign, Layers, Clock, Sparkles, Check, X, AlertTriangle } from 'lucide-react'
import HelpTip from '../components/HelpTip.jsx'
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts'

const BLUE = '#3B82F6'

function RiskModeBanner({ equity, lastTick }) {
  const mode      = lastTick?.risk_mode ?? equity?.risk_mode ?? 'NORMAL'
  const dailyHalt = Boolean(lastTick?.daily_halt ?? equity?.daily_halt ?? false)
  const drawdown  = lastTick?.drawdown_pct ?? equity?.drawdown_pct ?? 0
  const eq        = lastTick?.shadow_equity ?? equity?.shadow_equity ?? 0
  const peak      = equity?.peak_equity ?? 0

  if (mode === 'NORMAL' && !dailyHalt) return null

  const styles = {
    ALERT:  { bg:'rgba(245,158,11,0.12)', border:'rgba(245,158,11,0.40)', color:'#f59e0b', icon:'⚠️', label:'ALERT' },
    DERISK: { bg:'rgba(251,146,60,0.14)', border:'rgba(251,146,60,0.45)', color:'#fb923c', icon:'🟡', label:'DERISK' },
    HALT:   { bg:'rgba(239,68,68,0.14)',  border:'rgba(239,68,68,0.50)',  color:'#ef4444', icon:'🚨', label:'HALT' },
  }

  const isTier = mode !== 'NORMAL'
  const style  = isTier ? styles[mode] : { bg:'rgba(239,68,68,0.10)', border:'rgba(239,68,68,0.35)', color:'#f87171', icon:'🛑', label:'DAILY HALT' }

  let message
  if (isTier && mode === 'ALERT')   message = `Drawdown ${drawdown.toFixed(1)}% from peak (${peak ? `$${peak.toLocaleString()}` : '—'}). Monitoring.`
  if (isTier && mode === 'DERISK')  message = `Drawdown ${drawdown.toFixed(1)}% from peak. Position sizing × 0.5 until recovery.`
  if (isTier && mode === 'HALT')    message = `Drawdown ${drawdown.toFixed(1)}% from peak. Trading halted — use /resume after review.`
  if (!isTier && dailyHalt)         message = `Intraday loss limit reached. New entries blocked rest of session. Auto-resume next session.`

  return (
    <div style={{
      background: style.bg, border: `1px solid ${style.border}`,
      borderRadius: 10, padding: '12px 16px',
      display: 'flex', alignItems: 'center', gap: 12,
    }}>
      <span style={{ fontSize: 22, lineHeight: 1 }}>{style.icon}</span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontWeight: 700, fontSize: 13, letterSpacing: '0.06em', color: style.color }}>
          RISK MODE: {style.label}
          {isTier && dailyHalt && (
            <span style={{ marginLeft: 8, fontSize: 11, opacity: 0.85 }}>+ DAILY HALT</span>
          )}
        </div>
        <div style={{ fontSize: 12, color: 'var(--text-sub)', marginTop: 3 }}>{message}</div>
      </div>
      <div style={{ textAlign: 'right', fontFamily: "'JetBrains Mono', monospace", fontSize: 12 }}>
        <div style={{ color: 'var(--text-primary)', fontWeight: 700 }}>${eq.toLocaleString(undefined, {minimumFractionDigits:2,maximumFractionDigits:2})}</div>
        <div style={{ color: 'var(--text-muted)', fontSize: 10 }}>shadow equity</div>
      </div>
    </div>
  )
}

function LiveEventStrip({ event }) {
  if (!event) return null
  const { kind, data } = event
  let icon, color, bg, border, headline, detail
  if (kind === 'trade') {
    const isBuy  = (data.action || '').includes('BUY')
    const isSell = (data.action || '').includes('SELL')
    icon = isBuy ? '🟢' : (isSell ? '🔴' : '⚪')
    color  = isBuy ? '#10B981' : (isSell ? '#ef4444' : '#94a3b8')
    bg     = isBuy ? 'rgba(16,185,129,0.10)' : (isSell ? 'rgba(239,68,68,0.10)' : 'rgba(148,163,184,0.10)')
    border = `${color}40`
    headline = `${data.action} — ${data.symbol}`
    const px = typeof data.price === 'number' ? data.price.toFixed(5) : ''
    const qty = typeof data.amount === 'number' ? data.amount : ''
    detail = isSell
      ? `${qty} P&L @ ${px} · ${data.strategy || ''}`
      : `${qty} units @ ${px} · ${data.strategy || ''}`
  } else if (kind === 'risk_transition') {
    icon = '⚠️'
    color  = '#f59e0b'
    bg     = 'rgba(245,158,11,0.10)'
    border = `${color}40`
    headline = `Risk Mode: ${data.from} → ${data.to}`
    detail = data.reason || 'Risk state changed'
  } else {
    return null
  }
  return (
    <div style={{
      background: bg, border: `1px solid ${border}`, borderRadius: 10,
      padding: '10px 16px', display: 'flex', alignItems: 'center', gap: 12,
    }}>
      <span style={{ fontSize: 18, lineHeight: 1 }}>{icon}</span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontWeight: 600, fontSize: 13, color, letterSpacing: '0.02em' }}>{headline}</div>
        <div style={{ fontSize: 11, color: 'var(--text-sub)', marginTop: 2, fontFamily: "'JetBrains Mono', monospace" }}>{detail}</div>
      </div>
      <span style={{ fontSize: 10, color: 'var(--text-muted)', letterSpacing: '0.08em' }}>LIVE</span>
    </div>
  )
}

function SessionBadge({ session }) {
  if (!session) return null
  const colors = {
    'Open':            { bg: 'rgba(34,197,94,0.12)',  color: '#22c55e', dot: '#22c55e' },
    'Closed':          { bg: 'rgba(100,116,139,0.15)',color: '#94a3b8', dot: '#94a3b8' },
  }
  const c = colors[session.status] ?? colors['Closed']
  return (
    <span style={{ display:'inline-flex', alignItems:'center', gap:5, background:c.bg, color:c.color, border:`1px solid ${c.color}33`, padding:'2px 8px', borderRadius:999, fontSize:11, fontWeight:600 }}>
      <span style={{ width:6, height:6, borderRadius:'50%', background:c.dot, flexShrink:0 }} />
      {session.status}
      {session.closes_in_minutes != null && ` · closes in ${session.closes_in_minutes}m`}
      {session.opens_in_minutes  != null && ` · opens in ${session.opens_in_minutes}m`}
    </span>
  )
}

function MarketRail({ market, symbols }) {
  return (
    <div style={{ background:'var(--bg-rail)', border:'1px solid var(--border)', borderRadius:10, overflow:'hidden' }}>
      <div style={{ background:'var(--bg-rail-hdr)', borderBottom:'1px solid var(--border)', padding:'8px 16px', display:'flex', alignItems:'center', gap:8 }}>
        <div style={{ width:6, height:6, borderRadius:'50%', background:'#22c55e' }} />
        <span style={{ fontSize:10, fontWeight:600, color:'var(--text-muted)', letterSpacing:'0.12em', textTransform:'uppercase' }}>
          Ionic · Live Market Feed · Oanda
        </span>
        <span style={{ marginLeft:'auto', fontSize:10, color:'var(--text-dim)' }}>{symbols.length} symbols</span>
      </div>
      <div style={{ display:'grid', gridTemplateColumns:'repeat(auto-fill, minmax(140px, 1fr))' }}>
        {symbols.map((sym) => {
          const d = market.latest?.[sym]
          const rsi = d?.rsi ?? 0
          const adx = d?.adx ?? 0
          const regime = d?.regime ?? '—'
          const price = d?.price
          const isTrending = regime === 'TRENDING'
          const rsiColor = rsi > 70 ? '#f59e0b' : isTrending ? '#22c55e' : BLUE
          return (
            <div key={sym} style={{ padding:'12px 14px', borderRight:'1px solid var(--border-deep)', borderBottom:'1px solid var(--border-deep)', display:'flex', flexDirection:'column', gap:3 }}>
              <div style={{ fontSize:10, fontWeight:700, color:'var(--text-muted)', letterSpacing:'0.1em' }}>{sym}</div>
              <div style={{ fontSize:16, fontWeight:800, color:'var(--text-primary)', letterSpacing:-0.5, fontVariantNumeric:'tabular-nums' }}>
                {price ? `$${price.toLocaleString(undefined, { maximumFractionDigits:2 })}` : '—'}
              </div>
              <div style={{ display:'flex', alignItems:'center', gap:5, marginTop:1 }}>
                <span style={{ fontSize:9, fontWeight:700, padding:'1px 5px', borderRadius:3, letterSpacing:'0.08em', background: isTrending ? 'rgba(34,197,94,0.12)' : 'rgba(71,85,105,0.2)', color: isTrending ? '#22c55e' : '#64748b' }}>{regime}</span>
                <span style={{ fontSize:9, color:'var(--text-dim)' }}>ADX {adx.toFixed(1)}</span>
              </div>
              <div style={{ height:2, background:'var(--border-deep)', borderRadius:1, marginTop:3, overflow:'hidden' }}>
                <div style={{ height:'100%', borderRadius:1, background:rsiColor, width:`${Math.min(100,rsi)}%` }} />
              </div>
              <div style={{ fontSize:9, color:'var(--text-dim)' }}>RSI {rsi.toFixed(1)}</div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function StatCard({ label, value, sub, icon: Icon, color = 'accent' }) {
  const palette = {
    accent: { bg:'rgba(59,130,246,0.18)', color:BLUE,    border:'1px solid rgba(59,130,246,0.30)' },
    green:  { bg:'rgba(34,197,94,0.15)',  color:'#22c55e', border:'1px solid rgba(34,197,94,0.25)'  },
    red:    { bg:'rgba(239,68,68,0.15)',   color:'#f87171', border:'1px solid rgba(239,68,68,0.25)'  },
    slate:  { bg:'rgba(71,85,105,0.15)',   color:'var(--text-sub)', border:'1px solid rgba(71,85,105,0.20)' },
    amber:  { bg:'rgba(245,158,11,0.15)',  color:'#f59e0b', border:'1px solid rgba(245,158,11,0.25)' },
  }
  const c = palette[color] ?? palette.accent
  return (
    <div className="rounded-xl p-5" style={{ background:'var(--bg-surface)', border:'1px solid var(--border)' }}>
      <div className="flex items-center justify-between mb-3">
        <span className="text-xs uppercase tracking-wider" style={{ color:'var(--text-sub)' }}>{label}</span>
        <div style={{ background:c.bg, color:c.color, border:c.border, padding:'0.4rem', borderRadius:'0.5rem', display:'flex' }}>
          <Icon size={14} />
        </div>
      </div>
      <div className="text-2xl font-bold" style={{ color:'var(--text-primary)', fontFamily:"'JetBrains Mono', monospace" }}>{value}</div>
      {sub && <div className="text-xs mt-1" style={{ color:'var(--text-muted)' }}>{sub}</div>}
    </div>
  )
}

function LiveDot() {
  return (
    <span style={{ display:'flex', alignItems:'center', gap:8 }}>
      <span style={{ position:'relative', display:'inline-flex' }}>
        <span style={{ position:'absolute', width:10, height:10, borderRadius:'50%', background:'#22c55e', opacity:0.6, animation:'ping 1.5s cubic-bezier(0,0,0.2,1) infinite' }} />
        <span style={{ width:10, height:10, borderRadius:'50%', background:'#22c55e', position:'relative' }} />
        <style>{`@keyframes ping { 0% { transform:scale(1); opacity:0.6; } 100% { transform:scale(2.2); opacity:0; } }`}</style>
      </span>
      <span style={{ fontFamily:"'JetBrains Mono', monospace", fontSize:18, fontWeight:700 }}>Live</span>
    </span>
  )
}

function TradeRow({ trade }) {
  const isBuyAdd = trade.action.includes('BUY ADD')
  const isBuy    = trade.action.includes('BUY') && !isBuyAdd
  const verdict  = trade.verdict || ''
  const pnlMatch = verdict.match(/([+-]?\d+\.?\d*)%/)
  const pnl      = pnlMatch ? parseFloat(pnlMatch[1]) : null
  const isWin    = pnl !== null && pnl > 0
  const shares   = trade.amount ?? 0

  // BUY ADD chips are outlined (no fill) to differentiate pyramid legs from
  // fresh entries at a glance — same color family, different shape.
  const chipStyle = isBuyAdd
    ? { background:'transparent', color:BLUE, border:`1.5px solid ${BLUE}`, padding:'1.5px 8px', borderRadius:999, fontSize:11, fontWeight:600 }
    : isBuy
      ? { background:'rgba(59,130,246,0.18)', color:BLUE, border:'1px solid rgba(59,130,246,0.28)', padding:'2px 8px', borderRadius:999, fontSize:11, fontWeight:600 }
      : { background:'var(--bg-elevated)', color:'var(--text-sub)', padding:'2px 8px', borderRadius:999, fontSize:11, fontWeight:600 }

  return (
    <div style={{ borderBottom:'1px solid var(--border-row)' }} className="flex items-center justify-between py-3 last:border-0">
      <div className="flex items-center gap-3">
        <div style={{ width:4, height:32, borderRadius:2, flexShrink:0, background: (isBuy || isBuyAdd) ? BLUE : isWin ? '#22c55e' : '#f87171' }} />
        <div>
          <div className="text-sm font-medium" style={{ color:'var(--text-primary)' }}>{trade.symbol}</div>
          <div className="text-xs" style={{ color:'var(--text-muted)' }}>{trade.strategy}</div>
        </div>
      </div>
      <div style={chipStyle}>
        {trade.action}
      </div>
      <div className="text-right">
        <div className="text-sm" style={{ color:'var(--text-primary)' }}>
          ${trade.price?.toLocaleString(undefined, { maximumFractionDigits:2 })}
        </div>
        {isBuy && shares > 0 && (
          <div style={{ fontSize:11, color:'var(--text-muted)' }}>{shares} sh</div>
        )}
        {pnl !== null && (
          <div style={{ fontSize:11, fontWeight:600, color: isWin ? '#4ade80' : '#f87171' }}>
            {pnl > 0 ? '+' : ''}{pnl.toFixed(1)}%
          </div>
        )}
      </div>
    </div>
  )
}

// Parses Ionic's verdict tag — see core/strategy.py:144-200 + core/main.py:119
// for the exact strings written at trade time. Returns null when the verdict
// doesn't carry the consensus tag (e.g. legacy or manual trades).
function parseVerdict(verdict) {
  if (!verdict) return null
  const tag = verdict.match(/\[SCORE:(\d+)\/(\d+)\s*\|\s*VOL\s+([^|]+?)\s*\|\s*RSI\s+([^|]+?)\s*\|\s*ADX\s+([^\]]+?)\]/)
  if (!tag) return null
  const verdictMatch = verdict.match(/VERDICT:\s*(BULLISH|BEARISH|NEUTRAL)/i)
  return {
    score:    parseInt(tag[1], 10),
    minScore: parseInt(tag[2], 10),
    vol:      tag[3].trim(),   // "OK (1.5x avg)" or "WEAK (...)"
    rsi:      tag[4].trim(),   // "RISING (X→Y)" or "FALLING (X→Y)"
    adx:      tag[5].trim(),   // "STRONG+RISING (V)" / "LOW+FALLING (V)" / "WEAK (...)" / "NOT RANGING (...)"
    aiVerdict: verdictMatch ? verdictMatch[1].toUpperCase() : null,
  }
}

function StatusIcon({ status }) {
  if (status === 'pass') return <Check size={14} style={{ color: '#22c55e' }} />
  if (status === 'fail') return <X size={14} style={{ color: '#f87171' }} />
  return <AlertTriangle size={14} style={{ color: '#f59e0b' }} />
}

function AnatomyRow({ index, label, value, status }) {
  return (
    <div style={{ borderBottom: '1px solid var(--border-row)' }} className="flex items-center py-3 last:border-0">
      <div style={{ width: 22, height: 22, borderRadius: '50%', background: 'var(--bg-elevated)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 11, fontWeight: 700, color: 'var(--text-muted)', flexShrink: 0 }}>
        {index}
      </div>
      <div style={{ flex: '0 0 180px', marginLeft: 14, fontSize: 11, fontWeight: 600, letterSpacing: '0.08em', color: 'var(--text-muted)', textTransform: 'uppercase' }}>
        {label}
      </div>
      <div style={{ flex: 1, fontSize: 13, color: 'var(--text-primary)', fontFamily: "'JetBrains Mono', monospace" }}>
        {value}
      </div>
      <div style={{ flexShrink: 0, marginLeft: 12 }}>
        <StatusIcon status={status} />
      </div>
    </div>
  )
}

function AnatomyPanel({ trade }) {
  const parsed = trade ? parseVerdict(trade.verdict) : null
  if (!trade || !parsed) {
    return (
      <div className="rounded-xl p-5" style={{ background:'var(--bg-surface)', border:'1px solid var(--border)' }}>
        <h2 className="text-sm font-medium" style={{ color:'var(--text-sub)' }}>
          <Sparkles size={14} style={{ display:'inline', marginRight:6, color:'#f59e0b', verticalAlign:'-2px' }} />
          Anatomy of the most recent entry
        </h2>
        <p className="text-sm text-center py-6" style={{ color:'var(--text-dim)' }}>
          {trade ? 'Last entry has no consensus tag yet — waiting for next signal.' : 'No entries yet — waiting for first signal.'}
        </p>
      </div>
    )
  }

  // VOL: "OK ..." passes, anything else fails (per strategy.py:163-167)
  const volPass = /^OK\b/.test(parsed.vol)
  // RSI: RISING passes for long entries (Ionic aborts on BEARISH so all entries are long)
  const rsiPass = /^RISING/i.test(parsed.rsi)
  // ADX: regime-appropriate strength passes (per strategy.py:187-200)
  const adxPass = /STRONG\+RISING|LOW\+FALLING/.test(parsed.adx)
  // AI verdict — BULLISH = pass, BEARISH would have aborted, NEUTRAL = warn
  const aiStatus = parsed.aiVerdict === 'BULLISH' ? 'pass'
                 : parsed.aiVerdict === 'BEARISH' ? 'fail'
                 : 'warn'

  const ts = trade.timestamp ? new Date(trade.timestamp) : null
  const tsLabel = ts ? ts.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : ''

  return (
    <div className="rounded-xl p-5" style={{ background:'var(--bg-surface)', border:'1px solid var(--border)' }}>
      <div className="flex items-center justify-between mb-2">
        <h2 className="text-sm font-medium" style={{ color:'var(--text-sub)' }}>
          <Sparkles size={14} style={{ display:'inline', marginRight:6, color:'#f59e0b', verticalAlign:'-2px' }} />
          Anatomy of the most recent entry
          <HelpTip
            position="bottom"
            width={280}
            text="Ionic's 2+1+1 consensus chain: a paradigm fires, 2-of-3 supporting signals must confirm, and the LLM cannot return BEARISH. The total must clear the configured min_consensus to enter."
          />
        </h2>
        <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-muted)', letterSpacing: '0.06em', textTransform: 'uppercase' }}>
          {trade.symbol} · {trade.strategy}{tsLabel && ` · ${tsLabel}`}
        </div>
      </div>

      <div>
        <AnatomyRow index={1} label="Paradigm Fired"   value={trade.strategy}        status="pass" />
        <AnatomyRow index={2} label="Volume Support"   value={parsed.vol}            status={volPass ? 'pass' : 'fail'} />
        <AnatomyRow index={3} label="RSI Direction"    value={parsed.rsi}            status={rsiPass ? 'pass' : 'fail'} />
        <AnatomyRow index={4} label="ADX Conviction"   value={parsed.adx}            status={adxPass ? 'pass' : 'fail'} />
        <AnatomyRow index={5} label="AI Verdict"       value={parsed.aiVerdict || '—'} status={aiStatus} />
      </div>

      <div className="flex items-center justify-between" style={{ marginTop: 14, paddingTop: 12, borderTop: '1px solid var(--border)' }}>
        <span style={{ fontSize: 11, fontWeight: 600, letterSpacing: '0.08em', color: 'var(--text-muted)', textTransform: 'uppercase' }}>
          Consensus Score
        </span>
        <span style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ fontSize: 13, fontFamily: "'JetBrains Mono', monospace", color: BLUE, fontWeight: 700 }}>
            {parsed.score} / {parsed.minScore} required
          </span>
          <span style={{ fontSize: 10, fontWeight: 700, padding: '3px 8px', borderRadius: 4, background: 'rgba(59,130,246,0.18)', color: BLUE, border: '1px solid rgba(59,130,246,0.30)', letterSpacing: '0.08em' }}>
            → ENTERED
          </span>
        </span>
      </div>
    </div>
  )
}

export default function Dashboard() {
  const [equity, setEquity]       = useState(null)
  const [positions, setPositions] = useState([])
  const [trades, setTrades]       = useState([])
  const [lastTick, setLastTick]   = useState(null)
  const [equityHistory, setEquityHistory] = useState([])
  const [market, setMarket]       = useState({ latest:{}, history:{} })
  const [session, setSession]     = useState(null)
  const [railSymbols, setRailSymbols] = useState([])
  // Most recent engine-pushed event (trade or risk_transition). Renders an
  // ephemeral flash strip that auto-clears after 6s, so the user gets
  // immediate confirmation instead of waiting for the next 5s tick.
  const [liveEvent, setLiveEvent] = useState(null)

  useEffect(() => {
    api.get('/market').then(r => setMarket(r.data))
    api.get('/watchlist').then(r => setRailSymbols(r.data.symbols || []))
    api.get('/positions').then(r => setPositions(r.data.positions))
    api.get('/session').then(r => setSession(r.data))
    Promise.all([api.get('/trades?limit=100'), api.get('/equity')]).then(([tradesRes, eqRes]) => {
      setTrades(tradesRes.data.trades)
      setEquity(eqRes.data)
    })
    // Server-side equity curve (full timeseries, no client-side window).
    // Replaced 2026-05-26 client-side reconstruction that silently
    // truncated to ~24 SHADOW SELL rows from /api/trades?limit=100 and
    // showed a phantom step at "Now".
    api.get('/equity/curve').then(r => {
      const pts = (r.data.points || []).map(p => ({
        time:   new Date(p.timestamp).toLocaleDateString(),
        equity: p.equity,
      }))
      setEquityHistory([
        { time: 'Start', equity: r.data.initial_capital },
        ...pts,
        { time: 'Now',   equity: r.data.shadow_equity_now },
      ])
    })

    const proto  = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const token  = localStorage.getItem('token') || ''
    const socket = new WebSocket(`${proto}//${window.location.host}/ws?token=${encodeURIComponent(token)}`)
    socket.onmessage = e => {
      const d = JSON.parse(e.data)
      if (d.type === 'tick') {
        setLastTick(d)
        if (d.session) setSession(d.session)
      } else if (d.type === 'trade') {
        // Engine just fired a fill. Refresh trades + equity so the recent-
        // trades list and equity card update immediately rather than after
        // the next 5s tick. Flash the event strip with a one-line summary.
        api.get('/trades?limit=100').then(r => setTrades(r.data.trades))
        api.get('/equity').then(r => setEquity(r.data))
        setLiveEvent({ kind: 'trade', data: d })
      } else if (d.type === 'risk_transition') {
        api.get('/equity').then(r => setEquity(r.data))
        setLiveEvent({ kind: 'risk_transition', data: d })
      }
    }
    return () => socket.close()
  }, [])

  // Auto-clear the live-event flash strip after 6s.
  useEffect(() => {
    if (!liveEvent) return
    const t = setTimeout(() => setLiveEvent(null), 6000)
    return () => clearTimeout(t)
  }, [liveEvent])

  const shadowEquity = lastTick?.shadow_equity ?? equity?.shadow_equity ?? 0
  const pnlUsd       = lastTick?.pnl_usd ?? equity?.pnl_usd ?? 0
  const openCount    = lastTick?.open_positions ?? positions.length
  const pnlPct       = equity ? ((pnlUsd / equity.initial_capital) * 100).toFixed(2) : '0.00'
  const isUp         = pnlUsd >= 0

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold" style={{ color:'var(--text-primary)' }}>Dashboard</h1>
          <p className="text-sm mt-0.5" style={{ color:'var(--text-muted)' }}>Shadow trading · Paper portfolio</p>
        </div>
        <SessionBadge session={session} />
      </div>

      <RiskModeBanner equity={equity} lastTick={lastTick} />

      <LiveEventStrip event={liveEvent} />

      <MarketRail market={market} symbols={railSymbols} />

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          label={<>Shadow Equity <HelpTip text="Simulated portfolio value based on paper trade P&L. Starts at configured initial capital." /></>}
          value={`$${shadowEquity.toLocaleString(undefined, { minimumFractionDigits:2, maximumFractionDigits:2 })}`}
          sub={`Started at $${equity?.initial_capital?.toLocaleString() ?? '25,000'}`}
          icon={DollarSign} color="accent"
        />
        <StatCard
          label={<>Total P&L <HelpTip text="Sum of all closed shadow trade P&L in dollars and as a percentage of starting capital." /></>}
          value={`${isUp ? '+' : ''}$${Math.abs(pnlUsd).toFixed(2)}`}
          sub={`${isUp ? '+' : ''}${pnlPct}% return`}
          icon={isUp ? TrendingUp : TrendingDown} color={isUp ? 'green' : 'red'}
        />
        <StatCard
          label={<>Open Positions <HelpTip text="Number of shadow trades currently active." /></>}
          value={openCount} sub="Shadow trades active"
          icon={Layers} color="slate"
        />
        <StatCard
          label={<>Live Feed <HelpTip text="Real-time WebSocket connection. Updates every 5 seconds." /></>}
          value={lastTick ? <LiveDot /> : '○ Connecting'}
          sub={lastTick ? new Date(lastTick.timestamp).toLocaleTimeString() : 'WebSocket'}
          icon={Activity} color={lastTick ? 'green' : 'slate'}
        />
      </div>

      {equityHistory.length > 1 && (
        <div className="rounded-xl p-5" style={{ background:'var(--bg-surface)', border:'1px solid var(--border)' }}>
          <h2 className="text-sm font-medium mb-4" style={{ color:'var(--text-sub)' }}>Shadow Equity Curve</h2>
          <ResponsiveContainer width="100%" height={160}>
            <AreaChart data={equityHistory}>
              <defs>
                <linearGradient id="eqGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%"  stopColor={BLUE} stopOpacity={0.22} />
                  <stop offset="95%" stopColor={BLUE} stopOpacity={0}    />
                </linearGradient>
              </defs>
              <XAxis dataKey="time" tick={{ fontSize:10, fill:'#475569' }} />
              <YAxis domain={['auto','auto']} tick={{ fontSize:10, fill:'#475569' }} tickFormatter={v => `$${v.toLocaleString()}`} width={70} />
              <Tooltip contentStyle={{ background:'var(--bg-surface)', border:'1px solid var(--border)', borderRadius:8, fontSize:11, color:'var(--text-primary)' }} formatter={v => [`$${v.toFixed(2)}`, 'Equity']} />
              <Area type="monotone" dataKey="equity" stroke={BLUE} strokeWidth={2} fill="url(#eqGrad)" dot={{ fill:BLUE, r:3 }} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}

      <AnatomyPanel trade={trades.find(t => t.action?.includes('BUY'))} />

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="rounded-xl p-5" style={{ background:'var(--bg-surface)', border:'1px solid var(--border)' }}>
          <h2 className="text-sm font-medium mb-4" style={{ color:'var(--text-sub)' }}>Open Positions</h2>
          {positions.length === 0 ? (
            <p className="text-sm text-center py-6" style={{ color:'var(--text-dim)' }}>No open positions</p>
          ) : (
            <div className="space-y-1">
              {positions.map(p => {
                const legCount  = p.leg_count ?? 1
                const isPyramid = legCount > 1
                const mark      = p.mark_price ?? p.entry_price ?? 0
                const size      = p.mark_value_usd ?? 0
                const pnlUsd    = p.unrealized_pnl_usd ?? 0
                const pnlPct    = p.unrealized_pnl_pct ?? 0
                const isUp      = pnlUsd >= 0
                const stop      = p.current_stop ?? 0
                const stopDist  = p.stop_distance_pct
                const days      = p.days_held
                const heldText  = days == null
                  ? null
                  : days < 1
                    ? `${(p.hours_held ?? 0).toFixed(1)}h held`
                    : `${days.toFixed(1)}d held`
                return (
                  <div key={p.symbol} style={{ borderBottom:'1px solid var(--border-row)' }} className="flex items-center justify-between py-2.5 last:border-0">
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div className="flex items-center gap-1.5">
                        <span className="text-sm font-medium" style={{ color:'var(--text-primary)' }}>{p.symbol}</span>
                        {isPyramid && (
                          <span style={{
                            fontSize: 9, fontWeight: 700, padding: '1px 5px', borderRadius: 3,
                            background: 'rgba(59,130,246,0.15)', color: BLUE,
                            border: '1px solid rgba(59,130,246,0.30)', letterSpacing: '0.05em',
                          }}>×{legCount}</span>
                        )}
                      </div>
                      <div className="text-xs" style={{ color:'var(--text-muted)' }}>
                        {p.strategy}{heldText && <> · {heldText}</>}
                      </div>
                      <div className="text-xs mt-0.5" style={{ color:'var(--text-dim)', fontFamily:"'JetBrains Mono', monospace" }}>
                        entry ${(p.avg_entry_price ?? p.entry_price ?? 0).toFixed(2)} · stop ${stop.toFixed(2)}
                        {stopDist != null && (
                          <span style={{ marginLeft: 4 }}>({stopDist.toFixed(1)}%)</span>
                        )}
                      </div>
                    </div>
                    <div className="text-right" style={{ marginLeft: 12 }}>
                      <div className="text-sm" style={{ color:'var(--text-primary)', fontFamily:"'JetBrains Mono', monospace", fontWeight: 600 }}>
                        ${mark.toLocaleString(undefined, { maximumFractionDigits:2 })}
                      </div>
                      <div style={{ fontSize: 11, color: 'var(--text-muted)', fontFamily:"'JetBrains Mono', monospace" }}>
                        ${size.toLocaleString(undefined, { minimumFractionDigits:2, maximumFractionDigits:2 })} size
                      </div>
                      <div style={{ fontSize: 11, fontWeight: 600, color: isUp ? '#4ade80' : '#f87171', fontFamily:"'JetBrains Mono', monospace" }}>
                        {isUp ? '+' : '−'}${Math.abs(pnlUsd).toFixed(2)} <span style={{ opacity: 0.85 }}>{isUp ? '+' : ''}{pnlPct.toFixed(2)}%</span>
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>

        <div className="rounded-xl p-5" style={{ background:'var(--bg-surface)', border:'1px solid var(--border)' }}>
          <h2 className="text-sm font-medium mb-4" style={{ color:'var(--text-sub)' }}>Recent Activity</h2>
          <div>{trades.slice(0, 8).map(t => <TradeRow key={t.id} trade={t} />)}</div>
        </div>
      </div>
    </div>
  )
}
