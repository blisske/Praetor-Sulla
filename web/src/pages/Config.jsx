import { useEffect, useState } from 'react'
import api from '../lib/api.js'
import { Save, RotateCcw, AlertTriangle, RefreshCw, Plus, X, ChevronDown, ChevronRight } from 'lucide-react'
import HelpTip from '../components/HelpTip.jsx'

const BLUE = '#3B82F6'

function Field({ label, value, onChange, type = 'text', hint, help }) {
  return (
    <div>
      <label style={{ display:'flex', alignItems:'center', gap:4, fontSize:'0.67rem', fontWeight:600, letterSpacing:'0.11em', textTransform:'uppercase', color:'var(--text-sub)', marginBottom:'0.45rem' }}>
        {label}{help && <HelpTip text={help} />}
      </label>
      <input type={type} value={value ?? ''} onChange={e => onChange(type === 'number' ? parseFloat(e.target.value) : e.target.value)}
        style={{ width:'100%', background:'var(--bg-elevated)', border:'1px solid var(--border-input)', borderRadius:'0.5rem', padding:'0.5rem 0.75rem', color:'var(--text-primary)', fontSize:'0.875rem', outline:'none', transition:'border-color 0.15s', fontFamily:'inherit' }}
        onFocus={e => e.target.style.borderColor = BLUE}
        onBlur={e => e.target.style.borderColor = 'var(--border-input)'} />
      {hint && <p style={{ fontSize:'0.68rem', color:'var(--text-dim)', marginTop:'0.25rem' }}>{hint}</p>}
    </div>
  )
}

function Toggle({ label, value, onChange, hint, help }) {
  return (
    <div className="flex items-center justify-between py-2">
      <div>
        <div style={{ display:'flex', alignItems:'center', gap:4, fontSize:'0.875rem', color:'var(--text-sub)' }}>{label}{help && <HelpTip text={help} />}</div>
        {hint && <div style={{ fontSize:'0.72rem', color:'var(--text-dim)', marginTop:2 }}>{hint}</div>}
      </div>
      <button onClick={() => onChange(!value)} style={{ position:'relative', width:44, height:24, borderRadius:12, border:'none', cursor:'pointer', background: value ? BLUE : 'var(--bg-elevated)', transition:'background 0.2s', flexShrink:0 }}>
        <span style={{ position:'absolute', top:2, left:2, width:20, height:20, background:'#fff', borderRadius:'50%', transition:'transform 0.2s', transform: value ? 'translateX(20px)' : 'translateX(0)' }} />
      </button>
    </div>
  )
}

function Section({ title, children, collapsible = false, defaultOpen = true, badge }) {
  const [open, setOpen] = useState(defaultOpen)
  const isCollapsed = collapsible && !open
  return (
    <div className="rounded-xl p-5 space-y-4" style={{ background:'var(--bg-surface)', border:'1px solid var(--border)' }}>
      <h2
        onClick={() => collapsible && setOpen(o => !o)}
        style={{ fontSize:'0.8rem', fontWeight:600, color:'var(--text-sub)', borderBottom: isCollapsed ? 'none' : '1px solid var(--border-row)', paddingBottom: isCollapsed ? 0 : '0.75rem', display:'flex', alignItems:'center', gap:6, cursor: collapsible ? 'pointer' : 'default', userSelect:'none' }}
      >
        {collapsible && (open ? <ChevronDown size={14} style={{opacity:0.6}}/> : <ChevronRight size={14} style={{opacity:0.6}}/>)}
        {title}
        {badge && <span style={{ marginLeft:'auto', fontSize:'0.65rem', fontWeight:500, color:'var(--text-dim)', textTransform:'none', letterSpacing:0 }}>{badge}</span>}
      </h2>
      {!isCollapsed && children}
    </div>
  )
}

export default function Config() {
  const [config, setConfig]   = useState(null)
  const [original, setOriginal] = useState(null)
  const [saved, setSaved]     = useState(false)
  const [restarting, setRestarting] = useState(false)
  const [error, setError]     = useState('')
  const [newSymbol, setNewSymbol] = useState('')

  useEffect(() => {
    api.get('/config').then(r => { setConfig(r.data.config); setOriginal(JSON.stringify(r.data.config)) })
  }, [])

  const isDirty = config && JSON.stringify(config) !== original

  const set = (path, value) => {
    setConfig(prev => {
      const next = JSON.parse(JSON.stringify(prev))
      const keys = path.split('.')
      let obj = next
      for (let i = 0; i < keys.length - 1; i++) obj = obj[keys[i]]
      obj[keys[keys.length - 1]] = value
      return next
    })
  }

  const addSymbol = () => {
    const sym = newSymbol.trim().toUpperCase()
    if (!sym) return
    const current = config?.strategy?.active_symbols ?? []
    if (!current.includes(sym)) set('strategy.active_symbols', [...current, sym])
    setNewSymbol('')
  }

  const removeSymbol = sym => {
    const current = config?.strategy?.active_symbols ?? []
    set('strategy.active_symbols', current.filter(s => s !== sym))
  }

  const save = async () => {
    try {
      await api.post('/config', { config })
      setOriginal(JSON.stringify(config))
      setSaved(true)
      setTimeout(() => setSaved(false), 3000)
      setError('')
    } catch { setError('Save failed — check API logs') }
  }

  const restart = async () => {
    if (!window.confirm('Restart Sulla service now?')) return
    setRestarting(true)
    try { await api.post('/restart'); setTimeout(() => setRestarting(false), 8000) }
    catch { setRestarting(false) }
  }

  const reset = () => api.get('/config').then(r => { setConfig(r.data.config); setOriginal(JSON.stringify(r.data.config)) })

  if (!config) return <div className="p-6" style={{ color:'var(--text-muted)' }}>Loading config...</div>

  const risk      = config.risk ?? {}
  const strategy  = config.strategy ?? {}
  const consensus = config.consensus ?? {}
  const ratchet   = config.ratchet ?? {}
  const tuning    = config.tuning ?? {}
  const ai        = config.ai_agent?.sentiment_analysis ?? {}
  const shadow    = config.oanda?.shadow_mode ?? true
  const macro     = config.macro_blackout ?? {}
  const symbols   = strategy.active_symbols ?? []
  const mtf       = config.mtf_filter ?? {}
  const corr      = config.correlation_aware_sizing ?? {}
  const pyramid   = config.pyramiding ?? {}

  return (
    <div className="p-6 space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold" style={{ color:'var(--text-primary)' }}>Configuration</h1>
          <p className="text-sm mt-0.5" style={{ color:'var(--text-muted)' }}>Changes require service restart to take effect</p>
        </div>
        <div className="flex items-center gap-3">
          {isDirty && (
            <button onClick={reset} className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm transition-colors" style={{ color:'var(--text-sub)', background:'var(--bg-elevated)' }}>
              <RotateCcw size={14} /> Reset
            </button>
          )}
          <button onClick={restart} disabled={restarting}
            className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors disabled:opacity-40"
            style={{ background:'var(--bg-elevated)', border:`1px solid rgba(59,130,246,0.50)`, color:BLUE }}
            onMouseEnter={e => e.currentTarget.style.background = 'rgba(59,130,246,0.10)'}
            onMouseLeave={e => e.currentTarget.style.background = 'var(--bg-elevated)'}>
            <RefreshCw size={14} className={restarting ? 'animate-spin' : ''} />
            {restarting ? 'Restarting...' : 'Restart Service'}
          </button>
          <button onClick={save} disabled={!isDirty}
            className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors disabled:opacity-40"
            style={{ background:BLUE, color:'#fff', border:'none' }}>
            <Save size={14} />
            {saved ? 'Saved!' : 'Save Changes'}
          </button>
        </div>
      </div>

      {error && <div className="flex items-center gap-2 text-sm rounded-lg px-4 py-3" style={{ background:'rgba(239,68,68,0.09)', border:'1px solid rgba(239,68,68,0.25)', color:'#f87171' }}><AlertTriangle size={14} /> {error}</div>}
      {isDirty && <div className="flex items-center gap-2 text-sm rounded-lg px-4 py-3" style={{ background:'rgba(59,130,246,0.09)', border:'1px solid rgba(59,130,246,0.25)', color:BLUE }}><AlertTriangle size={14} /> Unsaved changes — restart service after saving</div>}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">

        <Section title="Mode">
          <Toggle label="Shadow Mode" value={shadow} onChange={v => set('oanda.shadow_mode', v)}
            hint="⚠️ Disable only when ready for live Oanda trading"
            help="Paper trading mode — full pipeline runs but no real orders placed on Oanda. Accumulate shadow trades before going live." />
          <Toggle label="Autonomous Mode" value={strategy.autonomous_mode ?? true} onChange={v => set('strategy.autonomous_mode', v)}
            hint="When off, bot advises but does not trade"
            help="When enabled, Sulla places orders automatically when consensus passes. When disabled, evaluates signals and logs what it would do without executing." />
        </Section>

        <Section title="Risk">
          <div className="grid grid-cols-2 gap-4">
            <Field label="Initial Capital ($)" value={risk.initial_capital} onChange={v => set('risk.initial_capital', v)} type="number" hint="Pivot reset: $10,000"
              help="Starting portfolio value for P&L and drawdown calculations. Phase 1 reset Sulla's shadow ledger to $10K to match Tiberius scale." />
            <Field label="Risk Per Trade (%)" value={risk.risk_per_trade_pct} onChange={v => set('risk.risk_per_trade_pct', v)} type="number" hint="Target: 2% before live"
              help="Percentage of equity risked per trade. Currently 5% for paper — target 2% for first live cycle, then tune upward based on performance." />
            <Field label="Position Cap (%)" value={risk.position_size_max_pct} onChange={v => set('risk.position_size_max_pct', v)} type="number" hint="Phase 2 — was hardcoded 5%"
              help="Hard cap on notional per position as % of equity. Risk-per-trade math may want a larger position on low-vol setups; this cap dominates. Default 5%; raise to 12% only after Phases 3–5 defensive features are validated." />
          </div>
        </Section>

        <Section title="Tiered Drawdown (Phase 3)">
          <div className="grid grid-cols-2 gap-4">
            <Field label="Alert (%)" value={risk.drawdown_alert_pct} onChange={v => set('risk.drawdown_alert_pct', v)} type="number" hint="Telegram warning"
              help="Drawdown level at which Telegram sends an ALERT. No behavior change at this tier." />
            <Field label="Derisk (%)" value={risk.drawdown_derisk_pct} onChange={v => set('risk.drawdown_derisk_pct', v)} type="number" hint="Sizing × multiplier"
              help="Drawdown level at which position sizing is multiplied by Derisk Multiplier (default 0.5). Hysteresis: stays in DERISK until equity recovers below Recovery." />
            <Field label="Halt (%)" value={risk.drawdown_halt_pct} onChange={v => set('risk.drawdown_halt_pct', v)} type="number" hint="Manual /resume required"
              help="Drawdown level at which trading halts. Open stops continue. Manual /resume on Telegram clears this state." />
            <Field label="Recovery (%)" value={risk.drawdown_recovery_pct} onChange={v => set('risk.drawdown_recovery_pct', v)} type="number" hint="Hysteresis exit"
              help="Drawdown must shrink below this value to exit DERISK back to NORMAL/ALERT. Prevents flapping at the threshold." />
            <Field label="Derisk Multiplier" value={risk.derisk_size_multiplier} onChange={v => set('risk.derisk_size_multiplier', v)} type="number" hint="Default 0.5"
              help="Position sizing multiplier applied while in DERISK mode. Composes multiplicatively with correlation-aware sizing." />
            <Field label="Daily Loss Limit (%)" value={risk.daily_session_loss_pct} onChange={v => set('risk.daily_session_loss_pct', v)} type="number" hint="ET-day intraday circuit"
              help="If intraday equity drops this far below the ET-day start equity, new entries are blocked for the rest of the day. Existing positions exit normally. Auto-clears at next day boundary." />
          </div>
        </Section>

        <Section title="Strategy">
          <div className="grid grid-cols-2 gap-4">
            <Field label="ADX Trend Threshold" value={strategy.adx_trend_threshold} onChange={v => set('strategy.adx_trend_threshold', v)} type="number"
              help="ADX value that separates trending from ranging markets. Above this triggers Trend Following and Volatility Breakout paradigms." />
            <Field label="Max Open Trades" value={strategy.max_open_trades} onChange={v => set('strategy.max_open_trades', v)} type="number"
              help="Maximum simultaneous open positions. With 5% risk per trade and 5 max positions, up to 25% of capital can be deployed." />
            <Field label="EMA Fast" value={strategy.ema_fast} onChange={v => set('strategy.ema_fast', v)} type="number"
              help="Fast EMA period for trend direction. On 30-min bars, EMA 9 = ~4.5 hours of price action." />
            <Field label="EMA Slow" value={strategy.ema_slow} onChange={v => set('strategy.ema_slow', v)} type="number"
              help="Slow EMA period. On 1h bars, EMA 21 = ~21 hours. The 9/21 crossover identifies trend direction." />
          </div>
        </Section>

        <Section title="Consensus Gate">
          <div className="grid grid-cols-2 gap-4">
            <Field label="Min Consensus Score" value={consensus.min_consensus_score} onChange={v => set('consensus.min_consensus_score', v)} type="number" hint="Default: 3"
              help="Minimum score to authorize a trade. Score = 1 (primary signal) + supporting signals passed. Default 3 requires the primary signal plus 2 of 3 supporting signals." />
          </div>
          <Toggle label="Bearish Abort" value={consensus.bearish_abort ?? true} onChange={v => set('consensus.bearish_abort', v)}
            hint="Hard abort on BEARISH AI verdict"
            help="When enabled, a BEARISH verdict from the AI layer immediately cancels the trade and logs the reason. Strongly recommended to keep enabled." />
        </Section>

        <Section title="ATR Ratchet">
          <div className="grid grid-cols-2 gap-4">
            <Field label="Initial Stop Multiplier" value={ratchet.initial_stop_mult} onChange={v => set('ratchet.initial_stop_mult', v)} type="number"
              help="ATR multiplier for the initial stop-loss. Stop = entry − (ATR × multiplier). Higher values give trades more room but risk larger losses per trade." />
            <Field label="Trailing Stop Multiplier" value={ratchet.trailing_stop_mult} onChange={v => set('ratchet.trailing_stop_mult', v)} type="number"
              help="ATR multiplier for the ratchet trailing stop. As price rises, stop is tightened to new_high − (ATR × multiplier). Stops only move up, never down." />
          </div>
          <Toggle label="Volatility-Window Defense" value={ratchet.power_hour_defense?.enabled ?? true} onChange={v => set('ratchet.power_hour_defense.enabled', v)}
            hint="Widens stops during London/NY overlap (08:00–12:00 ET)"
            help="FX's highest-volume window is the London/NY overlap. This widens the trailing stop by an ATR buffer to avoid being stopped out on normal overlap-period noise. Window is configurable via start_hour_et / end_hour_et in Config.yaml." />
        </Section>

        <Section title="Macro Blackout" collapsible defaultOpen={false} badge="Sulla-specific">
          <Toggle label="Macro Blackout Enabled" value={macro.enabled ?? true} onChange={v => set('macro_blackout.enabled', v)}
            hint="Skip entries around NFP / FOMC / ECB / BoE / BoJ prints"
            help="Pulls the ForexFactory weekly JSON feed and blocks new entries on any pair whose base or quote currency has a high-impact event inside the window. Open positions exit normally." />
          <div className="grid grid-cols-2 gap-4">
            <Field label="Minutes Before" value={macro.minutes_before} onChange={v => set('macro_blackout.minutes_before', v)} type="number" hint="Default 60"
              help="Block entries this many minutes BEFORE a high-impact event." />
            <Field label="Minutes After" value={macro.minutes_after} onChange={v => set('macro_blackout.minutes_after', v)} type="number" hint="Default 120"
              help="...and this many minutes AFTER, to let initial volatility settle." />
            <Field label="Importance Min" value={macro.importance_min} onChange={v => set('macro_blackout.importance_min', v)} hint="Low / Medium / High"
              help="ForexFactory impact threshold. 'High' is the right default (NFP / FOMC / CPI / rate decisions). Wider filters block ~half the trading week." />
          </div>
        </Section>

        <Section title="Pyramiding" collapsible defaultOpen={false} badge="Advanced">
          <Toggle label="Pyramiding Enabled" value={pyramid.enabled ?? false} onChange={v => set('pyramiding.enabled', v)}
            hint="⚠️ Default off — flip after first trend trade fires cleanly"
            help="Adds legs to TREND FOLLOWING and VOLATILITY BREAKOUT positions. Mean Reversion and Liquidity Sweep are excluded. Default OFF; enable only after watching single-leg mechanics work end-to-end." />
          <div className="grid grid-cols-2 gap-4">
            <Field label="Trigger ATR Multiple" value={pyramid.trigger_atr_mult} onChange={v => set('pyramiding.trigger_atr_mult', v)} type="number" hint="Default 1.0"
              help="Price must advance this many ATR from the last leg's entry before another leg is eligible." />
            <Field label="Size Decay" value={pyramid.size_decay} onChange={v => set('pyramiding.size_decay', v)} type="number" hint="Default 0.5 (geometric)"
              help="Each leg = previous-leg base × this. Default 0.5 → leg 2 is half-size, leg 3 is quarter-size." />
            <Field label="Max Legs — Trend" value={pyramid.max_legs?.trend_following} onChange={v => set('pyramiding.max_legs.trend_following', v)} type="number" hint="Default 3"
              help="Maximum simultaneous legs on a TREND FOLLOWING position." />
            <Field label="Max Legs — Breakout" value={pyramid.max_legs?.volatility_breakout} onChange={v => set('pyramiding.max_legs.volatility_breakout', v)} type="number" hint="Default 2 (tighter)"
              help="Maximum simultaneous legs on a VOLATILITY BREAKOUT position." />
          </div>
        </Section>

        <Section title="Multi-Timeframe Filter" collapsible defaultOpen={false} badge="Advanced">
          <Toggle label="MTF Filter Enabled" value={mtf.enabled ?? true} onChange={v => set('mtf_filter.enabled', v)}
            hint="Daily regime gate on TF and VB entries"
            help="When enabled, TREND FOLLOWING and VOLATILITY BREAKOUT entries additionally require the pair's daily EMA9/21 cross to be BULL. Mean Reversion and Liquidity Sweep are exempt — they're counter-trend by design." />
        </Section>

        <Section title="Correlation-Aware Sizing" collapsible defaultOpen={false} badge="Advanced">
          <Toggle label="Correlation Sizing Enabled" value={corr.enabled ?? true} onChange={v => set('correlation_aware_sizing.enabled', v)}
            hint="Scale down sizing as same-sector pairs accumulate"
            help="FX majors share huge correlation through their USD leg. Curve [1.0, 0.85, 0.70, 0.55, 0.40] indexed by count of same-bucket open positions. Edit curve and buckets directly in Config.yaml." />
        </Section>

        <Section title="Self-Tuning Engine" collapsible defaultOpen={false} badge="Advanced">
          <Toggle label="Tuning Enabled" value={tuning.enabled ?? true} onChange={v => set('tuning.enabled', v)}
            help="Master switch for autonomous parameter optimization." />
          <div className="grid grid-cols-2 gap-4">
            <Field label="Min Trades to Tune" value={tuning.min_trades_to_tune} onChange={v => set('tuning.min_trades_to_tune', v)} type="number"
              help="Minimum closed shadow trades per (symbol, paradigm) before tuning runs." />
            <Field label="Shadow Trades Required" value={tuning.shadow_trades_required} onChange={v => set('tuning.shadow_trades_required', v)} type="number"
              help="Additional shadow trade closes needed to validate a proposed parameter change before promotion." />
            <Field label="Cooling Off (hours)" value={tuning.cooling_off_hours} onChange={v => set('tuning.cooling_off_hours', v)} type="number"
              help="Minimum hours before the same parameter can be tuned again." />
            <Field label="Min Metric Improvement" value={tuning.min_metric_improvement} onChange={v => set('tuning.min_metric_improvement', v)} type="number"
              help="Min improvement in Profit Factor (0.05 = 5%) to promote a candidate." />
          </div>
        </Section>

        {/* Active Symbols — unique to Sulla */}
        <div className="rounded-xl p-5 space-y-4 lg:col-span-2" style={{ background:'var(--bg-surface)', border:'1px solid var(--border)' }}>
          <h2 style={{ fontSize:'0.8rem', fontWeight:600, color:'var(--text-sub)', borderBottom:'1px solid var(--border-row)', paddingBottom:'0.75rem', display:'flex', alignItems:'center', gap:6 }}>
            Active Watchlist
            <HelpTip text="FX pairs Sulla monitors and trades. Changes hot-reload at the next cycle — no restart needed. Macro blackout automatically checks the base + quote currency of each pair against the ForexFactory feed." />
          </h2>
          <div className="flex flex-wrap gap-2">
            {symbols.map(sym => (
              <span key={sym} style={{ display:'inline-flex', alignItems:'center', gap:6, background:'rgba(59,130,246,0.12)', color:BLUE, border:'1px solid rgba(59,130,246,0.25)', padding:'4px 10px', borderRadius:6, fontSize:12, fontWeight:600 }}>
                {sym}
                <button onClick={() => removeSymbol(sym)} style={{ color:'rgba(59,130,246,0.6)', cursor:'pointer', border:'none', background:'none', padding:0, display:'flex' }}>
                  <X size={12} />
                </button>
              </span>
            ))}
          </div>
          <div className="flex gap-2">
            <input value={newSymbol} onChange={e => setNewSymbol(e.target.value.toUpperCase())}
              onKeyDown={e => e.key === 'Enter' && addSymbol()}
              placeholder="Add pair (e.g. EUR/USD)"
              style={{ flex:1, background:'var(--bg-elevated)', border:'1px solid var(--border-input)', borderRadius:'0.5rem', padding:'0.5rem 0.75rem', color:'var(--text-primary)', fontSize:'0.875rem', outline:'none', fontFamily:'inherit' }}
              onFocus={e => e.target.style.borderColor = BLUE}
              onBlur={e => e.target.style.borderColor = 'var(--border-input)'} />
            <button onClick={addSymbol} className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium"
              style={{ background:BLUE, color:'#fff', border:'none' }}>
              <Plus size={14} /> Add
            </button>
          </div>
        </div>

      </div>
    </div>
  )
}
