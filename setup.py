
from dataclasses import dataclass
from datetime import date

# ---------- Utility Functions ---------
def calculate_federal_income_tax(taxable_income: float, joint: bool) -> float:
    if taxable_income = 0:
        return 0

    if joint: 
        taxable_income = taxable_income - 32000
    else 
        taxable_income = taxable_income - 16100

    """Calculates US federal income tax using sample progressive brackets."""
    if joint: 
        brackets = [
            (0, 0.10),
            (23850, 0.12),
            (96950, 0.22),
            (206700, 0.24),
            (394600, 0.32),
            (501050, 0.35),
            (751600, 0.37),
        ]
    else: 
        brackets = [
            (0, 0.10),
            (11925, 0.12),
            (48475, 0.22),
            (103350, 0.24),
            (197300, 0.32),
            (250525, 0.35),
            (626350, 0.37),
        ]


    tax = 0.0

    for i, (threshold, rate) in enumerate(brackets):
        if taxable_income > threshold:
            # Determine the amount of income falling into this bracket
            if i + 1 < len(brackets):
                next_threshold = brackets[i + 1][0]
                income_in_bracket = min(taxable_income, next_threshold) - threshold
            else:
                income_in_bracket = taxable_income - threshold
            tax += income_in_bracket * rate

    return tax/taxable_income

def calculate_ltcg(taxable_income: float, joint: bool):
    if joint: 
        b0, b1 = (98900, 496600)
    else: 
        b0, b1 = (49450, 441450)
        
    return 0.0 if taxable_income <= b0 else 0.15 if taxable_income <= b1 else 0.20


def monthly_pmt(principal, rate, num_years, pmts_per_year = 12):
    total_pmts = 1.0 * num_years * pmts_per_year
    return principal*(rate/pmts_per_year)*((1+rate/pmts_per_year)**total_pmts)/((1+rate/pmts_per_year)**total_pmts - 1)


def remaining_balance(principal, rate, num_years, years_since_start, pmts_per_year = 12):
    if years_since_start > num_years: 
        return 0
    pmt = monthly_pmt(principal, rate, num_years, pmts_per_year)
    pmts_made = years_since_start*pmts_per_year
    remaining_balance = principal * (1+rate/pmts_per_year)**pmts_made - pmt *  ((1+rate/pmts_per_year)**pmts_made - 1) / (rate/pmts_per_year)
    return remaining_balance

# ---------- Parameters ----------
START_YEAR = date.today().year
# TODO: lower bound age start by 30, retire by age_start, end pt income by retire, SS by 65 and end plan by retire
AGE_START, AGE_RETIRE, AGE_END_PT, AGE_SS, AGE_END = 35, 40, 55, 67, 99
HAS_SPOUSE = True
PRE_RET_INCOME, POST_RET_INCOME, SS_INCOME = 210_000, 0, 50_000 # TODO: link to SSA calculator for payment, prompt to think about entitlement cuts
# TODO: error if contributions are infeasible (ie. above IRS limits or total income)
PRETAX_CONTRIB, ROTH_CONTRIB, EMPLOYER_MATCH = 0, 8_000, 8_000
CONTRIB_ANNUAL_INCREASE = 1.01
EXP_WORKING, EXP_RETIRED = 100_000, 100_000 #non-housing expenses
HOUSING_OWN, HOUSING_RENT = 90_000, 60_000 #housing expenses
STATE, ORD, LTCG, PENALTY = 0.05, 0.25, 0.15, 0.10   # tax rates / early-withdrawal penalty
GROWTH, INFLATION, HOME_APPREC = 1.10, 1.04, 1.04
HOME_SALE_YEAR = 2028                                 # None to disable
NEW_HOME_YEAR, NEW_HOME_COST = 2035, 1_300_000        # None to disable
NEW_MORTGAGE_RATE, NEW_MORTGAGE_YEARS = 0.06, 30


class Account:
    def __init__(self, balance, basis=0):
        self.balance, self.basis = balance, basis

    def deposit(self, amt):
        self.balance += amt
        self.basis += amt

class Brokerage(Account):
    def net_available(self, ltcg_rate = LTCG):
        return self.basis + (self.balance - self.basis) * (1 - ltcg_rate - STATE)
        # not quite right, might be STCG, but keeping for now

    def sell(self, net, ltcg_rate = LTCG):
        """Raise `net` after-tax dollars, drawing basis and gains proportionally.
        Returns the amount still unmet."""
        avail = self.net_available(ltcg_rate)
        p = min(1, net / avail) if avail > 0 else 1
        self.balance *= 1 - p
        self.basis *= 1 - p
        return max(0, net - avail)


class Roth(Account):
    def withdraw(self, need, age, ltcg_rate = LTCG):
        """Returns the amount still unmet."""
        if age > 59:
            used = min(need, self.balance)
            self.balance -= used
            self.basis = min(self.basis, self.balance)
            return need - used
        k = (1 - PENALTY) * (1 - ltcg_rate - STATE)        # net per $ of early-withdrawn earnings
        avail = self.basis + k * (self.balance - self.basis)
        if need <= self.basis:                         # basis comes out tax/penalty free
            self.balance -= need
            self.basis -= need
            return 0
        if need < avail:
            self.balance -= self.basis + (need - self.basis) / k
            self.basis = 0
            return 0
        self.balance = self.basis = 0
        return need - avail


class Traditional(Account):
    def withdraw(self, need, age, ord_tax = ORD):
        gross = need / (1 - ord_tax - STATE) / (1 if age > 59 else 1 - PENALTY)
        self.balance = max(0, self.balance - gross)

@dataclass
class Portfolio:
    roth: Roth
    trad: Traditional
    brokerage: Brokerage

    def withdraw(self, need, age, ord_tax = ORD, ltcg_rate = LTCG):
        """Brokerage first, then Roth, then traditional."""
        need = self.brokerage.sell(need, ltcg_rate)
        if need > 0:
            need = self.roth.withdraw(need, age, ltcg_rate)
        if need > 0:
            self.trad.withdraw(need, age, ord_tax)

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
    annual_cost: float                 # total housing cost while the mortgage is active
    base_year: int

    def cost(self, year):
        upkeep = (self.annual_cost - self.mortgage.payment) * INFLATION ** (year - self.base_year)
        return upkeep + (0 if self.mortgage.paid_off(year) else self.mortgage.payment)

    def sale_proceeds(self, year, ord_tax = ORD, ltcg_rate = LTCG):
        price = self.value * 0.95      # 5% selling costs
        exclusion = 500_000 if HAS_SPOUSE else 250_000
        taxable = max(0, price - self.cost_basis - exclusion)
        rate = (ord_tax if year - self.mortgage.start_year < 2 else ltcg_rate) + STATE
        return price - self.mortgage.balance(year) - rate * taxable


# ---------- Simulation ----------
pf = Portfolio(Roth(900_000, 500_000), Traditional(100_000), Brokerage(700_000, 500_000))
home = Home(1_900_000, 1_600_000, Mortgage(750_000, 0.05375, 30, 2023), HOUSING_OWN, START_YEAR)  # None if renting

for t in range(AGE_END - AGE_START + 1):
    year, age = START_YEAR + t, AGE_START + t
    infl = INFLATION ** t
    contrib_growth = CONTRIB_ANNUAL_INCREASE ** t 
    working = age <= AGE_RETIRE
    print(f"===== {year} (age {age}) =====\nStart: {pf}")

    # Income
    if working:
        pre_tax = PRE_RET_INCOME * infl - PRETAX_CONTRIB*contrib_growth
        pf.trad.deposit(PRETAX_CONTRIB*contrib_growth + EMPLOYER_MATCH*contrib_growth)
    else:
        pre_tax = POST_RET_INCOME * infl if age <= AGE_END_PT else 0
    if age >= AGE_SS:
        pre_tax += SS_INCOME * infl # assume this keeps pace with inflation, because it generally does through CoLA
    fed_income_tax = calculate_federal_income_tax(pre_tax, HAS_SPOUSE)
    long_term_tax = calculate_ltcg(pre_tax, HAS_SPOUSE)
    post_tax = (1 - fed_income_tax - STATE) * pre_tax
    if working:
        pf.roth.deposit(ROTH_CONTRIB*contrib_growth)
        post_tax -= ROTH_CONTRIB*contrib_growth

    # Net income -> brokerage surplus or drawdown
    housing = home.cost(year) if home else HOUSING_RENT * infl
    net = post_tax - housing - (EXP_WORKING if working else EXP_RETIRED) * infl
    print(f"Pre-tax {pre_tax:,.0f} | Post-tax {post_tax:,.0f} | Net {net:,.0f}")
    if net >= 0:
        pf.brokerage.deposit(net)
    else:
        pf.withdraw(-net, age, fed_income_tax, long_term_tax)

    # Growth
    pf.grow(GROWTH)
    if home:
        home.value *= HOME_APPREC

    # One-time house events (use end-of-year inflated values, as before)
    if home and year == HOME_SALE_YEAR:
        pf.brokerage.deposit(home.sale_proceeds(year, fed_income_tax, long_term_tax))
        home = None

    if home == None and year == NEW_HOME_YEAR and pf.brokerage.balance > 0:     # no brokerage -> no purchase
        cost = NEW_HOME_COST * HOME_APPREC ** t
        down = min(0.2 * cost, pf.brokerage.net_available(long_term_tax))
        pf.brokerage.sell(down, long_term_tax)
        mortgage = Mortgage(cost - down, NEW_MORTGAGE_RATE, NEW_MORTGAGE_YEARS, year)
        home = Home(cost, cost, mortgage, mortgage.payment + 0.015 * cost, year)  # +1.5% insurance + taxes

    print(f"End:   {pf}")