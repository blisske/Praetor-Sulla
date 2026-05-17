import { BookOpen, Compass, Layers, Filter, Shield, FlaskConical, LayoutDashboard, MessageSquare, ListChecks, Library, CalendarClock, Globe2 } from 'lucide-react'

const BLUE   = '#3B82F6'   // Sulla brand accent (sections, primary, Trend Following)
const GREEN  = '#34d399'   // semantic — Mean Reversion paradigm
const AMBER  = '#fbbf24'   // semantic — Liquidity Sweep paradigm, drawdown Alert
const RED    = '#f87171'   // semantic — drawdown Halt
const CYAN   = '#06B6D4'   // Volatility Breakout paradigm

// ─── Reusable bits ──────────────────────────────────────────────────────────
const Card = ({ children, id, icon: Icon, title, subtitle }) => (
  <section id={id} className="rounded-xl p-6 scroll-mt-6" style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)' }}>
    <div className="flex items-center gap-2 mb-1">
      {Icon && <Icon size={18} style={{ color: BLUE }} />}
      <h2 className="text-lg font-bold" style={{ color: 'var(--text-primary)' }}>{title}</h2>
    </div>
    {subtitle && <p className="text-xs mb-4" style={{ color: 'var(--text-muted)' }}>{subtitle}</p>}
    <div className="space-y-4 text-sm leading-relaxed" style={{ color: 'var(--text-sub)' }}>{children}</div>
  </section>
)

const Tag = ({ children, color = BLUE }) => (
  <span className="inline-block px-2 py-0.5 rounded text-xs font-mono"
        style={{ background: color + '22', color, border: `1px solid ${color}55` }}>
    {children}
  </span>
)

const Code = ({ children }) => (
  <code className="px-1.5 py-0.5 rounded text-xs font-mono"
        style={{ background: 'var(--bg-elevated)', color: 'var(--text-primary)' }}>
    {children}
  </code>
)

// ─── Section 1 — Overview ───────────────────────────────────────────────────
const Section1 = () => (
  <Card id="overview" icon={Compass} title="What Sulla does"
        subtitle="The 30-second elevator pitch.">
    <p>
      <strong style={{ color: 'var(--text-primary)' }}>Sulla is an autonomous, long-only spot FX trader</strong> that
      runs on the Oanda v20 REST API across the seven major currency pairs.
      It scans every 5 minutes around the 1-hour bar, makes its own entry
      decisions, places its own protective stops, and exits on either a
      take-profit target or a trailing ATR stop that ratchets up but never
      down.
    </p>
    <p>
      <strong style={{ color: 'var(--text-primary)' }}>Unleveraged by design.</strong> Oanda offers retail leverage up to
      50:1 in the US; Sulla deliberately ignores it. Position sizing math
      treats your equity as the hard ceiling, so a $10,000 account can hold
      at most $10,000 of notional exposure across all open positions. Smaller
      positions for the same percent risk, slower compounding, but the tail
      risk that broke many an FX retail account is permanently capped.
    </p>
    <p>
      Right now Sulla is in <Tag>SHADOW MODE</Tag> — every decision the live
      engine would have made is recorded as a "paper" trade in the database,
      with full P&amp;L accounting against a synthetic $10,000 ledger, and no
      Oanda orders are placed. The shadow log is the validation gate before
      the flip to live (which itself still runs against Oanda's
      <em> practice</em> account first — there is no real-money mode shipped
      yet).
    </p>
    <p>
      Sulla is the FX sibling of two other Praetor instances:
      <strong style={{ color: 'var(--text-primary)' }}> Anton</strong> (TradFi
      equities on Alpaca, US session-bound) and
      <strong style={{ color: 'var(--text-primary)' }}> Tiberius</strong>
      (crypto on Kraken, 24/7). All three share the same 4-paradigm signal
      engine + 2+1+1 consensus + self-tuner architecture; they diverge only
      where the asset class forces them to (broker, hours, sizing units,
      blackout sources).
    </p>
    <p>
      Five pillars carry the system:
    </p>
    <ul className="space-y-1 ml-4 list-disc">
      <li><strong style={{ color: 'var(--text-primary)' }}>Multi-paradigm signal engine</strong> — four trading paradigms, each
        with a distinct thesis, route based on the current market regime.</li>
      <li><strong style={{ color: 'var(--text-primary)' }}>2+1+1 consensus</strong> — no single signal fires a trade. A primary
        paradigm + supporting indicators + an AI veto must all agree.</li>
      <li><strong style={{ color: 'var(--text-primary)' }}>Self-tuning</strong> — the engine measures its own profit factor per
        paradigm per pair and proposes bounded parameter adjustments.</li>
      <li><strong style={{ color: 'var(--text-primary)' }}>Macro-event blackout</strong> — high-impact macro releases (NFP,
        FOMC, CPI, ECB / BoJ / BoE rate decisions) skip the affected pairs
        through the event window.</li>
      <li><strong style={{ color: 'var(--text-primary)' }}>Pip-aware sizing</strong> — all stop and target distances respect
        pip granularity, including the 0.01-pip quirk on JPY pairs.</li>
    </ul>
  </Card>
)

// ─── Section 2 — The 7 majors ───────────────────────────────────────────────
const PairCard = ({ tag, name, nickname, character, color }) => (
  <div className="rounded-lg p-4" style={{ background: 'var(--bg-elevated)', border: `1px solid ${color}44` }}>
    <div className="flex items-center gap-2 mb-1">
      <span className="text-xs font-mono px-1.5 py-0.5 rounded" style={{ background: color + '33', color }}>{tag}</span>
      <span className="font-semibold text-sm" style={{ color: 'var(--text-primary)' }}>{name}</span>
      <span className="text-xs italic" style={{ color: 'var(--text-muted)' }}>{nickname}</span>
    </div>
    <div className="text-xs leading-relaxed" style={{ color: 'var(--text-sub)' }}>{character}</div>
  </div>
)

const Section2 = () => (
  <Card id="universe" icon={Globe2} title="The seven majors"
        subtitle="Sulla's universe. Six of the seven involve USD.">
    <p>
      Sulla trades the seven currency pairs that, between them, account for
      ~85% of daily FX volume. Six of them have USD on one side, which means
      almost any US macro print (NFP, CPI, FOMC) ripples through the entire
      watchlist simultaneously — context that drives the macro-blackout
      design in <a href="#blackout" className="underline" style={{ color: BLUE }}>§8</a>.
    </p>

    <div className="grid grid-cols-1 md:grid-cols-2 gap-3 my-3">
      <PairCard tag="EUR/USD" name="Euro / US Dollar" nickname="fiber"
        character="The most-traded pair on Earth (~25% of FX volume). Tightest spreads, deepest liquidity. Highly mean-reverting in calm markets; trends cleanly when the EU/US monetary-policy divergence opens up."
        color={BLUE} />
      <PairCard tag="GBP/USD" name="Pound / US Dollar" nickname="cable"
        character="The original wire-quoted pair. Higher vol than EUR/USD because GBP is sensitive to UK domestic data and BoE pivots. News-driven gaps around UK opens."
        color={BLUE} />
      <PairCard tag="USD/JPY" name="Dollar / Yen" nickname="ninja"
        character="The textbook carry-trade pair. Slow rallies when US/JPN rate differentials are wide; violent reversals when the BoJ shifts (rare but huge). 2-decimal quote — pip = 0.01."
        color={BLUE} />
      <PairCard tag="USD/CHF" name="Dollar / Swiss Franc" nickname="swissy"
        character="The classic safe-haven pair. When risk-off hits, CHF strengthens (sometimes harder than gold). The SNB's history of stealth intervention adds tail risk during stress events."
        color={BLUE} />
      <PairCard tag="AUD/USD" name="Aussie / US Dollar" nickname="aussie"
        character="Risk-on commodity proxy. Trades like a leveraged China / industrial-metals bet. RBA decisions move it, but global risk appetite moves it more."
        color={BLUE} />
      <PairCard tag="USD/CAD" name="Dollar / Loonie" nickname="loonie"
        character="The most oil-correlated major. WTI direction often drives CAD inversely to USD. BoC follows the Fed loosely; CAD-specific data (CPI, jobs) creates trading windows."
        color={BLUE} />
      <PairCard tag="NZD/USD" name="Kiwi / US Dollar" nickname="kiwi"
        character="Smaller cousin of AUD/USD — same risk-on commodity exposure but with NZ-specific dairy/agricultural sensitivity. Thinnest liquidity of the seven majors; wider spreads in the Asia session."
        color={BLUE} />
    </div>

    <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
      The active list is hot-reloaded from <Code>Config.yaml</Code> each cycle.
      To narrow or broaden the universe, edit <Code>strategy.active_symbols</Code>
      via the Config page — no restart needed. Adding non-major pairs (EUR/JPY,
      GBP/JPY etc.) works for indicator fetch + signal evaluation today, but
      position sizing for non-USD crosses is gated by <Code>fx_math.pip_value_usd()</Code>
      which currently raises NotImplementedError for them; the per-trade USD-conversion
      leg lands when the universe expands.
    </p>
  </Card>
)

// ─── Section 3 — Paradigms ──────────────────────────────────────────────────
const ParadigmCard = ({ tag, name, fires, thesis, color }) => (
  <div className="rounded-lg p-4" style={{ background: 'var(--bg-elevated)', border: `1px solid ${color}55` }}>
    <div className="flex items-center gap-2 mb-2">
      <span className="text-xs font-mono px-1.5 py-0.5 rounded" style={{ background: color + '33', color }}>{tag}</span>
      <span className="font-semibold" style={{ color: 'var(--text-primary)' }}>{name}</span>
    </div>
    <div className="text-xs mb-2" style={{ color: 'var(--text-muted)' }}><strong style={{ color: 'var(--text-sub)' }}>Fires when:</strong> {fires}</div>
    <div className="text-xs" style={{ color: 'var(--text-sub)' }}>{thesis}</div>
  </div>
)

const Section3 = () => (
  <Card id="paradigms" icon={Layers} title="The four trading paradigms"
        subtitle="Each paradigm has a different thesis and only activates when market conditions match.">
    <p>
      Before any paradigm runs, Sulla asks one question:
      <strong> is the market trending or ranging?</strong> It uses ADX
      (Average Directional Index) — a momentum-strength gauge — to decide.
      The threshold is configurable per pair (defaults to 30). Above it:
      trending. Below it: ranging.
    </p>

    <div className="rounded-lg p-4 my-4" style={{ background: 'var(--bg-base)', border: '1px solid var(--border)' }}>
      <div className="text-xs uppercase tracking-wider mb-3" style={{ color: 'var(--text-muted)' }}>Regime gate</div>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3 items-center text-xs">
        <div className="text-center p-3 rounded-lg" style={{ background: 'var(--bg-elevated)' }}>
          <div className="font-mono" style={{ color: 'var(--text-primary)' }}>ADX &gt; 30</div>
          <div className="mt-1" style={{ color: 'var(--text-muted)' }}>TRENDING</div>
        </div>
        <div className="text-center" style={{ color: 'var(--text-muted)' }}>→ routes to</div>
        <div className="space-y-2">
          <div className="p-2 rounded" style={{ background: BLUE + '22', color: BLUE }}>Trend Following</div>
          <div className="p-2 rounded" style={{ background: CYAN + '22', color: CYAN }}>Volatility Breakout (any regime)</div>
        </div>
        <div className="text-center p-3 rounded-lg" style={{ background: 'var(--bg-elevated)' }}>
          <div className="font-mono" style={{ color: 'var(--text-primary)' }}>ADX &lt; 30</div>
          <div className="mt-1" style={{ color: 'var(--text-muted)' }}>RANGING</div>
        </div>
        <div className="text-center" style={{ color: 'var(--text-muted)' }}>→ routes to</div>
        <div className="space-y-2">
          <div className="p-2 rounded" style={{ background: GREEN + '22', color: GREEN }}>Mean Reversion</div>
          <div className="p-2 rounded" style={{ background: AMBER + '22', color: AMBER }}>Liquidity Sweep</div>
        </div>
      </div>
    </div>

    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
      <ParadigmCard
        tag="A" name="Trend Following" color={BLUE}
        fires="Strong uptrend (ADX high, EMA9 &gt; EMA21) + RSI dipped below entry threshold"
        thesis='"Buy the dip in a strong trend." Catches pullbacks during established momentum, riding the trail until the trend rolls over. The cleanest paradigm for FX — currency trends measured in weeks last longer and are less prone to overnight reversal than equity trends.'
      />
      <ParadigmCard
        tag="B" name="Mean Reversion" color={GREEN}
        fires="Range-bound market + price crashes through Bollinger lower band + RSI deeply oversold"
        thesis='"Buy the floor of the range." Targets a return to the middle of the range; exits at the mean or upper band. Works particularly well on EUR/USD and USD/CHF when neither central bank is in a pivot window.'
      />
      <ParadigmCard
        tag="C" name="Volatility Breakout" color={CYAN}
        fires="Bollinger Band Width compressed (squeeze) + price pierces upper band + strong RSI surge"
        thesis='"Catch the explosion out of a coiled spring." Fires in either regime — when volatility has been suppressed and is about to expand. Common around central-bank decision windows (where Sulla is already gated by the macro blackout, by design).'
      />
      <ParadigmCard
        tag="D" name="Liquidity Sweep" color={AMBER}
        fires="Range-bound + ADX in true range zone (&lt;18) + wick pierces lower band but candle closes back inside + RSI exhausted"
        thesis='"Buy the algo-driven fakeout." Detects a sweep at the range floor that reverses immediately — the FX version of a stop-hunt. Most useful in the Asia session when liquidity is thinner and 100-pip wicks are commonplace.'
      />
    </div>

    <p className="text-xs mt-2" style={{ color: 'var(--text-muted)' }}>
      Each paradigm has its own entry RSI threshold, configurable per pair
      via <Code>strategy.symbol_overrides</Code> in <Code>Config.yaml</Code>.
      The paradigm logic is identical to Anton (TradFi) and Tiberius (crypto)
      — the underlying math doesn't care whether it's looking at AAPL bars or
      BTC bars or EUR/USD bars. What changes per asset class is the bar
      timeframe (Sulla uses 1h), session bounds (FX is 24/5), and event
      blackouts (Sulla uses macro releases, see <a href="#blackout" className="underline" style={{ color: BLUE }}>§8</a>).
    </p>
  </Card>
)

// ─── Section 4 — Consensus ──────────────────────────────────────────────────
const ConsensusRow = ({ n, label, desc, points, color = BLUE }) => (
  <div className="grid grid-cols-12 gap-3 items-start py-3 border-b last:border-b-0" style={{ borderColor: 'var(--border)' }}>
    <div className="col-span-1 flex justify-center">
      <div className="w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold"
           style={{ background: color + '22', color, border: `1px solid ${color}55` }}>{n}</div>
    </div>
    <div className="col-span-3 font-semibold text-sm" style={{ color: 'var(--text-primary)' }}>{label}</div>
    <div className="col-span-6 text-xs" style={{ color: 'var(--text-sub)' }}>{desc}</div>
    <div className="col-span-2 text-right text-xs font-mono" style={{ color }}>{points}</div>
  </div>
)

const Section4 = () => (
  <Card id="consensus" icon={Filter} title="The 2+1+1 consensus"
        subtitle="Why no single signal can fire a trade.">
    <p>
      Sulla doesn't trade on a single indicator. Every potential entry has to
      clear <strong>four independent layers</strong>. If any layer disagrees
      strongly enough, the trade is aborted and logged with the reason.
    </p>

    <div className="rounded-lg overflow-hidden" style={{ background: 'var(--bg-elevated)' }}>
      <ConsensusRow n="1" label="Primary signal" color={BLUE}
        desc="One of the four paradigms fires. This is the trade thesis." points="+1" />
      <ConsensusRow n="2" label="Supporting signals" color={GREEN}
        desc="Three orthogonal checks: volume participation (≥80% of 20-bar avg), RSI direction (rising for entries), ADX conviction (strong + rising in TRENDING, weak + falling in RANGING). At least 2 of 3 must pass."
        points="+1 each" />
      <ConsensusRow n="3" label="AI verdict" color={CYAN}
        desc="Gemma 4 26B (running on Battlemage) reads the signal context plus recent FX / central-bank news from Brave. A BEARISH verdict aborts the trade outright."
        points="veto only" />
      <ConsensusRow n="4" label="Score gate" color={AMBER}
        desc="Total score (1 paradigm + supporting signals + 1 if AI not BEARISH) must reach min_consensus (default 3)."
        points="≥ 3 / 4" />
    </div>

    <div className="rounded-lg p-4 mt-4" style={{ background: 'var(--bg-base)', border: '1px solid var(--border)' }}>
      <div className="text-xs uppercase tracking-wider mb-2" style={{ color: 'var(--text-muted)' }}>Why this matters</div>
      <p className="text-xs">
        FX is the largest financial market on Earth (~$7T daily turnover) and
        unsurprisingly the most algo-saturated. Single-indicator FX systems get
        front-run mercilessly. The four-layer funnel exists so we're trading
        only when the technical setup, the supporting microstructure, and the
        macro narrative all line up — leaving algos to fight over the noise.
      </p>
    </div>

    <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
      The Telegram <Code>SHADOW BUY</Code> notification surfaces the full
      consensus chain for every executed entry — score, supporting reasons,
      AI verdict, and the analyst's reasoning. That's your audit trail.
    </p>
  </Card>
)

// ─── Section 5 — Risk ───────────────────────────────────────────────────────
const Tier = ({ pct, label, action, color }) => (
  <div className="flex items-center gap-3 py-2">
    <div className="w-20 text-right font-mono text-sm" style={{ color }}>{pct}</div>
    <div className="flex-1 h-2 rounded" style={{ background: 'var(--bg-elevated)' }}>
      <div className="h-full rounded" style={{ background: color, width: '100%', opacity: 0.7 }} />
    </div>
    <div className="flex-1 text-xs">
      <div className="font-semibold" style={{ color: 'var(--text-primary)' }}>{label}</div>
      <div style={{ color: 'var(--text-muted)' }}>{action}</div>
    </div>
  </div>
)

const Section5 = () => (
  <Card id="risk" icon={Shield} title="Risk management"
        subtitle="Where Sulla will and won't take pain.">
    <p>
      Sulla is growth-focused but disciplined. Three knobs cover the bulk of
      position-level risk; a tiered drawdown ladder covers account-level risk;
      the macro blackout covers tail-event risk.
    </p>

    <div className="grid grid-cols-1 md:grid-cols-3 gap-3 my-3">
      <div className="rounded-lg p-3" style={{ background: 'var(--bg-elevated)' }}>
        <div className="text-xs" style={{ color: 'var(--text-muted)' }}>Per-trade risk</div>
        <div className="text-base font-mono" style={{ color: 'var(--text-primary)' }}>5% paper · 2% live</div>
        <div className="text-xs mt-1" style={{ color: 'var(--text-muted)' }}>Units sized via pip-distance to stop, capped by position cap</div>
      </div>
      <div className="rounded-lg p-3" style={{ background: 'var(--bg-elevated)' }}>
        <div className="text-xs" style={{ color: 'var(--text-muted)' }}>Position cap</div>
        <div className="text-base font-mono" style={{ color: 'var(--text-primary)' }}>12% of equity</div>
        <div className="text-xs mt-1" style={{ color: 'var(--text-muted)' }}>Hard ceiling per pair, USD-equivalent notional</div>
      </div>
      <div className="rounded-lg p-3" style={{ background: 'var(--bg-elevated)' }}>
        <div className="text-xs" style={{ color: 'var(--text-muted)' }}>Max open trades</div>
        <div className="text-base font-mono" style={{ color: 'var(--text-primary)' }}>5</div>
        <div className="text-xs mt-1" style={{ color: 'var(--text-muted)' }}>Composes with correlation-aware sizing (USD_short / USD_long buckets)</div>
      </div>
    </div>

    <p>
      Stops are placed automatically immediately after every fill at
      <Code>entry − ATR×2</Code> in price space. From there a trailing ATR
      ratchet only moves stops <em>up</em>, never down — so a position that
      runs in our favor locks in progress without giving it back. Pip-aware
      math handles the 0.01-pip JPY-pair convention so stops on USD/JPY are
      placed at the correct precision (3 decimals, not 5).
    </p>

    <div className="rounded-lg p-4 my-4" style={{ background: 'var(--bg-base)', border: '1px solid var(--border)' }}>
      <div className="text-xs uppercase tracking-wider mb-3" style={{ color: 'var(--text-muted)' }}>Tiered drawdown response</div>
      <Tier pct="−8%"  label="Alert"   action="Telegram notification, no automatic action. Heads-up."   color={AMBER} />
      <Tier pct="−15%" label="Derisk"  action="Risk-per-trade and position cap × 0.5. Recovers when DD &lt; 10%." color={BLUE} />
      <Tier pct="−25%" label="Halt"    action="Trading paused. Manual /resume required (re-checks DD first)." color={RED} />
    </div>

    <div className="rounded-lg p-4 my-4" style={{ background: BLUE + '11', border: `1px solid ${BLUE}44` }}>
      <div className="text-xs uppercase tracking-wider mb-2" style={{ color: BLUE }}>Sulla-specific: unleveraged spot</div>
      <p className="text-xs">
        Oanda offers retail leverage up to 50:1 in the US. Sulla deliberately
        runs <strong>unleveraged 1:1</strong>: position notional cannot exceed
        equity. This matches the no-margin discipline of Tiberius (spot crypto)
        and Anton (cash account, no margin) — the entire Praetor swarm is
        designed around "you can lose what you bet but not more than you bet."
        Smaller positions for the same percent risk, slower compounding, but
        margin-call risk and forced-liquidation risk are permanently absent.
        See <a href="#playbook" className="underline" style={{ color: BLUE }}>§10 Playbook</a>'s
        live-flip scenario for the gates that need to clear before flipping
        to a real-money Oanda account.
      </p>
    </div>

    <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
      Drawdown is computed against realized equity (closed P&amp;L only), not
      mark-to-market — so a single open position swinging unrealized cannot
      trigger a false halt. Peak equity is persisted to the database so the
      calculation survives restarts.
    </p>
  </Card>
)

// ─── Section 6 — Self-tuning ────────────────────────────────────────────────
const TuningStep = ({ n, title, body }) => (
  <div className="flex gap-3 items-start">
    <div className="w-7 h-7 rounded-full flex-shrink-0 flex items-center justify-center text-xs font-bold mt-0.5"
         style={{ background: BLUE + '22', color: BLUE, border: `1px solid ${BLUE}55` }}>{n}</div>
    <div>
      <div className="font-semibold text-sm" style={{ color: 'var(--text-primary)' }}>{title}</div>
      <div className="text-xs mt-0.5" style={{ color: 'var(--text-sub)' }}>{body}</div>
    </div>
  </div>
)

const Section6 = () => (
  <Card id="tuning" icon={FlaskConical} title="The self-tuning engine"
        subtitle="What it can and can't optimize, and why patience is required.">
    <div className="grid grid-cols-1 md:grid-cols-2 gap-3 my-3">
      <div className="rounded-lg p-4" style={{ background: GREEN + '11', border: `1px solid ${GREEN}44` }}>
        <div className="text-xs uppercase tracking-wider mb-2" style={{ color: GREEN }}>Tuner CAN adjust</div>
        <ul className="text-xs space-y-1 ml-4 list-disc" style={{ color: 'var(--text-sub)' }}>
          <li>RSI entry thresholds (per pair per paradigm, range 20–65)</li>
          <li>BBW threshold for Volatility Breakout (0.03–0.20)</li>
          <li>Initial stop multiplier (1.0–3.5 ATR)</li>
          <li>Trailing stop multiplier (1.5–4.0 ATR)</li>
          <li>ADX trend threshold per pair (18–40)</li>
        </ul>
      </div>
      <div className="rounded-lg p-4" style={{ background: RED + '11', border: `1px solid ${RED}44` }}>
        <div className="text-xs uppercase tracking-wider mb-2" style={{ color: RED }}>Tuner CANNOT change</div>
        <ul className="text-xs space-y-1 ml-4 list-disc" style={{ color: 'var(--text-sub)' }}>
          <li>Enable/disable a paradigm</li>
          <li>The daily multi-timeframe filter</li>
          <li>Consensus settings (min_consensus_score, volume threshold)</li>
          <li>Risk per trade, position cap, max open trades</li>
          <li>The active universe (add/drop pairs)</li>
          <li>Macro blackout window, impact threshold</li>
          <li>The unleveraged-by-design ceiling</li>
        </ul>
      </div>
    </div>

    <div className="rounded-lg p-4 my-4" style={{ background: 'var(--bg-base)', border: '1px solid var(--border)' }}>
      <div className="text-xs uppercase tracking-wider mb-4" style={{ color: 'var(--text-muted)' }}>Tuning lifecycle</div>
      <div className="space-y-3">
        <TuningStep n="1" title="Trigger gate"
          body="Wait for 10 closed shadow trades on this exact (pair, paradigm) slot. Below 10, no proposal." />
        <TuningStep n="2" title="Measure profit factor"
          body="Compute PF = gross profit ÷ gross loss. ≥ 1.5 means healthy — no change needed. Below 1.5, propose one bounded adjustment." />
        <TuningStep n="3" title="Validation window"
          body="Proposal enters SHADOW_PENDING. Wait for 10 more closed trades and recompute PF." />
        <TuningStep n="4" title="Promote or reject"
          body="If PF improved ≥ 5%: promoted to Config.yaml (live params updated). Otherwise: rejected, snapshot retained for audit." />
      </div>
    </div>

    <p>
      <strong style={{ color: 'var(--text-primary)' }}>Why patience is required.</strong> Each proposal needs ~10 trades to validate.
      Sulla's universe is small (7 majors × 4 paradigms = <strong>28 tuning slots</strong>)
      so the per-slot trade pace is roughly 1 trade per week per slot. A
      single parameter change typically validates over <strong>2–3 weeks</strong>.
      A complete optimization sweep across the universe is a <strong>6–12 month</strong>
      process. Pre-tuning the obviously-broken settings manually still matters
      — the engine is for refinement, not bootstrap.
    </p>
  </Card>
)

// ─── Section 7 — Reading the dashboard ──────────────────────────────────────
const Section7 = () => (
  <Card id="dashboard" icon={LayoutDashboard} title="Reading the dashboard"
        subtitle="What each number is actually telling you.">
    <div className="space-y-4">
      <div>
        <div className="font-semibold mb-1" style={{ color: 'var(--text-primary)' }}>Equity Card</div>
        <p>Top-left tile. Currently shows <strong>realized shadow equity only</strong> —
          starting capital ($10,000) plus the sum of closed-trade P&amp;L. It
          does <em>not</em> include unrealized swings on open positions. The
          card and the equity curve are anchored together via the
          <Code>shadow_equity</Code> endpoint, so they always match.</p>
      </div>
      <div>
        <div className="font-semibold mb-1" style={{ color: 'var(--text-primary)' }}>Equity Curve</div>
        <p>The chart traces realized P&amp;L through time, with one point per
          closed shadow sell plus a "Now" anchor at the current equity card
          value. Drops are stop-outs; jumps are take-profit closes; flats are
          weekends (FX is closed Sat-Sun) and periods with no exits.</p>
      </div>
      <div>
        <div className="font-semibold mb-1" style={{ color: 'var(--text-primary)' }}>Open Positions Card</div>
        <p>Each row shows entry price, current price (mark-to-market),
          unrealized P&amp;L in $ and %, position size in units, current
          trailing stop with stop-distance %. <strong>Stop distance &lt; 1%
          </strong> means the position is effectively about to take profit
          (or stop out) on the next tick — useful triage at a glance.</p>
      </div>
      <div>
        <div className="font-semibold mb-1" style={{ color: 'var(--text-primary)' }}>Market page</div>
        <p>Per-pair indicators with a multi-select chart toggle (Price / RSI /
          ADX / Volume). The card grid at the top shows current regime, BB
          position, RSI, and tick volume from Oanda. Switch timeframe with
          the selector for the higher-timeframe view. JPY pairs display at
          3 decimals; non-JPY pairs at 5.</p>
      </div>
      <div>
        <div className="font-semibold mb-1" style={{ color: 'var(--text-primary)' }}>Tuning page</div>
        <p>Two tables. <em>Validation Queue</em> shows proposals currently in
          SHADOW_PENDING — which parameter, old value, new value, status.
          <em> Audit Log</em> shows every promotion and rejection with full
          before/after, the metric delta, and the validation window count.</p>
      </div>
      <div>
        <div className="font-semibold mb-1" style={{ color: 'var(--text-primary)' }}>Macro blackout banner</div>
        <p>When any pair is currently in a high-impact event window, the
          <Code>/report</Code> Telegram cmd surfaces the triggering event in
          an "Active Macro Blackout" section. The web dashboard's Market
          page doesn't surface this yet (planned for Phase 5+); for now,
          send <Code>/calendar</Code> in Telegram to see what's coming.</p>
      </div>
    </div>
  </Card>
)

// ─── Section 8 — Macro blackout (NEW for Sulla) ─────────────────────────────
const Section8 = () => (
  <Card id="blackout" icon={CalendarClock} title="Macro-event blackout"
        subtitle="How Sulla dodges the prints that blow through stops.">
    <p>
      Single-event macro releases routinely produce stop-hunting volatility
      that no ATR-based stop can withstand. NFP is the textbook case — a
      bad number can move EUR/USD 80+ pips in 60 seconds. Stops at
      <Code>entry − ATR×2</Code> are perfectly calibrated for normal market
      conditions and perfectly useless against a print like that.
    </p>
    <p>
      Sulla's macro-blackout layer blocks new entries on any pair whose base
      OR quote currency has a high-impact event in the configured window. We
      let the print land, the dust settle, then resume scanning.
    </p>

    <div className="rounded-lg p-4 my-4" style={{ background: 'var(--bg-base)', border: '1px solid var(--border)' }}>
      <div className="text-xs uppercase tracking-wider mb-3" style={{ color: 'var(--text-muted)' }}>How it works</div>
      <div className="space-y-2 text-xs">
        <div className="grid grid-cols-12 gap-2">
          <div className="col-span-3 font-semibold" style={{ color: 'var(--text-primary)' }}>Data source</div>
          <div className="col-span-9">ForexFactory weekly JSON feed. Free, no auth, hand-curated by humans. Same data every retail FX bot uses for the last ~8 years.</div>
        </div>
        <div className="grid grid-cols-12 gap-2">
          <div className="col-span-3 font-semibold" style={{ color: 'var(--text-primary)' }}>Refresh cadence</div>
          <div className="col-span-9">In-memory cache, 30-minute TTL. Refreshes lazily on first use after expiry. Fail-open: a feed outage falls back to "no blackout" rather than halting trading.</div>
        </div>
        <div className="grid grid-cols-12 gap-2">
          <div className="col-span-3 font-semibold" style={{ color: 'var(--text-primary)' }}>Trigger filter</div>
          <div className="col-span-9">Default <Code>importance_min: High</Code> — only NFP, CPI, FOMC, rate decisions etc. trigger blackouts. "Medium" would block roughly half the trading week.</div>
        </div>
        <div className="grid grid-cols-12 gap-2">
          <div className="col-span-3 font-semibold" style={{ color: 'var(--text-primary)' }}>Window</div>
          <div className="col-span-9">Default <Code>60 min before</Code> + <Code>120 min after</Code> = 3-hour total window per high-impact event.</div>
        </div>
        <div className="grid grid-cols-12 gap-2">
          <div className="col-span-3 font-semibold" style={{ color: 'var(--text-primary)' }}>Pair selection</div>
          <div className="col-span-9">Per-currency match: an NFP print blocks all 6 USD-leg pairs simultaneously. A BoJ statement blocks only USD/JPY. ECB rate decision blocks EUR/USD.</div>
        </div>
      </div>
    </div>

    <div className="grid grid-cols-1 md:grid-cols-2 gap-3 my-4">
      <div className="rounded-lg p-4" style={{ background: 'var(--bg-elevated)', border: `1px solid ${BLUE}44` }}>
        <div className="text-xs uppercase tracking-wider mb-2" style={{ color: BLUE }}>Headline events</div>
        <ul className="text-xs space-y-1 ml-4 list-disc" style={{ color: 'var(--text-sub)' }}>
          <li><strong>USD:</strong> NFP (1st Friday/month), FOMC (8×/year), CPI, PPI, retail sales</li>
          <li><strong>EUR:</strong> ECB rate decision, Eurozone CPI, German IFO</li>
          <li><strong>GBP:</strong> BoE rate decision, UK CPI, employment</li>
          <li><strong>JPY:</strong> BoJ rate decision (less common but huge), CPI, Tankan</li>
          <li><strong>AUD:</strong> RBA rate decision, employment</li>
          <li><strong>NZD:</strong> RBNZ rate decision, dairy auctions</li>
          <li><strong>CHF:</strong> SNB quarterly assessment, CPI</li>
          <li><strong>CAD:</strong> BoC rate decision, CPI, employment</li>
        </ul>
      </div>
      <div className="rounded-lg p-4" style={{ background: 'var(--bg-elevated)', border: `1px solid ${AMBER}44` }}>
        <div className="text-xs uppercase tracking-wider mb-2" style={{ color: AMBER }}>What it does NOT do (yet)</div>
        <ul className="text-xs space-y-1 ml-4 list-disc" style={{ color: 'var(--text-sub)' }}>
          <li>Force-exit open positions before an event — if you're already long USD/CAD and BoC rate decision is in 30 min, the position rides through. Stop is your only defense.</li>
          <li>Differentiate per-event window length — FOMC and CPI get the same 60/120 minute window.</li>
          <li>Cover speech-event tail (Powell's testimony etc.) — those aren't always flagged as "High" in ForexFactory's classification.</li>
        </ul>
        <div className="text-xs mt-2" style={{ color: 'var(--text-muted)' }}>Force-exit is the obvious next iteration; planning to add after we've watched a real event window play out on a live shadow position.</div>
      </div>
    </div>

    <p>
      Run <Code>/calendar</Code> in Telegram to see what's coming this week.
      The default horizon is 48h; pass an argument like <Code>/calendar 168</Code>
      for the full week. Events are grouped by day with impact emoji
      (🔴 High, 🟡 Medium, ⚪ Low) and the forecast/previous values where
      ForexFactory has them.
    </p>
  </Card>
)

// ─── Section 9 — Telegram ───────────────────────────────────────────────────
const TelegramRow = ({ cmd, what }) => (
  <tr className="border-b last:border-b-0" style={{ borderColor: 'var(--border)' }}>
    <td className="py-2 pr-4 font-mono text-xs" style={{ color: BLUE }}>{cmd}</td>
    <td className="py-2 text-xs" style={{ color: 'var(--text-sub)' }}>{what}</td>
  </tr>
)

const Section9 = () => (
  <Card id="telegram" icon={MessageSquare} title="Telegram commands"
        subtitle="The remote-control surface. All commands are auth-scoped to the configured user ID.">
    <table className="w-full">
      <tbody>
        <TelegramRow cmd="/indicators" what="Regime-aware technical readout for all 7 majors. Best for a quick health check." />
        <TelegramRow cmd="/report" what="Portfolio audit — equity, open positions, active defense (stops), active macro blackouts if any." />
        <TelegramRow cmd="/pnl" what="Shadow performance — per-pair win rate, profit factor, average return, dollar P&L." />
        <TelegramRow cmd="/calendar" what="Upcoming high-impact macro events for the watchlist (default 48h). Optional hours arg: /calendar 168." />
        <TelegramRow cmd="/buy PAIR USD" what="Manual buy with auto stop-loss. Accepts EUR/USD or EURUSD or eur_usd; case-insensitive." />
        <TelegramRow cmd="/kill" what="Arms the emergency liquidation. Requires /confirm_kill within 60s." />
        <TelegramRow cmd="/confirm_kill" what="Closes ALL open shadow positions at market. Two-step on purpose." />
        <TelegramRow cmd="/resume" what="Resumes trading after a drawdown halt. Re-checks DD first." />
        <TelegramRow cmd="/restart" what="Queues a clean engine restart via the .restart_engine flag. Compose respawns with fresh Config.yaml." />
        <TelegramRow cmd="/help" what="Shows the full command reference inside the chat." />
        <TelegramRow cmd="[pair text]" what='Plain text like "EUR/USD" or "USDJPY" → direct AI sentiment query for that pair.' />
      </tbody>
    </table>
    <p className="text-xs mt-2" style={{ color: 'var(--text-muted)' }}>
      Typing <Code>/</Code> in Telegram surfaces all of these in the autocomplete
      menu — registered at engine startup via <Code>set_my_commands</Code>.
      Trade events (SHADOW BUY / SHADOW STOP HIT / SHADOW TAKE PROFIT /
      BEARISH VETO) push notifications to the configured user automatically.
    </p>
  </Card>
)

// ─── Section 10 — Playbook ──────────────────────────────────────────────────
const Scenario = ({ when, then, color = BLUE }) => (
  <div className="rounded-lg p-4" style={{ background: 'var(--bg-elevated)', border: `1px solid ${color}33` }}>
    <div className="text-xs uppercase tracking-wider mb-2" style={{ color }}>When you see…</div>
    <div className="font-semibold text-sm mb-3" style={{ color: 'var(--text-primary)' }}>{when}</div>
    <div className="text-xs uppercase tracking-wider mb-1" style={{ color: 'var(--text-muted)' }}>Do this</div>
    <div className="text-xs leading-relaxed" style={{ color: 'var(--text-sub)' }}>{then}</div>
  </div>
)

const Section10 = () => (
  <Card id="playbook" icon={ListChecks} title="Playbook — what to do when…"
        subtitle="Common scenarios and the canonical response.">
    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
      <Scenario color={AMBER}
        when="A drawdown alert (−8%) fires on Telegram"
        then={<>Don't react automatically. Pull <Code>/report</Code> and <Code>/pnl</Code> to see whether the drawdown is concentrated in 1–2 pairs or spread across the book. If concentrated, check whether stops are still appropriately placed. The system has not done anything yet — this is just a heads-up.</>}
      />
      <Scenario color={BLUE}
        when="The system is in Derisk mode (−15% DD)"
        then={<>New entries are sized at half the normal risk and half the position cap. This is automatic and self-recovering — when DD comes back inside −10%, the system returns to normal sizing. No action needed unless you want to override.</>}
      />
      <Scenario color={RED}
        when="A drawdown halt fires (−25%)"
        then={<>Trading is paused. Investigate first: which pair hit, what macro driver was behind it, were stops respected. When you're satisfied the system is healthy, send <Code>/resume</Code> on Telegram. The command re-checks the DD calculation before resuming — if you're still under threshold, it will refuse.</>}
      />
      <Scenario color={GREEN}
        when="You want to manually buy a pair"
        then={<>Telegram: <Code>/buy EUR/USD 1000</Code> (pair, USD notional). Sulla takes the trade at the current market and immediately places an ATR×2 stop. The trade is recorded with paradigm = "MANUAL OVERRIDE" so the tuner doesn't pick it up. Pair format is flexible: <Code>EURUSD</Code> / <Code>eur_usd</Code> / <Code>EUR/USD</Code> all work.</>}
      />
      <Scenario color={CYAN}
        when="A position is running hot and you want to lock in profit"
        then={<>Let the trailing stop ratchet up — it moves with each new high and harvests most of the move. The trailing multiplier (default 2.5 × ATR) lives in <Code>Config.yaml</Code> under <Code>ratchet.trailing_stop_mult</Code>; tighter values lock in more profit but cut runners short. To take immediate profit on a manual position, just <Code>/kill</Code> → <Code>/confirm_kill</Code>.</>}
      />
      <Scenario color={AMBER}
        when="A major macro event is approaching"
        then={<>Send <Code>/calendar</Code> to see the schedule. New entries on affected pairs auto-suspend 60 min before each high-impact event and resume 120 min after. <strong>Open positions ride through</strong> — if you're already long USD/CAD and BoC rate decision is in 30 min, the position's stop is your only defense. Consider manual flatten via <Code>/kill</Code> if you're nervous (Phase 4 doesn't force-exit; that's a planned addition).</>}
      />
      <Scenario color={BLUE}
        when="You want to add or drop a pair from the universe"
        then={<>Edit <Code>strategy.active_symbols</Code> in <Code>Config.yaml</Code> via the Config page. Sulla hot-reloads the watchlist each cycle — no restart needed. Adding non-USD crosses (EUR/JPY, GBP/JPY etc.) works for indicator + signal evaluation today, but position sizing for them is gated by <Code>fx_math.pip_value_usd</Code> which needs USD-leg conversion (planned addition).</>}
      />
      <Scenario color={GREEN}
        when="The flip from Shadow to Live (Oanda practice → real)"
        then={<>Verify all deployment gates: 30+ closed shadow trades across pairs, at least one complete self-tuning cycle, no active drawdown halt, <Code>risk_per_trade_pct</Code> lowered to 2.0%, macro-blackout proven against at least one FOMC fire. Edit <Code>Config.yaml</Code>: set <Code>oanda.shadow_mode: false</Code>. The Config page's Save button writes the YAML and triggers a clean engine restart. The first live trade will execute on the next consensus-passing signal. <strong>Note:</strong> Phase 3 ships with <Code>execution.execute_buy_with_stop</Code> as a NotImplementedError stub — the live-Oanda order submission is Phase 6, not yet built.</>}
      />
    </div>
  </Card>
)

// ─── Section 11 — Glossary ──────────────────────────────────────────────────
const Term = ({ term, abbr, children }) => (
  <div className="grid grid-cols-12 gap-3 py-2 border-b last:border-b-0" style={{ borderColor: 'var(--border)' }}>
    <div className="col-span-12 md:col-span-4">
      <div className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>{term}</div>
      {abbr && <div className="text-xs font-mono" style={{ color: 'var(--text-muted)' }}>{abbr}</div>}
    </div>
    <div className="col-span-12 md:col-span-8 text-xs leading-relaxed" style={{ color: 'var(--text-sub)' }}>{children}</div>
  </div>
)

const TermGroup = ({ title, color = BLUE, children }) => (
  <div className="rounded-lg p-4" style={{ background: 'var(--bg-elevated)', border: `1px solid ${color}33` }}>
    <div className="text-xs uppercase tracking-wider mb-2" style={{ color }}>{title}</div>
    <div>{children}</div>
  </div>
)

const Section11 = () => (
  <Card id="glossary" icon={Library} title="Appendix: Glossary"
        subtitle="Quick definitions for terms used elsewhere in the Guide.">
    <div className="space-y-4">

      <TermGroup title="FX mechanics" color={BLUE}>
        <Term term="Pip" abbr="Percentage in point">
          The smallest standard price increment for an FX pair. For 4-decimal
          majors (EUR/USD, GBP/USD etc.): 1 pip = 0.0001. For JPY pairs
          (USD/JPY, EUR/JPY): 1 pip = 0.01 (because JPY pairs quote with 2-3
          decimals, not 4-5). Stops and targets are typically discussed in
          pips ("80-pip stop on EUR/USD") because pip moves are roughly
          comparable across pairs.
        </Term>
        <Term term="Pipette">
          One-tenth of a pip — the 5th decimal on majors (or 3rd on JPY pairs).
          Modern brokers including Oanda quote at pipette precision; Sulla's
          stop math uses pipettes internally and displays at pip precision.
        </Term>
        <Term term="Lot">
          Standard FX position-size unit. <strong>Standard lot</strong> = 100,000 units
          of base currency. <strong>Mini lot</strong> = 10,000. <strong>Micro lot</strong> = 1,000.
          Oanda accepts down to 1 unit, so Sulla sizes in raw units (not lots)
          for flexibility. A pip of EUR/USD at 10,000 units is worth $1; at
          100,000 units it's $10.
        </Term>
        <Term term="Base / Quote currency">
          For pair <strong>EUR/USD</strong>: EUR is the <em>base</em>, USD is
          the <em>quote</em>. Price 1.0823 means 1 EUR = 1.0823 USD. Going
          "long EUR/USD" means buying EUR with USD. For Sulla's universe, six
          of the seven pairs have USD as either base or quote — see <a href="#universe"
          className="underline" style={{ color: BLUE }}>§2</a>.
        </Term>
        <Term term="Bid / Ask / Spread">
          The bid is the price you can sell at, the ask is the price you can
          buy at. The spread (ask − bid) is the broker's take. Oanda spreads
          on EUR/USD typically run 0.6–1.5 pips during liquid hours; wider
          in the Asia session or during news.
        </Term>
        <Term term="Long-only spot">
          Buying the actual base currency (paying in the quote currency), as
          opposed to derivatives (futures, forwards, options). Sulla is
          spot-only and long-only — no leverage, no shorts, no derivatives.
        </Term>
        <Term term="USD-quote vs USD-base pair">
          USD-quote pairs (EUR/USD, GBP/USD, AUD/USD, NZD/USD): USD is the
          <em>quote</em>, so a position's notional is denominated in USD
          directly. USD-base pairs (USD/JPY, USD/CHF, USD/CAD): USD is the
          <em>base</em>, so position size is in USD and pip value needs to
          be converted back through the current rate. Sulla's
          <Code>fx_math.pip_value_usd()</Code> handles both cases.
        </Term>
        <Term term="Weekend gap">
          The FX market is closed Friday 17:00 ET → Sunday 17:00 ET. News
          during the closure can produce a Sunday-open gap that bypasses
          your Friday stop. Equities have the same phenomenon but worse
          because of earnings; FX gaps are usually smaller but not negligible.
        </Term>
      </TermGroup>

      <TermGroup title="Technical indicators" color={CYAN}>
        <Term term="Average Directional Index" abbr="ADX (14)">
          A 0–100 gauge of <em>trend strength</em> (not direction). Above
          25–30 means a real trend is in place; below means the market is
          ranging or chopping. Sulla uses ADX as the regime gate to route
          signals to the right paradigm.
        </Term>
        <Term term="Average True Range" abbr="ATR (14)">
          The average size of a candle's high-to-low range over the lookback
          window — a measure of recent volatility in price units. All Sulla
          stops and trailing distances are set as multiples of ATR so they
          scale automatically with the pair's volatility.
        </Term>
        <Term term="Relative Strength Index" abbr="RSI (14)">
          0–100 momentum oscillator. Above 70 = overbought, below 30 = oversold
          (rough convention). Each paradigm has its own RSI entry threshold
          tuned to its thesis.
        </Term>
        <Term term="Exponential Moving Average" abbr="EMA (9, 21)">
          A weighted moving average that prioritizes recent prices. EMA9 vs
          EMA21 crossover is the "trend = BULL/BEAR" check — fast above slow
          = bullish.
        </Term>
        <Term term="Bollinger Bands" abbr="BB (20, 2)">
          A 20-period moving average with bands at ±2 standard deviations.
          Mean Reversion buys when price hits the lower band; Volatility
          Breakout buys when price pierces the upper band coming out of a
          squeeze.
        </Term>
        <Term term="Bollinger Band Width" abbr="BBW">
          (Upper − Lower) ÷ Middle. A measure of how compressed the bands
          are. Below the threshold (default 0.08) means "squeeze" — coiled-
          spring conditions Volatility Breakout looks for.
        </Term>
      </TermGroup>

      <TermGroup title="Macro events" color={AMBER}>
        <Term term="Non-Farm Payrolls" abbr="NFP">
          Monthly US jobs report, released first Friday of each month at
          08:30 ET. The single biggest USD mover on the calendar. Routinely
          produces 80–150 pip moves on EUR/USD in the first 60 seconds.
          Sulla blocks all USD-leg entries 60 min before / 120 min after.
        </Term>
        <Term term="FOMC meeting" abbr="Federal Open Market Committee">
          Eight times a year. Rate decision at 14:00 ET, Powell press
          conference 14:30 ET. The press conference often moves more than
          the rate decision itself (forward-guidance Q&amp;A). FOMC days
          should be considered USD-trade-free days regardless of paradigm
          signal quality.
        </Term>
        <Term term="CPI" abbr="Consumer Price Index">
          Monthly inflation print, 08:30 ET. In post-2021 markets, CPI has
          eclipsed NFP as the most-watched USD release because it directly
          drives Fed rate expectations.
        </Term>
        <Term term="ECB rate decision">
          Eight times a year, 08:15 ET (announcement) + 08:45 ET (Lagarde
          press conference). Moves EUR violently, often more in the press
          conference than the announcement itself.
        </Term>
        <Term term="BoJ rate decision">
          Roughly 8×/year. BoJ has been at near-zero rates for decades so
          most decisions are "no change"; the rare pivots (2022, 2024) move
          USD/JPY hundreds of pips overnight.
        </Term>
        <Term term="ForexFactory feed">
          The hand-curated weekly macro-events calendar at
          <Code>nfs.faireconomy.media/ff_calendar_thisweek.json</Code>.
          Free, no auth, ~8 years stable. The data source for Sulla's
          macro-blackout layer.
        </Term>
      </TermGroup>

      <TermGroup title="System concepts" color={GREEN}>
        <Term term="Paradigm">
          A self-contained trading thesis with its own entry conditions, exit
          logic, and risk profile. Sulla runs four: Trend Following, Mean
          Reversion, Volatility Breakout, Liquidity Sweep.
          See <a href="#paradigms" className="underline" style={{ color: BLUE }}>§3</a>.
        </Term>
        <Term term="Regime / regime gate">
          The market's macro state (TRENDING vs RANGING) computed from ADX.
          The regime gate routes signals to the appropriate paradigms — TF
          and VB to TRENDING, MR and LS to RANGING.
        </Term>
        <Term term="Consensus">
          The 4-layer agreement system that must clear before any trade fires:
          paradigm signal + supporting indicators + AI verdict + score gate.
          See <a href="#consensus" className="underline" style={{ color: BLUE }}>§4</a>.
        </Term>
        <Term term="Multi-timeframe filter" abbr="MTF">
          A higher-timeframe (daily) sanity check on a 1h entry signal.
          Blocks Trend Following and Volatility Breakout when the daily
          trend is BEAR; blocks all longs when the daily EMA21 is sloping
          down sharply.
        </Term>
        <Term term="Watchlist / universe">
          The set of pairs Sulla is currently scanning. Default: 7 majors.
          Defined as <Code>strategy.active_symbols</Code> in
          <Code>Config.yaml</Code> and hot-reloaded every cycle.
        </Term>
        <Term term="Self-tuning engine">
          Background process that measures profit factor per (pair × paradigm)
          and proposes bounded parameter adjustments.
          See <a href="#tuning" className="underline" style={{ color: BLUE }}>§6</a>.
        </Term>
        <Term term="Tiered drawdown">
          The 3-stage drawdown response (alert / derisk / halt) at −8% /
          −15% / −25%. Replaces single-threshold halts with a more
          graduated response.
        </Term>
        <Term term="Correlation-aware sizing">
          Optional risk feature that reduces position size when other
          correlated positions are already open. Sulla's buckets group pairs
          by USD-leg direction (USD_short: EUR/USD, GBP/USD, etc.;
          USD_long: USD/JPY, USD/CHF, etc.) — three USD_short longs are
          functionally one big short-USD bet, not three independent bets.
        </Term>
        <Term term="Macro blackout">
          Entry-side block triggered by upcoming high-impact macro events.
          Per-currency: an NFP print blocks all 6 USD-leg pairs;
          a BoJ decision blocks only USD/JPY. See <a href="#blackout"
          className="underline" style={{ color: BLUE }}>§8</a>.
        </Term>
      </TermGroup>

      <TermGroup title="Operational states" color={GREEN}>
        <Term term="Shadow mode">
          Paper-trading mode. Every signal that <em>would</em> have fired a
          real trade is recorded to the database with full P&amp;L accounting
          against a synthetic $10,000 ledger, but no Oanda orders are placed.
          Currently active. Flipped via <Code>oanda.shadow_mode</Code> in
          <Code>Config.yaml</Code>.
        </Term>
        <Term term="Live mode (planned)">
          Real Oanda practice-account orders. The opposite of shadow mode.
          Sulla is not in this state yet — the deployment gates need to
          clear first. <Code>execution.execute_buy_with_stop</Code> currently
          raises NotImplementedError; Phase 6 fills in the Oanda order
          submission against this same interface.
        </Term>
        <Term term="Orphan position">
          A position recorded in <Code>open_positions</Code> for a pair
          that's not currently in the active scan loop (e.g. dropped from
          the watchlist mid-trade). The shadow exit engine fetches indicators
          on-demand to manage these through to natural close.
        </Term>
        <Term term="Kill switch">
          Two-step emergency liquidation. <Code>/kill</Code> arms it (60-second
          window), <Code>/confirm_kill</Code> closes ALL positions at market.
          Two steps on purpose so a fat-finger can't dump the book.
        </Term>
        <Term term="Restart flag">
          <Code>/app/data/.restart_engine</Code> — touched by either the web
          Config page's Restart button or the <Code>/restart</Code> Telegram
          command. The engine watches it at the top of every loop, deletes
          it, and exits the process; compose's
          <Code>restart: unless-stopped</Code> spawns a fresh container with
          the latest Config.yaml.
        </Term>
      </TermGroup>

      <TermGroup title="Praetor / stack" color={CYAN}>
        <Term term="Praetor">
          The platform — the React + FastAPI + Python stack that hosts Anton
          (TradFi), Tiberius (crypto), and Sulla (FX). What gets distributed.
        </Term>
        <Term term="Sulla">
          The FX trading instance. Runs on Oanda v20 REST. This system.
        </Term>
        <Term term="Anton">
          The TradFi sibling. Long-only US equities on Alpaca paper. Session-
          bound (9:30 AM–4:00 PM ET) with earnings blackouts. Separate repo
          at <Code>blisske/Praetor-Anton</Code>.
        </Term>
        <Term term="Tiberius">
          The crypto sibling. Long-only spot on Kraken via CCXT. 24/7 with
          no calendar blackouts. Separate repo at
          <Code>blisske/Praetor</Code>.
        </Term>
        <Term term="Oanda">
          The FX broker Sulla trades through. Currently using the practice
          account (free, unlimited). API access via direct v20 REST calls
          (no SDK dependency).
        </Term>
        <Term term="Gemma">
          Gemma 4 26B (a4b-it). The local LLM that provides the AI verdict
          layer of consensus. Runs on the Battlemage machine via LM Studio.
        </Term>
        <Term term="Battlemage">
          The host machine — Windows 11 + WSL2 + Docker Desktop. LAN IP
          192.168.0.135. Runs the Praetor swarm containers (Anton, Tiberius,
          Sulla) and LM Studio side-by-side. Intel Arc Pro B70 GPU.
        </Term>
        <Term term="Brave Search">
          The news/sentiment data source the AI brain queries when forming
          its verdict. FX-tuned query: <Code>"{'{base} {quote} forex central bank news'}"</Code>
          to bias toward macro headlines that actually move FX rather than
          corporate news of similarly-named tickers.
        </Term>
      </TermGroup>

    </div>
  </Card>
)

// ─── TOC sidebar ────────────────────────────────────────────────────────────
const TOC_ITEMS = [
  { id: 'overview',  label: '1. What Sulla does',         icon: Compass },
  { id: 'universe',  label: '2. The seven majors',        icon: Globe2 },
  { id: 'paradigms', label: '3. The four paradigms',      icon: Layers },
  { id: 'consensus', label: '4. The 2+1+1 consensus',     icon: Filter },
  { id: 'risk',      label: '5. Risk management',         icon: Shield },
  { id: 'tuning',    label: '6. The self-tuning engine',  icon: FlaskConical },
  { id: 'dashboard', label: '7. Reading the dashboard',   icon: LayoutDashboard },
  { id: 'blackout',  label: '8. Macro-event blackout',    icon: CalendarClock },
  { id: 'telegram',  label: '9. Telegram commands',       icon: MessageSquare },
  { id: 'playbook',  label: '10. Playbook',               icon: ListChecks },
  { id: 'glossary',  label: 'Appendix · Glossary',        icon: Library },
]

// ─── Page ───────────────────────────────────────────────────────────────────
export default function Guide() {
  return (
    <div className="p-6">
      <div className="flex items-center gap-2 mb-1">
        <BookOpen size={20} style={{ color: BLUE }} />
        <h1 className="text-xl font-bold" style={{ color: 'var(--text-primary)' }}>Sulla Guide</h1>
      </div>
      <p className="text-sm mb-6" style={{ color: 'var(--text-muted)' }}>
        How the FX system works, what each number means, and what to do when
        things happen. Written for the operator, not the developer.
      </p>

      <div className="grid grid-cols-12 gap-6">
        {/* Sticky TOC */}
        <aside className="hidden lg:block col-span-3">
          <div className="sticky top-6 rounded-xl p-4" style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)' }}>
            <div className="text-xs uppercase tracking-wider mb-3" style={{ color: 'var(--text-muted)' }}>Contents</div>
            <nav className="space-y-1">
              {TOC_ITEMS.map(({ id, label, icon: Icon }) => (
                <a key={id} href={`#${id}`}
                   className="flex items-center gap-2 px-2 py-1.5 rounded text-xs transition-colors"
                   style={{ color: 'var(--text-sub)' }}>
                  <Icon size={13} className="opacity-70" />
                  <span>{label}</span>
                </a>
              ))}
            </nav>
          </div>
        </aside>

        {/* Main content */}
        <div className="col-span-12 lg:col-span-9 space-y-6">
          <Section1 />
          <Section2 />
          <Section3 />
          <Section4 />
          <Section5 />
          <Section6 />
          <Section7 />
          <Section8 />
          <Section9 />
          <Section10 />
          <Section11 />
        </div>
      </div>
    </div>
  )
}
