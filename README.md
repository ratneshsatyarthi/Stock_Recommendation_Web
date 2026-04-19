# Stock Recommendation System

A Flask‑based web dashboard that helps you identify and rank stock opportunities using delivery data, trade volume, price action, and repeated appearances across days.

You upload NSE CSV reports, the backend cleans and scores each symbol, and the frontend shows Top Opportunities, Pre‑Breakout setups, and a full recommendation table.

---

## 1. Features

### 1.1 Data Ingestion & Cleaning

- Upload one or more CSV files via the UI (or drop them directly into the `data/` folder).
- Robust CSV parsing:
  - Extracts report date from the header using a regex.
  - Reads data after skipping a fixed number of header rows.
  - Normalizes column names to a consistent internal schema.
- Cleans and coerces numeric fields (delivery, trade, change %) to numeric types, falling back to `0` when invalid.

### 1.2 Derived Metrics

For each row (symbol, date), the backend computes:

- `Delv x` – Delivered quantity vs average delivered quantity:  
  `Delv x = Delivered Qty / (Avg Delv Qty + 1)`
- `Trade x` – Traded quantity vs average traded quantity:  
  `Trade x = Traded Qty / (Avg Traded Qty + 1)`
- Delivery ratio – Balance between delivery and trading:  
  `Delivery ratio = Delv x / (Trade x + 0.1)`

These metrics are later aggregated per symbol and used by the scoring engine.

### 1.3 Scoring Engine

The core scoring function aggregates per‑symbol history and produces:

- Composite score per symbol (higher = stronger conviction).
- Signal (qualitative label):
  - Strong accumulation
  - Breakout candidate
  - Accumulation phase
  - Distribution risk
  - Neutral
- Recommendation, based on score thresholds:
  - Strong Buy: score ≥ 85
  - Accumulate: score ≥ 65
  - Watch: score ≥ 45
  - Avoid: otherwise
- Reasons (comma‑separated) explaining the score, e.g.:
  - Delivery > Trade (Accumulation)
  - Repeated 3 days
  - Rising delivery trend
  - Positive momentum
  - Delivery spike
- Action (plain‑English guidance):
  - Enter early, accumulation confirmed
  - Start building position
  - Wait for breakout confirmation
  - Avoid for now

Signals and scores are driven by:

- Delivery spike vs symbol’s own history.
- Trade spike vs symbol’s own history.
- Delivery ratio thresholds.
- Price change (strong positive, mild positive, strong negative).
- Repeat Count (number of distinct trading days seen).
- Delivery trend (monotonic increasing vs not).

### 1.4 Dashboard & Visualization

Rendered via Flask + Jinja2 as `dashboard.html`:

- Theme toggle:
  - Dark / Light mode using CSS custom properties and `data-theme` on `<html>`.
  - Persisted in `localStorage` (`srs-theme`).
- Header:
  - Project title, short description, and “Live scan” meta info.
- KPI row:
  - Total Stocks
  - Avg Score
  - Strong Buy count
  - Watchlist count
- Upload area:
  - Upload multiple CSV files (`/upload`).
  - Clear all data (`/clear-uploads`).
- Top Opportunities panel:
  - Cards for the top 5 Strong Buy / Accumulate names by score.
  - Shows Stock, Symbol, Sector, Score, Delv x, Trade x, Recommendation, Signal.
- Signal Rules panel:
  - Human‑readable description of the scoring model, Top Opportunities, and Pre‑Breakout logic (kept in sync with backend).
- Pre‑Breakout Opportunities panel:
  - Cards for early watchlist candidates (Watch or Breakout candidate) with a Pre‑breakout action.
- Full Recommendation Table:
  - Scrollable table with sticky header.
  - Columns: Date, Symbol, Name, Sector, Signal, Delv x, Trade x, Delivery ratio, Chg %, Appearances, Score.
- Client‑side filters (vanilla JS):
  - Text search input (`#search`) filters rows on any text match.
  - Recommendation dropdown (`#rec`) filters by Strong Buy / Accumulate / Watch / Avoid.

---

## 2. Project Structure

A minimal structure can look like this:

```text
project-root/
├─ app.py                 # Main Flask application (routes + scoring logic)
├─ data/                  # CSV files for analysis (input)
├─ uploads/               # Reserved for uploads (currently unused)
└─ templates/
   └─ dashboard.html      # Main dashboard template (HTML + Jinja2)
```

You can expand this into a more modular Flask structure later (e.g., `app/`, `services/`, `blueprints/`, `static/`), but this README describes the current simple layout.

---

## 3. Installation

### 3.1 Prerequisites

- Python 3.9+ (recommended)
- pip (Python package manager)

### 3.2 Clone & Setup

```bash
# Clone your repository (example)
git clone https://github.com/your-user/stock-reco-system.git
cd stock-reco-system
```

### 3.3 Create and Activate Virtual Environment

```bash
# Create virtual environment
python -m venv .venv

# Activate on Windows
.venv\Scripts\activate

# Activate on macOS / Linux
source .venv/bin/activate
```

### 3.4 Install Dependencies

Create a `requirements.txt` similar to:

```text
Flask
pandas
Werkzeug
```

Then install:

```bash
pip install -r requirements.txt
```

---

## 4. Running the App

From the project root (with your virtual environment activated):

```bash
python app.py
```

By default, Flask runs in debug mode on:

```text
http://127.0.0.1:5000/
```

Open that URL in your browser to view the dashboard.

---

## 5. Expected CSV Format

The backend currently expects CSVs in a format similar to NSE reports, with:

1. Header region (first few lines) including a line with a date, e.g.:  
   `Report for 12-Jan-2025`  
   A regex extracts dates of the form `DD-MMM-YYYY`.
2. Data header row after 5 lines of header, so the data is read with `skiprows=5`.

### 5.1 Required Columns

The code renames and expects these logical columns after normalization:

- `Stock`: stock name (from `Stock Name`).
- `Symbol`: stock symbol (from `Symbol`).
- `Sector`: sector name (from `Sector Name`).
- `Delivered Qty`: delivered quantity.
- `Avg Delv Qty`: average delivered quantity.
- `Traded Qty`: traded quantity.
- `Avg Traded Qty`: average traded quantity.
- `Chg %`: daily price change percentage.

If any required column is missing, it is created with value `0` so the pipeline still runs, but some metrics may be less meaningful.

### 5.2 Column Renaming Logic

Internally, this mapping is applied:

```python
df.rename(columns={
    'Stock Name': 'Stock',
    'Symbol': 'Symbol',
    'Sector Name': 'Sector',
    'Delivered Qty': 'Delivered Qty',
    'Avg Delv Qty': 'Avg Delv Qty',
    'Traded Qty': 'Traded Qty',
    'Avg Traded Qty': 'Avg Traded Qty',
    'Chg %': 'Chg %'
}, inplace=True)
```

If your CSV headers differ (e.g., `Delivery Quantity` instead of `Delivered Qty`), update this mapping accordingly.

### 5.3 Numeric Conversion Rules

The following columns are converted to numeric:

- `Delivered Qty`
- `Avg Delv Qty`
- `Traded Qty`
- `Avg Traded Qty`
- `Chg %`

Rules:

- Non‑numeric values are coerced to NaN, then filled with 0.
- This makes the system robust to messy CSVs, at the cost of sometimes treating bad data as 0 instead of dropping rows.

---

## 6. How the Scoring Works (High Level)

For each symbol:

1. History aggregation:
   - Load all rows for that symbol across multiple days.
   - Sort by Date, use the latest row as the current snapshot.
   - Compute:
     - `Delv x`, `Trade x`, `Chg %` (latest).
     - `avg_delv`, `avg_trade`.
     - `delivery_ratio = Delv x / (Trade x + 0.1)`.
     - `repeat_count` = number of distinct days seen.
     - `trend_up` = `Delv x` is monotonic increasing over time.

2. Score components (weights are adjustable in `score_df`):
   - Higher `Delv x` and `delv_spike` (vs average).
   - Higher `Trade x` and `trade_spike` (vs average).
   - Delivery ratio thresholds > 1 / > 0.7.
   - Price change:
     - `Chg % > 3` → strong positive.
     - `Chg % > 0` → mildly positive.
     - `Chg % < -3` → strong negative penalty.
   - Repeat Count:
     - 2, 3, 4+ days get increasing bonus.
   - Upward trend in delivery.

3. Signal & Recommendation:
   - Signal is based on combinations of ratio, spikes, repeat, price change.
   - Recommendation is a direct mapping from final score into four levels.

4. Final output per symbol:
   - Stock, Symbol, Sector.
   - Date (latest, as `YYYY-MM-DD`).
   - Score, Recommendation, Signal.
   - Delv x, Trade x, Chg %, Delivery ratio, Appearances (= Repeat Count).
   - Reasons (text) + Action (actionable suggestion).

---

## 7. Dashboard Views

### 7.1 Top Opportunities

- Uses `top5(df)` helper.
- Filters for Strong Buy and Accumulate.
- Ranks by Score and takes the top 5.
- Shown as cards in the Top Opportunities panel.

### 7.2 Pre‑Breakout Opportunities

- Uses `prebreakout(df)` helper.
- Filters for:
  - `Recommendation == "Watch"`, or
  - `Signal == "Breakout candidate"`.
- Excludes any symbol already present in top 5 Strong Buy / Accumulate.
- Takes the top 5 by score.
- Shown as cards with Pre‑breakout action text.

### 7.3 Repeated Symbols

- Uses `repeated(df)` helper.
- Aggregates by `Symbol, Stock, Sector`.
- Computes:
  - `Avg_Score`, `Max_Score`, `Appearances`, `Avg_Delv`, `Avg_Trade`.
- Keeps symbols with `Appearances >= 2`.
- Currently prepared for future UI components or exports.

---

## 8. Extending & Customizing

### 8.1 Customizing the Scoring Model

Inside `score_df(df)`:

- Adjust weights (e.g., `+25`, `+15`, `+10`) to emphasize or de‑emphasize:
  - Delivery spikes
  - Trade spikes
  - Trend
  - Repeat Count
  - Price action
- Change thresholds for:
  - Delivery ratio (`> 1`, `> 0.7`, etc.).
  - Price change ranges.
  - Score → Recommendation mapping (e.g., making Strong Buy harder to achieve).

This lets you adapt the model to different markets, timeframes, or risk profiles.

### 8.2 Changing the UI

- Add/remove fields in the cards/table by editing `dashboard.html`.
- Use existing CSS variables (`--bg-body`, `--accent`, etc.) to keep themes consistent.
- Move inline `<style>` and `<script>` into dedicated `static/` files if you want a more modular structure.

---

## 9. Routes Summary

| Route            | Method | Description                                        |
|------------------|--------|----------------------------------------------------|
| `/`              | GET    | Main dashboard – loads data, scores, renders       |
| `/upload`        | POST   | Uploads one/more CSV files into `data/`           |
| `/clear-uploads` | POST   | Deletes all CSVs from `data/` (resets data)       |

---

## 10. Notes & Next Steps

- In production, run this app with a WSGI server (e.g., `gunicorn`) behind a reverse proxy, instead of `app.run(debug=True)`.
- You can move the scoring logic into a separate module (e.g., `services/scoring.py`) and call it from the Flask routes for better separation of concerns.
- Adding tests for `read_one_csv`, `score_df`, and helper functions will make refactoring safer.


