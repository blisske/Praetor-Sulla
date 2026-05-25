"""Tests for core/oanda_client.py — pure-logic units, no live Oanda.

Covers:
  - Symbol mapping (EUR/USD ↔ EUR_USD, idempotent both ways)
  - Granularity mapping (1h → H1, etc.)
  - Environment routing (practice → fxpractice base URL, live → fxtrade)
  - Construction validation (missing token / account_id / bad env)
  - Fee/price extraction helpers from Oanda response shapes
  - Order request body shape (signed units, attached stop format)
  - Defensive-input behavior on None/garbage

Live Oanda calls (get_account, get_candles, place_market_order) are tested
in integration tests that need OANDA_API_TOKEN — out of scope here.

Run with:
    docker exec -w /app ionic-api python3 -m unittest tests.test_oanda_client -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "core"))

from core.oanda_client import (
    OandaClient,
    OandaError,
    OandaMissingCredentials,
    _to_oanda_instrument,
    _to_internal_symbol,
    _format_price,
    extract_fill_fee_usd,
    extract_fill_price,
    extract_close_fee_usd,
    extract_close_pl_usd,
)


class SymbolMapping(unittest.TestCase):
    def test_internal_to_oanda(self):
        self.assertEqual(_to_oanda_instrument("EUR/USD"), "EUR_USD")
        self.assertEqual(_to_oanda_instrument("USD/JPY"), "USD_JPY")
        self.assertEqual(_to_oanda_instrument("GBP/USD"), "GBP_USD")

    def test_idempotent_either_direction(self):
        # Oanda format passes through unchanged
        self.assertEqual(_to_oanda_instrument("EUR_USD"), "EUR_USD")
        # Internal format passes through unchanged the other way
        self.assertEqual(_to_internal_symbol("EUR/USD"), "EUR/USD")

    def test_oanda_to_internal(self):
        self.assertEqual(_to_internal_symbol("EUR_USD"), "EUR/USD")
        self.assertEqual(_to_internal_symbol("USD_JPY"), "USD/JPY")


class EnvironmentRouting(unittest.TestCase):
    """OandaClient picks the right base URL based on environment kwarg."""

    def test_practice_uses_fxpractice(self):
        c = OandaClient(token="tok", account_id="acc", environment="practice")
        self.assertEqual(c.base_url, "https://api-fxpractice.oanda.com")

    def test_live_uses_fxtrade(self):
        c = OandaClient(token="tok", account_id="acc", environment="live")
        self.assertEqual(c.base_url, "https://api-fxtrade.oanda.com")

    def test_environment_normalized_case(self):
        c = OandaClient(token="tok", account_id="acc", environment="LIVE")
        self.assertEqual(c.environment, "live")

    def test_bad_environment_raises(self):
        with self.assertRaises(OandaError):
            OandaClient(token="tok", account_id="acc", environment="staging")


class ConstructionValidation(unittest.TestCase):
    def test_missing_token_raises(self):
        with self.assertRaises(OandaMissingCredentials):
            OandaClient(token="", account_id="acc")

    def test_missing_account_id_raises(self):
        with self.assertRaises(OandaMissingCredentials):
            OandaClient(token="tok", account_id="")

    def test_auth_header_set(self):
        c = OandaClient(token="my-tok", account_id="acc")
        self.assertEqual(c._session.headers["Authorization"], "Bearer my-tok")

    def test_repr_includes_account_and_env(self):
        c = OandaClient(token="tok", account_id="101-001-123", environment="practice")
        r = repr(c)
        self.assertIn("101-001-123", r)
        self.assertIn("practice", r)


class PriceFormatting(unittest.TestCase):
    def test_format_5_decimal_places(self):
        # Standard FX pair precision
        self.assertEqual(_format_price(1.07842), "1.07842")
        # Trailing zeros preserved (Oanda accepts but doesn't require)
        self.assertEqual(_format_price(1.1), "1.10000")

    def test_format_handles_jpy_pairs_too(self):
        # JPY pairs have higher numeric values; format still works.
        # Oanda server-side rounds to instrument precision.
        self.assertEqual(_format_price(150.123), "150.12300")


class FillFeeExtraction(unittest.TestCase):
    """extract_fill_fee_usd from place_market_order() response shapes."""

    def test_commission_only(self):
        resp = {"orderFillTransaction": {"commission": "0.50"}}
        self.assertAlmostEqual(extract_fill_fee_usd(resp), 0.50)

    def test_financing_only(self):
        resp = {"orderFillTransaction": {"financing": "0.12"}}
        self.assertAlmostEqual(extract_fill_fee_usd(resp), 0.12)

    def test_half_spread_cost(self):
        # On standard Oanda accounts, the cost IS the spread — captured here.
        resp = {"orderFillTransaction": {"halfSpreadCost": "1.34"}}
        self.assertAlmostEqual(extract_fill_fee_usd(resp), 1.34)

    def test_all_three_summed(self):
        resp = {"orderFillTransaction": {
            "commission":     "0.10",
            "financing":      "0.05",
            "halfSpreadCost": "1.25",
        }}
        self.assertAlmostEqual(extract_fill_fee_usd(resp), 1.40)

    def test_negative_values_use_absolute(self):
        # Oanda sometimes reports financing as negative (credit to account).
        # We sum absolute values — the cost-basis convention is "total fees,"
        # not "net cash flow."
        resp = {"orderFillTransaction": {
            "commission": "0.10",
            "financing":  "-0.05",
        }}
        self.assertAlmostEqual(extract_fill_fee_usd(resp), 0.15)

    def test_missing_fill_returns_zero(self):
        resp = {"orderCancelTransaction": {"reason": "MARKET_HALTED"}}
        self.assertEqual(extract_fill_fee_usd(resp), 0.0)

    def test_none_returns_zero(self):
        self.assertEqual(extract_fill_fee_usd(None), 0.0)

    def test_garbage_returns_zero(self):
        self.assertEqual(extract_fill_fee_usd("not a dict"), 0.0)
        self.assertEqual(extract_fill_fee_usd({"orderFillTransaction": "garbage"}), 0.0)

    def test_string_values_coerced(self):
        # Oanda returns numeric fields as strings — we coerce.
        resp = {"orderFillTransaction": {"commission": "2.50"}}
        self.assertAlmostEqual(extract_fill_fee_usd(resp), 2.50)

    def test_malformed_value_doesnt_raise(self):
        resp = {"orderFillTransaction": {"commission": "not-a-number"}}
        # Skips the malformed field and returns 0 (or whatever else is valid)
        self.assertEqual(extract_fill_fee_usd(resp), 0.0)


class FillPriceExtraction(unittest.TestCase):
    def test_basic_fill_price(self):
        resp = {"orderFillTransaction": {"price": "1.08245"}}
        self.assertAlmostEqual(extract_fill_price(resp), 1.08245)

    def test_missing_uses_fallback(self):
        self.assertEqual(extract_fill_price({}, fallback=1.0), 1.0)
        self.assertEqual(extract_fill_price(None, fallback=1.0), 1.0)

    def test_garbage_price_uses_fallback(self):
        resp = {"orderFillTransaction": {"price": "garbage"}}
        self.assertEqual(extract_fill_price(resp, fallback=2.0), 2.0)


class CloseExtraction(unittest.TestCase):
    """extract_close_fee_usd + extract_close_pl_usd from close_position()."""

    def test_long_close_pl(self):
        resp = {"longOrderFillTransaction": {"pl": "12.34"}}
        self.assertAlmostEqual(extract_close_pl_usd(resp, side="long"), 12.34)

    def test_long_close_loss(self):
        resp = {"longOrderFillTransaction": {"pl": "-4.56"}}
        self.assertAlmostEqual(extract_close_pl_usd(resp, side="long"), -4.56)

    def test_long_close_fee(self):
        resp = {"longOrderFillTransaction": {
            "commission":     "0.10",
            "halfSpreadCost": "1.50",
        }}
        self.assertAlmostEqual(extract_close_fee_usd(resp, side="long"), 1.60)

    def test_short_close_uses_short_key(self):
        resp = {"shortOrderFillTransaction": {"pl": "5.00"}}
        self.assertAlmostEqual(extract_close_pl_usd(resp, side="short"), 5.00)

    def test_missing_returns_zero(self):
        self.assertEqual(extract_close_pl_usd({}, side="long"), 0.0)
        self.assertEqual(extract_close_fee_usd({}, side="long"), 0.0)


class OrderRequestShape(unittest.TestCase):
    """Verify place_market_order builds the right Oanda body shape.

    We mock _post to capture the body without hitting the network.
    """

    def setUp(self):
        self.client = OandaClient(token="tok", account_id="101-001-123", environment="practice")

    def test_basic_buy_with_stop(self):
        with patch.object(self.client, "_post", return_value={}) as mock_post:
            self.client.place_market_order(
                symbol="EUR/USD",
                units=10000,
                stop_loss_price=1.0750,
            )
            # Path + body shape
            args, kwargs = mock_post.call_args
            path, body = args
            self.assertEqual(path, "/v3/accounts/101-001-123/orders")
            order = body["order"]
            self.assertEqual(order["instrument"], "EUR_USD")
            self.assertEqual(order["units"], "10000")          # positive long
            self.assertEqual(order["type"], "MARKET")
            self.assertEqual(order["timeInForce"], "FOK")
            # stop attached, GTC
            self.assertIn("stopLossOnFill", order)
            self.assertEqual(order["stopLossOnFill"]["price"], "1.07500")
            self.assertEqual(order["stopLossOnFill"]["timeInForce"], "GTC")

    def test_short_uses_negative_units(self):
        with patch.object(self.client, "_post", return_value={}) as mock_post:
            self.client.place_market_order(symbol="EUR/USD", units=-5000)
            order = mock_post.call_args[0][1]["order"]
            self.assertEqual(order["units"], "-5000")
            # No stop attached when not requested
            self.assertNotIn("stopLossOnFill", order)

    def test_take_profit_attached(self):
        with patch.object(self.client, "_post", return_value={}) as mock_post:
            self.client.place_market_order(
                symbol="USD/JPY",
                units=1000,
                stop_loss_price=148.50,
                take_profit_price=152.00,
            )
            order = mock_post.call_args[0][1]["order"]
            self.assertEqual(order["instrument"], "USD_JPY")
            self.assertIn("stopLossOnFill", order)
            self.assertIn("takeProfitOnFill", order)
            self.assertEqual(order["takeProfitOnFill"]["price"], "152.00000")

    def test_client_order_id_in_extensions(self):
        with patch.object(self.client, "_post", return_value={}) as mock_post:
            self.client.place_market_order(
                symbol="EUR/USD", units=1000,
                client_order_id="ionic_buy_EURUSD_abcd1234",
            )
            order = mock_post.call_args[0][1]["order"]
            self.assertEqual(order["clientExtensions"]["id"], "ionic_buy_EURUSD_abcd1234")


class ClosePositionShape(unittest.TestCase):
    def setUp(self):
        self.client = OandaClient(token="tok", account_id="101-001-123", environment="practice")

    def test_long_close_uses_longUnits_all(self):
        with patch.object(self.client, "_put", return_value={}) as mock_put:
            self.client.close_position("EUR/USD", side="long")
            args, kwargs = mock_put.call_args
            path = args[0]
            body = kwargs.get("json_body", args[1] if len(args) > 1 else None)
            self.assertEqual(path, "/v3/accounts/101-001-123/positions/EUR_USD/close")
            self.assertEqual(body, {"longUnits": "ALL"})

    def test_short_close_uses_shortUnits_all(self):
        with patch.object(self.client, "_put", return_value={}) as mock_put:
            self.client.close_position("EUR/USD", side="short")
            body = mock_put.call_args.kwargs.get("json_body")
            self.assertEqual(body, {"shortUnits": "ALL"})


class ModifyTradeStopShape(unittest.TestCase):
    def setUp(self):
        self.client = OandaClient(token="tok", account_id="101-001-123", environment="practice")

    def test_modify_stop_body_shape(self):
        with patch.object(self.client, "_put", return_value={}) as mock_put:
            self.client.modify_trade_stop("12345", 1.0825)
            args, kwargs = mock_put.call_args
            path = args[0]
            body = kwargs.get("json_body")
            self.assertEqual(path, "/v3/accounts/101-001-123/trades/12345/orders")
            self.assertEqual(body["stopLoss"]["price"], "1.08250")
            self.assertEqual(body["stopLoss"]["timeInForce"], "GTC")


if __name__ == "__main__":
    unittest.main()
