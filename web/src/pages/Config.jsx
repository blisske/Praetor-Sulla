import { useEffect, useState } from 'react'
import api from '../lib/api.js'
import { Save, RotateCcw, AlertTriangle, RefreshCw, Plus, X } from 'lucide-react'
import HelpTip from '../components/HelpTip.jsx'

const GREEN = '#10B981'

function Field({ label, value, onChange, type = 'text', hint, help }) {
  return (
    <div>
      <label style={{ display:'flex', alignItems:'center', gap:4, fontSize:'0.67rem', fontWeight:600, letterSpacing:'0.11em', textTransform:'uppercase', color:'var(--text-sub)', marginBottom:'0.45rem' }}>
        {label}{help && <HelpTip text={help} />}
      </label>
      <input type={type} value={value ?? ''} onChange={e => onChange(type === 'number' ? parseFloat(e.target.value) : e.target.value)}
        style={{ width:'100%', background:'var(--bg-elevated)', border:'1px solid var(--border-input)', borderRadius:'0.5rem', padding:'0.5rem 0.75rem', color:'var(--text-primary)', fontSize:'0.875rem', outline:'none', transition:'border-color 0.15s', fontFamily:'inherit' }}
        onFocus={e => e.target.style.borderColor = GREEN}
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
      <button onClick={() => onChange(!value)} style={{ position:'relative', width:44, height:24, borderRadius:12, border:'none', cursor:'pointer', background: value ? GREEN : 'var(--bg-elevated)', transition:'background 0.2s', flexShrink:0 }}>
        <span style={{ position:'absolute', top:2, left:2, width:20, height:20, background:'#fff', borderRadius:'50%', transition:'transform 0.2s', transform: value ? 'translateX(20px)' : 'translateX(0)' }} />
      </button>
    </div>
  )
}

function Section({ title, children }) {
  return (
    <div className="rounded-xl p-5 space-y-4" style={{ background:'var(--bg-surface)', border:'1px solid var(--border)' }}>
      <h2 style={{ fontSize:'0.8rem', fontWeight:600, color:'var(--text-sub)', borderBottom:'1px solid var(--border-row)', paddingBottom:'0.75rem' }}>{title}</h2>
      {children}
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
  const shadow    = config.alpaca?.shadow_mode ?? true
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
            style={{ background:'var(--bg-elevated)', border:`1px solid rgba(16,185,129,0.50)`, color:GREEN }}
            onMouseEnter={e => e.currentTarget.style.background = 'rgba(16,185,129,0.10)'}
            onMouseLeave={e => e.currentTarget.style.background = 'var(--bg-elevated)'}>
            <RefreshCw size={14} className={restarting ? 'animate-spin' : ''} />
            {restarting ? 'Restarting...' : 'Restart Service'}
          </button>
          <button onClick={save} disabled={!isDirty}
            className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors disabled:opacity-40"
            style={{ background:GREEN, color:'#fff', border:'none' }}>
            <Save size={14} />
            {saved ? 'Saved!' : 'Save Changes'}
          </button>
        </div>
      </div>

      {error && <div className="flex items-center gap-2 text-sm rounded-lg px-4 py-3" style={{ background:'rgba(239,68,68,0.09)', border:'1px solid rgba(239,68,68,0.25)', color:'#f87171' }}><AlertTriangle size={14} /> {error}</div>}
      {isDirty && <div className="flex items-center gap-2 text-sm rounded-lg px-4 py-3" style={{ background:'rgba(16,185,129,0.09)', border:'1px solid rgba(16,185,129,0.25)', color:GREEN }}><AlertTriangle size={14} /> Unsaved changes — restart service after saving</div>}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">

        <Section title="Mode">
          <Toggle label="Shadow Mode" value={shadow} onChange={v => set('alpaca.shadow_mode', v)}
            hint="⚠️ Disable only when ready for live trading"
            help="Paper trading mode — full pipeline runs but no real orders placed on Alpaca. Accumulate shadow trades before going live." />
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
            <Field label="Daily Loss Limit (%)" value={risk.daily_session_loss_pct} onChange={v => set('risk.daily_session_loss_pct', v)} type="number" hint="Sulla-only intraday circuit"
              help="If intraday equity drops this far below the session-start equity, new entries are blocked rest of session. Existing positions exit normally. Auto-clears at next session." />
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
              help="Slow EMA period. On 30-min bars, EMA 21 = ~10.5 hours. The 9/21 crossover identifies trend direction." />
            <Field label="Earnings Blackout (days)" value={strategy.earnings_blackout_days} onChange={v => set('strategy.earnings_blackout_days', v)} type="number"
              help="Number of days before a scheduled earnings release during which Sulla will not open new positions in that stock. Prevents entering before high-volatility events." />
            <Field label="EOD Exit Hour (ET)" value={strategy.eod_exit_hour} onChange={v => set('strategy.eod_exit_hour', v)} type="number"
              help="Hour (ET) at which Sulla force-exits all positions before market close. Default 15 = 3 PM ET. Combined with EOD Exit Minute." />
            <Field label="EOD Exit Minute" value={strategy.eod_exit_minute} onChange={v => set('strategy.eod_exit_minute', v)} type="number"
              hint="Default: 50 (3:50 PM ET)"
              help="Minute combined with EOD Exit Hour. Default 15:50 ET gives 10 minutes of buffer before the 4:00 PM close." />
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
          <Toggle label="Power Hour Defense" value={ratchet.power_hour_defense?.enabled ?? true} onChange={v => set('ratchet.power_hour_defense.enabled', v)}
            hint="Widens stops 3:00–4:00 PM ET"
            help="During the final hour of trading (Power Hour), institutional activity increases volatility. This widens the trailing stop by an ATR buffer to avoid being stopped out on normal end-of-day noise." />
        </Section>

        <Section title="Self-Tuning Engine">
          <Toggle label="Tuning Enabled" value={tuning.enabled ?? true} onChange={v => set('tuning.enabled', v)}
            help="Master switch for autonomous parameter optimization. When enabled, Sulla evaluates shadow trade performance and proposes parameter adjustments within defined safety bounds." />
          <div className="grid grid-cols-2 gap-4">
            <Field label="Min Trades to Tune" value={tuning.min_trades_to_tune} onChange={v => set('tuning.min_trades_to_tune', v)} type="number"
              help="Minimum closed shadow trades per symbol before the tuning engine analyzes performance. Ensures a statistically meaningful sample." />
            <Field label="Shadow Trades Required" value={tuning.shadow_trades_required} onChange={v => set('tuning.shadow_trades_required', v)} type="number"
              help="Additional shadow trade closes needed to validate a proposed parameter change before promotion." />
            <Field label="Cooling Off (hours)" value={tuning.cooling_off_hours} onChange={v => set('tuning.cooling_off_hours', v)} type="number"
              help="Minimum hours before the same parameter can be tuned again. Enforces a deliberate, measured tuning cadence." />
            <Field label="Min Metric Improvement" value={tuning.min_metric_improvement} onChange={v => set('tuning.min_metric_improvement', v)} type="number"
              help="Minimum improvement in Profit Factor required to promote a candidate parameter. At 0.05, candidate must beat baseline by at least 5%." />
          </div>
        </Section>

        <Section title="Multi-Timeframe Filter (Phase 4)">
          <Toggle label="MTF Filter Enabled" value={mtf.enabled ?? true} onChange={v => set('mtf_filter.enabled', v)}
            hint="Daily regime gate on TF and VB entries"
            help="When enabled, TREND FOLLOWING and VOLATILITY BREAKOUT entries additionally require the symbol's daily EMA9/21 cross to be BULL. Mean Reversion and Liquidity Sweep are exempt — they're counter-trend by design." />
        </Section>

        <Section title="Correlation-Aware Sizing (Phase 5)">
          <Toggle label="Correlation Sizing Enabled" value={corr.enabled ?? true} onChange={v => set('correlation_aware_sizing.enabled', v)}
            hint="Scale down sizing as same-sector positions accumulate"
            help="Acknowledges that 5 simultaneous tech-sector longs aren't 5 bets. Curve [1.0, 0.85, 0.70, 0.55, 0.40] indexed by count of same-sector open positions. Edit curve and sectors directly in Config.yaml." />
        </Section>

        <Section title="Pyramiding (Phase 7)">
          <Toggle label="Pyramiding Enabled" value={pyramid.enabled ?? false} onChange={v => set('pyramiding.enabled', v)}
            hint="⚠️ Default off — flip after first trend trade fires cleanly"
            help="Adds legs to TREND FOLLOWING and VOLATILITY BREAKOUT positions. Mean Reversion and Liquidity Sweep are excluded — pyramiding into counter-trend paradigms means doubling down against the entry thesis. Default OFF; enable only after watching single-leg mechanics work end-to-end." />
          <div className="grid grid-cols-2 gap-4">
            <Field label="Trigger ATR Multiple" value={pyramid.trigger_atr_mult} onChange={v => set('pyramiding.trigger_atr_mult', v)} type="number" hint="Default 1.0"
              help="Price must advance this many ATR from the last leg's entry before another leg is eligible. Default 1.0 ATR keeps legs separated by meaningful price action." />
            <Field label="Size Decay" value={pyramid.size_decay} onChange={v => set('pyramiding.size_decay', v)} type="number" hint="Default 0.5 (geometric)"
              help="Each leg = previous-leg base × this. Default 0.5 → leg 2 is half-size, leg 3 is quarter-size. Caps total notional commitment as legs accumulate." />
            <Field label="Max Legs — Trend" value={pyramid.max_legs?.trend_following} onChange={v => set('pyramiding.max_legs.trend_following', v)} type="number" hint="Default 3"
              help="Maximum simultaneous legs on a TREND FOLLOWING position. With size decay 0.5, total committed = base × (1 + 0.5 + 0.25) = 1.75× the base notional." />
            <Field label="Max Legs — Breakout" value={pyramid.max_legs?.volatility_breakout} onChange={v => set('pyramiding.max_legs.volatility_breakout', v)} type="number" hint="Default 2 (tighter)"
              help="Maximum simultaneous legs on a VOLATILITY BREAKOUT position. Capped tighter than trend because equity breakouts fade harder than crypto breakouts (mean-reversion is stronger in equities)." />
          </div>
        </Section>

        {/* Active Symbols — unique to Sulla */}
        <div className="rounded-xl p-5 space-y-4 lg:col-span-2" style={{ background:'var(--bg-surface)', border:'1px solid var(--border)' }}>
          <h2 style={{ fontSize:'0.8rem', fontWeight:600, color:'var(--text-sub)', borderBottom:'1px solid var(--border-row)', paddingBottom:'0.75rem', display:'flex', alignItems:'center', gap:6 }}>
            Active Watchlist
            <HelpTip text="Symbols Sulla monitors and trades. Changes take effect after restart — no code change needed. Sulla automatically runs earnings checks on all symbols in this list." />
          </h2>
          <div className="flex flex-wrap gap-2">
            {symbols.map(sym => (
              <span key={sym} style={{ display:'inline-flex', alignItems:'center', gap:6, background:'rgba(16,185,129,0.12)', color:GREEN, border:'1px solid rgba(16,185,129,0.25)', padding:'4px 10px', borderRadius:6, fontSize:12, fontWeight:600 }}>
                {sym}
                <button onClick={() => removeSymbol(sym)} style={{ color:'rgba(16,185,129,0.6)', cursor:'pointer', border:'none', background:'none', padding:0, display:'flex' }}>
                  <X size={12} />
                </button>
              </span>
            ))}
          </div>
          <div className="flex gap-2">
            <input value={newSymbol} onChange={e => setNewSymbol(e.target.value.toUpperCase())}
              onKeyDown={e => e.key === 'Enter' && addSymbol()}
              placeholder="Add symbol (e.g. TSLA)"
              style={{ flex:1, background:'var(--bg-elevated)', border:'1px solid var(--border-input)', borderRadius:'0.5rem', padding:'0.5rem 0.75rem', color:'var(--text-primary)', fontSize:'0.875rem', outline:'none', fontFamily:'inherit' }}
              onFocus={e => e.target.style.borderColor = GREEN}
              onBlur={e => e.target.style.borderColor = 'var(--border-input)'} />
            <button onClick={addSymbol} className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium"
              style={{ background:GREEN, color:'#fff', border:'none' }}>
              <Plus size={14} /> Add
            </button>
          </div>
        </div>

      </div>
    </div>
  )
}
