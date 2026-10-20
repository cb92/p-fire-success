from dataclasses import dataclass
from datetime import date
from typing import Optional
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


@dataclass
class TaxYear:
    """Running ordinary income and realized gains for one year, so withdrawals stack."""
    infl: float
    ordinary: float = 0.0
    gains: float = 0.0
    has_spouse: bool = True
    state: float = 0.05

    def _total(self, o, g):
        return fed_tax(o, g, self.has_spouse, self.infl) + self.state * (o + g)

    def total(self):
        return self._total(self.ordinary, self.gains)

    def extra(self, d_ord=0.0, d_gains=0.0):
        """Incremental tax from realizing more income this year."""
        return self._total(self.ordinary + d_ord, self.gains + d_gains) - self.total()

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
    return principal * (1 + r) ** k - pmt * ((1 + r) ** k - 1) / r


# ---------- Accounts ----------
class Account:
    def __init__(self, balance, basis=0):
        self.balance, self.basis = balance, basis

    def deposit(self, amt):
        self.balance += amt
        self.basis += amt


class Brokerage(Account):
    def sell(self, need, tx):
        """Raise `need` after-tax dollars, drawing basis and gains proportionally.
        Returns the amount still unmet."""
        if self.balance <= 0:
            return need
        f = max(0, (self.balance - self.basis) / self.balance)          # gain fraction
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
        free = min(need, self.basis)                                   # basis: no tax or penalty
        self.balance -= free
        self.basis -= free
        need -= free
        if need <= 0:
            return 0
        # earnings: ordinary income + penalty
        e, unmet = solve_gross(need, lambda e: e - tx.extra(d_ord=e) - penalty * e,
                               self.balance - self.basis)
        tx.realize(d_ord=e)
        self.balance -= e
        return unmet


class Traditional(Account):
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

    def withdraw(self, need, age, tx, penalty):
        """Brokerage first, then Roth, then traditional. Returns the unmet amount."""
        need = self.brokerage.sell(need, tx)
        if need > 1e-6:
            need = self.roth.withdraw(need, age, tx, penalty)
        if need > 1e-6:
            need = self.trad.withdraw(need, age, tx, penalty)
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
        upkeep = (self.annual_cost - self.mortgage.payment) * inflation ** (year - self.base_year)
        return upkeep + (0 if self.mortgage.paid_off(year) else self.mortgage.payment)

    def sale_proceeds(self, year, tx, has_spouse):
        """Net proceeds; records any taxable gain in `tx`."""
        price = self.value * 0.95      # 5% selling costs
        exclusion = 500_000 if has_spouse else 250_000
        taxable = max(0, price - self.cost_basis - exclusion)
        kw = {"d_ord": taxable} if year - self.mortgage.start_year < 2 else {"d_gains": taxable}
        tax = tx.extra(**kw)
        tx.realize(**kw)
        return price - self.mortgage.balance(year) - tax


# ---------- Simulation ----------
@dataclass
class SimulationRun:
    portfolio: Portfolio
    home: Optional[Home]                                # None if renting

    # Planning ages
    start_year: int = date.today().year
    age_start: int = 35
    age_retire: int = 40
    age_end_pt: int = 55
    age_ss: int = 67
    age_end: int = 99

    # Household
    has_spouse: bool = True
    earners: int = 1                                    # number of wage earners (for the FICA wage base)

    # Income
    pre_ret_income: float = 210_000
    post_ret_income: float = 0
    ss_income: float = 50_000                           # TODO: link to SSA calculator, think about entitlement cuts

    # Contributions
    pretax_contrib: float = 0
    roth_contrib: float = 8_000
    employer_match: float = 8_000
    contrib_annual_increase: float = 1.01

    # Expenses
    exp_working: float = 100_000                        # non-housing
    exp_retired: float = 100_000                        # non-housing
    housing_own: float = 90_000
    housing_rent: float = 60_000

    # Taxes
    state_tax: float = 0.05                             # flat state rate
    penalty: float = 0.10                               # early-withdrawal penalty

    # Economy
    growth: float = 1.10
    inflation: float = 1.04
    home_apprec: float = 1.04

    # Optional events (None to disable)
    home_sale_year: Optional[int] = 2028
    new_home_year: Optional[int] = 2035
    new_home_cost: float = 1_300_000
    new_mortgage_rate: float = 0.06
    new_mortgage_years: int = 30

    def __post_init__(self):
        if self.home_sale_year and self.new_home_year:
            assert self.new_home_year > self.home_sale_year
        # TODO: age bounds and contribution feasibility checks go here
    
    def run(self):
        """Simulate year by year. Returns a list of per-year result dicts."""
        pf, home = deepcopy(self.portfolio), deepcopy(self.home)
        results = []

        def balances(prefix):
            return {f"{prefix}_brokerage": pf.brokerage.balance,
                    f"{prefix}_traditional": pf.trad.balance,
                    f"{prefix}_roth": pf.roth.balance}

        def house_status(year):
            if home is None:
                return {"owns_home": False, "home_value": None, "mortgage_balance": None, "mortgage_paid_off": None}
            return {"owns_home": True, "home_value": home.value,
                    "mortgage_balance": home.mortgage.balance(year),
                    "mortgage_paid_off": home.mortgage.paid_off(year)}

        for t in range(self.age_end - self.age_start + 1):
            year, age = self.start_year + t, self.age_start + t
            infl = self.inflation ** t
            cg = self.contrib_annual_increase ** t
            working = age <= self.age_retire
            row = {"year": year, "age": age, **balances("start")}

            # Income
            wages = (self.pre_ret_income if working else self.post_ret_income if age <= self.age_end_pt else 0) * infl
            pretax = self.pretax_contrib * cg if working else 0
            if working:
                pf.trad.deposit(pretax + self.employer_match * cg)
                pf.roth.deposit(self.roth_contrib * cg)
            ss = self.ss_income * infl if age >= self.age_ss else 0      # COLA-adjusted

            tx = TaxYear(infl, ordinary=wages - pretax + ss, has_spouse = self.has_spouse, state = self.state_tax)
            fica = 0.062 * min(wages, self.earners * SS_WAGE_BASE * infl) + 0.0145 * wages
            post_tax = tx.ordinary - tx.total() - fica - (self.roth_contrib * cg if working else 0)

            # Net income -> brokerage surplus or drawdown
            housing = home.cost(year, self.inflation) if home else self.housing_rent * infl
            net = post_tax - housing - (self.exp_working if working else self.exp_retired) * infl
            row.update(pre_tax_income=tx.ordinary, post_tax_income=post_tax, net_income=net, failed=False, unmet=0.0)

            if net >= 0:
                pf.brokerage.deposit(net)
            else:
                unmet = pf.withdraw(-net, age, tx, self.penalty)
                if unmet > 1:
                    row.update(failed=True, unmet=unmet, **balances("end"), **house_status(year))
                    results.append(row)
                    break

            # Growth
            pf.grow(self.growth)
            if home:
                home.value *= self.home_apprec

            # One-time house events (end-of-year values, as before)
            if home and year == self.home_sale_year:
                pf.brokerage.deposit(home.sale_proceeds(year, tx, self.has_spouse))
                home = None

            if home is None and year == self.new_home_year and pf.brokerage.balance > 0:
                cost = self.new_home_cost * self.home_apprec ** t
                down = 0.2 * cost
                down -= pf.brokerage.sell(down, tx)              # down payment = what the brokerage could raise
                mortgage = Mortgage(cost - down, self.new_mortgage_rate, self.new_mortgage_years, year)
                home = Home(cost, cost, mortgage, mortgage.payment + 0.015 * cost, year)  # +1.5% insurance + taxes

            row.update(**balances("end"), **house_status(year))
            results.append(row)

        return results

sim = SimulationRun(
    Portfolio(Roth(900_000, 500_000), Traditional(100_000), Brokerage(700_000, 500_000)) ,
    Home(1_900_000, 1_600_000, Mortgage(750_000, 0.05375, 30, 2023), 90_000, date.today().year)
)

print(pd.DataFrame(sim.run()))