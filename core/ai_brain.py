import asyncio
import logging
import requests

# ==============================================================================
# SULLA V1 - AI SENTIMENT & CONSENSUS ENGINE (TradFi Analyst)
# Three-tier verdict: BULLISH (execute) / NEUTRAL (hold) / BEARISH (hard abort)
# Configurable model — swap model_id in Config.yaml with no code changes.
# ==============================================================================

logger = logging.getLogger("sulla")

def _get_ai_consensus_sync(symbol, price, brave_key, llm_base_url, model_id,
                            paradigm, support_reasons, indicators, llm_reasoning=True):
    """
    Synchronous worker. Isolated so asyncio loop stays responsive.
    """
    # ------------------------------------------------------------------
    # STEP 1: WEB SCRAPE — TradFi headlines via Brave Search
    # ------------------------------------------------------------------
    news_text = ""
    try:
        resp = requests.get(
            "https://api.search.brave.com/res/v1/web/search",
            headers={"X-Subscription-Token": brave_key, "Accept": "application/json"},
            params={"q": f"{symbol} stock market news", "count": 3},
            timeout=10
        )
        results   = resp.json().get('web', {}).get('results', [])
        news_text = " | ".join([r['title'] for r in results])
    except Exception as e:
        news_text = f"[News unavailable: {e}]"

    # ------------------------------------------------------------------
    # STEP 2: BUILD ENRICHED PROMPT
    # ------------------------------------------------------------------
    support_str = " | ".join(support_reasons) if support_reasons else "N/A"

    rsi_val = indicators.get('rsi')
    adx_val = indicators.get('adx')
    rsi_str = f"{rsi_val:.1f}" if rsi_val is not None else "N/A"
    adx_str = f"{adx_val:.1f}" if adx_val is not None else "N/A"

    prompt = (
        f"SYMBOL: {symbol} (US Equity)\n"
        f"PRICE: ${price:.2f}\n"
        f"PARADIGM TRIGGERED: {paradigm}\n"
        f"SUPPORTING SIGNALS: {support_str}\n"
        f"RSI: {rsi_str} | ADX: {adx_str} | "
        f"Regime: {indicators.get('regime', 'N/A')} | Trend: {indicators.get('trend', 'N/A')}\n"
        f"NEWS CONTEXT: {news_text}\n\n"
        f"Given the technical setup and news context above, assess whether this is a safe "
        f"environment to go long on {symbol}.\n"
        f"You MUST end your response with exactly one of:\n"
        f"VERDICT: BULLISH\n"
        f"VERDICT: NEUTRAL\n"
        f"VERDICT: BEARISH"
    )

    # ------------------------------------------------------------------
    # STEP 3: QUERY THE LLM
    # ------------------------------------------------------------------
    payload = {
        "model": model_id,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a rigid, elite Wall Street quantitative analyst. "
                    "You use perfect spelling and professional grammar. "
                    "You MUST end your analysis with exactly 'VERDICT: BULLISH', "
                    "'VERDICT: NEUTRAL', or 'VERDICT: BEARISH'. "
                    "Do not misspell or abbreviate these words."
                )
            },
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.3,
        "frequency_penalty": 0.5,
        "presence_penalty": 0.2,
    }

    # Thinking mode — Qwen3 only. Gemma / Llama / others ignore or error on this param.
    if llm_reasoning and model_id and "qwen3" in model_id.lower():
        payload["thinking"] = {"type": "enabled", "budget_tokens": 512}

    ai_resp  = requests.post(f"{llm_base_url}/chat/completions", json=payload, timeout=30).json()
    full_text = ai_resp['choices'][0]['message']['content']

    # ------------------------------------------------------------------
    # STEP 4: PARSE THREE-TIER VERDICT
    # Anchor exclusively to the "VERDICT:" line so stray words in the
    # body (tickers, qualifiers, notes) don't pollute the match.
    # ------------------------------------------------------------------
    import re
    verdict_line = ""
    for line in full_text.splitlines():
        if "VERDICT:" in line.upper():
            verdict_line = line.upper()
            break

    if verdict_line:
        if "BEARISH" in verdict_line:
            return "BEARISH", full_text
        if "BULLISH" in verdict_line or re.search(r'\bBULL\b', verdict_line):
            return "BULLISH", full_text
        if "NEUTRAL" in verdict_line:
            return "NEUTRAL", full_text

    # Fallback if no VERDICT: line found — log and default to NEUTRAL (safe)
    logger.warning(f"[AI] No VERDICT: line found in LLM response — defaulting to NEUTRAL")
    return "NEUTRAL", full_text


async def get_ai_consensus(symbol, price, brave_key, llm_base_url, model_id,
                            paradigm="UNKNOWN", support_reasons=None, indicators=None,
                            llm_reasoning=True):
    """
    Async wrapper. Returns (verdict_str, full_text) where verdict_str is
    one of: 'BULLISH', 'NEUTRAL', 'BEARISH'.

    Callers should check:
        if verdict == 'BULLISH': execute
        if verdict == 'BEARISH': hard abort + log
        else: hold
    """
    if indicators is None:
        indicators = {}
    if support_reasons is None:
        support_reasons = []

    try:
        return await asyncio.to_thread(
            _get_ai_consensus_sync,
            symbol, price, brave_key, llm_base_url, model_id,
            paradigm, support_reasons, indicators, llm_reasoning
        )
    except requests.exceptions.Timeout:
        return "NEUTRAL", "AI GATE OFFLINE: Read Timed Out (>30s)."
    except Exception as e:
        return "NEUTRAL", f"AI GATE OFFLINE: {e}"

