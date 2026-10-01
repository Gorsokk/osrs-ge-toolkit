"""Cash with platinum tokens, GE tax, number display: tests that need no network and no language model.

Run from the repository root:   python -m unittest discover -s tests -v

Context: since "Beyond Max Cash" (30 September 2026) the Grand Exchange pays in a mix of coins and platinum
tokens (1 token = 1,000 coins, item 13204), and item stacks still stop at 2,147,483,647. Cash is coins + tokens.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# paths.py creates the toolkit's data folder when it is imported: point it at a throw-away folder first.
_HOME = tempfile.mkdtemp(prefix="toolkit-tests-")
os.environ["HOME"] = _HOME
os.environ["USERPROFILE"] = _HOME
os.environ["APPDATA"] = _HOME
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "toolkit"))

import money  # noqa: E402

COINS, TOKEN = 995, 13204
MAX_STACK = 2_147_483_647


def item(iid, qty, name="x"):
    return {"id": iid, "quantity": qty, "name": name}


class CashTests(unittest.TestCase):
    def test_coins_only(self):
        self.assertEqual(money.cash_of([item(COINS, 4_468_431)]), 4_468_431)

    def test_tokens_are_worth_1000_coins(self):
        self.assertEqual(money.cash_of([item(TOKEN, 3)]), 3_000)
        self.assertEqual(money.cash_of([item(COINS, 500), item(TOKEN, 2)]), 2_500)

    def test_the_new_maximum(self):
        # a full stack of coins + a full stack of tokens = just over 2.149 trillion gp
        full = [item(COINS, MAX_STACK), item(TOKEN, MAX_STACK)]
        self.assertEqual(money.cash_of(full), 2_149_631_130_647)

    def test_other_items_and_bad_entries_are_ignored(self):
        junk = [item(4151, 1), {"id": None, "quantity": 5}, {"quantity": 5}, item(COINS, 0), item(TOKEN, -4),
                {"id": TOKEN}]    # no quantity = 1 token
        self.assertEqual(money.cash_of(junk), 1_000)
        self.assertEqual(money.cash_of(None), 0)
        self.assertEqual(money.cash_of([]), 0)

    def test_split_cash(self):
        self.assertEqual(money.split_cash([item(COINS, 7), item(TOKEN, 2), item(COINS, 3)]), (10, 2))

    def test_swapping_coins_for_tokens_does_not_change_cash(self):
        before = [item(COINS, 3_000_000)]
        after = [item(COINS, 0), item(TOKEN, 3_000)]
        self.assertEqual(money.cash_of(before), money.cash_of(after))


class TaxTests(unittest.TestCase):
    def test_tax_rules(self):
        self.assertEqual(money.tax(49), 0)
        self.assertEqual(money.tax(50), 1)
        self.assertEqual(money.net_sell(100), 98)
        self.assertEqual(money.net_sell(250_000_000), 245_000_000)       # the cap starts here
        self.assertEqual(money.net_sell(249_999_999), 245_000_000)
        self.assertEqual(money.net_sell(3_000_000_000), 2_995_000_000)   # above 2.147B: still 5M at most
        self.assertEqual(money.net_sell(2_149_000_000_000), 2_148_995_000_000)

    def test_net_sell_never_goes_down_when_the_price_goes_up(self):
        # breakeven() relies on this
        previous = money.net_sell(0)
        for price in range(1, 20_000):
            current = money.net_sell(price)
            self.assertGreaterEqual(current, previous, price)
            previous = current


def brute_force_breakeven(cost):
    """The original algorithm: count up one gp at a time."""
    p = int(cost)
    while money.net_sell(p) < cost:
        p += 1
    return p


class BreakevenTests(unittest.TestCase):
    def test_same_answer_as_counting_up_for_ordinary_costs(self):
        for cost in list(range(0, 3_000)) + [c + 0.5 for c in range(0, 300)] + [49.99, 50.01, 1234.567]:
            self.assertEqual(money.breakeven(cost), brute_force_breakeven(cost), cost)

    def test_same_answer_around_the_tax_cap(self):
        for cost in (249_999_990, 250_000_000, 250_000_001, 251_234_567.5):
            self.assertEqual(money.breakeven(cost), brute_force_breakeven(cost), cost)

    def test_huge_costs_are_instant_and_never_lose_money(self):
        for cost in (3_000_000_000, 12_877_762_346, 2_000_000_000_000):
            be = money.breakeven(cost)
            self.assertGreaterEqual(money.net_sell(be), cost)
            self.assertLess(money.net_sell(be - 1), cost)
            self.assertEqual(be, cost + 5_000_000)     # above the cap the tax is a flat 5M

    def test_one_huge_case_against_counting_up(self):
        # 5 million steps: the reason the binary search exists
        self.assertEqual(money.breakeven(3_000_000_000), brute_force_breakeven(3_000_000_000))

    def test_zero_and_negative(self):
        self.assertEqual(money.breakeven(0), 0)
        self.assertEqual(money.breakeven(-5), brute_force_breakeven(-5))


class DisplayTests(unittest.TestCase):
    def test_gp(self):
        self.assertEqual(money.gp(950), "950")
        self.assertEqual(money.gp(9_999), "9,999")
        self.assertEqual(money.gp(12_345), "12.3k")
        self.assertEqual(money.gp(1_500_000), "1.50M")
        self.assertEqual(money.gp(999_999_999), "1000.00M")      # unchanged behaviour below 1B
        self.assertEqual(money.gp(1_500_000_000), "1.50B")
        self.assertEqual(money.gp(MAX_STACK), "2.15B")
        self.assertEqual(money.gp(2_149_631_130_647), "2.15T")
        self.assertEqual(money.gp(-3_500_000_000), "-3.50B")

    def test_gp_with_spaces_for_the_alerts(self):
        self.assertEqual(money.gp(9_999, sep=" "), "9 999")
        self.assertEqual(money.gp(3_500_000_000, sep=" "), "3.50B")


# ------------------------------------------------------------------ the rest of the toolkit ---
import data  # noqa: E402
import income  # noqa: E402


class FakePrices:
    """Prices for a few items, no network."""

    def __init__(self, prices):
        self.prices = prices

    def latest(self):
        return {str(i): {"high": p, "low": p} for i, p in self.prices.items()}


class PlatinumInTheToolkitTests(unittest.TestCase):
    def setUp(self):
        self.latest = mock.patch.object(data.PRICES, "latest", FakePrices({4151: 1_500_000}).latest)
        self.latest.start()
        self.addCleanup(self.latest.stop)

    def test_tokens_are_valued_at_the_fixed_rate_not_looked_up(self):
        self.assertEqual(data.PRICES.unit_value(COINS), 1)
        self.assertEqual(data.PRICES.unit_value(TOKEN), 1000)
        self.assertEqual(data.PRICES.unit_value(4151), 1_500_000)

    def test_data_still_exports_the_tax_helpers(self):
        # other modules do `from data import net_sell, tax`
        self.assertIs(data.net_sell, money.net_sell)
        self.assertIs(data.tax, money.tax)
        self.assertEqual(data.COINS_ID, COINS)
        self.assertEqual(data.PLATINUM_TOKEN_ID, TOKEN)

    def snapshot(self, bank, inventory=None, equipment=None, ge=None):
        exports = {"bank.json": {"items": bank}, "inventory.json": {"items": inventory or []},
                   "equipment.json": {"items": equipment or []}, "ge_offers.json": ge or {"slots": []}}
        with mock.patch.object(income, "read_export", lambda folder, fn: (exports.get(fn), 1.0)):
            return income.snapshot(Path(_HOME))

    def test_income_snapshot_counts_tokens_as_cash(self):
        s = self.snapshot(bank=[item(COINS, 1_000_000), item(TOKEN, 2_000), item(4151, 1)],
                          inventory=[item(COINS, 500), item(TOKEN, 1)])
        self.assertEqual(s["cash"], 1_000_000 + 2_000_000 + 500 + 1_000)
        self.assertEqual(s["items"], 1_500_000)

    def test_swapping_coins_for_tokens_is_not_income(self):
        before = self.snapshot(bank=[item(COINS, 3_000_000), item(4151, 1)])
        after = self.snapshot(bank=[item(TOKEN, 3_000), item(4151, 1)])
        self.assertEqual(before["cash"] + before["items"], after["cash"] + after["items"])

    def test_gp_locked_in_a_buy_offer_above_the_32_bit_range(self):
        ge = {"slots": [{"state": "BUYING", "type": "BUY", "price": 3_500_000_000,
                         "quantity_total": 2, "quantity_filled": 1}]}
        s = self.snapshot(bank=[item(COINS, 10)], ge=ge)
        self.assertEqual(s["cash"], 10 + 3_500_000_000)

    def test_get_bank_reports_tokens_and_total_cash(self):
        import mcp_server
        bank = {"items": [item(COINS, 4_468_431, "Coins"), item(TOKEN, 2_000, "Platinum token"),
                          item(4151, 1, "Abyssal whip")]}
        with mock.patch.object(mcp_server, "_char", lambda args: ("Test", Path(_HOME))), \
                mock.patch.object(mcp_server, "_need", lambda folder, fn, hint: (bank, 5.0)):
            out = mcp_server.t_get_bank({})
        self.assertEqual(out["coins"], 4_468_431)
        self.assertEqual(out["platinum_tokens"], 2_000)
        self.assertEqual(out["cash_total"], 4_468_431 + 2_000_000)
        self.assertEqual(out["cash_total_text"], "6.47M")
        self.assertEqual(out["total_value"], 4_468_431 + 2_000_000 + 1_500_000)
        self.assertNotIn("Platinum token", out["untradeable_or_unpriced"])


if __name__ == "__main__":
    unittest.main()
