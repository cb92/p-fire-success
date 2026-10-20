from dataclasses import dataclass
from datetime import date
from copy import deepcopy
import pandas as pd

# Tax bracket information
STD_DED = {True: 32_000, False: 16_100}
BRACKETS = {
    True:  [(0, .10), (23_850, .12), (96_950, .22), (206_700, .24), (394_600, .32), (501_050, .35), (751_600, .37)],
    False: [(0, .10), (11_925, .12), (48_475, .22), (103_350, .24), (197_300, .32), (250_525, .35), (626_350, .37)],
}
LTCG_BREAKS = {True: (98_900, 613_700), False: (49_450, 545_500)}   # 0%->15% and 15%->20% thresholds
SS_WAGE_BASE = 184_500                                              # per earner, indexed by inflation

# ---------- Taxes ----------
def bracket_tax(ti, joint):
    b = BRACKETS[joint]
    return sum(max(0, min(ti, b[i + 1][0] if i + 1 < len(b) else float("inf")) - lo) * rate
               for i, (lo, rate) in enumerate(b))


def fed_tax(ordinary, gains, joint, infl):
    """Total federal tax in nominal $, with gains stacked on top of ordinary income.
    Brackets and deduction are deflated/re-inflated so they track inflation."""
    ordinary, gains = ordinary / infl, gains / infl
    ord_ti = max(0, ordinary - STD_DED[joint])
    tot_ti = max(0, ordinary + gains - STD_DED[joint])
    b0, b1 = LTCG_BREAKS[joint]
    tax = bracket_tax(ord_ti, joint)
    tax += 0.15 * max(0, min(tot_ti, b1) - max(ord_ti, b0)) + 0.20 * max(0, tot_ti - max(ord_ti, b1))
    return tax * infl


                next_threshold = brackets[i + 1][0]
                income_in_bracket = min(taxable_income, next_threshold) - threshold
            else:
                income_in_bracket = taxable_income - threshold
            tax += income_in_bracket * rate
@dataclass
    gains: float = 0.0
    has_spouse: bool = True
    state: float = 0.05

    def _total(self, o, g):
        return fed_tax(o, g, self.has_spouse, self.infl) + self.state * (o + g)
    def total(self):
        return self._total(self.ordinary, self.gains)

    def extra(self, d_ord=0.0, d_gains=0.0):

    def realize(self, d_ord=0.0, d_gains=0.0):
        self.ordinary += d_ord
        self.gains += d_gains


def solve_gross(need, net_fn, cap):
    """Smallest gross g in [0, cap] with net_fn(g) >= need (net_fn increasing).
    Returns (g, unmet)."""
    if net_fn(cap) < need:
        return cap, need - net_fn(cap)
    lo, hi = 0.0, cap
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if net_fn(mid) < need else (lo, mid)
    return hi, 0.0


# ---------- Mortgage math ----------
def monthly_pmt(principal, rate, num_years, pmts_per_year=12):
    r, n = rate / pmts_per_year, num_years * pmts_per_year
    return principal * r * (1 + r) ** n / ((1 + r) ** n - 1)


def remaining_balance(principal, rate, num_years, years_since_start, pmts_per_year=12):
    if years_since_start > num_years:
        return 0
    r, k = rate / pmts_per_year, years_since_start * pmts_per_year
    pmt = monthly_pmt(principal, rate, num_years, pmts_per_year)
START_YEAR = date.today().year
    return principal * (1 + r) ** k - pmt * ((1 + r) ** k - 1) / r


# ---------- Accounts ----------
class Account:
    def __init__(self, balance, basis=0):
        self.balance, self.basis = balance, basis

    def deposit(self, amt):
        self.balance += amt
        self.basis += amt


        Returns the amount still unmet."""
        avail = self.net_available(ltcg_rate)
        p = min(1, net / avail) if avail > 0 else 1
        self.balance *= 1 - p
        if self.balance <= 0:
            return need
        g, unmet = solve_gross(need, lambda s: s - tx.extra(d_gains=f * s), self.balance)
        tx.realize(d_gains=f * g)
        self.balance -= g
        self.basis -= (1 - f) * g
        return unmet


class Roth(Account):
    def withdraw(self, need, age, tx, penalty):
        """Returns the amount still unmet."""
        if age > 59:
            used = min(need, self.balance)
            self.balance -= used
            self.basis = min(self.basis, self.balance)
            return need - used
            self.balance -= need
            self.basis -= need
        self.balance -= free
        self.basis -= free
            return 0
        if need < avail:
            self.balance -= self.basis + (need - self.basis) / k
            self.basis = 0
        # earnings: ordinary income + penalty
        e, unmet = solve_gross(need, lambda e: e - tx.extra(d_ord=e) - penalty * e,
                               self.balance - self.basis)
        tx.realize(d_ord=e)
        self.balance -= e
        return unmet


class Traditional(Account):
        self.balance = max(0, self.balance - gross)
    def withdraw(self, need, age, tx, penalty):
        pen = 0 if age > 59 else penalty
        g, unmet = solve_gross(need, lambda g: g - tx.extra(d_ord=g) - pen * g, self.balance)
        tx.realize(d_ord=g)
        self.balance -= g
        return unmet


@dataclass
class Portfolio:
    roth: Roth
    trad: Traditional
    brokerage: Brokerage

        return need

    def grow(self, rate):
        for a in (self.roth, self.trad, self.brokerage):
            a.balance *= rate          # cost basis doesn't grow

    def __str__(self):
        return (f"Brokerage {self.brokerage.balance:,.0f} | "
                f"Traditional {self.trad.balance:,.0f} | Roth {self.roth.balance:,.0f}")


# ---------- Housing ----------
@dataclass
class Mortgage:
    amount: float
    rate: float
    years: int
    start_year: int

    @property
    def payment(self):                 # annual
        return 12 * monthly_pmt(self.amount, self.rate, self.years)

    def balance(self, year):
        return remaining_balance(self.amount, self.rate, self.years, year - self.start_year)

    def paid_off(self, year):
        return year - self.start_year >= self.years


@dataclass
class Home:
    value: float
    cost_basis: float
    mortgage: Mortgage
    annual_cost: float                 # mortgage payment + upkeep, in base_year dollars
    base_year: int

    def cost(self, year, inflation):
        return upkeep + (0 if self.mortgage.paid_off(year) else self.mortgage.payment)

    def sale_proceeds(self, year, ord_tax = ORD, ltcg_rate = LTCG):
        price = self.value * 0.95      # 5% selling costs
        exclusion = 500_000 if has_spouse else 250_000
        taxable = max(0, price - self.cost_basis - exclusion)
        rate = (ord_tax if year - self.mortgage.start_year < 2 else ltcg_rate) + STATE
        return price - self.mortgage.balance(year) - rate * taxable
        return price - self.mortgage.balance(year) - tax


# ---------- Simulation ----------
pf = Portfolio(Roth(900_000, 500_000), Traditional(100_000), Brokerage(700_000, 500_000))
home = Home(1_900_000, 1_600_000, Mortgage(750_000, 0.05375, 30, 2023), HOUSING_OWN, START_YEAR)  # None if renting
    print(f"===== {year} (age {age}) =====\nStart: {pf}")
    start_year: int = date.today().year
    age_start: int = 35
    earners: int = 1                                    # number of wage earners (for the FICA wage base)

    # Income
    net = post_tax - housing - (EXP_WORKING if working else EXP_RETIRED) * infl
    print(f"Pre-tax {pre_tax:,.0f} | Post-tax {post_tax:,.0f} | Net {net:,.0f}")
    if net >= 0:
        pf.brokerage.deposit(net)

    # Growth
    pf.grow(GROWTH)
        pf.brokerage.deposit(home.sale_proceeds(year, fed_income_tax, long_term_tax))
        home = None

    if home == None and year == NEW_HOME_YEAR and pf.brokerage.balance > 0:     # no brokerage -> no purchase
        cost = NEW_HOME_COST * HOME_APPREC ** t
        down = min(0.2 * cost, pf.brokerage.net_available(long_term_tax))
        home = Home(cost, cost, mortgage, mortgage.payment + 0.015 * cost, year)  # +1.5% insurance + taxes
    ss_income: float = 50_000                           # TODO: link to SSA calculator, think about entitlement cuts

    pretax_contrib: float = 0
    roth_contrib: float = 8_000
    employer_match: float = 8_000
    contrib_annual_increase: float = 1.01
