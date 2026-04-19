from flask import Flask, render_template, request, redirect, url_for
from werkzeug.utils import secure_filename
import pandas as pd, os, glob, re

app = Flask(__name__)

# -------------------------------------------------------------------
# PATH CONFIGURATION
# - BASE_DIR   : folder where this script lives
# - UPLOAD_DIR : (currently unused for saved files, but kept for future)
# - DATA_DIR   : where cleaned CSVs are stored and read from
#   If you change folder names, update the HTML form actions/paths too.
# -------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, 'uploads')
DATA_DIR = os.path.join(BASE_DIR, 'data')

# Ensure upload and data folders exist at startup
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)


# ===================================================================
#  FILE READER
#  - Reads single CSV exported from your data source
#  - Extracts date from header
#  - Normalizes columns, types, and derived metrics (Delv x / Trade x)
# ===================================================================

def read_one_csv(path):
    """
    Read a single CSV file and return a cleaned DataFrame with columns:
    ['Stock', 'Symbol', 'Sector', 'Date', 'Delv x', 'Trade x', 'Chg %'].

    Expected file format:
    - Date information in the second row (index 1) of the file header.
    - Actual data starts after 5 rows of header (skiprows=5).
    - Contains delivery / trade quantity and change % columns.

    If the file doesn't match the expected pattern, returns None.
    """
    try:
        # Read first 3 rows only to extract date from header area
        raw = pd.read_csv(path, header=None, nrows=3)

        # The date is expected somewhere in row 1, column 0,
        # like "Report for 12-Jan-2025" -> extract "12-Jan-2025".
        date_row = str(raw.iloc[1, 0])
        match = re.search(r'(\d{2}-[A-Za-z]{3}-\d{4})', date_row)
        file_date = pd.to_datetime(match.group(1)) if match else pd.NaT

        # Read the actual data starting after header lines
        df = pd.read_csv(path, skiprows=5)

        # ----------------------------------------------------------------
        # STANDARDIZE COLUMN NAMES
        # - This makes downstream logic independent of exact export labels.
        # - If the source CSV headers change, update this mapping.
        # ----------------------------------------------------------------
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

        # Required columns for scoring engine
        required = [
            'Stock', 'Symbol', 'Sector',
            'Delivered Qty', 'Avg Delv Qty',
            'Traded Qty', 'Avg Traded Qty',
            'Chg %'
        ]

        # Add missing columns as 0 to avoid KeyErrors later
        for col in required:
            if col not in df.columns:
                df[col] = 0

        # ----------------------------------------------------------------
        # CLEAN NUMERIC COLUMNS
        # - Convert to numeric, coercing invalid values to 0
        # - This avoids crashes due to bad data, at the cost of
        #   potentially losing some rows' accuracy.
        #   If you prefer dropping bad rows, handle NaNs instead.
        # ----------------------------------------------------------------
        for col in ['Delivered Qty', 'Avg Delv Qty', 'Traded Qty', 'Avg Traded Qty', 'Chg %']:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

        # ----------------------------------------------------------------
        # DERIVED METRICS
        # - Delv x  : current delivered qty vs average delivered qty
        # - Trade x : current traded qty vs average traded qty
        #   A "+1" is added in denominator to avoid division by zero.
        # ----------------------------------------------------------------
        df['Delv x'] = df['Delivered Qty'] / (df['Avg Delv Qty'] + 1)
        df['Trade x'] = df['Traded Qty'] / (df['Avg Traded Qty'] + 1)

        # Store file date as pure date (no time) in each row
        if pd.notna(file_date):
            df['Date'] = file_date.normalize()
        else:
            df['Date'] = pd.NaT

        # Normalize symbol: uppercase, strip whitespace
        df['Symbol'] = df['Symbol'].astype(str).str.upper().str.strip()

        # Return only the columns needed by scoring engine
        return df[['Stock', 'Symbol', 'Sector', 'Date', 'Delv x', 'Trade x', 'Chg %']]

    except Exception as e:
        # Log error and skip file; UI will show this via 'bad_files' list
        print("Error:", path, e)
        return None


# ===================================================================
#  LOAD ALL DATA FILES
#  - Reads all CSVs in DATA_DIR using read_one_csv
#  - Returns concatenated DataFrame + list of bad files
# ===================================================================

def load_data():
    """
    Load all .csv files from DATA_DIR, clean each via read_one_csv,
    and concatenate them into a single DataFrame.

    Returns:
        (dataframe, bad_files_list)
        - dataframe : all good records combined
        - bad_files_list : [{file: 'name.csv', error: '...'}, ...]
    """
    # Find all CSV files in data directory
    paths = glob.glob(os.path.join(DATA_DIR, '*.csv'))
    frames, bad_files = [], []

    for p in paths:
        df = read_one_csv(p)
        if df is not None and not df.empty:
            frames.append(df)
        else:
            bad_files.append({'file': os.path.basename(p), 'error': 'Unreadable or empty'})

    # No valid files -> return empty df
    if not frames:
        return pd.DataFrame(), bad_files

    # Combine all loaded data into one table
    return pd.concat(frames, ignore_index=True), bad_files


# ===================================================================
#  SCORING ENGINE
#  - Aggregates per-symbol data
#  - Computes composite score, signal, recommendation, reasons, action
#  - Designed to match the rules described in the dashboard UI.
# ===================================================================

def score_df(df):
    """
    Apply the scoring model on the raw per-date data.

    Input:
        df: DataFrame with columns
            ['Stock','Symbol','Sector','Date','Delv x','Trade x','Chg %']

    Output:
        DataFrame with one row per symbol containing:
            - Stock, Symbol, Sector, Date (latest)
            - Score, Recommendation, Signal
            - Delv x, Trade x, Chg %
            - Repeat Count / Appearances, Delivery ratio
            - Reasons (string), Action
    """

    if df.empty:
        return pd.DataFrame()

    # --------------------------------------------------------------
    # REPEAT COUNTS
    # - How many distinct days each symbol appears across files
    # - Used as a proxy for persistence/consistency of signals.
    # --------------------------------------------------------------
    repeat_counts = df.groupby('Symbol')['Date'].nunique().to_dict()
    rows = []

    # Group full history per symbol
    for sym, group in df.groupby('Symbol'):

        # Ensure records are in chronological order
        group = group.sort_values('Date')
        repeat = repeat_counts.get(sym, 1)
        # Latest row (most recent date) is the primary snapshot
        latest = group.iloc[-1]

        delv = float(latest['Delv x'])
        trade = float(latest['Trade x'])
        chg = float(latest['Chg %'])

        # ratio = delivery vs trade intensity
        # Used both for scoring and display as "Delivery ratio"
        ratio = delv / (trade + 0.1) if trade is not None else 0.0
        avg_delv = group['Delv x'].mean()
        avg_trade = group['Trade x'].mean()

        # Simple trend check: is Delv x non-decreasing over time?
        trend_up = group['Delv x'].is_monotonic_increasing

        # Spike conditions relative to symbol's own history
        trade_spike = trade > (1.5 * avg_trade)
        delv_spike = delv > (1.3 * avg_delv)

        # Initialize composite score
        score = 0

        # ----------------------------------------------------------
        # SCORE COMPONENTS
        # - All weights here drive final Recommendation mapping.
        # - To tweak model behaviour, adjust these numbers carefully.
        # ----------------------------------------------------------

        # Delivery spike / above-average delivery
        if delv_spike:
            score += 25
        elif delv > avg_delv:
            score += 15

        # Trade spike
        if trade_spike:
            score += 15

        # Delivery ratio (delivery vs trade balance)
        if ratio > 1:
            score += 25
        elif ratio > 0.7:
            score += 10

        # Price change (momentum / weakness)
        if chg > 3:
            score += 15
        elif chg > 0:
            score += 8
        elif chg < -3:
            score -= 10

        # Repeat appearances (persistence over days)
        if repeat >= 4:
            score += 20
        elif repeat >= 3:
            score += 15
        elif repeat == 2:
            score += 8

        # Upward delivery trend
        if trend_up:
            score += 10

        # ----------------------------------------------------------
        # SIGNAL LABEL (qualitative view)
        # - Higher-level categorization from score drivers:
        #   accumulation / breakout / distribution / neutral.
        # ----------------------------------------------------------
        if ratio > 1 and repeat >= 3 and chg > 0:
            signal = "Strong accumulation"
        elif delv_spike and trade_spike and chg > 0:
            signal = "Breakout candidate"
        elif ratio > 1:
            signal = "Accumulation phase"
        elif chg < 0 and trade_spike:
            signal = "Distribution risk"
        else:
            signal = "Neutral"

        # ----------------------------------------------------------
        # RECOMMENDATION
        # - Thresholds must match what is documented in UI:
        #   Strong Buy >= 85
        #   Accumulate >= 65
        #   Watch >= 45
        #   Avoid otherwise
        # ----------------------------------------------------------
        if score >= 85:
            rec = "Strong Buy"
        elif score >= 65:
            rec = "Accumulate"
        elif score >= 45:
            rec = "Watch"
        else:
            rec = "Avoid"

        # ----------------------------------------------------------
        # REASONS (human-readable justification)
        # - Collected into a comma-separated string for display
        #   or export.
        # ----------------------------------------------------------
        reasons = []

        if ratio > 1:
            reasons.append("Delivery > Trade (Accumulation)")

        if repeat >= 3:
            reasons.append(f"Repeated {repeat} days")

        if trend_up:
            reasons.append("Rising delivery trend")

        if chg > 0:
            reasons.append("Positive momentum")

        if delv_spike:
            reasons.append("Delivery spike")

        if not reasons:
            reasons.append("No strong signals")

        # ----------------------------------------------------------
        # ACTION (suggested next step in plain English)
        # - Mirrors Recommendation but in more actionable language.
        # ----------------------------------------------------------
        if rec == "Strong Buy":
            action = "Enter early, accumulation confirmed"
        elif rec == "Accumulate":
            action = "Start building position"
        elif rec == "Watch":
            action = "Wait for breakout confirmation"
        else:
            action = "Avoid for now"

        # ----------------------------------------------------------
        # UI-FRIENDLY FIELDS
        # - Date normalized to string
        # - Ratio formatted for the dashboard table
        # ----------------------------------------------------------
        if pd.notna(latest['Date']):
            date_str = latest['Date'].strftime('%Y-%m-%d')
        else:
            date_str = ""

        delivery_ratio = f"{ratio:.2f}"

        rows.append({
            'Stock': latest['Stock'],
            'Symbol': sym,
            'Sector': latest['Sector'],
            'Date': date_str,
            'Score': round(score, 0),
            'Recommendation': rec,
            'Signal': signal,
            'Delv x': round(delv, 2),
            'Trade x': round(trade, 2),
            'Chg %': round(chg, 2),
            'Repeat Count': repeat,          # keep for helper functions
            'Appearances': repeat,           # used by table column
            'Delivery ratio': delivery_ratio,
            'Reasons': ", ".join(reasons),
            'Action': action
        })

    # Final ranked table: highest score first
    return pd.DataFrame(rows).sort_values('Score', ascending=False)


# ===================================================================
#  HELPER VIEWS FOR DASHBOARD (Top5, Pre-breakout, Repeated)
#  - These transform scored DataFrame into subsets used by UI.
# ===================================================================

def top5(df):
    """Top Opportunities: strongest 'Strong Buy' / 'Accumulate' names."""
    if df.empty:
        return []
    return (
        df[df['Recommendation'].isin(['Strong Buy', 'Accumulate'])]
          .head(5)
          .to_dict('records')
    )

def prebreakout(df):
    """
    Pre-Breakout Opportunities: early setups to watch.

    Rules:
    - Include stocks with 'Watch' recommendation or 'Breakout candidate' signal.
    - Exclude symbols already in top 5 Strong Buy / Accumulate to avoid duplicates.
    - Limit to top 5 candidates by score.
    """
    if df.empty:
        return []

    # Candidates: promising watchlist / breakout names
    mask = (
        (df['Recommendation'] == 'Watch') |
        (df['Signal'] == 'Breakout candidate')
    )
    candidates = df[mask].copy()

    # Identify top 5 high-conviction names to exclude from pre-breakout
    top_syms = set(
        df[df['Recommendation'].isin(['Strong Buy', 'Accumulate'])]
        .head(5)['Symbol']
    )
    candidates = candidates[~candidates['Symbol'].isin(top_syms)]

    return candidates.head(5).to_dict('records')

def repeated(df):
    """
    Summary of symbols that have appeared on 2+ distinct days.

    Returns records with:
    - Avg_Score, Max_Score, Appearances, Avg_Delv, Avg_Trade
    grouped by (Symbol, Stock, Sector).
    """
    if df.empty:
        return []

    # Aggregate stability metrics for each symbol
    rep = df.groupby(['Symbol','Stock','Sector'], as_index=False).agg(
        Avg_Score=('Score','mean'),
        Max_Score=('Score','max'),
        Appearances=('Repeat Count','max'),
        Avg_Delv=('Delv x','mean'),
        Avg_Trade=('Trade x','mean')
    )

    # Only keep names that repeat at least twice
    rep = rep[rep['Appearances'] >= 2]

    return rep.to_dict('records')


# ===================================================================
#  FLASK ROUTES
#  - index        : main dashboard
#  - upload       : receive CSVs and save to DATA_DIR
#  - clear_uploads: delete all CSVs from DATA_DIR
# ===================================================================

@app.route('/')
def index():
    """
    Main dashboard route.

    Steps:
    - Load all CSV data from DATA_DIR.
    - Score each symbol via scoring engine.
    - Compute summary KPIs.
    - Render 'dashboard.html' template with:
        - records      : full scored table (list of dicts)
        - summary      : KPI dictionary for header cards
        - top5         : top opportunities
        - prebreakout  : early watchlist names
        - repeated     : persistence summary (not yet used in UI)
    """
    raw, bad_files = load_data()
    scored = score_df(raw)

    summary = {
        'rows': len(scored),
        'avg_score': round(scored['Score'].mean(), 2) if not scored.empty else 0,
        'strong_buys': (scored['Recommendation'] == 'Strong Buy').sum() if not scored.empty else 0,
        'watchlist': (scored['Recommendation'] == 'Watch').sum() if not scored.empty else 0
    }

    return render_template(
        'dashboard.html',
        records=scored.to_dict('records'),
        summary=summary,
        top5=top5(scored),
        prebreakout=prebreakout(scored),
        repeated=repeated(scored)
    )

@app.route('/upload', methods=['POST'])
def upload():
    """
    Upload endpoint:
    - Accepts one or more CSV files from the form (name='files').
    - Saves them into DATA_DIR using a safe filename.
    - After saving, redirects back to main dashboard for re-scan.

    NOTE:
    - Currently, UPLOAD_DIR is unused; files go directly to DATA_DIR.
      If you want to separate raw vs processed files, change 'path'.
    """
    files = request.files.getlist('files')

    for f in files:
        # Only accept CSV files by extension.
        # If you want stricter validation, inspect file content as well.
        if f and f.filename.endswith('.csv'):
            path = os.path.join(DATA_DIR, secure_filename(f.filename))
            f.save(path)

    return redirect(url_for('index'))

@app.route('/clear-uploads', methods=['POST'])
def clear_uploads():
    """
    Clear uploads endpoint:
    - Deletes all CSV files currently present in DATA_DIR.
    - Useful to reset the dashboard data quickly.
    """
    for f in glob.glob(os.path.join(DATA_DIR, '*.csv')):
        os.remove(f)
    return redirect(url_for('index'))

if __name__ == '__main__':
    # Development server (debug=True).
    # In production, use a proper WSGI server (gunicorn, uwsgi, etc.).
    app.run(debug=True)