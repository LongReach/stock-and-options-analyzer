# Machine Learning for ETF Trading — Notes

## Problem Setup

- **Target assets**: Major index ETFs (SPY, QQQ, IWM, DIA, etc.)
- **Input data**: Daily OHLCV bars + technical indicators (moving averages, etc.)
- **Task**: Predict profitable trade signals

### Target Variable Options
- **Binary classification**: will close be higher N days from now? (recommended starting point)
- **Regression**: predict the N-day forward return

Binary classification tends to be more stable and easier to threshold into a trade signal.

---

## Recommended Model: Gradient Boosting

**XGBoost** or **LightGBM** are the best fit for this use case:
- Consistently outperforms other approaches on tabular/time-series data
- Trains in seconds on ~8,000 rows (SPY since 1993)
- Handles non-linear relationships without heavy manual feature engineering
- LightGBM is particularly fast on older hardware

Random Forest is a good baseline to compare against first.

### Suggested Stack

| Purpose | Library |
|---|---|
| Model | `lightgbm` or `xgboost` |
| Indicators | `pandas-ta` |
| Validation | `sklearn.model_selection.TimeSeriesSplit` |
| Data | IB pipeline or `yfinance` |

---

## Feature Engineering

- **Use returns and ratios, not raw prices** — raw prices are non-stationary
- Keep feature count modest (10–20) relative to ~8,000 rows to avoid overfitting
- Good starting features:
  - Daily return (close-to-close)
  - High-low range / close (normalized volatility)
  - Volume ratio (today vs. N-day average)
  - Distance from moving averages (e.g., `close / MA50 - 1`)
  - MA crossover signals (5/20, 10/50, 50/200)

---

## Critical Pitfall: Validation

**Do not use random train/test splits.** Always use **walk-forward validation** —
train on the past, test on the future. Use `sklearn.model_selection.TimeSeriesSplit`.

A model that sees future data during training will appear profitable and fail completely live.

---

## On LSTMs / Neural Networks

Daily bars for a single ETF (~8,000 rows) are insufficient. The problem is
**independent samples**: with a 50-day lookback, you get ~8,000 overlapping
sequences but only ~160 truly independent ones.

LSTMs need tens of thousands of independent sequences to generalize.

### Ways to get enough data for LSTMs

**Option 1 — Higher-frequency bars:**

| Bar Size | ~Years of Data | Approx. Rows |
|---|---|---|
| 1-day | 30 years | 8,000 |
| 1-hour | 10 years | 25,000 |
| 5-min | 10 years | 500,000+ |
| 1-min | 5 years | 600,000+ |

5-minute bars going back several years is viable. IB can fetch this via repeated
historical data requests.

**Option 2 — Train across many assets (recommended):**
Train one model on SPY + QQQ + IWM + DIA + sector ETFs + large-caps.
50 assets × 8,000 daily bars = 400,000 rows. The model learns general market
patterns rather than SPY-specific ones. This is a legitimate and common approach.

**Option 3 — Both:** many bars × many assets is most robust, but more data
engineering work.

### Hardware note
LSTMs on 500k+ rows without a modern GPU can take hours per training run,
making hyperparameter tuning painful. Gradient boosting trains in seconds on
the same data and is competitive on tabular financial data.

---

## Realistic Expectations

- Daily bar models on liquid ETFs like SPY are hard to beat — these are among
  the most efficiently priced instruments in the world
- Any edge tends to be small and regime-dependent
- A model trained on 1993–2010 may behave differently post-2020
- Always walk-forward test across multiple market regimes (bull, bear, high-vol)
  for an honest picture
