import os, sqlite3, yaml, math, logging, shutil
from pathlib import Path
from datetime import datetime, timedelta

BASE_DIR = Path(__file__).parent
# Container-friendly: env overrides for paths bind-mounted into a container.
DB_PATH  = Path(os.environ.get('SQLITE_PATH', BASE_DIR / 'sulla_data.db'))
CFG_PATH = Path(os.environ.get('CONFIG_PATH', BASE_DIR / 'Config.yaml'))

logger = logging.getLogger(__name__)

# Maps Config.yaml paradigm keys to the strategy string written to trades.action.
# Used by check_promotions to filter validation pool to the proposal's paradigm.
_STRAT_KEY_TO_NAME = {
    'trend_following':     'TREND FOLLOWING',
    'mean_reversion':      'MEAN REVERSION',
    'liquidity_sweep':     'LIQUIDITY SWEEP',
    'volatility_breakout': 'VOLATILITY BREAKOUT',
}

# ==============================================================================
# TUNER.PY — Self-Tuning Parameter Optimization Engine
#
# Architecture:
#   1. run_tuning_cycle()  — entry point called by main.py on trade-count trigger
#   2. _get_tuning_config() — reads tuning: section from Config.yaml
#   3. _compute_metrics()   — calculates profit_factor / win_rate per symbol+paradigm
#   4. _select_candidate()  — picks which parameter to nudge and in which direction
#   5. _within_bounds()     — enforces safety rails from Config.yaml param_bounds
#   6. _cooling_off_ok()    — enforces per-parameter cooldown period
#   7. _propose_change()    — writes candidate to DB via database.log_tuning_change()
#   8. check_promotions()   — called on every SHADOW SELL; promotes or rejects ready candidates
#   9. _apply_to_config()   — Phase 4: writes promoted value to Config.yaml (stubbed for now)
# ==============================================================================


def _get_tuning_config():
    """Load and return the tuning: section from Config.yaml."""
    with open(CFG_PATH, 'r') as f:
        cfg = yaml.safe_load(f)
    return cfg.get('tuning', {})


def _compute_metrics(symbol, strategy, trades):
    """
    Compute performance metrics from a list of closed shadow trade dicts.
    Each dict has: pnl_pct (float), symbol, strategy, timestamp.

    Returns dict with:
        profit_factor  — gross_profit / gross_loss (infinity if no losses)
        win_rate       — fraction of winning trades
        avg_pnl        — average P&L per trade
        trade_count    — number of trades analyzed
        gross_profit   — sum of positive P&L
        gross_loss     — absolute sum of negative P&L
    """
    if not trades:
        return None

    wins   = [t['pnl_pct'] for t in trades if t['pnl_pct'] > 0]
    losses = [t['pnl_pct'] for t in trades if t['pnl_pct'] <= 0]

    gross_profit = sum(wins)
    gross_loss   = abs(sum(losses))

    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float('inf')
    win_rate      = len(wins) / len(trades)
    avg_pnl       = sum(t['pnl_pct'] for t in trades) / len(trades)

    return {
        'symbol':        symbol,
        'strategy':      strategy,
        'profit_factor': profit_factor,
        'win_rate':      win_rate,
        'avg_pnl':       avg_pnl,
        'trade_count':   len(trades),
        'gross_profit':  gross_profit,
        'gross_loss':    gross_loss,
    }


def _cooling_off_ok(symbol, parameter, cooling_off_hours):
    """
    Check if enough time has passed since the last tuning change for this
    symbol+parameter combination. Prevents thrashing.

    Returns True if the parameter is eligible for re-tuning.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''
            SELECT timestamp FROM tuning_log
            WHERE symbol = ? AND parameter = ?
            ORDER BY timestamp DESC LIMIT 1
        ''', (symbol, parameter))
        row = c.fetchone()
        conn.close()

        if not row:
            return True  # Never been tuned

        last_tuned = datetime.strptime(row[0], '%Y-%m-%d %H:%M:%S')
        cooldown_end = last_tuned + timedelta(hours=cooling_off_hours)
        eligible = datetime.utcnow() >= cooldown_end

        if not eligible:
            remaining = (cooldown_end - datetime.utcnow()).seconds // 3600
            logger.info(f"[TUNER] {symbol}/{parameter} cooling off — {remaining}h remaining")
        return eligible

    except Exception as e:
        logger.error(f"[TUNER] Cooling-off check failed for {symbol}/{parameter}: {e}")
        return False


def _within_bounds(parameter, value, bounds_cfg):
    """
    Clamp a candidate value to the safety rails defined in Config.yaml param_bounds.
    Returns the clamped value.
    """
    param_key = parameter.split('.')[-1]  # e.g. 'paradigms.trend_following.rsi_entry' -> 'rsi_entry'
    bounds = bounds_cfg.get(param_key)
    if not bounds:
        logger.warning(f"[TUNER] No bounds defined for {parameter} — skipping")
        return None

    clamped = max(bounds['min'], min(bounds['max'], value))
    if clamped != value:
        logger.info(f"[TUNER] Clamped {parameter} candidate {value} -> {clamped} (bounds [{bounds['min']}, {bounds['max']}])")
    return clamped


def _get_current_param_value(symbol, parameter, full_cfg):
    """
    Extract the current live value for a given symbol+parameter from Config.yaml.
    parameter is a dot-path like 'paradigms.trend_following.rsi_entry'.

    Looks in symbol_overrides first, then falls back to global strategy config.
    """
    parts = parameter.split('.')

    # Try symbol override first — Sulla stores under strategy.symbol_overrides
    sym_override = full_cfg.get('strategy', {}).get('symbol_overrides', {}).get(symbol, {})
    val = sym_override
    for p in parts:
        if isinstance(val, dict):
            val = val.get(p)
        else:
            val = None
            break
    if val is not None:
        return float(val)

    # Fall back to global strategy config
    val = full_cfg.get('strategy', {})
    for p in parts:
        if isinstance(val, dict):
            val = val.get(p)
        else:
            val = None
            break
    return float(val) if val is not None else None


def _select_candidate(metrics, tuning_cfg, full_cfg):
    """
    Given a performance metrics dict, decide whether and how to adjust a parameter.

    Strategy:
    - If profit_factor is below 1.0 (losing money): aggressive nudge
    - If profit_factor is 1.0-1.5 (marginal): moderate nudge
    - If profit_factor >= 1.5 (healthy): no tuning needed

    For RSI entry thresholds:
    - Low win_rate + good avg_pnl on winners → RSI is too wide, tighten (raise threshold)
    - Good win_rate + low avg_pnl → RSI is too tight, widen (lower threshold)
    - Overall poor performance → tighten stops (reduce stop mult)

    Returns: (parameter_dotpath, current_value, candidate_value, direction_reason)
             or None if no tuning is warranted.
    """
    pf       = metrics['profit_factor']
    win_rate = metrics['win_rate']
    symbol   = metrics['symbol']
    strategy = metrics['strategy']
    bounds   = tuning_cfg.get('param_bounds', {})
    min_imp  = tuning_cfg.get('min_metric_improvement', 0.05)

    # Map paradigm name to config key + RSI entry direction.
    # 'rsi_dir' = sign of `rsi - threshold` that fires entry:
    #   -1 → entry when rsi < threshold (oversold buys: TF dip, MR, LS)
    #   +1 → entry when rsi > threshold (momentum buys: VB)
    # Tightening (rarer entry) means moving the threshold AWAY from the firing
    # zone. For -1 paradigms we LOWER the threshold; for +1 we RAISE it.
    strategy_meta = {
        'TREND FOLLOWING':     ('trend_following',     -1),
        'MEAN REVERSION':      ('mean_reversion',      -1),
        'LIQUIDITY SWEEP':     ('liquidity_sweep',     -1),
        'VOLATILITY BREAKOUT': ('volatility_breakout', +1),
    }
    meta = strategy_meta.get(strategy.upper())
    if not meta:
        logger.warning(f"[TUNER] Unknown strategy '{strategy}' — cannot select candidate")
        return None
    strat_key, rsi_dir = meta

    # No tuning needed if already performing well
    if pf >= 1.5:
        logger.info(f"[TUNER] {symbol}/{strategy} PF={pf:.2f} — healthy, no tuning needed")
        return None

    # Determine RSI parameter path
    rsi_param = f'paradigms.{strat_key}.rsi_entry'
    rsi_bounds = bounds.get('rsi_entry', {})
    max_step   = rsi_bounds.get('max_step', 5)

    current_rsi = _get_current_param_value(symbol, rsi_param, full_cfg)
    if current_rsi is None:
        logger.warning(f"[TUNER] Could not read current value for {rsi_param} on {symbol}")
        return None

    # Tightening step: move threshold AWAY from the entry-firing zone.
    # For -1 paradigms (rsi < threshold fires), tighter = lower threshold.
    # For +1 paradigms (rsi > threshold fires), tighter = higher threshold.
    tighten_delta = -max_step if rsi_dir == -1 else +max_step

    # Decide direction
    # Low win_rate: too many false entries → tighten RSI threshold
    # Very low PF with decent win_rate: exits are the issue → tune stop mult
    if win_rate < 0.40:
        candidate_rsi = current_rsi + tighten_delta
        reason = (f"win_rate={win_rate:.0%} < 0.40 — tightening "
                  f"{strat_key}.rsi_entry from {current_rsi} to {candidate_rsi}")
    elif win_rate >= 0.40 and pf < 1.0:
        # Winners too small, losers too big → tighten stop mult
        stop_param   = 'trailing_stop_mult'
        stop_bounds  = bounds.get('trailing_stop_mult', {})
        stop_step    = stop_bounds.get('max_step', 0.1)
        current_stop = _get_current_param_value(symbol, stop_param, full_cfg)
        if current_stop is None:
            return None
        candidate_stop = current_stop - stop_step  # tighter stop = smaller losses
        clamped = _within_bounds(stop_param, candidate_stop, bounds)
        if clamped is None or clamped == current_stop:
            return None
        reason = f"PF={pf:.2f} with ok win_rate — tightening trailing_stop_mult {current_stop} -> {clamped}"
        return (stop_param, current_stop, clamped, reason)
    else:
        # Moderate underperformance: nudge RSI threshold tighter
        candidate_rsi = current_rsi + tighten_delta
        reason = (f"PF={pf:.2f} marginal — tightening "
                  f"{strat_key}.rsi_entry from {current_rsi} to {candidate_rsi}")

    clamped_rsi = _within_bounds(rsi_param, candidate_rsi, bounds)
    if clamped_rsi is None or clamped_rsi == current_rsi:
        logger.info(f"[TUNER] {symbol}/{rsi_param} already at boundary or no change — skipping")
        return None

    return (rsi_param, current_rsi, clamped_rsi, reason)

def run_tuning_cycle(symbols):
    """
    Main entry point. Called by main.py when the trade-count trigger fires.
    Evaluates performance for each symbol+paradigm pair and proposes
    parameter changes where warranted.

    Args:
        symbols (list[str]): Active trading symbols e.g. ['BTC/USD', 'ETH/USD']

    Returns:
        int: Number of parameter change proposals submitted
    """
    import database

    tuning_cfg = _get_tuning_config()
    if not tuning_cfg.get('enabled', False):
        logger.info("[TUNER] Tuning disabled in Config.yaml — skipping")
        return 0

    with open(CFG_PATH, 'r') as f:
        full_cfg = yaml.safe_load(f)

    min_trades      = tuning_cfg.get('min_trades_to_tune', 10)
    cooling_hours   = tuning_cfg.get('cooling_off_hours', 48)
    shadow_req      = tuning_cfg.get('shadow_trades_required', 10)
    max_per_run     = tuning_cfg.get('max_params_per_run', 2)
    metric_key      = tuning_cfg.get('metric', 'profit_factor')

    proposals = 0

    for symbol in symbols:
        params_changed_this_symbol = 0

        # Get all strategies that have traded this symbol
        all_trades = database.get_closed_trades(symbol=symbol)
        strategies = list({t['strategy'] for t in all_trades})

        for strategy in strategies:
            if params_changed_this_symbol >= max_per_run:
                logger.info(f"[TUNER] {symbol} hit max_params_per_run={max_per_run} — stopping")
                break

            trades = [t for t in all_trades if t['strategy'] == strategy]
            if len(trades) < min_trades:
                logger.info(f"[TUNER] {symbol}/{strategy}: only {len(trades)} trades "
                            f"(need {min_trades}) — skipping")
                continue

            metrics = _compute_metrics(symbol, strategy, trades)
            if metrics is None:
                continue

            logger.info(f"[TUNER] {symbol}/{strategy}: "
                        f"PF={metrics['profit_factor']:.2f} | "
                        f"WR={metrics['win_rate']:.0%} | "
                        f"AvgPnL={metrics['avg_pnl']:.2f}% | "
                        f"n={metrics['trade_count']}")

            result = _select_candidate(metrics, tuning_cfg, full_cfg)
            if result is None:
                continue

            param, old_val, new_val, reason = result

            if not _cooling_off_ok(symbol, param, cooling_hours):
                continue

            log_id = database.log_tuning_change(
                symbol=symbol,
                parameter=param,
                old_value=old_val,
                new_value=new_val,
                metric_name=metric_key,
                metric_value=metrics[metric_key],
                trade_count=metrics['trade_count'],
                shadow_trades_required=shadow_req
            )

            if log_id:
                logger.info(f"[TUNER] Proposal #{log_id}: {symbol}/{param} "
                            f"{old_val} -> {new_val} | {reason}")
                proposals += 1
                params_changed_this_symbol += 1

    logger.info(f"[TUNER] Cycle complete — {proposals} proposal(s) submitted")
    return proposals


def check_promotions(symbol):
    """
    Called by main.py every time a SHADOW SELL closes for a given symbol.
    Increments shadow validation counters and promotes or rejects candidates
    that have accumulated enough trades.

    Args:
        symbol (str): Symbol whose shadow trade just closed

    Returns:
        list[str]: Human-readable promotion/rejection messages for Telegram
    """
    import database

    ready_ids = database.increment_shadow_validation_trades(symbol)
    messages = []

    if not ready_ids:
        return messages

    tuning_cfg = _get_tuning_config()
    metric_key = tuning_cfg.get('metric', 'profit_factor')
    min_imp    = tuning_cfg.get('min_metric_improvement', 0.05)

    for snap_id in ready_ids:
        # Get the validation record
        pending = database.get_pending_shadow_validations()
        snap = next((p for p in pending if p['id'] == snap_id), None)
        if not snap:
            continue

        # Compute metrics using only trades that occurred after the proposal was
        # created, identified by trade ID (not index, which shifts on deletes).
        all_trades    = database.get_closed_trades(symbol=symbol)
        baseline_id   = snap.get('baseline_max_trade_id', 0)
        recent_trades = [t for t in all_trades if t['trade_id'] > baseline_id]

        # Filter validation pool to the same paradigm the proposal targets.
        # Without this, a `paradigms.trend_following.rsi_entry` candidate gets
        # validated against MR/LS/VB trades that happened to fire after the
        # proposal — promotes/rejects on noise unrelated to the change.
        param_path = snap['parameter']
        parts = param_path.split('.')
        if len(parts) >= 3 and parts[0] == 'paradigms':
            target_paradigm = _STRAT_KEY_TO_NAME.get(parts[1])
            if target_paradigm:
                recent_trades = [t for t in recent_trades if t['strategy'] == target_paradigm]
        # Stop-mult and other cross-paradigm parameters keep the mixed pool.

        metrics       = _compute_metrics(symbol, '(all)', recent_trades)

        # Simple promotion decision: did overall performance improve?
        # We compare the current metric to what it was when proposal was made
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('SELECT metric_value FROM tuning_log WHERE id = ?', (snap['tuning_log_id'],))
        row = c.fetchone()
        conn.close()

        if not row or metrics is None:
            database.update_tuning_status(snap['tuning_log_id'], 'REJECTED')
            msg = (f"🔬 TUNER REJECTED: {snap['symbol']}/{snap['parameter']} "
                   f"{snap['baseline_value']} -> {snap['candidate_value']} "
                   f"(insufficient data for validation)")
            messages.append(msg)
            logger.info(msg)
            continue

        baseline_metric = row[0]
        current_metric  = metrics.get(metric_key, 0)

        # If baseline PF was infinite (zero losses), arithmetic breaks down.
        # Promote only if current is also infinite (zero losses sustained).
        if baseline_metric == float('inf'):
            improved = (current_metric == float('inf'))
        else:
            improved = (current_metric - baseline_metric) >= (baseline_metric * min_imp)

        if improved:
            database.update_tuning_status(snap['tuning_log_id'], 'PROMOTED_LIVE')
            _apply_to_config(snap['symbol'], snap['parameter'], snap['candidate_value'])
            msg = (f"✅ TUNER PROMOTED: {snap['symbol']}/{snap['parameter']} "
                   f"{snap['baseline_value']} -> {snap['candidate_value']} "
                   f"({metric_key}: {baseline_metric:.2f} -> {current_metric:.2f})")
        else:
            database.update_tuning_status(snap['tuning_log_id'], 'REJECTED')
            msg = (f"❌ TUNER REJECTED: {snap['symbol']}/{snap['parameter']} "
                   f"{snap['baseline_value']} -> {snap['candidate_value']} "
                   f"({metric_key}: {baseline_metric:.2f} vs {current_metric:.2f} — no improvement)")

        messages.append(msg)
        logger.info(msg)

    return messages


def _apply_to_config(symbol, parameter, new_value):
    """
    Writes a promoted parameter value to Config.yaml using ruamel.yaml,
    which preserves all comments and formatting.

    Navigates the symbol_overrides section first; if the key doesn't exist
    there it creates it. Falls back to updating the global strategy section.

    Args:
        symbol (str): Target symbol e.g. 'BTC/USD'
        parameter (str): Dot-path key e.g. 'paradigms.trend_following.rsi_entry'
        new_value (float): Promoted value to write
    """
    from ruamel.yaml import YAML

    yaml = YAML()
    yaml.preserve_quotes = True

    # Back up Config.yaml before any write
    backup_path = str(CFG_PATH) + '.bak'
    shutil.copy2(CFG_PATH, backup_path)
    logger.info(f"[TUNER] Config backup written to {backup_path}")

    with open(CFG_PATH, 'r') as f:
        cfg = yaml.load(f)

    parts = parameter.split('.')

    # Navigate to the symbol override node under strategy.symbol_overrides
    if 'strategy' not in cfg:
        cfg['strategy'] = {}
    if 'symbol_overrides' not in cfg['strategy']:
        cfg['strategy']['symbol_overrides'] = {}
    if symbol not in cfg['strategy']['symbol_overrides']:
        cfg['strategy']['symbol_overrides'][symbol] = {}

    node = cfg['strategy']['symbol_overrides'][symbol]

    # Walk the dot-path, creating intermediate dicts as needed
    for part in parts[:-1]:
        if part not in node:
            node[part] = {}
        node = node[part]

    leaf = parts[-1]
    old_val = node.get(leaf, '(not set)')
    node[leaf] = new_value

    with open(CFG_PATH, 'w') as f:
        yaml.dump(cfg, f)

    logger.info(f"[TUNER] ✅ Config updated: {symbol}/{parameter} "
                f"{old_val} -> {new_value} (backup: {backup_path})")
