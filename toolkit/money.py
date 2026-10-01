"""Money helpers shared by the whole toolkit: cash (coins + platinum tokens), GE tax, number display.

No imports on purpose: pure functions, easy to test, usable from every module.

Coins and platinum tokens
-------------------------
Since "Beyond Max Cash" (30 September 2026) the Grand Exchange takes payment, and gives change, in a mix of
coins and platinum tokens. Item stacks, coins included, still stop at 2,147,483,647, so a rich player holds
part of their cash as platinum tokens. One token is always worth exactly 1,000 coins (fixed rate at any
banker; item id 13204 on the OSRS Wiki). Cash therefore means coins + 1,000 x tokens: never count item 995 alone.

Tokens have no price on the Wiki price list, so they are valued by this fixed rate, like coins.
"""

COINS_ID = 995
PLATINUM_TOKEN_ID = 13204
PLATINUM_TOKEN_VALUE = 1000     # gp per token

MIN_TAXABLE_PRICE = 50          # GE tax: nothing under 50 gp
TAX_RATE = 0.02                 # 2 %, rounded down
TAX_CAP = 5_000_000             # at most 5M per item (unchanged by Beyond Max Cash)


# ------------------------------------------------------------------- cash ---
def split_cash(items):
    """(coins, platinum_tokens) in a list of exported items [{'id': ..., 'quantity': ...}, ...]."""
    coins = tokens = 0
    for it in items or []:
        iid, qty = it.get("id"), it.get("quantity", 1) or 0
        if iid is None or qty <= 0:
            continue
        if iid == COINS_ID:
            coins += qty
        elif iid == PLATINUM_TOKEN_ID:
            tokens += qty
    return coins, tokens


def cash_of(items):
    """Cash in gp of a list of exported items: coins + 1,000 x platinum tokens."""
    coins, tokens = split_cash(items)
    return coins + tokens * PLATINUM_TOKEN_VALUE


# -------------------------------------------------------------------- tax ---
def tax(price):
    """GE tax on one item sold at `price`: 2 % (rounded down), none under 50 gp, capped at 5M."""
    if price < MIN_TAXABLE_PRICE:
        return 0
    return min(int(price * TAX_RATE), TAX_CAP)


def net_sell(price):
    """What you keep after the GE tax."""
    return price - tax(price)


def breakeven(cost):
    """Smallest sell price that does not lose money after the GE tax.

    net_sell never goes down when the price goes up, so a binary search finds it in about 25 steps
    (counting up one gp at a time took millions of steps once the tax cap of 5M applies).
    """
    lo = int(cost)
    if net_sell(lo) >= cost:
        return lo
    hi = lo + TAX_CAP + 2           # net_sell(hi) >= hi - TAX_CAP > cost
    while hi - lo > 1:              # invariant: net_sell(lo) < cost <= net_sell(hi)
        mid = (lo + hi) // 2
        if net_sell(mid) >= cost:
            hi = mid
        else:
            lo = mid
    return hi


# ---------------------------------------------------------------- display ---
def gp(n, sep=","):
    """Short text for an amount of gp: 950, 12.3k, 1.50M, 2.15B, 2.15T. `sep` separates thousands under 10k."""
    n = int(n)
    a = abs(n)
    if a >= 1_000_000_000_000:
        return f"{n / 1_000_000_000_000:.2f}T"
    if a >= 1_000_000_000:
        return f"{n / 1_000_000_000:.2f}B"
    if a >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if a >= 10_000:
        return f"{n / 1000:.1f}k"
    return f"{n:,}".replace(",", sep)
