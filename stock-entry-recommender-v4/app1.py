
from flask import Flask, render_template, request, redirect, url_for
from werkzeug.utils import secure_filename
import pandas as pd, os, glob, json, hashlib, re

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, 'uploads')
DATA_DIR = os.path.join(BASE_DIR, 'data')
REGISTRY_PATH = os.path.join(UPLOAD_DIR, 'registry.json')

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

# ---------------- FILE READER (FIXED FOR YOUR FORMAT) ---------------- #

def read_one_csv(path):
    try:
        # Extract date from row 2
        raw = pd.read_csv(path, header=None, nrows=3)
        date_row = str(raw.iloc[1,0])
        match = re.search(r'(\d{2}-[A-Za-z]{3}-\d{4})', date_row)
        file_date = pd.to_datetime(match.group(1)) if match else pd.NaT

        # Actual data starts after row 5
        df = pd.read_csv(path, skiprows=5)

        df.rename(columns={
            'Stock Name': 'Stock',
            'Symbol': 'Symbol',
            'Sector Name': 'Sector',
            'Delivered Qty': 'Delv x',
            'Traded Qty': 'Trade x',
            'Chg %': 'Chg %'
        }, inplace=True)

        required = ['Stock','Symbol','Sector','Delv x','Trade x','Chg %']
        for col in required:
            if col not in df.columns:
                df[col] = 0

        df['Date'] = file_date

        df['Symbol'] = df['Symbol'].astype(str).str.upper().str.strip()

        for col in ['Delv x','Trade x','Chg %']:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

        return df[['Stock','Symbol','Sector','Date','Delv x','Trade x','Chg %']]

    except Exception as e:
        print("Error:", path, e)
        return None

# ---------------- LOAD DATA ---------------- #

def load_data():
    paths = glob.glob(os.path.join(DATA_DIR, '*.csv'))

    frames = []
    bad_files = []

    for p in paths:
        df = read_one_csv(p)
        if df is not None and not df.empty:
            frames.append(df)
        else:
            bad_files.append({'file': os.path.basename(p), 'error': 'Unreadable or empty'})

    if not frames:
        return pd.DataFrame(), bad_files

    return pd.concat(frames, ignore_index=True), bad_files

# ---------------- SMART SCORING ENGINE ---------------- #


def score_df(df):

    if df.empty:
        return pd.DataFrame()

    repeat_counts = df.groupby('Symbol')['Date'].nunique().to_dict()
    rows = []

    for sym, group in df.groupby('Symbol'):

        group = group.sort_values('Date')
        repeat = repeat_counts.get(sym, 1)

        latest = group.iloc[-1]

        delv = latest['Delv x']
        trade = latest['Trade x']
        chg = latest['Chg %']

        # 🔥 NEW FEATURES
        ratio = delv / (trade + 1)
        avg_delv = group['Delv x'].mean()
        avg_trade = group['Trade x'].mean()

        trend_up = group['Delv x'].is_monotonic_increasing
        trade_spike = trade > (1.5 * avg_trade)
        delv_spike = delv > (1.3 * avg_delv)

        score = 0

        # ---------------- CORE SIGNALS ---------------- #

        # 1. Delivery strength
        if delv_spike:
            score += 25
        elif delv > avg_delv:
            score += 15

        # 2. Trade participation
        if trade_spike:
            score += 15

        # 3. Accumulation (MOST IMPORTANT)
        if ratio > 1:
            score += 25
        elif ratio > 0.7:
            score += 10

        # 4. Momentum
        if chg > 3:
            score += 15
        elif chg > 0:
            score += 8
        elif chg < -3:
            score -= 10

        # 5. Repeat strength (KEY)
        if repeat >= 4:
            score += 20
        elif repeat >= 3:
            score += 15
        elif repeat == 2:
            score += 8

        # 6. Trend
        if trend_up:
            score += 10

        # ---------------- SIGNAL CLASSIFICATION ---------------- #

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

        # ---------------- RECOMMENDATION ---------------- #

        if score >= 85:
            rec = "Strong Buy"
        elif score >= 65:
            rec = "Accumulate"
        elif score >= 45:
            rec = "Watch"
        else:
            rec = "Avoid"

        rows.append({
            'Stock': latest['Stock'],
            'Symbol': sym,
            'Sector': latest['Sector'],
            'Date': latest['Date'],
            'Score': score,
            'Recommendation': rec,
            'Signal': signal,
            'Delv x': delv,
            'Trade x': trade,
            'Chg %': chg,
            'Repeat Count': repeat,
            'Rec Color': '#dff4e8' if rec=='Strong Buy' else '#dff0f2' if rec=='Accumulate' else '#fff3db' if rec=='Watch' else '#fde5e5',
            'Signal Color': '#e7efff'
        })

    return pd.DataFrame(rows).sort_values('Score', ascending=False)


# ---------------- HELPERS ---------------- #

def top5(df):
    if df.empty or 'Recommendation' not in df:
        return []
    return df[df['Recommendation'].isin(['Strong Buy','Accumulate'])].head(5).to_dict('records')


def repeated(df):
    if df.empty or 'Score' not in df.columns:
        return []

    rep = df.groupby(['Symbol','Stock','Sector'], as_index=False).agg(
        Avg_Score=('Score','mean'),
        Max_Score=('Score','max'),
        Appearances=('Repeat Count','max'),
        Avg_Delv=('Delv x','mean'),
        Avg_Trade=('Trade x','mean')
    )

    rep = rep[rep['Appearances'] >= 2]

    rep['Current Recommendation'] = rep['Avg_Score'].apply(
        lambda x: 'Strong Buy' if x >= 80 else
                  'Accumulate' if x >= 60 else
                  'Watch' if x >= 40 else
                  'Avoid'
    )

    return rep.sort_values(['Appearances','Avg_Score'], ascending=[False,False]).to_dict('records')



def chart_payload(df):
    if df.empty:
        return {'mix': {}}
    return {'mix': df['Recommendation'].value_counts().to_dict()}

# ---------------- ROUTES ---------------- #

@app.route('/')
def index():

    raw, bad_files = load_data()
    scored = score_df(raw)

    summary = {
        'rows': len(scored),
        'avg_score': round(scored['Score'].mean(),2) if not scored.empty else 0,
        'strong_buys': (scored['Recommendation']=='Strong Buy').sum() if not scored.empty else 0,
        'watchlist': (scored['Recommendation']=='Watch').sum() if not scored.empty else 0
    }

    status = {
        'using_uploads': False,
        'demo_count': len(bad_files),
        'uploaded_count': 0,
        'bad_files': bad_files
    }

    return render_template(
        'dashboard.html',
        records=scored.to_dict('records'),
        summary=summary,
        status=status,
        charts=chart_payload(scored),
        top5=top5(scored),
        repeated=repeated(scored),
        sectors=[],
        q='', rec='', sector='',
        sort='Score', direction='desc'
    )

@app.route('/upload', methods=['POST'])
def upload():
    files = request.files.getlist('files')

    for f in files:
        if f and f.filename.endswith('.csv'):
            path = os.path.join(DATA_DIR, secure_filename(f.filename))
            f.save(path)

    return redirect(url_for('index'))

@app.route('/clear-uploads', methods=['POST'])
def clear_uploads():
    for f in glob.glob(os.path.join(DATA_DIR, '*.csv')):
        os.remove(f)
    return redirect(url_for('index'))

# ---------------- RUN ---------------- #

if __name__ == '__main__':
    app.run(debug=True)

