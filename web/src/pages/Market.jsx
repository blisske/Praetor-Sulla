import { useEffect, useState } from 'react'
import api from '../lib/api.js'
import {
  ComposedChart, Line, Bar,
  XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine,
} from 'recharts'
import { TrendingUp, TrendingDown, Minus } from 'lucide-react'
import HelpTip from '../components/HelpTip.jsx'

const BLUE     = '#3B82F6'
const ADX_BLUE  = '#60a5fa'
const TIMEFRAMES = [
  { label:'2h',  hours:2  },
  { label:'6h',  hours:6  },
  { label:'12h', hours:12 },
  { label:'24h', hours:24 },
]
const ALL_SERIES = ['Price', 'RSI', 'ADX', 'Volume']

function RegimeBadge({ regime }) {
  const styles = {
    TRENDING: { background:'rgba(34,197,94,0.10)',   color:'#22c55e', border:'1px solid rgba(34,197,94,0.20)' },
    RANGING:  { background:'rgba(100,116,139,0.10)', color:'var(--text-sub)', border:'1px solid rgba(100,116,139,0.20)' },
  }
  return <span className="text-xs px-2 py-0.5 rounded-full font-medium" style={styles[regime] ?? styles.RANGING}>{regime}</span>
}

function TrendIcon({ trend }) {
  if (trend === 'BULL') return <TrendingUp size={14} className="text-green-400" />
  if (trend === 'BEAR') return <TrendingDown size={14} className="text-red-400" />
  return <Minus size={14} style={{ color:'var(--text-sub)' }} />
}

const SERIES_COLORS = {
  Price:  null,   // dynamic: green if up, red if down
  RSI:    BLUE,
  ADX:    ADX_BLUE,
  Volume: BLUE,
}

function MultiSeriesChart({ data, active, isUp }) {
  if (!data || data.length < 2) return (
    <div className="flex items-center justify-center h-40 text-sm" style={{ color:'var(--text-dim)' }}>Not enough data</div>
  )

  const chartData = data.map(d => ({
    time:       new Date(d.timestamp).toLocaleTimeString([], { hour:'2-digit', minute:'2-digit' }),
    price:      d.price,
    rsi:        d.rsi,
    adx:        d.adx,
    volume:     d.volume ?? null,
    avg_volume: d.avg_volume ?? null,
  }))

  const priceVals = chartData.map(d => d.price).filter(Boolean)
  const priceMin  = Math.min(...priceVals)
  const priceMax  = Math.max(...priceVals)
  const pricePad  = (priceMax - priceMin) * 0.05 || 1
  const priceColor = isUp ? '#22c55e' : '#ef4444'

  const CustomTooltip = ({ active: a, payload, label }) => {
    if (!a || !payload?.length) return null
    return (
      <div style={{ background:'var(--bg-surface)', border:'1px solid var(--border)', borderRadius:8, padding:'8px 12px', fontSize:11 }}>
        <div style={{ color:'var(--text-muted)', marginBottom:4 }}>{label}</div>
        {payload.map((p, i) => {
          let display = p.value?.toFixed(2)
          if (p.name === 'price') display = `$${Number(p.value).toLocaleString(undefined, { maximumFractionDigits:2 })}`
          if (p.name === 'volume') display = p.value ? `${(p.value / 1e6).toFixed(2)}M` : '—'
          const label = p.name.charAt(0).toUpperCase() + p.name.slice(1)
          return (
            <div key={i} style={{ color: p.color || p.fill, fontWeight:600 }}>
              {label}: {display}
            </div>
          )
        })}
      </div>
    )
  }

  return (
    <ResponsiveContainer width="100%" height={200}>
      <ComposedChart data={chartData} margin={{ top:4, right:8, left:0, bottom:0 }}>
        <XAxis dataKey="time" tick={{ fontSize:10, fill:'#475569' }} interval="preserveStartEnd" tickLine={false} axisLine={false} />

        {/* Left Y-axis: price */}
        {active.includes('Price') && (
          <YAxis
            yAxisId="price"
            orientation="left"
            domain={[parseFloat((priceMin - pricePad).toFixed(2)), parseFloat((priceMax + pricePad).toFixed(2))]}
            tick={{ fontSize:10, fill:'#475569' }}
            tickFormatter={v => `$${v >= 1000 ? (v/1000).toFixed(1)+'k' : v.toFixed(2)}`}
            width={56}
            tickLine={false}
            axisLine={false}
          />
        )}

        {/* Right Y-axis: RSI + ADX (0–100 scale) */}
        {(active.includes('RSI') || active.includes('ADX')) && (
          <YAxis
            yAxisId="osc"
            orientation="right"
            domain={[0, 100]}
            tick={{ fontSize:10, fill:'#475569' }}
            tickFormatter={v => v.toFixed(0)}
            width={32}
            tickLine={false}
            axisLine={false}
          />
        )}

        {/* Hidden Y-axis: volume auto-scale */}
        {active.includes('Volume') && (
          <YAxis yAxisId="vol" hide={true} />
        )}

        <Tooltip content={<CustomTooltip />} />

        {/* RSI reference lines */}
        {active.includes('RSI') && (<>
          <ReferenceLine yAxisId="osc" y={70} stroke="#f59e0b" strokeDasharray="3 3" strokeOpacity={0.4} />
          <ReferenceLine yAxisId="osc" y={30} stroke="#22c55e" strokeDasharray="3 3" strokeOpacity={0.4} />
          <ReferenceLine yAxisId="osc" y={50} stroke="#475569" strokeDasharray="2 2" strokeOpacity={0.3} />
        </>)}

        {/* ADX threshold line */}
        {active.includes('ADX') && (
          <ReferenceLine yAxisId="osc" y={25} stroke={ADX_BLUE} strokeDasharray="3 3" strokeOpacity={0.4} />
        )}

        {/* Volume bars */}
        {active.includes('Volume') && (
          <Bar yAxisId="vol" dataKey="volume" fill={BLUE} fillOpacity={0.35} isAnimationActive={false} />
        )}

        {/* Price line */}
        {active.includes('Price') && (
          <Line yAxisId="price" type="monotone" dataKey="price" stroke={priceColor} strokeWidth={1.5} dot={false} activeDot={{ r:3, fill:priceColor }} />
        )}

        {/* RSI line */}
        {active.includes('RSI') && (
          <Line yAxisId="osc" type="monotone" dataKey="rsi" stroke={BLUE} strokeWidth={1.5} dot={false} activeDot={{ r:3, fill:BLUE }} />
        )}

        {/* ADX line */}
        {active.includes('ADX') && (
          <Line yAxisId="osc" type="monotone" dataKey="adx" stroke={ADX_BLUE} strokeWidth={1.5} dot={false} activeDot={{ r:3, fill:ADX_BLUE }} />
        )}
      </ComposedChart>
    </ResponsiveContainer>
  )
}

function VolRatio({ volume, avg_volume }) {
  if (!volume || !avg_volume) return <span style={{ color:'var(--text-dim)' }}>—</span>
  const ratio = volume / avg_volume
  const color = ratio >= 1.0 ? '#22c55e' : '#ef4444'
  const label = ratio >= 1.0 ? 'CONFIRMING' : 'WEAK'
  return <span style={{ color, fontWeight:600 }}>{ratio.toFixed(2)}x {label}</span>
}

export default function Market() {
  const [market, setMarket]       = useState({ latest:{}, history:{} })
  const [symbols, setSymbols]     = useState([])
  const [selected, setSelected]   = useState('SPY')
  const [lastUpdate, setLastUpdate] = useState(null)
  const [countdown, setCountdown] = useState(30)
  const [hours, setHours]         = useState(6)
  const [active, setActive]       = useState(['Price'])

  useEffect(() => {
    api.get('/watchlist').then(r => {
      setSymbols(r.data.symbols || [])
      if (r.data.symbols?.length > 0) setSelected(r.data.symbols[0])
    })
  }, [])

  useEffect(() => {
    const fetch = () => {
      api.get(`/market?hours=${hours}`).then(r => { setMarket(r.data); setLastUpdate(new Date()); setCountdown(30) })
    }
    fetch()
    const iv = setInterval(fetch, 30000)
    return () => clearInterval(iv)
  }, [hours])

  useEffect(() => {
    const t = setInterval(() => setCountdown(c => c > 0 ? c - 1 : 0), 1000)
    return () => clearInterval(t)
  }, [])

  const toggleSeries = (s) => {
    setActive(prev => {
      if (prev.includes(s)) {
        if (prev.length === 1) return prev  // minimum one always active
        return prev.filter(x => x !== s)
      }
      return [...prev, s]
    })
  }

  const sym  = market.latest[selected]
  const hist = market.history[selected] ?? []
  const priceVals = hist.map(d => d.price).filter(Boolean)
  const isUp = priceVals.length >= 2 && priceVals[priceVals.length - 1] >= priceVals[0]

  return (
    <div className="p-6 space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold" style={{ color:'var(--text-primary)' }}>Market Analysis</h1>
          <p className="text-sm mt-0.5" style={{ color:'var(--text-muted)' }}>Live indicator readouts · 30-min bars</p>
        </div>
        <div className="text-right">
          {lastUpdate && <div className="text-xs" style={{ color:'var(--text-muted)' }}>Updated {lastUpdate.toLocaleTimeString()}</div>}
          <div className="text-xs mt-0.5" style={{ color:'var(--text-dim)' }}>Refreshing in {countdown}s</div>
        </div>
      </div>

      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex gap-2 flex-wrap">
          {symbols.map(s => (
            <button key={s} onClick={() => setSelected(s)}
              className="px-3 py-2 rounded-lg text-sm font-medium transition-colors"
              style={selected === s ? { background:BLUE, color:'#fff' } : { background:'var(--bg-elevated)', color:'var(--text-sub)' }}>
              {s}
            </button>
          ))}
        </div>
        <div className="flex gap-2">
          {TIMEFRAMES.map(tf => (
            <button key={tf.label} onClick={() => setHours(tf.hours)}
              className="px-3 py-1.5 rounded-lg text-xs font-medium transition-colors"
              style={hours === tf.hours ? { background:'#475569', color:'#fff' } : { background:'var(--bg-elevated)', color:'var(--text-muted)' }}>
              {tf.label}
            </button>
          ))}
        </div>
      </div>

      {sym ? (<>
        <div className="rounded-xl p-5" style={{ background:'var(--bg-surface)', border:'1px solid var(--border)' }}>
          <div className="flex items-start justify-between mb-5">
            <div>
              <div className="text-3xl font-bold" style={{ color:'var(--text-primary)' }}>
                ${sym.price?.toLocaleString(undefined, { maximumFractionDigits:2 })}
              </div>
              <div className="text-sm mt-1" style={{ color:'var(--text-muted)' }}>{selected}</div>
            </div>
            <div className="flex items-center gap-3">
              {/* Multi-select chart toggles */}
              <div className="flex gap-1.5">
                {ALL_SERIES.map(s => {
                  const color = s === 'Price' ? (isUp ? '#22c55e' : '#ef4444') : s === 'ADX' ? ADX_BLUE : BLUE
                  const isActive = active.includes(s)
                  return (
                    <button key={s} onClick={() => toggleSeries(s)}
                      className="px-3 py-1 rounded-lg text-xs font-medium transition-colors"
                      style={isActive
                        ? { background: color, color:'#fff', opacity: 1 }
                        : { background:'var(--bg-elevated)', color:'var(--text-sub)', opacity: 0.6 }}>
                      {s}
                    </button>
                  )
                })}
              </div>
              <div className="flex items-center gap-2">
                <TrendIcon trend={sym.trend} />
                <RegimeBadge regime={sym.regime} />
              </div>
            </div>
          </div>
          <MultiSeriesChart data={hist} active={active} isUp={isUp} />
          {hist.length > 0 && (
            <div className="text-xs mt-2 text-right" style={{ color:'var(--text-dim)' }}>
              {hist.length} data points · {hours}h window
            </div>
          )}
        </div>

        {/* Indicator cards — 4 across */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <div className="rounded-xl p-4" style={{ background:'var(--bg-surface)', border:'1px solid var(--border)' }}>
            <div className="text-xs uppercase tracking-wider mb-3 flex items-center" style={{ color:'var(--text-muted)' }}>
              RSI (14)<HelpTip text="Relative Strength Index. Under 30 = oversold. Above 70 = overbought. Sulla uses RSI to confirm entry signals on 30-min bars." />
            </div>
            <div className="text-2xl font-bold mb-2" style={{ color:'var(--text-primary)' }}>{sym.rsi?.toFixed(1)}</div>
            <div className="flex items-center gap-2">
              <div className="flex-1 h-1.5 rounded-full overflow-hidden" style={{ background:'var(--bg-elevated)' }}>
                <div className="h-full rounded-full" style={{ width:`${Math.min(100,Math.max(0,sym.rsi))}%`, background: sym.rsi > 70 ? '#f59e0b' : sym.rsi < 30 ? '#22c55e' : BLUE }} />
              </div>
              <span className="text-xs w-8 text-right" style={{ color:'var(--text-sub)' }}>{sym.rsi?.toFixed(1)}</span>
            </div>
            <div className="text-xs mt-2" style={{ color:'var(--text-dim)' }}>{sym.rsi > 70 ? 'Overbought' : sym.rsi < 30 ? 'Oversold' : 'Neutral'}</div>
          </div>

          <div className="rounded-xl p-4" style={{ background:'var(--bg-surface)', border:'1px solid var(--border)' }}>
            <div className="text-xs uppercase tracking-wider mb-3 flex items-center" style={{ color:'var(--text-muted)' }}>
              ADX (14)<HelpTip text="Average Directional Index. Measures trend strength. Above 25 = trending. Sulla uses ADX to route between paradigms." />
            </div>
            <div className="text-2xl font-bold mb-2" style={{ color:'var(--text-primary)' }}>{sym.adx?.toFixed(1)}</div>
            <div className="flex-1 h-1.5 rounded-full overflow-hidden mt-1" style={{ background:'var(--bg-elevated)' }}>
              <div className="h-full rounded-full" style={{ width:`${Math.min(100,(sym.adx/50)*100)}%`, background:BLUE }} />
            </div>
            <div className="text-xs mt-2" style={{ color:'var(--text-dim)' }}>{sym.adx > 25 ? 'Trending' : 'Ranging'}</div>
          </div>

          <div className="rounded-xl p-4" style={{ background:'var(--bg-surface)', border:'1px solid var(--border)' }}>
            <div className="text-xs uppercase tracking-wider mb-3 flex items-center" style={{ color:'var(--text-muted)' }}>
              Trend<HelpTip text="EMA crossover direction. BULL = fast EMA above slow. BEAR = fast below slow. Sulla only takes long entries aligned with trend." />
            </div>
            <div className="flex items-center gap-2 mt-2">
              <TrendIcon trend={sym.trend} />
              <span className="text-2xl font-bold" style={{ color: sym.trend === 'BULL' ? '#4ade80' : sym.trend === 'BEAR' ? '#f87171' : 'var(--text-sub)' }}>{sym.trend}</span>
            </div>
            <div className="text-xs mt-3" style={{ color:'var(--text-dim)' }}>{new Date(sym.timestamp).toLocaleTimeString()}</div>
          </div>

          <div className="rounded-xl p-4" style={{ background:'var(--bg-surface)', border:'1px solid var(--border)' }}>
            <div className="text-xs uppercase tracking-wider mb-3 flex items-center" style={{ color:'var(--text-muted)' }}>
              Volume<HelpTip text="Last closed bar volume vs 20-bar average. CONFIRMING ≥ 1.0× avg means participation backs the move. WEAK = low conviction." />
            </div>
            <div className="text-lg font-bold mb-1" style={{ color:'var(--text-primary)' }}>
              {sym.volume ? `${(sym.volume / 1e6).toFixed(2)}M` : '—'}
            </div>
            <div className="text-xs mt-1" style={{ color:'var(--text-dim)' }}>
              avg {sym.avg_volume ? `${(sym.avg_volume / 1e6).toFixed(2)}M` : '—'}
            </div>
            <div className="text-xs mt-2 font-semibold">
              <VolRatio volume={sym.volume} avg_volume={sym.avg_volume} />
            </div>
          </div>
        </div>

        {/* All symbols table */}
        <div className="rounded-xl overflow-hidden" style={{ background:'var(--bg-surface)', border:'1px solid var(--border)' }}>
          <div className="px-5 py-3" style={{ borderBottom:'1px solid var(--border-row)' }}>
            <h2 className="text-sm font-medium" style={{ color:'var(--text-sub)' }}>All Symbols ({symbols.length})</h2>
          </div>
          <table className="w-full text-sm">
            <thead>
              <tr style={{ borderBottom:'1px solid var(--border-row)' }}>
                {['Symbol','Price','Regime','Trend','RSI','ADX','Vol Ratio','Data Pts'].map(h => (
                  <th key={h} className="text-left px-4 py-2.5 text-xs uppercase tracking-wider" style={{ color:'var(--text-muted)' }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {symbols.map(s => {
                const d   = market.latest[s]
                const pts = market.history[s]?.length ?? 0
                if (!d) return null
                const ratio = d.volume && d.avg_volume ? d.volume / d.avg_volume : null
                const ratioColor = ratio === null ? 'var(--text-dim)' : ratio >= 1.0 ? '#22c55e' : '#ef4444'
                return (
                  <tr key={s} onClick={() => setSelected(s)} className="cursor-pointer transition-colors"
                    style={{ borderBottom:'1px solid var(--border-row)', background: selected === s ? 'rgba(59,130,246,0.08)' : 'transparent' }}
                    onMouseEnter={e => { if (selected !== s) e.currentTarget.style.background = 'var(--bg-elevated)' }}
                    onMouseLeave={e => { e.currentTarget.style.background = selected === s ? 'rgba(59,130,246,0.08)' : 'transparent' }}>
                    <td className="px-4 py-3 font-medium" style={{ color: selected === s ? BLUE : 'var(--text-primary)' }}>{s}</td>
                    <td className="px-4 py-3" style={{ color:'var(--text-primary)' }}>${d.price?.toLocaleString(undefined, { maximumFractionDigits:2 })}</td>
                    <td className="px-4 py-3"><RegimeBadge regime={d.regime} /></td>
                    <td className="px-4 py-3"><TrendIcon trend={d.trend} /></td>
                    <td className="px-4 py-3" style={{ color:'var(--text-sub)' }}>{d.rsi?.toFixed(1)}</td>
                    <td className="px-4 py-3" style={{ color:'var(--text-sub)' }}>{d.adx?.toFixed(1)}</td>
                    <td className="px-4 py-3 text-xs font-semibold" style={{ color: ratioColor }}>
                      {ratio !== null ? `${ratio.toFixed(2)}x` : '—'}
                    </td>
                    <td className="px-4 py-3 text-xs" style={{ color:'var(--text-muted)' }}>{pts} pts</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </>) : (
        <div className="rounded-xl p-12 text-center" style={{ background:'var(--bg-surface)', border:'1px solid var(--border)' }}>
          <p style={{ color:'var(--text-dim)' }}>No market data yet — engine populates this each cycle during market hours</p>
        </div>
      )}
    </div>
  )
}
