# p-fire-success


# Monte Carlo Portfolio Simulator

## Motivation
Most FIRE (Financial Independence Retire Early) calculators just tell you how much longer you need to work or how many assets you need to have to achieve success at a pre-determined level of certainty. For example, a common rule of thumb is to assume a safe withdrawal rate of 4% in order for a set of assets to support a lifestyle in perpetuity. This calculator takes a probabilistic approach to the question of FIRE readiness, and instead reports the likelihood of FIRE success based on several relevant user inputs. The ability to quickly toggle different inputs allows for comparison of success rates by changing one or two variables, and can thus be used to aid in more robust decision making. 

## Overview
This tool runs Monte Carlo (MC) simulations to project lifetime financial trajectories, portfolio longevity, and failure risks across retirement. 

### Why Monte Carlo?
Deterministic models assume fixed average returns (e.g., 7% annually), ignoring sequence-of-returns risk (the probability that market drawdowns early in retirement deplete capital permanently). Monte Carlo simulation addresses this by randomly sampling historical market regimes over thousands of iterations to produce probabilistic outcomes.

### Data Sampling & Assumptions
- **Joint Sampling (Stocks & Inflation):** Inflation rate and equity returns (average of Dow Jones and S&P 500) are drawn together as paired annual observations from historical data since 1928/1930. Preserving this joint distribution maintains the empirical correlation between inflation and stock performance, which drives withdrawal risk.
- **Independent Sampling (Housing):** House price appreciation is sampled independently from Case-Shiller/HPI history. This preserves full historical depth for housing while forfeiting only the secondary link between home values and broader market/inflation metrics. While less than ideal, this is a required simplification due to the shorter data availability window for the Case-Shiller index relative to stock returns and inflation measures.
- **Execution Order & Intra-Year Timing:** Within each simulated year, operations occur sequentially: 
1. Tax-adjusted income is collected
2. Retirement contributions are made
3. Living expenses and housing costs are paid
4. Any shortfalls trigger withdrawals (brokerage first, then tax-advantaged accounts)
5. Equities account returns are applied
6. Any real estate transactions are settled
While these cash flows occur continuously in reality, sequential discrete simulation introduces at worst a negligible off-by-one timing effect that does not materially alter long-term distributions.

### How the Math & Engine Work
1. **Tax Stacking:** Federal tax brackets, standard deductions, and capital gains thresholds adjust annually for cumulative simulated inflation. Withdrawals and gains stack on top of base income to calculate exact progressive income tax, FICA, state tax, and early withdrawal penalties.
2. **Withdrawal Hierarchy:** Cash shortfalls draw first from taxable Brokerage accounts (pro-rata capital gains vs. basis), then tax-free Roth accounts (basis tax-free, earnings taxed/penalized if under age 59.5), and finally Traditional pre-tax accounts.
3. **Solver Logic:** For taxable or penalized accounts, a binary search solver calculates the exact gross withdrawal necessary to yield the target net cash after marginal tax impact.
4. **Mortgages & Real Estate:** Amortization uses standard monthly compounding math. Selling a primary residence applies single ($250k) or joint ($500k) Section 121 capital gains tax exclusions.

## Input Data & Sources

Historical datasets powering the bootstrap simulation are stored in `data/`:

- **Inflation Rates:** Annual CPI percentage change ([Macrotrends Historical Inflation Rate](https://www.macrotrends.net/2497/historical-inflation-rate-by-year))
- **US House Price Index:** Annualized national real estate index ([Macrotrends House Price Index](https://www.macrotrends.net/3072/us-house-price-index))
- **Dow Jones Industrial Average:** Annual historical returns ([Macrotrends Dow Jones 100-Year Chart](https://www.macrotrends.net/1319/dow-jones-100-year-historical-chart))
- **S&P 500:** Annual historical returns ([Macrotrends S&P 500 Historical Returns](https://www.macrotrends.net/2526/sp-500-historical-annual-returns))

## Outputs

The app runs 1,000 iterations per simulation and provides full distributional insights:

- **Success Rate:** Overall percentage of paths where total liquid wealth remains above $0 through the end modeled age.
- **Cumulative Failure Probability:** A CDF curve tracking the probability of portfolio depletion over time.
- **Asset Class & Debt Trajectories:** Breakdown charts plotting the median path alongside 25th–75th percentile range bands across time for:
  - Taxable Brokerage Balance
  - Non-Roth (Traditional Pre-Tax) Balance
  - Roth Balance
  - Home Equity
  - Remaining Mortgage Debt

## What Isn't Modeled / When This Tool Is Not For You

This tool models a constant equity allocation across the entire simulation timeframe rather than a dynamic glidepath (e.g., shifting toward fixed income near retirement). Because Sequence-of-Returns Risk (SoRR) is most critical during the early decumulation years just after retirement - where severe drawdowns combined with ongoing withdrawals can prematurely exhaust a portfolio - holding a fixed equity exposure through retirement may understate tail-risk failures in adverse scenarios. Conversely, for successful paths, maintaining full equity exposure throughout late retirement yields overly aggressive terminal portfolio values, which should be discounted accordingly. As always, this tool does NOT constitute financial advice or guidance and should be used with a full understanding of the reality of its inner workings. 

This tool favors streamlined, robust core mechanics over hyper-complex edge cases. It may not fit your needs if you:
- Own or manage multiple residential or commercial real estate properties simultaneously.
- Have highly complex income streams, equity compensation, structured trusts, or active business pass-through holdings.
- Plan around significant, non-standard inheritance, estate tax structures, or variable multi-state tax rules.

## Setup and Use

### Prerequisites
Python 3.9+ with `pip`.

### Installation
1. Clone the repository:
   ```
   git clone <repository-url>
   cd <repository-folder>
   ```
2. Install dependencies:
   ```
   pip install streamlit pandas numpy altair
   ```

### Running the App
Run the Streamlit application from the root directory:
```
streamlit run setup.py
```
Adjust your financial parameters, account balances, and real estate events in the sidebar, then click **Run Simulation**.