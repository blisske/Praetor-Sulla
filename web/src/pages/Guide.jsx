import { BookOpen, Compass, Layers, Filter, Shield, FlaskConical, LayoutDashboard, MessageSquare, ListChecks, Library, Clock } from 'lucide-react'

const BLUE   = '#3B82F6'   // Sulla brand accent (sections, primary, Trend Following)
const GREEN  = '#34d399'   // semantic — Mean Reversion paradigm
const AMBER  = '#fbbf24'   // semantic — Liquidity Sweep paradigm, drawdown Alert
const RED    = '#f87171'   // semantic — drawdown Halt
const CYAN   = '#06B6D4'   // Volatility Breakout paradigm (was BLUE before brand rename)

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
      <strong style={{ color: 'var(--text-primary)' }}>Sulla is an autonomous, long-only US-equity trader</strong> that
      runs on Alpaca paper across a curated set of large-cap US equities. The active watchlist
      lives in <Code>Config.yaml</Code> and is hot-reloaded each cycle — currently around a dozen
      names spanning Technology, Energy, Financials, and Healthcare, with room reserved for
      Consumer and Industrials expansion. It scans every cycle during market
      hours, makes its own entry decisions, places its own protective stops, and exits on either
      a take-profit target, a trailing ATR stop that ratchets up but never down, or the
      end-of-day force-exit at 3:50&nbsp;PM&nbsp;ET.
    </p>
    <p>
      The goal is <strong style={{ color: 'var(--text-primary)' }}>consistent, risk-adjusted growth that
      compounds over years</strong> on a serious-but-not-casino posture. Long-only spot equities,
      no leverage, no shorts, no options tail risk. Every trade carries automatic risk controls;
      capital preservation isn't the primary mandate but tail-risk discipline still is.
      See <a href="#risk" className="underline" style={{color: BLUE}}>§4 Risk management</a>.
    </p>
    <p>
      Right now Sulla is in <Tag>SHADOW MODE</Tag> — every decision the live engine would have
      made is recorded as a "paper" trade in the database, with full P&amp;L accounting against a
      synthetic $10,000 ledger, and no Alpaca orders are placed. The shadow log is the
      validation gate before the flip to live (which itself still runs against Alpaca's
      <em> paper</em> account — there is no real-money mode in this incarnation).
    </p>
    <p>
      Sulla is the TradFi sister of <strong style={{ color: 'var(--text-primary)' }}>Tiberius</strong>,
      the crypto trader running the same Praetor stack on Kraken. The two share design DNA but
      diverge wherever the asset class forces it: session hours instead of 24/7, shares instead
      of USD notional, earnings blackouts, EOD force-exit, and a cash account (no PDT).
    </p>
    <p>
      Four pillars carry the system:
    </p>
    <ul className="space-y-1 ml-4 list-disc">
      <li><strong style={{ color: 'var(--text-primary)' }}>Multi-paradigm signal engine</strong> — four trading paradigms, each
        with a distinct thesis, route based on the current market regime.</li>
      <li><strong style={{ color: 'var(--text-primary)' }}>2+1+1 consensus</strong> — no single signal fires a trade. A primary
        paradigm + supporting indicators + an AI veto must all agree.</li>
      <li><strong style={{ color: 'var(--text-primary)' }}>Self-tuning</strong> — the engine measures its own profit factor per
        paradigm per symbol and proposes bounded parameter adjustments.</li>
      <li><strong style={{ color: 'var(--text-primary)' }}>Session-aware</strong> — entries cut off at 3:30 PM ET, all positions
        force-exit at 3:50 PM ET, weekends and holidays idle, earnings blackouts skip affected names.</li>
    </ul>
  </Card>
)

// ─── Section 2 — Paradigms ──────────────────────────────────────────────────
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

const Section2 = () => (
  <Card id="paradigms" icon={Layers} title="The four trading paradigms"
        subtitle="Each paradigm has a different thesis and only activates when market conditions match.">
    <p>
      Before any paradigm runs, Sulla asks one question: <strong>is the market trending or ranging?</strong>
      It uses the ADX (Average Directional Index) — a momentum-strength gauge — to decide. The
      threshold is configurable per symbol (defaults to 30). Above it: trending. Below: ranging.
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
        thesis='"Buy the dip in a strong trend." Catches pullbacks during established momentum, riding the trail until the trend rolls over. The bread-and-butter paradigm for trending blue-chips like NVDA in a momentum tape.'
      />
      <ParadigmCard
        tag="B" name="Mean Reversion" color={GREEN}
        fires="Range-bound market + price crashes through Bollinger lower band + RSI deeply oversold"
        thesis='"Buy the floor of the range." Targets a return to the middle of the range; exits at the mean or upper band. Works best on stable, mean-reverting names like SPY or large utilities/staples.'
      />
      <ParadigmCard
        tag="C" name="Volatility Breakout" color={CYAN}
        fires="Bollinger Band Width compressed (squeeze) + price pierces upper band + strong RSI surge"
        thesis='"Catch the explosion out of a coiled spring." Fires in either regime — when volatility has been suppressed and is about to expand. Common around earnings drift and macro reveals (where Sulla is already gated by the blackout, by design).'
      />
      <ParadigmCard
        tag="D" name="Liquidity Sweep" color={AMBER}
        fires="Range-bound + ADX in true range zone (&lt;18) + wick pierces lower band but candle closes back inside + RSI exhausted"
        thesis='"Buy the algo-driven fakeout." Detects a sweep at the range floor that reverses immediately — the equities version of a stop-hunt. Most useful intraday on heavily-traded ETFs.'
      />
    </div>

    <p className="text-xs mt-2" style={{ color: 'var(--text-muted)' }}>
      Each paradigm has its own entry RSI threshold, configurable per symbol via <Code>symbol_overrides</Code>.
      The same paradigm logic ports cleanly from Tiberius (crypto) — the underlying math doesn't care
      whether it's looking at BTC bars or AAPL bars. What does change is the timeframe (Sulla uses
      30-min bars; Tiberius uses 1-hour) and session bounds. See the <a href="#tuning" className="underline" style={{color: BLUE}}>Self-tuning</a> section
      for how thresholds get refined over time.
    </p>
  </Card>
)

// ─── Section 3 — Consensus ──────────────────────────────────────────────────
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

const Section3 = () => (
  <Card id="consensus" icon={Filter} title="The 2+1+1 consensus"
        subtitle="Why no single signal can fire a trade.">
    <p>
      Sulla doesn't trade on a single indicator. Every potential entry has to clear <strong>four
      independent layers</strong>. If any layer disagrees strongly enough, the trade is aborted and
      logged with the reason.
    </p>

    <div className="rounded-lg overflow-hidden" style={{ background: 'var(--bg-elevated)' }}>
      <ConsensusRow n="1" label="Primary signal" color={BLUE}
        desc="One of the four paradigms fires. This is the trade thesis." points="+1" />
      <ConsensusRow n="2" label="Supporting signals" color={GREEN}
        desc="Three orthogonal checks: volume participation (≥80% of 20-bar avg), RSI direction (rising for entries), ADX conviction (strong + rising in TRENDING, weak + falling in RANGING). At least 2 of 3 must pass."
        points="+1 each" />
      <ConsensusRow n="3" label="AI verdict" color={CYAN}
        desc="Gemma 4 26B (running on Battlemage) reads the signal context plus recent news/sentiment from Brave. A BEARISH verdict aborts the trade outright."
        points="veto only" />
      <ConsensusRow n="4" label="Score gate" color={AMBER}
        desc="Total score (1 paradigm + supporting signals + 1 if AI not BEARISH) must reach min_consensus (default 3)."
        points="≥ 3 / 4" />
    </div>

    <div className="rounded-lg p-4 mt-4" style={{ background: 'var(--bg-base)', border: '1px solid var(--border)' }}>
      <div className="text-xs uppercase tracking-wider mb-2" style={{ color: 'var(--text-muted)' }}>Why this matters</div>
      <p className="text-xs">
        Equity markets fire a lot of false signals — every macro headline, every earnings drift, every
        sector rotation throws candles around. The four-layer funnel is what keeps Sulla from chasing
        every twitch. A daily multi-timeframe filter sits on top of all of this too, blocking longs
        when the higher-timeframe trend is bearish.
      </p>
    </div>

    <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
      The "Anatomy of a Trade" panel on the Dashboard shows you the actual chain for the most recent
      executed entry — all 5 layers, pass/fail, color-coded. That's the audit trail.
    </p>
  </Card>
)

// ─── Section 4 — Risk ───────────────────────────────────────────────────────
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

const Section4 = () => (
  <Card id="risk" icon={Shield} title="Risk management"
        subtitle="Where Sulla will and won't take pain.">
    <p>
      Sulla is growth-focused but disciplined. Three knobs cover the bulk of position-level risk,
      and a tiered drawdown response covers account-level risk. A separate daily-loss circuit
      breaker covers the "everything goes wrong at once" case intraday.
    </p>

    <div className="grid grid-cols-1 md:grid-cols-3 gap-3 my-3">
      <div className="rounded-lg p-3" style={{ background: 'var(--bg-elevated)' }}>
        <div className="text-xs" style={{ color: 'var(--text-muted)' }}>Per-trade risk</div>
        <div className="text-base font-mono" style={{ color: 'var(--text-primary)' }}>5% paper · 2% live</div>
        <div className="text-xs mt-1" style={{ color: 'var(--text-muted)' }}>Position shares = (equity × risk%) ÷ stop distance, floored to whole shares</div>
      </div>
      <div className="rounded-lg p-3" style={{ background: 'var(--bg-elevated)' }}>
        <div className="text-xs" style={{ color: 'var(--text-muted)' }}>Position cap</div>
        <div className="text-base font-mono" style={{ color: 'var(--text-primary)' }}>12% of equity</div>
        <div className="text-xs mt-1" style={{ color: 'var(--text-muted)' }}>Hard ceiling per single position. Lifted from 5% post-pivot once defensive features were validated.</div>
      </div>
      <div className="rounded-lg p-3" style={{ background: 'var(--bg-elevated)' }}>
        <div className="text-xs" style={{ color: 'var(--text-muted)' }}>Max open trades</div>
        <div className="text-base font-mono" style={{ color: 'var(--text-primary)' }}>5</div>
        <div className="text-xs mt-1" style={{ color: 'var(--text-muted)' }}>Tied to correlation-aware sizing if enabled</div>
      </div>
    </div>

    <p>
      Stops are placed automatically immediately after every fill at <Code>entry − ATR×2</Code>. From
      there a trailing ATR ratchet only moves stops <em>up</em>, never down — so a position that
      runs in our favor locks in progress without giving it back. During Power Hour (3:00–4:00 PM ET)
      stops are widened by an <Code>atr_buffer</Code> to handle the elevated chop near the bell.
    </p>

    <div className="rounded-lg p-4 my-4" style={{ background: 'var(--bg-base)', border: '1px solid var(--border)' }}>
      <div className="text-xs uppercase tracking-wider mb-3" style={{ color: 'var(--text-muted)' }}>Tiered drawdown response</div>
      <Tier pct="−8%"  label="Alert"   action="Telegram notification, no automatic action. Heads-up."   color={AMBER} />
      <Tier pct="−15%" label="Derisk"  action="Risk-per-trade and position cap × 0.5. Recovers when DD &lt; 10%." color={BLUE} />
      <Tier pct="−25%" label="Halt"    action="Trading paused. Manual /resume required (re-checks DD first)." color={RED} />
    </div>

    <div className="rounded-lg p-4 my-4" style={{ background: 'var(--bg-base)', border: '1px solid var(--border)' }}>
      <div className="text-xs uppercase tracking-wider mb-2" style={{ color: 'var(--text-muted)' }}>Daily session loss limit</div>
      <p className="text-xs">
        Independent of the long-term drawdown ladder, Sulla halts <em>new entries</em> for the rest
        of the session if intraday loss from session-open equity hits <Code>−3%</Code>. Existing
        positions continue to be managed by the exit engine; the limit only blocks fresh buys until
        the next session opens.
      </p>
    </div>

    <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
      Drawdown is computed against realized equity (closed P&amp;L only), not mark-to-market — so a
      single open position swinging unrealized cannot trigger a false halt. Peak equity is
      persisted to the database so the calculation survives restarts.
    </p>
  </Card>
)

// ─── Section 5 — Self-tuning ────────────────────────────────────────────────
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

const Section5 = () => (
  <Card id="tuning" icon={FlaskConical} title="The self-tuning engine"
        subtitle="What it can and can't optimize, and why patience is required.">
    <div className="grid grid-cols-1 md:grid-cols-2 gap-3 my-3">
      <div className="rounded-lg p-4" style={{ background: GREEN + '11', border: `1px solid ${GREEN}44` }}>
        <div className="text-xs uppercase tracking-wider mb-2" style={{ color: GREEN }}>Tuner CAN adjust</div>
        <ul className="text-xs space-y-1 ml-4 list-disc" style={{ color: 'var(--text-sub)' }}>
          <li>RSI entry thresholds (per symbol per paradigm, range 20–65)</li>
          <li>BBW threshold for Volatility Breakout (0.03–0.20)</li>
          <li>Initial stop multiplier (1.0–3.5 ATR)</li>
          <li>Trailing stop multiplier (1.5–4.0 ATR)</li>
          <li>ADX trend threshold per symbol (18–40)</li>
        </ul>
      </div>
      <div className="rounded-lg p-4" style={{ background: RED + '11', border: `1px solid ${RED}44` }}>
        <div className="text-xs uppercase tracking-wider mb-2" style={{ color: RED }}>Tuner CANNOT change</div>
        <ul className="text-xs space-y-1 ml-4 list-disc" style={{ color: 'var(--text-sub)' }}>
          <li>Enable/disable a paradigm</li>
          <li>The daily multi-timeframe filter</li>
          <li>Consensus settings (min_consensus_score, volume threshold)</li>
          <li>Risk per trade, position cap, max open trades</li>
          <li>The active watchlist (add/drop symbols)</li>
          <li>Session windows, EOD time, earnings blackout days</li>
        </ul>
      </div>
    </div>

    <div className="rounded-lg p-4 my-4" style={{ background: 'var(--bg-base)', border: '1px solid var(--border)' }}>
      <div className="text-xs uppercase tracking-wider mb-4" style={{ color: 'var(--text-muted)' }}>Tuning lifecycle</div>
      <div className="space-y-3">
        <TuningStep n="1" title="Trigger gate"
          body="Wait for 10 closed shadow trades on this exact (symbol, paradigm) pair. Below 10, no proposal." />
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
      Sulla trades during US session hours only (~6.5 hours/day, ~5 days/week) so its raw clock
      is roughly a third of Tiberius's 24/7 footprint. With one tuning slot per
      <Code>(symbol, paradigm)</Code> pair and a per-slot trade pace measured in weeks, a complete
      optimization sweep across the watchlist is a <strong>multi-month process</strong>. Pre-tuning the obviously-broken settings
      manually still matters — the engine is for refinement, not bootstrap.
    </p>
  </Card>
)

// ─── Section 6 — Reading the dashboard ──────────────────────────────────────
const Section6 = () => (
  <Card id="dashboard" icon={LayoutDashboard} title="Reading the dashboard"
        subtitle="What each number is actually telling you.">
    <div className="space-y-4">
      <div>
        <div className="font-semibold mb-1" style={{ color: 'var(--text-primary)' }}>Equity Card</div>
        <p>Top-left tile. Currently shows <strong>realized shadow equity only</strong> —
          starting capital ($10,000) plus the sum of closed-trade P&amp;L. It does <em>not</em>
          include unrealized swings on open positions. The card and the equity curve are anchored
          together via the <Code>shadow_equity</Code> endpoint, so they always match.</p>
      </div>
      <div>
        <div className="font-semibold mb-1" style={{ color: 'var(--text-primary)' }}>Equity Curve</div>
        <p>The chart traces realized P&amp;L through time, with one point per closed shadow sell
          plus a "Now" anchor at the current equity card value. Drops are stop-outs; jumps are
          take-profit closes; flats are overnight gaps and weekends with no exits. A daily
          step-down at 3:50&nbsp;PM&nbsp;ET is the EOD force-exit harvesting whatever was open.</p>
      </div>
      <div>
        <div className="font-semibold mb-1" style={{ color: 'var(--text-primary)' }}>Open Positions Card</div>
        <p>Each row shows entry price, current price (mark-to-market), unrealized P&amp;L in $ and
          %, days held, current trailing stop with stop-distance %. <strong>Stop distance &lt; 1%
          </strong> means the position is effectively about to take profit (or stop out) on the next
          tick — useful triage at a glance.</p>
      </div>
      <div>
        <div className="font-semibold mb-1" style={{ color: 'var(--text-primary)' }}>Anatomy of a Trade panel</div>
        <p>Below the equity curve. Reconstructs the consensus chain for the most recent buy: 5
          numbered rows (paradigm, volume, RSI, ADX, AI verdict), each with a pass/warn/fail icon,
          plus the final score and verdict. This is your audit trail when reviewing what fired and
          why.</p>
      </div>
      <div>
        <div className="font-semibold mb-1" style={{ color: 'var(--text-primary)' }}>Market page</div>
        <p>Per-symbol indicators with a multi-select chart toggle (Price / RSI / ADX / Volume).
          The card grid at the top shows current regime, BB position, RSI, and volume vs 20-bar
          average. Switch timeframe with the selector for the higher-timeframe view.</p>
      </div>
      <div>
        <div className="font-semibold mb-1" style={{ color: 'var(--text-primary)' }}>Tuning page</div>
        <p>Two tables. <em>Validation Queue</em> shows proposals currently in SHADOW_PENDING —
          which parameter, old value, new value, status. <em>Audit Log</em> shows every promotion
          and rejection with full before/after, the metric delta, and the validation window count.</p>
      </div>
      <div>
        <div className="font-semibold mb-1" style={{ color: 'var(--text-primary)' }}>Session banner</div>
        <p>If the dashboard shows <Tag color={AMBER}>Pre-market</Tag>, <Tag color={AMBER}>After-hours</Tag>,
          <Tag color={RED}>Closed</Tag>, or <Tag color={RED}>Holiday</Tag>, that's not a bug — it's why
          you're seeing no fresh signals. Sulla idles outside the 9:30 AM–4:00 PM&nbsp;ET window.</p>
      </div>
    </div>
  </Card>
)

// ─── Section 7 — Session lifecycle (Sulla-specific) ─────────────────────────
const SessionRow = ({ time, what }) => (
  <tr className="border-b last:border-b-0" style={{ borderColor: 'var(--border)' }}>
    <td className="py-2 pr-4 font-mono text-xs" style={{ color: BLUE }}>{time}</td>
    <td className="py-2 text-xs" style={{ color: 'var(--text-sub)' }}>{what}</td>
  </tr>
)

const Section7 = () => (
  <Card id="session" icon={Clock} title="Session lifecycle"
        subtitle="What Sulla does at each phase of the trading day. (All times in America/New_York.)">
    <p>
      Unlike Tiberius (crypto, 24/7), Sulla lives inside the US-equity session and behaves
      differently across the day. The autonomous loop reads the wall clock every cycle to decide
      which path to run.
    </p>
    <table className="w-full">
      <tbody>
        <SessionRow time="Pre-market" what="No new entries, no exits. Loop idles, dashboard shows session status. Earnings cache refreshed daily." />
        <SessionRow time="9:30 AM" what="Bell. Scan resumes, paradigms eligible to fire, exit engine active on open positions." />
        <SessionRow time="9:30 AM – 3:30 PM" what="Normal trading. New entries allowed if consensus clears, AI verdict isn't BEARISH, and no daily-loss halt is active." />
        <SessionRow time="3:00 PM – 4:00 PM" what="Power Hour. Stops widen by atr_buffer to handle elevated chop. Entries still permitted until 3:30." />
        <SessionRow time="3:30 PM" what="Entry cutoff. No new buys for the rest of the session. Existing positions still get exit-engine treatment." />
        <SessionRow time="3:50 PM" what="EOD force-exit. ALL open positions close at market, regardless of P&L. Avoids overnight gap risk on the next morning's open." />
        <SessionRow time="4:00 PM – next open" what="Closed. Loop idles. Some maintenance tasks (heartbeat, tuner promotions) still run." />
        <SessionRow time="Weekends / holidays" what="Loop idles. Engine stays healthy for the next session open." />
      </tbody>
    </table>
    <div className="rounded-lg p-4 mt-2" style={{ background: AMBER + '11', border: `1px solid ${AMBER}44` }}>
      <div className="text-xs uppercase tracking-wider mb-2" style={{ color: AMBER }}>Earnings blackout</div>
      <p className="text-xs">
        Each watchlist symbol is checked against yfinance daily. If earnings are within
        <Code>earnings_blackout_days</Code> (default 2) of today, the symbol is excluded from new
        entries. Any existing position in that symbol is force-exited 1&nbsp;business&nbsp;day before
        earnings — overnight gap risk on an earnings reveal is the single biggest stop-jumping risk
        in equities trading, and the cleanest way to avoid it is to not be in the trade.
      </p>
    </div>
  </Card>
)

// ─── Section 8 — Telegram ───────────────────────────────────────────────────
const TelegramRow = ({ cmd, what }) => (
  <tr className="border-b last:border-b-0" style={{ borderColor: 'var(--border)' }}>
    <td className="py-2 pr-4 font-mono text-xs" style={{ color: BLUE }}>{cmd}</td>
    <td className="py-2 text-xs" style={{ color: 'var(--text-sub)' }}>{what}</td>
  </tr>
)

const Section8 = () => (
  <Card id="telegram" icon={MessageSquare} title="Telegram commands"
        subtitle="The remote-control surface. All commands are auth-scoped to the configured user ID.">
    <table className="w-full">
      <tbody>
        <TelegramRow cmd="/indicators" what="Regime-aware technical readout for the full watchlist, grouped by sector." />
        <TelegramRow cmd="/report" what="Portfolio audit — current equity, open positions with mark-to-market, naked-stop alerts, earnings blackouts." />
        <TelegramRow cmd="/pnl" what="Shadow performance — per-symbol win rate, profit factor, average return, dollar P&L." />
        <TelegramRow cmd="/buy [SYMBOL] [USD]" what="Manual buy with auto stop-loss. Bypasses consensus (intentional override). Use SPY/AAPL etc., not /USD." />
        <TelegramRow cmd="/protect" what="Scans open positions for missing stops and places ATR×2 protection on each." />
        <TelegramRow cmd="/kill" what="Arms the emergency liquidation. Requires /confirm_kill within 60s." />
        <TelegramRow cmd="/confirm_kill" what="Closes ALL open positions at market. Two-step on purpose so a fat-finger can't dump the book." />
        <TelegramRow cmd="/resume" what="Resumes trading after a drawdown halt or daily-loss halt. Re-checks DD first." />
        <TelegramRow cmd="/restart" what="Queues a clean engine restart via the .restart_engine flag. Compose respawns the container with fresh Config.yaml." />
        <TelegramRow cmd="/help" what="Shows the full command reference inside the chat." />
        <TelegramRow cmd="[ticker text]" what='Plain text like "AAPL" or "SPY" → direct AI sentiment query for that symbol.' />
      </tbody>
    </table>
    <p className="text-xs mt-2" style={{ color: 'var(--text-muted)' }}>
      Typing <Code>/</Code> in Telegram surfaces all of these in the autocomplete menu — registered
      at engine startup via <Code>set_my_commands</Code>.
    </p>
  </Card>
)

// ─── Section 9 — Playbook ───────────────────────────────────────────────────
const Scenario = ({ when, then, color = BLUE }) => (
  <div className="rounded-lg p-4" style={{ background: 'var(--bg-elevated)', border: `1px solid ${color}33` }}>
    <div className="text-xs uppercase tracking-wider mb-2" style={{ color }}>When you see…</div>
    <div className="font-semibold text-sm mb-3" style={{ color: 'var(--text-primary)' }}>{when}</div>
    <div className="text-xs uppercase tracking-wider mb-1" style={{ color: 'var(--text-muted)' }}>Do this</div>
    <div className="text-xs leading-relaxed" style={{ color: 'var(--text-sub)' }}>{then}</div>
  </div>
)

const Section9 = () => (
  <Card id="playbook" icon={ListChecks} title="Playbook — what to do when…"
        subtitle="Common scenarios and the canonical response.">
    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
      <Scenario color={AMBER}
        when="A drawdown alert (−8%) fires on Telegram"
        then={<>Don't react automatically. Pull <Code>/report</Code> and <Code>/pnl</Code> to see whether the drawdown is concentrated in 1–2 positions or spread across the book. If concentrated, check whether stops are still appropriately placed. The system has not done anything yet — this is just a heads-up.</>}
      />
      <Scenario color={BLUE}
        when="The system is in Derisk mode (−15% DD)"
        then={<>New entries are sized at half the normal risk and half the position cap. This is automatic and self-recovering — when DD comes back inside −10%, the system returns to normal sizing. No action needed unless you want to override.</>}
      />
      <Scenario color={RED}
        when="A drawdown halt fires (−25%)"
        then={<>Trading is paused. Investigate first: which positions hit, what regime drove it, were stops respected, was there an earnings or macro event. When you're satisfied the system is healthy, send <Code>/resume</Code> on Telegram. The command re-checks the DD calculation before resuming — if you're still under threshold, it will refuse.</>}
      />
      <Scenario color={AMBER}
        when="Daily session loss limit hits (−3% from session-open)"
        then={<>New entries blocked for the rest of the session. Existing positions continue to be managed normally. Usually this fires on a bad-tape day; the limit auto-clears at the next session open. No action required.</>}
      />
      <Scenario color={GREEN}
        when="You want to manually buy a position"
        then={<>Telegram: <Code>/buy AAPL 500</Code> (symbol, dollars). Sulla takes the trade at market and immediately places an ATR×2 stop. The trade is recorded with strategy = "MANUAL OVERRIDE" so the tuner doesn't pick it up. Only works during market hours.</>}
      />
      <Scenario color={CYAN}
        when="It's 3:45 PM and you're still in 3 positions"
        then={<>Don't intervene. Sulla's EOD engine fires at 3:50 PM ET and closes everything at market — that's by design. You'll get an <Code>EOD Shadow Exit</Code> message with the day-tally (per-position P&L, W/L count, net dollars, equity at bell) right after.</>}
      />
      <Scenario color={AMBER}
        when="A symbol you watch is approaching earnings"
        then={<>Sulla blacks out entries 2 days before earnings (configurable). If you currently hold the position, it's force-exited 1 business day before — Sulla trades the chart, not the earnings reveal. The <Code>/report</Code> output lists active blackouts.</>}
      />
      <Scenario color={RED}
        when="The engine looks frozen or unresponsive"
        then={<>The dashboard healthcheck reads <Code>.engine_heartbeat</Code> mtime; stale = sick. Pull recent logs with <Code>docker compose logs sulla-engine --tail 100</Code> from <Code>~/swarm/</Code>. Once you've found the cause, the web Config page's Restart button (or <Code>/restart</Code> on Telegram) queues a clean respawn via the flag-file pattern.</>}
      />
      <Scenario color={BLUE}
        when="You want to add or drop a symbol from the watchlist"
        then={<>Edit <Code>active_symbols</Code> in <Code>Config.yaml</Code> via the Config page. Sulla hot-reloads the watchlist each cycle — no restart needed. To drop one with an open position: close the position first (manual sell or wait for stop), then remove from the list.</>}
      />
      <Scenario color={GREEN}
        when="The flip from Shadow to Live (Alpaca paper)"
        then={<>Verify all deployment gates from <Code>WORKING_STATE.md</Code> are green: 30+ closed shadow trades, at least one complete self-tuning cycle, no active drawdown halt, <Code>risk_per_trade_pct</Code> lowered to 2.0%. Edit <Code>Config.yaml</Code>: set <Code>shadow_mode: false</Code>. The Config page's Save button writes the YAML and the engine picks it up on the next cycle.</>}
      />
    </div>
  </Card>
)

// ─── Section 10 — Glossary appendix ─────────────────────────────────────────
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

const Section10 = () => (
  <Card id="glossary" icon={Library} title="Appendix: Glossary"
        subtitle="Quick definitions for terms used elsewhere in the Guide. Grouped by area; use browser Find (Ctrl/⌘-F) to jump to a specific term.">
    <div className="space-y-4">

      <TermGroup title="Technical indicators" color={CYAN}>
        <Term term="Average Directional Index" abbr="ADX (14)">
          A 0–100 gauge of <em>trend strength</em> (not direction). Above 25–30 means a real trend
          is in place; below means the market is ranging or chopping. Sulla uses ADX as the
          regime gate to route signals to the right paradigm.
        </Term>
        <Term term="Average True Range" abbr="ATR (14)">
          The average size of a candle's high-to-low range over the lookback window — a measure of
          recent volatility in absolute price units. All Sulla stops and trailing distances are
          set as multiples of ATR so they scale automatically with the symbol's volatility (tighter
          stops on SPY, wider stops on TSLA, etc.).
        </Term>
        <Term term="Relative Strength Index" abbr="RSI (14)">
          0–100 momentum oscillator. Above 70 = overbought, below 30 = oversold (rough convention).
          Each paradigm has its own RSI entry threshold tuned to its thesis (e.g. Mean Reversion
          fires below 25, Trend Following fires below 55 on a dip).
        </Term>
        <Term term="Exponential Moving Average" abbr="EMA (9, 21)">
          A weighted moving average that prioritizes recent prices. EMA9 vs EMA21 crossover is the
          "trend = BULL/BEAR" check — fast above slow = bullish.
        </Term>
        <Term term="Bollinger Bands" abbr="BB (20, 2)">
          A 20-period moving average with bands at ±2 standard deviations. Mean Reversion buys when
          price hits the lower band; Volatility Breakout buys when price pierces the upper band
          coming out of a squeeze. The middle band is the take-profit target for range plays.
        </Term>
        <Term term="Bollinger Band Width" abbr="BBW">
          (Upper − Lower) ÷ Middle. A measure of how compressed the bands are. Below the threshold
          (default 0.10) means the market is in a "squeeze" — coiled spring conditions that
          Volatility Breakout looks for.
        </Term>
        <Term term="OHLCV">
          Open / High / Low / Close / Volume — the five values that define a single price candle.
          The raw data Sulla pulls from Alpaca every cycle.
        </Term>
      </TermGroup>

      <TermGroup title="Trade mechanics" color={GREEN}>
        <Term term="Spot equities">
          Buying the actual share (AAPL, MSFT, etc.), as opposed to derivatives (options, futures).
          Sulla is spot-only and long-only — no leverage, no shorts, no options.
        </Term>
        <Term term="Long-only">
          The system only takes positions that profit when price <em>rises</em>. Down moves either
          stop us out or are simply ignored.
        </Term>
        <Term term="Stop loss">
          A pre-placed sell order that triggers if price drops to a defined level — caps the loss
          on any trade. Sulla places one at <Code>entry − ATR×2</Code> immediately after every fill.
        </Term>
        <Term term="Trailing stop / ATR ratchet">
          A stop that moves <em>with</em> the position as it gains, but never moves backward. As
          price runs in our favor, the stop ratchets up to lock in profit. If price reverses, the
          stop holds where it last moved to.
        </Term>
        <Term term="Take profit">
          A pre-defined exit at a target price. For range paradigms (Mean Reversion, Liquidity
          Sweep), the target is the middle Bollinger band. Trend / Breakout paradigms have no fixed
          target — they ride the trail until stopped out.
        </Term>
        <Term term="Mark-to-market">
          Valuing a position at its <em>current</em> price rather than the price you paid. The
          Open Positions card shows mark-to-market unrealized P&amp;L in real time.
        </Term>
        <Term term="Realized vs unrealized P&L">
          <strong>Realized</strong> = P&amp;L from positions that have been closed. <strong>Unrealized</strong> =
          paper P&amp;L on positions still open. Sulla's drawdown halt math uses realized only —
          one open position swinging against us cannot trigger a false halt.
        </Term>
        <Term term="Slippage">
          The difference between the price you expected and the price you actually got. Always
          works against you. Most pronounced near the open and during Power Hour. Backtest results
          don't model slippage; live results will be slightly worse for that reason alone.
        </Term>
        <Term term="Pyramiding">
          Adding more legs to a winning position as it runs. Disabled by default in
          <Code>Config.yaml</Code>; enable-able after the first trend trade is observed at single-leg
          behavior. Only Trend Following and Volatility Breakout paradigms are eligible — Mean
          Reversion and Liquidity Sweep are deliberately excluded because adding to those paradigms
          means betting against their entry thesis.
        </Term>
        <Term term="R-multiple">
          P&amp;L expressed as a multiple of initial risk. A trade risking $100 and making $250 is
          a +2.5R trade. A useful normalization across positions of different size.
        </Term>
      </TermGroup>

      <TermGroup title="TradFi specifics" color={AMBER}>
        <Term term="Session hours">
          The 9:30 AM – 4:00 PM Eastern window when the NYSE / Nasdaq are open. Sulla only fires
          entries inside this window. Pre-market (4:00–9:30 AM) and after-hours (4:00–8:00 PM) are
          excluded for liquidity and spread reasons.
        </Term>
        <Term term="Pre-market / after-hours">
          The thin-volume trading windows around regular session hours. Real exchanges accept
          orders during these but Sulla skips them — the spreads are wide, the volume is thin, and
          gap risk is high.
        </Term>
        <Term term="Power Hour">
          3:00–4:00 PM ET. The final hour of the session, historically a period of elevated
          volatility and institutional flow. Sulla widens stops by <Code>atr_buffer</Code> during
          this window to handle the chop.
        </Term>
        <Term term="EOD force-exit" abbr="End of Day">
          At 3:50 PM ET, Sulla closes ALL open positions at market regardless of P&amp;L. The
          purpose: avoid overnight gap risk on the next morning's open. Triggers the
          <Code>EOD Shadow Exit</Code> Telegram message with a per-position day tally.
        </Term>
        <Term term="Entry cutoff">
          3:30 PM ET. No new entries after this point, even if a paradigm fires and consensus
          clears. The remaining 20 minutes belong to the exit engine, not the entry engine.
        </Term>
        <Term term="Earnings blackout">
          The 2-day window around a symbol's earnings report during which Sulla skips new entries.
          Open positions in the symbol are force-exited 1 business day before earnings. Earnings
          dates come from yfinance, cached daily.
        </Term>
        <Term term="Pattern Day Trader" abbr="PDT">
          A regulatory rule (FINRA) that restricts accounts under $25K from making more than 3 day
          trades in a 5-day rolling window. <strong>Sulla is on a cash account</strong>, which is
          exempt from PDT — there is no day-trade limit. Trade-off: cash settles T+1, so buying
          power doesn't replenish same-day. The system is designed around this constraint.
        </Term>
        <Term term="Cash account">
          Brokerage account funded with cash only (no margin). No PDT restriction, but trades
          settle T+1 — so buying power isn't restored until the next session. The reason Sulla
          isn't constrained by day-trade limits.
        </Term>
        <Term term="Daily session loss limit">
          Independent of long-term drawdown, Sulla halts <em>new entries</em> for the rest of the
          session if intraday loss from session-open equity hits −3%. Existing positions continue
          to be managed. Limit auto-clears at the next session open.
        </Term>
      </TermGroup>

      <TermGroup title="Performance metrics" color={BLUE}>
        <Term term="Profit Factor" abbr="PF">
          Gross winning P&amp;L ÷ gross losing P&amp;L. A PF of 1.0 = breakeven; ≥ 1.5 is healthy;
          ≥ 2.0 is excellent. The self-tuner's primary success metric.
        </Term>
        <Term term="Win rate">
          Percentage of trades closed at a profit. <em>Not</em> the same as PF — a 30% win rate
          with big winners and small losers can produce a great PF, while 70% with tiny winners
          and large losers can lose money.
        </Term>
        <Term term="Drawdown" abbr="DD">
          Percentage decline from peak equity. Tracked continuously; tiered alerts at −8% / −15% /
          −25% (alert / derisk / halt). Computed against peak realized equity stored in the
          database.
        </Term>
        <Term term="Max drawdown">
          The largest peak-to-trough drawdown over a given period. The single most-watched risk
          number for any trading system.
        </Term>
      </TermGroup>

      <TermGroup title="System concepts" color={AMBER}>
        <Term term="Paradigm">
          A self-contained trading thesis with its own entry conditions, exit logic, and risk
          profile. Sulla runs four: Trend Following, Mean Reversion, Volatility Breakout,
          Liquidity Sweep. See <a href="#paradigms" className="underline" style={{color: BLUE}}>section 2</a>.
        </Term>
        <Term term="Regime / regime gate">
          The market's macro state (TRENDING vs RANGING) computed from ADX. The regime gate routes
          signals to the appropriate paradigms — TF and VB to TRENDING, MR and LS to RANGING.
        </Term>
        <Term term="Consensus">
          The 4-layer agreement system that must clear before any trade fires: paradigm signal +
          supporting indicators + AI verdict + score gate. See <a href="#consensus" className="underline" style={{color: BLUE}}>section 3</a>.
        </Term>
        <Term term="Multi-timeframe filter" abbr="MTF">
          A higher-timeframe (daily) sanity check on the 30-min entry signal. Blocks Trend
          Following and Volatility Breakout when the daily trend is BEAR; blocks all longs when
          the daily EMA21 is sloping down sharply (strong-downtrend filter).
        </Term>
        <Term term="Higher timeframe">
          The longer-period chart used for confirmation (daily in our case, vs 30-min for primary
          signals). The principle: don't fight the bigger trend.
        </Term>
        <Term term="Watchlist / universe">
          The set of symbols Sulla is currently scanning. Curated large-cap US equities defined as
          <Code>strategy.active_symbols</Code> in <Code>Config.yaml</Code> and hot-reloaded every
          cycle — no restart needed to add or drop a symbol. Current list spans Tech, Energy,
          Financials, and Healthcare with capacity reserved for Consumer and Industrials.
        </Term>
        <Term term="Self-tuning engine">
          Background process that measures profit factor per (symbol × paradigm) and proposes
          bounded parameter adjustments. See <a href="#tuning" className="underline" style={{color: BLUE}}>section 5</a>.
        </Term>
        <Term term="Validation gate">
          The 10-trade window the tuner uses to test a proposed parameter change before promoting
          it to live config. Non-bypassable — the system's check on its own self-modifications.
        </Term>
        <Term term="Promotion / rejection">
          Tuner outcomes. <strong>Promoted</strong> = validation passed (PF improved ≥ 5%), value
          written to <Code>Config.yaml</Code>. <strong>Rejected</strong> = validation failed,
          live config unchanged, snapshot retained for audit.
        </Term>
        <Term term="Param bounds">
          Hard-coded min/max for every tuner-adjustable parameter (defined in
          <Code>Config.yaml</Code> under <Code>tuning.param_bounds</Code>). The tuner literally
          cannot propose a value outside these. A safety rail.
        </Term>
        <Term term="Symbol overrides">
          Per-symbol parameter values that supersede the global defaults. Lets SPY use a tighter
          stop (1.8× ATR) while TSLA uses a wider one (3.5× ATR). Defined under
          <Code>strategy.symbol_overrides</Code> in <Code>Config.yaml</Code>.
        </Term>
        <Term term="Tiered drawdown">
          The 3-stage drawdown response (alert / derisk / halt) at −8% / −15% / −25%. Replaces
          the legacy single-threshold halt with a more graduated, less brittle response.
        </Term>
        <Term term="Correlation-aware sizing">
          Optional risk feature that reduces position size when other correlated positions are
          already open. Acknowledges that "5 large-cap tech longs" is functionally one big tech
          bet in five wrappers. Default curve floors at 0.40× when many correlated names are open.
        </Term>
        <Term term="Daily multi-timeframe filter">
          The daily-bar sanity check on the 30-min entry signal. Sulla-specific: Tiberius uses a
          4-hour MTF since crypto runs 24/7. For equities the daily bar is the right cadence to
          confirm "is this dip-buy actually happening in an uptrend or am I catching a falling
          knife."
        </Term>
      </TermGroup>

      <TermGroup title="Operational states" color={GREEN}>
        <Term term="Shadow mode">
          Paper-trading mode. Every signal that <em>would</em> have fired a real trade is recorded
          to the database with full P&amp;L accounting against a synthetic $10,000 ledger, but no
          Alpaca orders are placed. Currently active. Flipped via <Code>alpaca.shadow_mode</Code>
          in <Code>Config.yaml</Code>.
        </Term>
        <Term term="Live mode">
          Real Alpaca orders against the paper account. The opposite of shadow mode. Sulla is not
          in this state yet — the deployment gates (30+ closed shadow trades, one complete tuning
          cycle, risk_per_trade lowered to 2%, etc.) need to clear first.
        </Term>
        <Term term="Orphan position">
          A position recorded in <Code>open_positions</Code> for a symbol that's not currently in
          the active scan loop (e.g. removed from the watchlist while still open). The shadow exit
          engine fetches indicators on-demand to manage these through to natural close.
        </Term>
        <Term term="Naked stop">
          An open position with no active stop-loss in place. The <Code>/protect</Code> Telegram
          command scans for these and places ATR×2 stops on each. In shadow mode all stops live
          in the DB, not at Alpaca — naked-stop detection still applies.
        </Term>
        <Term term="Kill switch">
          Two-step emergency liquidation. <Code>/kill</Code> arms it (60-second window),
          <Code>/confirm_kill</Code> closes ALL positions at market. Two steps on purpose so a
          fat-finger can't dump the book.
        </Term>
        <Term term="Restart flag">
          <Code>/app/data/.restart_engine</Code> — touched by either the web Config page's Restart
          button or the <Code>/restart</Code> Telegram command. The engine watches it at the top
          of every loop, deletes it, and exits the process; compose's <Code>restart: unless-stopped</Code>
          spawns a fresh container with the latest Config.yaml.
        </Term>
      </TermGroup>

      <TermGroup title="Praetor / stack" color={CYAN}>
        <Term term="Praetor">
          The platform — the React + FastAPI + Python stack that hosts both Sulla (TradFi) and
          Tiberius (crypto). What gets distributed.
        </Term>
        <Term term="Sulla">
          The TradFi trading instance. Runs on Alpaca paper. This system.
        </Term>
        <Term term="Tiberius">
          The crypto sister instance. Runs on Kraken via CCXT. Separate repo
          (<Code>blisske/Praetor</Code>), same Praetor architecture, deployed alongside Sulla in the
          swarm stack at <Code>~/swarm/</Code>.
        </Term>
        <Term term="Alpaca">
          The brokerage Sulla trades through. Currently using the paper account (no real money).
          API access via <Code>alpaca-py</Code>.
        </Term>
        <Term term="Gemma">
          Gemma 4 26B (a4b-it). The local LLM that provides the AI verdict layer of consensus.
          Runs on the Battlemage machine via LM Studio.
        </Term>
        <Term term="Battlemage">
          The host machine — Windows 11 + WSL2 + Docker Desktop. LAN IP 192.168.0.135. Same
          machine that runs the Praetor swarm containers and LM Studio. Intel Arc Pro B70 GPU.
        </Term>
        <Term term="Brave Search">
          The news/sentiment data source the AI brain queries when forming its verdict. Recent
          equity-specific headlines for the symbol under consideration get folded into the
          BULLISH / NEUTRAL / BEARISH call.
        </Term>
      </TermGroup>

    </div>
  </Card>
)

// ─── TOC sidebar ────────────────────────────────────────────────────────────
const TOC_ITEMS = [
  { id: 'overview',  label: '1. What Sulla does',          icon: Compass },
  { id: 'paradigms', label: '2. The four paradigms',       icon: Layers },
  { id: 'consensus', label: '3. The 2+1+1 consensus',      icon: Filter },
  { id: 'risk',      label: '4. Risk management',          icon: Shield },
  { id: 'tuning',    label: '5. The self-tuning engine',   icon: FlaskConical },
  { id: 'dashboard', label: '6. Reading the dashboard',    icon: LayoutDashboard },
  { id: 'session',   label: '7. Session lifecycle',        icon: Clock },
  { id: 'telegram',  label: '8. Telegram commands',        icon: MessageSquare },
  { id: 'playbook',  label: '9. Playbook',                 icon: ListChecks },
  { id: 'glossary',  label: 'Appendix · Glossary',         icon: Library },
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
        How the system works, what each number means, and what to do when things happen. Written
        for the operator, not the developer.
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
        </div>
      </div>
    </div>
  )
}
