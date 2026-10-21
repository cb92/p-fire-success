from dataclasses import dataclass, field
from datetime import date
from typing import Optional
from copy import deepcopy
import pandas as pd
from pathlib import Path
import numpy as np
import streamlit as st
import altair as alt

DATA_DIR = Path("data")
st.set_page_config(
    page_title="Monte Carlo Portfolio Simulator",
    page_icon=":chart_with_upward_trend:",
    layout="wide")

# Tax bracket information
STD_DED = {True: 32_000, False: 16_100}
BRACKETS = {
    True:  [(0, .10), (23_850, .12), (96_950, .22), (206_700, .24), (394_600, .32), (501_050, .35), (751_600, .37)],
    False: [(0, .10), (11_925, .12), (48_475, .22), (103_350, .24), (197_300, .32), (250_525, .35), (626_350, .37)],
}
LTCG_BREAKS = {True: (98_900, 613_700), False: (49_450, 545_500)}   # 0%->15% and 15%->20% thresholds
SS_WAGE_BASE = 184_500                                              # per earner, indexed by inflation
RISK_FREE_GROWTH = 1.025                                            # assumed growth rate for risk free asset (2 pct per year)

# ---------- Data Functions ----------
def _annual(fname):
    """Load a Date,Value csv (dates like 12/31/1933) as a Series indexed by year (last observation per year)."""
    df = pd.read_csv(DATA_DIR / fname)
    years = pd.to_datetime(df["Date"], format="%m/%d/%Y").dt.year
    s = pd.Series(df["Value"].to_numpy(), index=years.to_numpy())
    return s.groupby(level=0).last().sort_index()


def hist_apprec():
    return 1 + _annual("house_price_index.csv").to_numpy() / 100


def hist_macro():
    """(inflation factor, market growth factor) pairs, by year present in all three files."""
    infl = _annual("inflation_rates_annualpctchange.csv")
    dow, sp = _annual("dow_annual_return.csv"), _annual("sp500_annual_return.csv")
    yrs = infl.index.intersection(dow.index).intersection(sp.index)
    return np.column_stack([1 + infl[yrs].to_numpy() / 100,
                            1 + ((dow[yrs] + sp[yrs]) / 2).to_numpy() / 100])
# ---------- Tax Functions + Class ----------
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


# ---------- Mortgage functions ----------
def monthly_pmt(principal, rate, num_years, pmts_per_year=12):
    r, n = rate / pmts_per_year, num_years * pmts_per_year
    return principal * r * (1 + r) ** n / ((1 + r) ** n - 1)


def remaining_balance(principal, rate, num_years, years_since_start, pmts_per_year=12):
    if years_since_start > num_years:
        return 0
    r, k = rate / pmts_per_year, years_since_start * pmts_per_year
    pmt = monthly_pmt(principal, rate, num_years, pmts_per_year)
    return principal * (1 + r) ** k - pmt * ((1 + r) ** k - 1) / r


# ---------- Account classes ----------
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


# ---------- Housing classes ----------
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
    upkeep: float = field(init=False)
    last_year: int = field(init=False)

    def __post_init__(self):
        self.upkeep = self.annual_cost - self.mortgage.payment
        self.last_year = self.base_year

    def cost(self, year, inflation):

        if year > self.last_year:
            self.upkeep *= inflation
            self.last_year = year
        return self.upkeep + (0 if self.mortgage.paid_off(year) else self.mortgage.payment)

    def sale_proceeds(self, year, tx, has_spouse):
        """Net proceeds; records any taxable gain in `tx`."""
        price = self.value * 0.95      # 5% selling costs
        exclusion = 500_000 if has_spouse else 250_000
        taxable = max(0, price - self.cost_basis - exclusion)
        kw = {"d_ord": taxable} if year - self.mortgage.start_year < 2 else {"d_gains": taxable}
        tax = tx.extra(**kw)
        tx.realize(**kw)
        return price - self.mortgage.balance(year) - tax


# ---------- Simulation class ----------
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
    pre_ret_income: float = 120_000
    post_ret_income: float = 0
    ss_income: float = 30_000                           # TODO: link to SSA calculator, think about entitlement cuts

    # Contributions
    pretax_contrib: float = 15_000
    roth_contrib: float = 7_000
    employer_match: float = 5_000
    contrib_annual_increase: float = 1.01

    # Expenses
    exp_working: float = 50_000                        # non-housing
    exp_retired: float = 50_000                        # non-housing
    housing_own: float = 30_000
    housing_rent: float = 24_000

    # Taxes
    state_tax: float = 0.05                             # flat state rate
    penalty: float = 0.10                               # early-withdrawal penalty

    # Optional events (None to disable)
    home_sale_year: Optional[int] = 2030
    new_home_year: Optional[int] = 2035
    new_home_cost: float = 600_000
    new_mortgage_rate: float = 0.06
    new_mortgage_years: int = 30

    def __post_init__(self):
        if self.home_sale_year and self.new_home_year:
            assert self.new_home_year > self.home_sale_year, "New home purchase year must be after home sale year."
        assert self.age_retire >= self.age_start, "Retirement age cannot be prior to start age."
        assert self.age_end >= self.age_retire, "End age cannot be prior to retirement age."


    def run(self, seed = None):
        """Simulate year by year with bootstrapped inflation, house appreciation and market growth.
        Returns a list of per-year result dicts."""
        rng = np.random.default_rng(seed)
        pf, home = deepcopy(self.portfolio), deepcopy(self.home)
        results = []
        T = self.age_end - self.age_start + 1

        # One sampled factor per simulated year; factor[t] is the change during year t
        macro = hist_macro()
        pick = rng.integers(len(macro), size=T)           # one shared year per simulated year
        infl_f, growth = macro[pick, 0], macro[pick, 1]
        apprec = rng.choice(hist_apprec(), T)
        cum_infl = np.r_[1.0, np.cumprod(infl_f)[:-1]]       # price level at start of year t (1.0 at t=0)
        cum_apprec = np.r_[1.0, np.cumprod(apprec)[:-1]]
        sim_failed = False

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
            infl = cum_infl[t]
            cg = self.contrib_annual_increase ** t
            working = age <= self.age_retire
            row = {"year": year, "age": age, **balances("start"),
                   "inflation": float(infl_f[t]), "cum_inflation": float(infl),
                   "home_apprec": float(apprec[t]), "market_growth": float(growth[t])}

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
            housing = home.cost(year, infl_f[max(t - 1, 0)]) if home else self.housing_rent * infl
            net = post_tax - housing - (self.exp_working if working else self.exp_retired) * infl
            row.update(pre_tax_income=tx.ordinary, post_tax_income=post_tax, net_income=net, failed=False, unmet=0.0)

            if net >= 0:
                pf.brokerage.deposit(net)
            else:
                unmet = pf.withdraw(-net, age, tx, self.penalty)
                if unmet > 1:
                    row.update(failed=True, unmet=unmet, **balances("end"), **house_status(year))
                    sim_failed = True
                    results.append(row)
                    break

            # Growth
            # adjust glidepath by percent of portfolio that should be held in equities by age using a relatively aggressive growth assumption
            pct_stock = np.clip((110 - age) / 100.0, 0.10, 1.0)
            pf.grow(pct_stock*growth[t] + (1-pct_stock)*RISK_FREE_GROWTH)
            if home:
                home.value *= apprec[t]

            # One-time house events (end-of-year values, as before)
            if home and year == self.home_sale_year:
                pf.brokerage.deposit(home.sale_proceeds(year, tx, self.has_spouse))
                home = None

            if home is None and year == self.new_home_year and pf.brokerage.balance > 0:
                cost = self.new_home_cost * cum_apprec[t]
                down = 0.2 * cost
                down -= pf.brokerage.sell(down, tx)              # down payment = what the brokerage could raise
                mortgage = Mortgage(cost - down, self.new_mortgage_rate, self.new_mortgage_years, year)
                home = Home(cost, cost, mortgage, mortgage.payment + 0.015 * cost, year)  # +1.5% insurance + taxes

            row.update(**balances("end"), **house_status(year))
            results.append(row)

        return results, sim_failed


# ---------- Streamlit Interface ----------
st.title("Monte Carlo Portfolio Simulator")
st.caption('Most FIRE (Financial Independence Retire Early) calculators tell you how much longer you need to work or how many '
    'assets you need to have to achieve success at a pre-determined level of certainty. For example, a common rule of thumb is to '
    'assume a safe withdrawal rate of 4% in order for a set of assets to support a lifestyle in perpetuity. This calculator takes '
    'a probabilistic approach to the question of FIRE readiness, and instead reports the likelihood of FIRE success based on several '
    'relevant user inputs. The ability to quickly toggle different inputs allows for comparison of success rates by changing one or '
    'two variables, and can thus be used to aid in more robust decision making.'
    '\n\nTo use the tool, change the constraints in the sidebar to the left and run the simulation. For more information on how this tool '
    'works under the hood, visit the [README](https://github.com/cb92/p-fire-success) at the github repo.')

# Initialize session state for simulation results
if "results_df" not in st.session_state:
    st.session_state.results_df = None
if "runs" not in st.session_state:
    st.session_state.runs = None

# Toggle for currency adjustment (runs seamlessly without re-executing simulation)
dollar_mode = st.radio(
    "Display Currency Mode",
    ["Real Dollars (Future)", "Today's Dollars (Inflation-Adjusted)"],
    horizontal=True
)
is_todays_dollars = dollar_mode == "Today's Dollars (Inflation-Adjusted)"

with st.sidebar:
    st.header("Simulation Parameters")

    st.subheader("Accounts & Home")
    roth_bal = st.number_input("Roth Balance ($)", value=60_000, step=5_000, help="Initial Roth IRA/401(k) balance")
    roth_basis = st.number_input("Roth Basis ($)", value=40_000, step=5_000, help="Initial Roth IRA/401(k) contribution basis")
    trad_bal = st.number_input("Traditional Balance ($)", value=100_000, step=5_000, help="Initial Traditional IRA/401(k) balance")
    brok_bal = st.number_input("Brokerage Balance ($)", value=50_000, step=5_000, help="Initial taxable brokerage balance")
    brok_basis = st.number_input("Brokerage Basis ($)", value=40_000, step=5_000, help="Initial taxable brokerage cost basis")
    
    has_home = st.checkbox("Owns Home", value=True, help="Whether you currently own your primary residence")
    if has_home:
        home_val = st.number_input("Home Value ($)", value=450_000, step=25_000, help="Current home market value")
        home_basis = st.number_input("Home Basis ($)", value=400_000, step=25_000, help="Purchase price/cost basis")
        mort_amt = st.number_input("Mortgage Amount ($)", value=320_000, step=10_000, help="Total original mortgage principal") 
        mort_rate = st.number_input("Mortgage Rate", value=0.065, step=0.001, format="%.5f", help="Annual interest rate decimal")
        mort_years = st.number_input("Mortgage Term (Years)", value=30, min_value=1, max_value=50, step=1, help="Original loan term in years")
        mort_start = st.number_input("Mortgage Start Year", value=2021, step=1, help="Year mortgage began")
        housing_own = st.number_input("Annual Home Cost ($)", value=30_000, step=2_500, help="Annual payment plus upkeep")

    st.subheader("Timeline & Ages")
    start_year = date.today().year # = st.number_input("Start Year", value=date.today().year, step=1, help="Base simulation start year") # don't allow user to change this
    age_start = st.slider("Start Age", 18, 100, 35, help="Starting age of person")
    age_retire = st.slider("Retire Age", age_start, 100, 55, help="Age when full retirement begins")
    age_end_pt = st.slider("Part-Time End Age", age_retire, 100, 65, help="Age when part-time income ends")
    age_ss = st.slider("Social Security Age", 62, 70, 67, help="Age Social Security benefit starts")
    age_end = st.slider("End Age", age_end_pt, 110, 99, help="Simulation end age")

    st.subheader("Household & Income")
    has_spouse = st.checkbox("Has Spouse", value=True, help="Check this box if you are planning with a spouse (affects tax treatment)")
    earners = st.slider("Earners", 1, 2, 1, help="Number of wage earners for Social Security wage base limit")
    pre_ret_income = st.number_input("Pre-Retirement Income ($)", value=120_000, step=5_000, help="Annual gross earned income")
    post_ret_income = st.number_input("Post-Retirement Income ($)", value=0, step=5_000, help="Annual gross post-retirement income until part-time end age")
    ss_income = st.number_input("Social Security Income ($)", value=30_000, step=2_500, help="Annual Social Security payout in base dollars. Check the [SSA Quick Calc](https://www.ssa.gov/OACT/quickcalc/) for an estimate.")

    st.subheader("Contributions & Expenses")
    pretax_contrib = st.number_input("Pre-tax Contribution ($)", value=15_000, step=1_000, help="Annual pre-tax 401(k) contribution")
    roth_contrib = st.number_input("Roth Contribution ($)", value=7_000, step=500, help="Annual Roth IRA contribution")
    employer_match = st.number_input("Employer Match ($)", value=5_000, step=500, help="Annual employer matching contribution")
    contrib_annual_increase = st.number_input("Contrib Growth Multiplier", value=1.01, step=0.005, format="%.3f", help="Annual growth factor on contributions")
    exp_working = st.number_input("Working Non-Housing Expenses ($)", value=50_000, step=2_500, help="Annual living expenses prior to retirement, not including housing")
    exp_retired = st.number_input("Retired Non-Housing Expenses ($)", value=50_000, step=2_500, help="Annual living expenses during retirement, not including housing")
    housing_rent = st.number_input("Rent Cost ($)", value=24_000, step=1_000, help="Annual rent when not owning a home")

    st.subheader("Taxes & Events")
    state_tax = st.number_input("State Tax Rate", value=0.05, step=0.005, format="%.3f", help="Flat state income tax rate")
    penalty = st.number_input("Early Withdrawal Penalty Rate", value=0.10, step=0.01, help="Penalty on early traditional/Roth withdrawals (10 pct is standard but you can model something else)")
    
    enable_events = st.checkbox("Enable Home Real Estate Events", value=True, help="Enable home sale and repurchase events")
    if enable_events:
        home_sale_year = st.number_input("Home Sale Year", value=2030, step=1, help="Year current home is sold")
        new_home_year = st.number_input("New Home Purchase Year", value=2035, step=1, help="Year new home is purchased")
        new_home_cost = st.number_input("New Home Cost ($)", value=600_000, step=25_000, help="New home price estimate in current year dollars")
        new_mortgage_rate = st.number_input("New Mortgage Rate", value=0.06, step=0.005, help="Interest rate for new mortgage")
        new_mortgage_years = st.number_input("New Mortgage Term (Years)", value=30, step=1, help="Term of new mortgage")
    else:
        home_sale_year, new_home_year = None, None
        new_home_cost, new_mortgage_rate, new_mortgage_years = 600_000, 0.06, 30

    run_button = st.button("Run Simulation", type="primary", use_container_width=True)
    st.caption('Once this button is pressed, it will take a few seconds to produce outputs while 500 different simulations '
        'under different market conditions are run. Please be patient!')

if run_button:
    try:
        portfolio = Portfolio(Roth(roth_bal, roth_basis), Traditional(trad_bal), Brokerage(brok_bal, brok_basis))
        home = Home(home_val, home_basis, Mortgage(mort_amt, mort_rate, mort_years, mort_start), housing_own, start_year) if has_home else None

        sim = SimulationRun(
            portfolio=portfolio, home=home, start_year=start_year, age_start=age_start,
            age_retire=age_retire, age_end_pt=age_end_pt, age_ss=age_ss, age_end=age_end,
            has_spouse=has_spouse, earners=earners, pre_ret_income=pre_ret_income,
            post_ret_income=post_ret_income, ss_income=ss_income, pretax_contrib=pretax_contrib,
            roth_contrib=roth_contrib, employer_match=employer_match,
            contrib_annual_increase=contrib_annual_increase, exp_working=exp_working,
            exp_retired=exp_retired, housing_rent=housing_rent, state_tax=state_tax,
            penalty=penalty, home_sale_year=home_sale_year, new_home_year=new_home_year,
            new_home_cost=new_home_cost, new_mortgage_rate=new_mortgage_rate,
            new_mortgage_years=new_mortgage_years
        )

        runs = []
        for i in range(500):
            res_df, _ = sim.run()
            df = pd.DataFrame(res_df)
            df["end_portfolio_value"] = df["end_brokerage"] + df["end_traditional"] + df["end_roth"]
            df["home_equity"] = df["home_value"].fillna(0.0) - df["mortgage_balance"].fillna(0.0)
            df["debt"] = -df["mortgage_balance"].fillna(0.0)
            df["net_worth"] = df["end_portfolio_value"] + df["home_equity"]
            df["run_id"] = f"Run {i + 1}"
            runs.append(df)

        st.session_state.results_df = pd.concat(runs, ignore_index=True)
        st.session_state.runs = runs
    except Exception as e:
        st.error(f"Simulation Error: {e}")

if st.session_state.results_df is not None:
    try:
        results_df = st.session_state.results_df.copy()
        runs = st.session_state.runs

        dollar_cols = ["end_portfolio_value", "net_worth", "home_equity", "debt", "end_roth", "end_traditional", "end_brokerage"]
        if is_todays_dollars and "cum_inflation" in results_df.columns:
            for col in dollar_cols:
                if col in results_df.columns:
                    results_df[col] = results_df[col] / results_df["cum_inflation"]

        # 1. Spaghetti Chart
        pivot_df = results_df.pivot(index="year", columns="run_id", values="end_portfolio_value")
        pivot_df.index = pivot_df.index.astype(str)
        pivot_df = pivot_df.sample(n=min(100, pivot_df.shape[1]), axis=1, random_state=42)
        pivot_df_reset = pivot_df.reset_index()
        melted_df = pivot_df_reset.melt(id_vars="year", var_name="run_id", value_name="end_portfolio_value")

        chart = (
            alt.Chart(melted_df)
            .mark_line(opacity=0.7, strokeWidth=1.5)
            .encode(
                x=alt.X("year:O", title="Year"),
                y=alt.Y(
                    "end_portfolio_value:Q",
                    title=f"End Portfolio Value ({'Today$' if is_todays_dollars else '$'})",
                    axis=alt.Axis(format="$~s")
                ),
                color=alt.Color("run_id:N", legend=None),
                tooltip=[
                    alt.Tooltip("year:O", title="Year"),
                    alt.Tooltip("run_id:N", title="Run ID"),
                    alt.Tooltip("end_portfolio_value:Q", title="Value", format="$~s")
                ]
            )
            .properties(height=400)
        )

        st.subheader(f"Simulated Portfolio End Value Over Time ({'Today’s Dollars' if is_todays_dollars else 'Real Dollars'})")
        st.altair_chart(chart, use_container_width=True)

        # 2. Failure analysis & Success Rate
        failure_years = []
        for df in runs:
            failed_rows = df[df["failed"] == True]
            if not failed_rows.empty:
                failure_years.append(failed_rows["year"].iloc[0])
            else:
                failure_years.append(None)

        success_count = sum(1 for f in failure_years if f is None)
        success_pct = (success_count / len(runs)) * 100
        st.metric(label="Success Rate (Reached end of plan with > $0)", value=f"{success_pct:.1f}%")

        failed_years_series = pd.Series([y for y in failure_years if y is not None])
        if not failed_years_series.empty:
            all_years_seq = range(start_year, start_year + (age_end - age_start + 1))
            fail_counts_by_year = failed_years_series.value_counts().reindex(all_years_seq, fill_value=0)
            cdf_series = (fail_counts_by_year.cumsum() / len(runs)) * 100
            cdf_series.index = cdf_series.index.astype(str)
            
            st.subheader("Cumulative Probability of Running Out of Money by Year (%)")
            st.line_chart(cdf_series)
        else:
            st.info("No simulation runs ran out of money before the plan ended.")

        # 3. Component Charts
        components = ["net_worth", "home_equity", "debt", "end_portfolio_value", "end_roth", "end_traditional", "end_brokerage"]
        component_labels = {
            "end_portfolio_value": "Total Equities Portfolio Value",
            "net_worth": "Net Worth (including debt)",
            "home_equity": "Home Equity",
            "debt": "Mortgage Debt",
            "end_roth": "Roth Balance",
            "end_traditional": "Non-Roth (Traditional) Balance",
            "end_brokerage": "Taxable Brokerage Balance"
        }
        
        component_descriptions = {
            "end_portfolio_value": "Total brokerage portfolio value, including retirement and non-retirement",
            "net_worth": "Net worth (portfolio plus home equity less debt)",
            "home_equity": "Difference between estimated home value and remaining mortgage principal.",
            "debt": "Remaining unpaid principal balance on mortgage (shown as negative).",
            "end_roth": "Tax-free growth account balance (Roth IRA / Roth 401(k)).",
            "end_traditional": "Pre-tax account balance (Traditional IRA / 401(k)).",
            "end_brokerage": "Standard taxable investment brokerage account balance."
        }

        chart_rows = []
        for comp in components:
            comp_pivot = results_df.pivot(index="year", columns="run_id", values=comp).fillna(0.0)
            median_series = comp_pivot.quantile(0.5, axis=1)
            p25_series = comp_pivot.quantile(0.25, axis=1)
            p75_series = comp_pivot.quantile(0.75, axis=1)

            for yr in comp_pivot.index:
                chart_rows.append({
                    "year": int(yr),
                    "component_key": comp,
                    "median": median_series.loc[yr],
                    "p25": p25_series.loc[yr],
                    "p75": p75_series.loc[yr]
                })

        df_altair = pd.DataFrame(chart_rows)

        colors = {
            "end_portfolio_value": "#fbbf24",
            "net_worth": "#0ea5e9",
            "home_equity": "#059669",
            "debt": "#059669",
            "end_roth": "#059669",
            "end_traditional": "#059669",
            "end_brokerage": "#059669"
        }

        def render_component_chart(comp):
            title_text = f"{component_labels[comp]}: Median & 25th–75th Percentile Band"   
            desc_text = component_descriptions[comp]
            comp_df = df_altair[df_altair["component_key"] == comp]

            band = alt.Chart(comp_df).mark_area(opacity=0.25).encode(
                x=alt.X("year:O", title="Year"),
                y=alt.Y("p25:Q", title="Value ($)", axis=alt.Axis(format="$~s")),
                y2="p75:Q",
                color=alt.value(colors[comp]),
                tooltip=[
                    alt.Tooltip("year:O", title="Year"),
                    alt.Tooltip("median:Q", title="Median", format="$~s"),
                    alt.Tooltip("p25:Q", title="25th Percentile", format="$~s"),
                    alt.Tooltip("p75:Q", title="75th Percentile", format="$~s")
                ]
            )

            line = alt.Chart(comp_df).mark_line(strokeWidth=2.5).encode(
                x=alt.X("year:O", title="Year"),
                y=alt.Y("median:Q", title="Value ($)", axis=alt.Axis(format="$~s")),
                color=alt.value(colors[comp]),
                tooltip=[
                    alt.Tooltip("year:O", title="Year"),
                    alt.Tooltip("median:Q", title="Median", format="$~s"),
                    alt.Tooltip("p25:Q", title="25th Percentile", format="$~s"),
                    alt.Tooltip("p75:Q", title="75th Percentile", format="$~s")
                ]
            )

            chart = (band + line).properties(height=280)

            st.markdown(f"#### {title_text}")
            st.caption(desc_text)
            st.altair_chart(chart, use_container_width=True)

        render_component_chart("net_worth")

        col1, col2 = st.columns(2)
        with col1:
            render_component_chart("home_equity")
        with col2:
            render_component_chart("debt")

        render_component_chart("end_portfolio_value")

        col3, col4, col5 = st.columns(3)
        with col3:
            render_component_chart("end_roth")
        with col4:
            render_component_chart("end_traditional")
        with col5:
            render_component_chart("end_brokerage")

    except Exception as e:
        st.error(f"Render Error: {e}")

st.caption(
'_Important notes: \n'
'This is a financial planning tool and does not constitute personalized investment, tax, or legal advice. '
'The presence of this application does not imply a certain level of skill or training and the results '
'are not guarantees of future performance. This model relies on historical data and assumed return distributions that may not reflect actual future returns. '
'Always consult a qualified financial or tax professional before making material financial decisions._'
)
