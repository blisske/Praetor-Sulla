"""
Sulla — AI Sentiment & Consensus Engine.

Three-tier verdict: BULLISH (execute) / NEUTRAL (hold) / BEARISH (hard abort).

FX-adapted from Anton's TradFi version + Tiberius's May-12 rendering fix:
  - Brave Search query is FX-tuned ("EUR USD forex" not "EUR/USD stock")
  - The system prompt frames Gemma as an FX strategist, not an equity analyst
  - Returns the model's polished answer body (post-`</think>`) rather than
    the raw `<think>` chain-of-thought (the bug Tiberius hit; same fix here)
  - Surfaces up to 3500 chars of reasoning so Telegram messages stay rich
    (4096 cap minus our message prefix headers)
"""

import asyncio
import logging
import requests

logger = logging.getLogger("sulla")


_SYSTEM_PROMPT = (
    "You are a senior currency strategist at a global macro desk. You assess "
    "long FX positions on the basis of price action, momentum, and the "
    "current macro tape. You write in clear, professional prose — no jargon "
    "without context, no hedging hand-waving. You consider central-bank "
    "stance, recent macro releases, interest-rate differentials, and "
    "near-term risk events when evaluating a potential long. "
    "You MUST end your analysis with exactly one of:\n"
    "VERDICT: BULLISH\n"
    "VERDICT: NEUTRAL\n"
    "VERDICT: BEARISH"
)


def _parse_verdict(text: str) -> tuple[str, str, bool]:
    """
    Returns (verdict_str, answer_body, is_bullish).

    Strips any `<think>...</think>` (Qwen3 chain-of-thought) and keeps the
    answer body — the polished analyst commentary that follows. That's
    what we surface to the user; the think-block is internal scratchwork.
    """
    if "<think>" in text and "</think>" in text:
        think_end = text.index("</think>")
        text = text[think_end + len("</think>"):].strip()

    answer_body = text.strip()
    text_upper  = answer_body.upper()

    if "VERDICT: BEARISH" in text_upper:
        return "BEARISH", answer_body, False
    if "VERDICT: BULLISH" in text_upper and "VERDICT: NEUTRAL" not in text_upper:
        return "BULLISH", answer_body, True
    if "VERDICT: NEUTRAL" in text_upper:
        return "NEUTRAL", answer_body, False
    if "BEARISH" in text_upper:
        return "BEARISH (inferred)", answer_body, False
    if "BULLISH" in text_upper:
        return "BULLISH (inferred)", answer_body, True
    logger.warning("No VERDICT line in LLM response — defaulting to NEUTRAL")
    return "NEUTRAL (no verdict found)", answer_body, False


def _build_news_query(symbol: str) -> str:
    """
    FX-tuned Brave Search query. Both legs of the pair matter (you want EUR
    news AND USD news for EUR/USD), so the query joins them with 'forex' as
    the search anchor + 'central bank' to bias toward macro headlines that
    actually move FX rather than corporate news of similarly-named tickers.
    """
    base, quote = symbol.split("/")
    return f"{base} {quote} forex central bank news"


def _build_prompt(symbol, price, paradigm, support_reasons, indicators, news_text):
    support_str = " | ".join(support_reasons) if support_reasons else "N/A"
    rsi = indicators.get('rsi')
    adx = indicators.get('adx')
    rsi_str = f"{rsi:.1f}" if rsi is not None else "N/A"
    adx_str = f"{adx:.1f}" if adx is not None else "N/A"

    return (
        f"PAIR: {symbol}\n"
        f"PRICE: {price:.5f}\n"
        f"PARADIGM TRIGGERED: {paradigm}\n"
        f"SUPPORTING SIGNALS: {support_str}\n"
        f"RSI: {rsi_str} | ADX: {adx_str} | "
        f"Regime: {indicators.get('regime', 'N/A')} | Trend: {indicators.get('trend', 'N/A')}\n"
        f"NEWS CONTEXT: {news_text}\n\n"
        f"Given the technical setup and news context above, assess whether this is "
        f"a safe environment to go long on {symbol} for an intermediate-term hold "
        f"(hours-to-days). Consider near-term macro events (NFP, FOMC, CPI, ECB, "
        f"BoJ, etc.) that could invalidate the trade."
    )


def _get_ai_consensus_sync(symbol, price, brave_key, llm_base_url, model_id,
                            paradigm, support_reasons, indicators,
                            llm_reasoning=True):
    """
    Synchronous worker. Returns (is_bullish, verdict_str, summary).
    """
    # ── 1. News context via Brave ──
    news_text = ""
    try:
        resp = requests.get(
            "https://api.search.brave.com/res/v1/web/search",
            headers={"X-Subscription-Token": brave_key, "Accept": "application/json"},
            params={"q": _build_news_query(symbol), "count": 3},
            timeout=10,
        )
        results   = resp.json().get('web', {}).get('results', [])
        news_text = " | ".join(r['title'] for r in results) if results else "[No recent headlines]"
    except Exception as e:
        news_text = f"[News unavailable: {e}]"

    # ── 2. Build prompt + query LLM ──
    prompt = _build_prompt(symbol, price, paradigm, support_reasons, indicators, news_text)
    payload = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user",   "content": prompt},
        ],
        "temperature":       0.15,
        "max_tokens":        2048,
        "frequency_penalty": 0.3,
    }
    try:
        ai_resp = requests.post(
            f"{llm_base_url}/chat/completions",
            json=payload,
            timeout=90,
        ).json()
        raw_text = ai_resp['choices'][0]['message']['content']
    except requests.exceptions.Timeout:
        return False, "NEUTRAL", "AI GATE OFFLINE: Read Timed Out (>90s)."
    except Exception as e:
        return False, "NEUTRAL", f"AI GATE OFFLINE: {e}"

    # ── 3. Parse + return ──
    verdict_str, answer_body, is_bullish = _parse_verdict(raw_text)
    if answer_body:
        summary = answer_body[:3500] + ("…" if len(answer_body) > 3500 else "")
    else:
        summary = f"VERDICT: {verdict_str}"
    return is_bullish, verdict_str, summary


async def get_ai_consensus(symbol, price, strategy_type, indicators,
                           supporting_reasons, brave_key, llm_base_url,
                           model_id, use_reasoning=True):
    """
    Async wrapper. Returns (is_bullish: bool, verdict_str: str, summary: str).
    """
    if indicators is None:
        indicators = {}
    if supporting_reasons is None:
        supporting_reasons = []

    try:
        return await asyncio.to_thread(
            _get_ai_consensus_sync,
            symbol, price, brave_key, llm_base_url, model_id,
            strategy_type, supporting_reasons, indicators, use_reasoning,
        )
    except Exception as e:
        return False, "NEUTRAL", f"AI GATE OFFLINE: {e}"
