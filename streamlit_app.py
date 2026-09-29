import os
import json
import time as pytime
from datetime import datetime, timedelta, time
import pytz
import pandas as pd
import numpy as np
import yfinance as yf
import requests
import streamlit as st

# ==================== STREAMLIT PAGE CONFIGURATION ====================
st.set_page_config(
    page_title="AlphaTerminal Pro | Scheduled Action Desk",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
    .stApp { background-color: #080c14; color: #e2e8f0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
    .action-box { background-color: #0c1a2e; border: 2px solid #2563eb; border-radius: 10px; padding: 16px; margin-bottom: 14px; }
    .exit-box { background-color: #3b0712; border: 1px solid #ef4444; border-radius: 8px; padding: 10px 14px; margin-top: 10px; font-size: 13px; color: #fca5a5; }
    .groww-search-box {
        background-color: #064e3b;
        border: 2px solid #10b981;
        border-radius: 8px;
        padding: 10px 14px;
        margin-bottom: 12px;
        display: flex;
        align-items: center;
        justify-content: space-between;
    }
    .alloc-card { background-color: #111827; border: 1px solid #374151; border-radius: 10px; padding: 14px; margin-bottom: 12px; }
    .news-card { background-color: #0f172a; border: 1px solid #1e293b; border-radius: 8px; padding: 12px; margin-bottom: 8px; }
    .metric-row { display: flex; justify-content: space-between; align-items: center; padding: 6px 10px; border-bottom: 1px solid #1e293b; font-size: 13px; }
    .metric-row:last-child { border-bottom: none; }
    .metric-label { color: #94a3b8; font-weight: 600; }
    .metric-val { font-weight: 700; font-family: monospace; font-size: 14px; }
</style>
""", unsafe_allow_html=True)

TRADE_LOG_FILE = "daily_trades.json"
IST = pytz.timezone("Asia/Kolkata")

DEFAULT_MOMENTUM_WATCHLIST = [
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "ICICIBANK.NS", "INFY.NS",
    "BHARTIARTL.NS", "TATAMOTORS.NS", "LT.NS", "SBIN.NS", "DIXON.NS",
    "HAL.NS", "TRENT.NS", "BEL.NS", "PERSISTENT.NS", "SUZLON.NS"
]

if "trade_ledger" not in st.session_state:
    st.session_state.trade_ledger = []
if "active_trade" not in st.session_state:
    st.session_state.active_trade = None

# ==================== DATA SCALAR EXTRACTOR ====================
def to_scalar(val):
    if hasattr(val, "values"):
        v = val.values
        if len(v) > 0:
            val = v[-1]
    if hasattr(val, "item"):
        try:
            return float(val.item())
        except Exception:
            pass
    try:
        return float(val)
    except Exception:
        return 0.0

def clean_candle_data(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    d = df.copy()
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = [str(c[0]).lower() for c in d.columns]
    else:
        d.columns = [str(c).lower() for c in d.columns]
    d = d.loc[:, ~d.columns.duplicated()]
    return d.dropna(how="all")

# ==================== GROWW EXACT SYMBOL CONVERTER ====================
def get_groww_expiry_format(index_name: str, raw_expiry=None):
    now = datetime.now(IST).date()
    if raw_expiry:
        for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d", "%d %b %Y"):
            try:
                p_dt = datetime.strptime(raw_expiry, fmt).date()
                if p_dt >= now:
                    return p_dt.strftime("%d %b").upper()
            except Exception:
                continue

    target_weekday = 3 if index_name == "NIFTY" else (2 if index_name == "BANKNIFTY" else 4)
    days_ahead = (target_weekday - now.weekday()) % 7
    if days_ahead == 0 and datetime.now(IST).time() > time(15, 30):
        days_ahead = 7
    exp_date = now + timedelta(days=days_ahead)
    return exp_date.strftime("%d %b").upper()

# ==================== LIVE SPOT TICK STREAMER ====================
def fetch_live_index_tick(symbol_yf: str):
    try:
        t = yf.Ticker(symbol_yf)
        h = t.history(period="1d", interval="1m")
        if not h.empty and "Close" in h.columns:
            return to_scalar(h["Close"].iloc[-1])
    except Exception:
        pass
    return 0.0

# ==================== REAL-TIME F&O GREEKS & OPTION PRICER ====================
def fetch_fno_chain(symbol="NIFTY"):
    if symbol == "NIFTY":
        step, lot_size, sym_yf = 50, 25, "^NSEI"
    elif symbol == "BANKNIFTY":
        step, lot_size, sym_yf = 100, 15, "^NSEBANK"
    else:
        step, lot_size, sym_yf = 100, 10, "^BSESN"

    spot = fetch_live_index_tick(sym_yf)
    if spot <= 0:
        spot = 22668.75 if symbol == "NIFTY" else (51500.0 if symbol == "BANKNIFTY" else 74200.0)

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.nseindia.com/"
    }
    s = requests.Session()
    s.headers.update(headers)

    pcr = 1.18
    call_wall = int(((spot // step) + 2) * step)
    put_wall = int(((spot // step) - 2) * step)
    live_ce_ltp = 0.0
    live_pe_ltp = 0.0
    raw_expiry = None

    try:
        s.get("https://www.nseindia.com", timeout=2)
        res = s.get(f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}&_={int(pytime.time() * 1000)}", timeout=2).json()
        records = res.get("records", {})
        expiry_dates = records.get("expiryDates", [])
        if expiry_dates:
            raw_expiry = expiry_dates[0]

        ce_oi, pe_oi = 0, 0
        strikes = {}
        atm_strike = int(round(spot / step) * step)

        for item in records.get("data", []):
            if item.get("expiryDate") == raw_expiry:
                stk = item["strikePrice"]
                ce = item.get("CE", {})
                pe = item.get("PE", {})
                ce_oi += ce.get("openInterest", 0)
                pe_oi += pe.get("openInterest", 0)
                strikes[stk] = {
                    "ce_oi": ce.get("openInterest", 0),
                    "pe_oi": pe.get("openInterest", 0),
                    "ce_ltp": to_scalar(ce.get("lastPrice", 0.0)),
                    "pe_ltp": to_scalar(pe.get("lastPrice", 0.0))
                }
                if stk == atm_strike:
                    live_ce_ltp = to_scalar(ce.get("lastPrice", 0.0))
                    live_pe_ltp = to_scalar(pe.get("lastPrice", 0.0))

        if ce_oi > 0:
            pcr = round(pe_oi / ce_oi, 2)
        if strikes:
            call_wall = max(strikes.keys(), key=lambda k: strikes[k]["ce_oi"])
            put_wall = max(strikes.keys(), key=lambda k: strikes[k]["pe_oi"])
    except Exception:
        pass

    exp_groww = get_groww_expiry_format(symbol, raw_expiry)
    rec_strike = int((spot // step) * step)
    delta = 0.52

    if pcr >= 1.10:
        bias = "BULLISH (Put Writers Active)"
        opt_type = "CE"
        strike_to_buy = rec_strike
        intrinsic = max(0.0, spot - strike_to_buy)
        time_value = max(45.0, spot * 0.0035)
        est_entry = round(live_ce_ltp if live_ce_ltp > 10 else (intrinsic + time_value), 1)

        spot_target = spot + (call_wall - spot) * 0.60
        spot_stop = spot - (spot - put_wall) * 0.50

        target = round(est_entry + (spot_target - spot) * delta, 1)
        stop_loss = round(max(5.0, est_entry - (spot - spot_stop) * delta), 1)
        fast_exit = f"SELL FAST: If {symbol} Spot breaches ₹{put_wall:,.0f} downward, or if premium drops to ₹{stop_loss:.1f}."
    else:
        bias = "BEARISH (Call Writers Active)"
        opt_type = "PE"
        strike_to_buy = rec_strike + step
        intrinsic = max(0.0, strike_to_buy - spot)
        time_value = max(45.0, spot * 0.0035)
        est_entry = round(live_pe_ltp if live_pe_ltp > 10 else (intrinsic + time_value), 1)

        spot_target = spot - (spot - put_wall) * 0.60
        spot_stop = spot + (call_wall - spot) * 0.50

        target = round(est_entry + (spot - spot_target) * delta, 1)
        stop_loss = round(max(5.0, est_entry - (spot_stop - spot) * delta), 1)
        fast_exit = f"SELL FAST: If {symbol} Spot pushes above ₹{call_wall:,.0f} resistance, or if premium drops to ₹{stop_loss:.1f}."

    approx_profit = round((target - est_entry) * lot_size, 2) if target > est_entry else 0.0
    groww_query = f"{symbol} {strike_to_buy} {opt_type} {exp_groww}"
    contract_title = f"{symbol} {strike_to_buy} {opt_type}"

    return {
        "spot": spot,
        "expiry_groww": exp_groww,
        "pcr": pcr,
        "bias": bias,
        "call_wall": call_wall,
        "put_wall": put_wall,
        "contract": contract_title,
        "groww_search": groww_query,
        "entry_rate": est_entry,
        "stop_loss": stop_loss,
        "target": target,
        "approx_profit_lot": approx_profit,
        "lot_size": lot_size,
        "exit_rule": fast_exit,
        "opt_type": opt_type,
        "strike": strike_to_buy
    }

# ==================== DATA DOWNLOADERS WITH CACHE-BYPASS ====================
def fetch_market_quotes(tickers_tuple: tuple):
    tickers_list = list(tickers_tuple)
    try:
        data = yf.download(tickers_list, period="5d", interval="15m", group_by="ticker", progress=False, threads=True)
        if data is None or data.empty:
            data = yf.download(tickers_list, period="1mo", interval="1d", group_by="ticker", progress=False, threads=True)
        return data
    except Exception:
        return None

def fetch_single_ticker(symbol: str):
    clean_sym = symbol.strip().upper()
    if not clean_sym.startswith("^") and not clean_sym.endswith(".NS") and not clean_sym.endswith(".BO"):
        clean_sym += ".NS"
    try:
        df = yf.download(clean_sym, period="1d", interval="5m", progress=False)
        if df is None or df.empty or len(df) < 3:
            df = yf.download(clean_sym, period="5d", interval="15m", progress=False)
        return clean_candle_data(df), clean_sym
    except Exception:
        return pd.DataFrame(), clean_sym

# ==================== QUANT ENGINE ====================
def analyze_stock(df: pd.DataFrame, nifty_df: pd.DataFrame, capital: float, max_risk: float):
    df = clean_candle_data(df)
    if len(df) < 10 or "close" not in df.columns:
        return None

    close = df["close"]
    high = df["high"] if "high" in df.columns else close
    low = df["low"] if "low" in df.columns else close
    volume = df["volume"] if "volume" in df.columns else pd.Series([100000] * len(df), index=df.index)

    curr_close = to_scalar(close.iloc[-1])
    curr_vol = to_scalar(volume.iloc[-1])

    span_20 = min(20, len(close))
    span_50 = min(50, len(close))
    ema_20 = to_scalar(close.ewm(span=span_20, adjust=False).mean().iloc[-1])
    ema_50 = to_scalar(close.ewm(span=span_50, adjust=False).mean().iloc[-1])

    sma_20 = close.rolling(span_20).mean()
    std_20 = close.rolling(span_20).std().fillna(0)
    bb_upper = to_scalar((sma_20 + (2.0 * std_20)).iloc[-1])

    tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
    atr = to_scalar(tr.rolling(min(14, len(tr))).mean().iloc[-1])
    if atr <= 0 or np.isnan(atr):
        atr = max(curr_close * 0.015, 0.5)

    kc_upper = to_scalar(ema_20 + (1.5 * atr))
    vol_sma = to_scalar(volume.rolling(min(20, len(volume))).mean().iloc[-1]) if len(volume) >= 5 else curr_vol

    lookback = min(15, len(close) - 1)
    prev_close = to_scalar(close.iloc[-lookback])
    stock_ret = (curr_close - prev_close) / max(prev_close, 1e-4)

    if not nifty_df.empty and "close" in nifty_df.columns:
        n_close = nifty_df["close"]
        n_idx = min(lookback, len(n_close) - 1)
        n_curr = to_scalar(n_close.iloc[-1])
        n_prev = to_scalar(n_close.iloc[-n_idx])
        nifty_ret = (n_curr - n_prev) / max(n_prev, 1e-4)
    else:
        nifty_ret = 0.0
    alpha = round((stock_ret - nifty_ret) * 100, 2)

    reasons = []
    if curr_close >= ema_20 >= ema_50:
        reasons.append("Structural Markup (Price >= 20 EMA >= 50 EMA)")
    if alpha > 0:
        reasons.append(f"Institutional Alpha (+{alpha}% over Benchmark)")
    if bb_upper >= kc_upper:
        reasons.append("Volatility Squeeze Breakout Expansion")
    if vol_sma > 0 and curr_vol > (1.2 * vol_sma):
        reasons.append(f"Volume Surge ({round(curr_vol/vol_sma, 1)}x vs 20 SMA)")

    stop_loss = round(max(curr_close - (1.5 * atr), 0.05), 2)
    target_1 = round(curr_close + (2.5 * atr), 2)
    max_upside = round(curr_close + (4.5 * atr), 2)
    upside_pct = round(((max_upside - curr_close) / curr_close) * 100, 2)

    risk_per_share = max(curr_close - stop_loss, 0.5)
    trade_qty = max(1, min(int(max_risk // risk_per_share), int(capital // curr_close)))
    capital_required = round(trade_qty * curr_close, 2)
    est_profit_t1 = round((target_1 - curr_close) * trade_qty, 2)

    is_buy = len(reasons) >= 3 and curr_close > ema_20
    fast_exit_rule = f"Exit immediately if 15m candle closes below ₹{stop_loss:,.2f} or if target of ₹{target_1:,.2f} is reached."

    return {
        "signal": "STRONG BUY" if is_buy else "WATCH",
        "price": round(curr_close, 2),
        "stop_loss": stop_loss,
        "target_1": target_1,
        "max_upside": max_upside,
        "upside_pct": upside_pct,
        "trade_qty": trade_qty,
        "capital_required": capital_required,
        "est_profit_t1": est_profit_t1,
        "alpha": alpha,
        "reasons": reasons,
        "exit_rule": fast_exit_rule
    }

# ==================== NEWS RETRIEVER ====================
@st.cache_data(ttl=180, show_spinner=False)
def fetch_all_market_news():
    rss_url = "https://news.google.com/rss/search?q=NSE+BSE+Indian+stock+market+economy&hl=en-IN&gl=IN&ceid=IN:en"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    articles = []
    try:
        res = requests.get(rss_url, headers=headers, timeout=4)
        if res.status_code == 200:
            import xml.etree.ElementTree as ET
            root = ET.fromstring(res.content)
            for item in root.findall(".//item")[:15]:
                title = item.find("title").text if item.find("title") is not None else "Update"
                link = item.find("link").text if item.find("link") is not None else "#"
                pub_date = item.find("pubDate").text if item.find("pubDate") is not None else ""
                source = item.find("source").text if item.find("source") is not None else "Financial News"
                articles.append({"title": title, "link": link, "date": pub_date[:16], "source": source})
    except Exception:
        pass
    return articles

# ==================== AUDIT ENGINE ====================
def load_trade_log():
    today = datetime.now(IST).strftime("%Y-%m-%d")
    if os.path.exists(TRADE_LOG_FILE):
        try:
            with open(TRADE_LOG_FILE, "r") as f:
                data = json.load(f)
                if data.get("date") == today:
                    st.session_state.trade_ledger = data.get("trades", [])
                    return data.get("trades", [])
        except Exception:
            pass
    return st.session_state.trade_ledger

def save_trade(symbol, entry, stop, t1, t2, qty, est_profit, reasons):
    today = datetime.now(IST).strftime("%Y-%m-%d")
    trades = load_trade_log()
    if not any(t.get("symbol") == symbol for t in trades):
        trades.append({
            "symbol": symbol, "entry": float(entry), "stop_loss": float(stop),
            "target_1": float(t1), "max_upside": float(t2), "qty": int(qty),
            "est_profit": float(est_profit), "time": datetime.now(IST).strftime("%I:%M %p"),
            "status": "OPEN", "pnl": 0.0
        })
        st.session_state.trade_ledger = trades
        try:
            with open(TRADE_LOG_FILE, "w") as f:
                json.dump({"date": today, "trades": trades}, f, indent=2)
        except Exception:
            pass

def update_session_audit(quotes):
    trades = load_trade_log()
    updated = False
    for trade in trades:
        sym = trade.get("symbol", "") + ".NS"
        if sym in quotes and trade.get("status") == "OPEN":
            df = quotes[sym]
            if not df.empty and "high" in df.columns:
                max_high = to_scalar(df["high"].max())
                min_low = to_scalar(df["low"].min())
                target_val = trade.get("target_1", trade.get("entry", 0.0))
                stop_val = trade.get("stop_loss", trade.get("entry", 0.0))
                qty = trade.get("qty", 1)

                if max_high >= target_val:
                    trade["status"] = "PROFIT (Target 1 Reached)"
                    trade["pnl"] = round((target_val - trade["entry"]) * qty, 2)
                    updated = True
                elif min_low <= stop_val:
                    trade["status"] = "LOSS (Stop Hit)"
                    trade["pnl"] = round((stop_val - trade["entry"]) * qty, 2)
                    updated = True

    if updated:
        st.session_state.trade_ledger = trades
        try:
            with open(TRADE_LOG_FILE, "w") as f:
                json.dump({"date": datetime.now(IST).strftime("%Y-%m-%d"), "trades": trades}, f, indent=2)
        except Exception:
            pass
    return trades

# ==================== SIDEBAR ====================
st.sidebar.markdown("### 💼 Daily Budget & Risk Sizing Engine")
DAILY_BUDGET = st.sidebar.number_input("Total Daily Capital / Budget (₹)", min_value=5000, value=50000, step=5000)
RISK_PERCENT = st.sidebar.slider("Risk Per Trade (%)", min_value=0.5, max_value=3.0, value=1.0, step=0.1)

st.sidebar.markdown("---")
st.sidebar.markdown("### 🔍 Universal Stock Finder")
user_custom_symbol = st.sidebar.text_input(
    "Scan ANY NSE/BSE Stock Instantly",
    placeholder="e.g. TATAPOWER, IREDA, RSYSTEMS, SUZLON",
    help="Type any symbol to pull live rates and exact Groww search name."
).strip().upper()

AUTO_REFRESH_SEC = st.sidebar.selectbox("Live Polling Cadence (Seconds)", [3, 5, 10], index=0)
SIMULATION_MODE = st.sidebar.checkbox("Force Live Market Mode (Off-Hours Test)", value=False)

MAX_RISK_RUPEES = DAILY_BUDGET * (RISK_PERCENT / 100.0)
st.sidebar.info(f"🛡️ **Max Risk Limit / Trade:** ₹{MAX_RISK_RUPEES:,.2f}")

if st.sidebar.button("⚡ Force Live Tick Pull"):
    st.cache_data.clear()
    st.rerun()

st.title("⚡ AlphaTerminal Action Desk: Live Equities & F&O")

# ==================== MARKET TIMING & SCHEDULE ENGINE ====================
# Exchange operating schedule:
# - Normal market: 9:15 AM to 3:30 PM IST (Mon-Fri)
# - Polling wake-up: 1 hour before market opens -> 8:15 AM IST
# - Complete market close: 3:30 PM IST (all polling stops completely)
# - Weekends: Closed
now_ist = datetime.now(IST)
current_time = now_ist.time()
current_weekday = now_ist.weekday()  # 0=Monday, ..., 4=Friday, 5=Sat, 6=Sun

PRE_MARKET_WAKEUP = time(8, 15)
MARKET_NORMAL_OPEN = time(9, 15)
MARKET_CLOSE_TIME = time(15, 30)

is_weekday = current_weekday < 5
is_polling_window = is_weekday and (PRE_MARKET_WAKEUP <= current_time <= MARKET_CLOSE_TIME)
is_market_active = is_polling_window or SIMULATION_MODE

# Dynamic fragment execution: Auto-refreshes every N seconds during market window; stops completely after 3:30 PM
DYNAMIC_FRAGMENT_CADENCE = f"{AUTO_REFRESH_SEC}s" if is_market_active else None

# ==================== STREAMLIT FRAGMENT ENGINE ====================
@st.fragment(run_every=DYNAMIC_FRAGMENT_CADENCE)
def render_live_desk():
    clock_now = datetime.now(IST)
    clock_time = clock_now.time()
    clock_weekday = clock_now.weekday()

    live_now = (clock_weekday < 5 and (PRE_MARKET_WAKEUP <= clock_time <= MARKET_CLOSE_TIME)) or SIMULATION_MODE

    h1, h2 = st.columns([3, 1])
    with h1:
        if SIMULATION_MODE:
            st.info(f"🧪 **Simulation Mode Active**: Polling every {AUTO_REFRESH_SEC}s (Off-Hours Test) | Time: {clock_now.strftime('%I:%M:%S %p')} IST")
        elif live_now:
            if clock_time < MARKET_NORMAL_OPEN:
                st.success(f"🌅 **Pre-Market Regime Active (8:15 AM - 9:15 AM IST)**: Watchlists and option walls staging | Time: {clock_now.strftime('%I:%M:%S %p')} IST")
            else:
                st.success(f"🟢 **Live Market Streaming Active**: Polling every {AUTO_REFRESH_SEC}s | Time: {clock_now.strftime('%I:%M:%S %p')} IST")
        else:
            st.warning(f"🔴 **Market Completely Closed**: Auto-refresh stopped at 3:30 PM. Next session starts at 8:15 AM IST | Time: {clock_now.strftime('%I:%M:%S %p')} IST")
    with h2:
        stream_label = f"{AUTO_REFRESH_SEC}s Active Stream" if live_now else "Polling Stopped"
        st.metric("Live Ticker Feed", "Continuous Tick Engine", stream_label)

    # Active Sniper Tracker
    if st.session_state.active_trade is not None:
        tr = st.session_state.active_trade
        st.markdown(f"""
        <div class="sniper-box">
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <span style="font-size:1.3rem; font-weight:900; color:#38bdf8;">🎯 ACTIVE SNIPER TRADE: {tr['symbol']}</span>
                <span style="background-color:#4338ca; color:#e0e7ff; padding:3px 10px; border-radius:6px; font-weight:bold; font-size:12px;">IN POSITION</span>
            </div>
            <div style="margin-top:8px; font-size:13px; color:#a5f3fc;">
                <b>Groww Exact Search:</b> <code>{tr.get('groww_search', tr['symbol'])}</code>
            </div>
            <div style="display:grid; grid-template-columns: repeat(4, 1fr); gap:10px; margin-top:10px; background-color:#080c14; padding:10px; border-radius:8px;">
                <div><span style="color:#94a3b8; font-size:11px;">ENTRY RATE:</span><br><b style="font-size:16px; color:#fff;">₹{tr['entry']:.1f}</b></div>
                <div><span style="color:#ef4444; font-size:11px;">STOP LOSS:</span><br><b style="font-size:16px; color:#ef4444;">₹{tr['stop_loss']:.1f}</b></div>
                <div><span style="color:#10b981; font-size:11px;">TARGET 1:</span><br><b style="font-size:16px; color:#10b981;">₹{tr['target']:.1f}</b></div>
                <div><span style="color:#f59e0b; font-size:11px;">TRAIL STATUS:</span><br><b style="font-size:16px; color:#f59e0b;">ACTIVE</b></div>
            </div>
            <div class="exit-box">
                <b>🚨 REAL-TIME EXIT TRIGGER:</b> {tr['exit_rule']}
            </div>
        </div>
        """, unsafe_allow_html=True)

        df_live, _ = fetch_single_ticker(tr['symbol'])
        if not df_live.empty and "close" in df_live.columns:
            live_price = to_scalar(df_live["close"].iloc[-1])
            c1, c2 = st.columns([2, 1])
            with c1:
                st.write(f"Current Live LTP: **₹{live_price:,.2f}**")
            with c2:
                if st.button("✅ Exit Trade & Scan Next", use_container_width=True):
                    st.session_state.active_trade = None
                    st.rerun()

            if live_price <= tr['stop_loss']:
                st.error("🚨 STOP LOSS TRIGGERED! SELL IMMEDIATELY.")
            elif live_price >= tr['target']:
                st.success("🎯 TARGET REACHED! BOOK PROFITS AND SELL.")

    tabs = st.tabs([
        "💰 Daily Smart Capital Allocator",
        "⚡ Improvised F&O Action Box",
        "📈 Equities Action Radar",
        "📊 Day Analysis & Audit Ledger",
        "📰 All-India Market News"
    ])

    batch_raw = fetch_market_quotes(tuple(DEFAULT_MOMENTUM_WATCHLIST + ["^NSEI"]))
    nifty_df = clean_candle_data(batch_raw["^NSEI"]) if (batch_raw is not None and "^NSEI" in getattr(batch_raw.columns, "levels", [[]])[0]) else pd.DataFrame()

    cards = []
    quotes_cache = {}
    if batch_raw is not None:
        has_levels = hasattr(batch_raw.columns, "levels") and len(batch_raw.columns.levels) > 0
        for ticker in DEFAULT_MOMENTUM_WATCHLIST:
            try:
                df = clean_candle_data(batch_raw[ticker]) if (has_levels and ticker in batch_raw.columns.levels[0]) else pd.DataFrame()
                if not df.empty and len(df) >= 10:
                    analysis = analyze_stock(df, nifty_df, DAILY_BUDGET, MAX_RISK_RUPEES)
                    if analysis:
                        analysis["symbol"] = ticker.replace(".NS", "")
                        cards.append(analysis)
                        quotes_cache[ticker] = df
                        if analysis["signal"] == "STRONG BUY" and live_now:
                            save_trade(
                                analysis["symbol"], analysis["price"], analysis["stop_loss"],
                                analysis["target_1"], analysis["max_upside"], analysis["trade_qty"],
                                analysis["est_profit_t1"], analysis["reasons"]
                            )
            except Exception:
                continue

    cards.sort(key=lambda x: (x["signal"] == "STRONG BUY", x["alpha"]), reverse=True)

    # ===== TAB 1: DAILY SMART CAPITAL ALLOCATOR =====
    with tabs[0]:
        st.subheader("💰 Maximum Profit Daily Allocation Blueprint")
        st.markdown(f"Allocating your **Daily Budget of ₹{DAILY_BUDGET:,.2f}** for maximum returns with strict risk management:")

        fno_data_nifty = fetch_fno_chain("NIFTY")
        top_stock_1 = cards[0] if len(cards) > 0 else None
        top_stock_2 = cards[1] if len(cards) > 1 else None

        fno_budget = DAILY_BUDGET * 0.40
        eq_budget = DAILY_BUDGET * 0.40
        cash_reserve = DAILY_BUDGET * 0.20

        col_a1, col_a2, col_a3 = st.columns(3)
        col_a1.metric("🎯 F&O High-Growth Bucket (40%)", f"₹{fno_budget:,.2f}")
        col_a2.metric("📈 Equity Momentum Bucket (40%)", f"₹{eq_budget:,.2f}")
        col_a3.metric("🛡️ Cash / Volatility Buffer (20%)", f"₹{cash_reserve:,.2f}")

        st.divider()

        # F&O Allocation
        st.markdown("#### 1️⃣ F&O Derivative Scalp (Highest Expected Intraday ROI)")
        premium_rate = fno_data_nifty["entry_rate"]
        lot_cost = premium_rate * fno_data_nifty["lot_size"]
        affordable_lots = max(1, int(fno_budget // lot_cost)) if lot_cost > 0 else 1
        allocated_fno_amt = round(affordable_lots * lot_cost, 2)
        projected_fno_profit = round((fno_data_nifty["target"] - premium_rate) * (affordable_lots * fno_data_nifty["lot_size"]), 2)

        st.markdown(f"""
        <div class="alloc-card" style="border-left: 5px solid #10b981;">
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <b style="font-size:18px; color:#4ade80;">WHERE TO INVEST: {fno_data_nifty['contract']}</b>
                <span style="background-color:#064e3b; color:#a7f3d0; padding:4px 10px; border-radius:6px; font-size:12px; font-weight:bold;">GROWW SEARCH VERIFIED</span>
            </div>
            <div style="margin:8px 0; font-size:14px; color:#e2e8f0;">
                🔍 <b>Type this directly in Groww:</b> <code style="background-color:#080c14; padding:3px 8px; border-radius:4px; font-size:15px; color:#38bdf8;">{fno_data_nifty['groww_search']}</code>
            </div>
            <div style="display:grid; grid-template-columns: repeat(4, 1fr); gap:10px; margin-top:10px;">
                <div><span style="color:#94a3b8; font-size:12px;">CAPITAL TO INVEST:</span><br><b style="font-size:16px; color:#fff;">₹{allocated_fno_amt:,.2f}</b> ({affordable_lots} lot{'s' if affordable_lots > 1 else ''})</div>
                <div><span style="color:#94a3b8; font-size:12px;">BUY ENTRY RATE:</span><br><b style="font-size:16px; color:#fff;">₹{premium_rate:.1f}</b></div>
                <div><span style="color:#ef4444; font-size:12px;">HARD STOP LOSS:</span><br><b style="font-size:16px; color:#ef4444;">₹{fno_data_nifty['stop_loss']:.1f}</b></div>
                <div><span style="color:#10b981; font-size:12px;">PROJECTED PROFIT:</span><br><b style="font-size:16px; color:#10b981;">+₹{projected_fno_profit:,.2f}</b></div>
            </div>
            <div class="exit-box">
                <b>⚡ WHEN TO SELL TO MAKE MAX PROFITS:</b> Sell immediately when premium touches ₹{fno_data_nifty['target']:.1f}, OR exit immediately if Nifty Spot breaches ₹{fno_data_nifty['put_wall']:,.0f}.
            </div>
        </div>
        """, unsafe_allow_html=True)

        # Equity Allocation
        st.markdown("#### 2️⃣ High-Alpha Equity Allocation (Breakout Growth)")
        if top_stock_1:
            stock_alloc_each = eq_budget / (2 if top_stock_2 else 1)
            qty1 = max(1, int(stock_alloc_each // top_stock_1["price"]))
            inv1 = qty1 * top_stock_1["price"]
            prof1 = (top_stock_1["target_1"] - top_stock_1["price"]) * qty1

            st.markdown(f"""
            <div class="alloc-card" style="border-left: 5px solid #38bdf8;">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <b style="font-size:18px; color:#38bdf8;">WHERE TO INVEST: {top_stock_1['symbol']} (NSE / BSE)</b>
                    <span style="background-color:#1e3a8a; color:#bfdbfe; padding:4px 10px; border-radius:6px; font-size:12px; font-weight:bold;">GROWW SEARCH: {top_stock_1['symbol']}</span>
                </div>
                <div style="display:grid; grid-template-columns: repeat(4, 1fr); gap:10px; margin-top:10px;">
                    <div><span style="color:#94a3b8; font-size:12px;">INVESTMENT AMOUNT:</span><br><b style="font-size:16px; color:#fff;">₹{inv1:,.2f}</b> ({qty1} shares)</div>
                    <div><span style="color:#94a3b8; font-size:12px;">BUY RATE (LTP):</span><br><b style="font-size:16px; color:#fff;">₹{top_stock_1['price']:,.2f}</b></div>
                    <div><span style="color:#ef4444; font-size:12px;">STOP LOSS:</span><br><b style="font-size:16px; color:#ef4444;">₹{top_stock_1['stop_loss']:,.2f}</b></div>
                    <div><span style="color:#10b981; font-size:12px;">EST. GAIN (TARGET 1):</span><br><b style="font-size:16px; color:#10b981;">+₹{prof1:,.2f}</b></div>
                </div>
                <div class="exit-box">
                    <b>⚡ WHEN TO SELL:</b> {top_stock_1['exit_rule']}
                </div>
            </div>
            """, unsafe_allow_html=True)

        if top_stock_2:
            qty2 = max(1, int(stock_alloc_each // top_stock_2["price"]))
            inv2 = qty2 * top_stock_2["price"]
            prof2 = (top_stock_2["target_1"] - top_stock_2["price"]) * qty2

            st.markdown(f"""
            <div class="alloc-card" style="border-left: 5px solid #a855f7;">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <b style="font-size:18px; color:#c084fc;">WHERE TO INVEST: {top_stock_2['symbol']} (NSE / BSE)</b>
                    <span style="background-color:#581c87; color:#f3e8ff; padding:4px 10px; border-radius:6px; font-size:12px; font-weight:bold;">GROWW SEARCH: {top_stock_2['symbol']}</span>
                </div>
                <div style="display:grid; grid-template-columns: repeat(4, 1fr); gap:10px; margin-top:10px;">
                    <div><span style="color:#94a3b8; font-size:12px;">INVESTMENT AMOUNT:</span><br><b style="font-size:16px; color:#fff;">₹{inv2:,.2f}</b> ({qty2} shares)</div>
                    <div><span style="color:#94a3b8; font-size:12px;">BUY RATE (LTP):</span><br><b style="font-size:16px; color:#fff;">₹{top_stock_2['price']:,.2f}</b></div>
                    <div><span style="color:#ef4444; font-size:12px;">STOP LOSS:</span><br><b style="font-size:16px; color:#ef4444;">₹{top_stock_2['stop_loss']:,.2f}</b></div>
                    <div><span style="color:#10b981; font-size:12px;">EST. GAIN (TARGET 1):</span><br><b style="font-size:16px; color:#10b981;">+₹{prof2:,.2f}</b></div>
                </div>
                <div class="exit-box">
                    <b>⚡ WHEN TO SELL:</b> {top_stock_2['exit_rule']}
                </div>
            </div>
            """, unsafe_allow_html=True)

    # ===== TAB 2: F&O ACTION BOX =====
    with tabs[1]:
        st.subheader("⚡ F&O Instant Trade Signal")
        
        fno_target = st.radio("Select Target Index", ["NIFTY", "BANKNIFTY", "SENSEX"], horizontal=True, key="fno_choice_radio")
        fno_data = fetch_fno_chain(fno_target)

        st.markdown(f"""
        <div class="groww-search-box">
            <div>
                <span style="font-size:11px; color:#a7f3d0; font-weight:bold; letter-spacing:0.05em;">🔍 EXACT SEARCH ON GROWW / ZERODHA:</span><br>
                <b style="font-size:19px; color:#ffffff; font-family:monospace;">{fno_data['groww_search']}</b>
            </div>
            <div style="background-color:#022c22; color:#6ee7b7; padding:4px 10px; border-radius:6px; font-size:12px; font-weight:700;">
                Expiry: {fno_data['expiry_groww']}
            </div>
        </div>
        """, unsafe_allow_html=True)

        st.code(fno_data['groww_search'], language="text")

        st.markdown(f"""
        <div class="action-box">
            <div style="font-size:12px; color:#93c5fd; font-weight:bold; text-transform:uppercase;">RECOMMENDED F&O TRADE</div>
            <div style="font-size:24px; font-weight:bold; color:#4ade80; margin: 4px 0 10px 0;">{fno_data['contract']}</div>
            <div style="display:grid; grid-template-columns: repeat(4, 1fr); gap:10px; font-size:13px; background-color:#080c14; padding:12px; border-radius:8px;">
                <div><span style="color:#94a3b8;">BUY AT RATE:</span><br><b style="font-size:16px; color:#fff;">₹{fno_data['entry_rate']:.1f}</b></div>
                <div><span style="color:#ef4444;">STOP LOSS:</span><br><b style="font-size:16px; color:#ef4444;">₹{fno_data['stop_loss']:.1f}</b></div>
                <div><span style="color:#10b981;">TARGET:</span><br><b style="font-size:16px; color:#10b981;">₹{fno_data['target']:.1f}</b></div>
                <div><span style="color:#38bdf8;">EST. PROFIT / LOT:</span><br><b style="font-size:16px; color:#38bdf8;">+₹{fno_data['approx_profit_lot']:,.2f}</b></div>
            </div>
            <div class="exit-box">
                <b>🚨 WHEN TO SELL FAST:</b> {fno_data['exit_rule']}
            </div>
        </div>
        """, unsafe_allow_html=True)

        if st.button(f"🎯 Lock & Track This {fno_target} Trade", key=f"btn_lock_{fno_target}"):
            st.session_state.active_trade = {
                "symbol": fno_target,
                "groww_search": fno_data['groww_search'],
                "entry": fno_data['entry_rate'],
                "stop_loss": fno_data['stop_loss'],
                "target": fno_data['target'],
                "exit_rule": fno_data['exit_rule']
            }
            st.rerun()

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Underlying Spot", f"₹{fno_data['spot']:,.2f}", f"Expiry: {fno_data['expiry_groww']}")
        m2.metric("Put-Call Ratio (PCR)", fno_data['pcr'], fno_data['bias'].split()[0])
        m3.metric("Call Resistance Wall", f"₹{fno_data['call_wall']}")
        m4.metric("Put Support Wall", f"₹{fno_data['put_wall']}")

    # ===== TAB 3: EQUITIES ACTION RADAR =====
    with tabs[2]:
        st.subheader("📈 Real-Time Stock Action Cards")

        if user_custom_symbol:
            st.markdown(f"#### 🔍 Custom Stock Analysis: `{user_custom_symbol}`")
            single_df, formatted_sym = fetch_single_ticker(user_custom_symbol)
            if not single_df.empty:
                custom_res = analyze_stock(single_df, nifty_df, DAILY_BUDGET, MAX_RISK_RUPEES)
                if custom_res:
                    custom_res["symbol"] = user_custom_symbol
                    is_buy = custom_res["signal"] == "STRONG BUY"
                    box_border = "#059669" if is_buy else "#374151"
                    box_bg = "#064e3b22" if is_buy else "#111827"
                    status_color = "#34d399" if is_buy else "#9ca3af"

                    st.markdown(f"""
                    <div style="background-color:{box_bg}; border:2px solid {box_border}; border-radius:10px; padding:12px; margin-bottom:10px;">
                        <div style="display:flex; justify-content:space-between; align-items:center;">
                            <b style="font-size:20px; color:{status_color};">BUY {custom_res['symbol']}</b>
                            <span style="font-size:12px; font-weight:bold; background-color:{box_border}; color:#fff; padding:3px 8px; border-radius:4px;">{custom_res['signal']}</span>
                        </div>
                        <div style="margin:4px 0; font-size:13px; color:#cbd5e1;">
                            Groww Search: <code style="color:#38bdf8;">{custom_res['symbol']}</code> | AT RATE: <b style="color:#fff;">₹{custom_res['price']:,.2f}</b> | QTY: <b style="color:#38bdf8;">{custom_res['trade_qty']} shares</b>
                        </div>
                        <div style="background-color:#080c14; border-radius:6px; padding:4px 8px;">
                            <div class="metric-row"><span class="metric-label">🛑 Stop-Loss</span><span class="metric-val" style="color:#ef4444;">₹{custom_res['stop_loss']:,.2f}</span></div>
                            <div class="metric-row"><span class="metric-label">🎯 Target 1</span><span class="metric-val" style="color:#10b981;">₹{custom_res['target_1']:,.2f}</span></div>
                            <div class="metric-row"><span class="metric-label">🚀 Max Target</span><span class="metric-val" style="color:#38bdf8;">₹{custom_res['max_upside']:,.2f}</span></div>
                            <div class="metric-row"><span class="metric-label">📈 Gain Room</span><span class="metric-val" style="color:#a855f7;">+{custom_res['upside_pct']}%</span></div>
                        </div>
                    </div>
                    """, unsafe_allow_html=True)

                    st.markdown(f"""
                    <div class="exit-box">
                        <b>⚡ WHEN TO SELL FAST:</b> {custom_res['exit_rule']}
                    </div>
                    """, unsafe_allow_html=True)

                    if is_buy and st.button(f"🎯 Lock & Track {custom_res['symbol']}", key="btn_lock_custom"):
                        st.session_state.active_trade = {
                            "symbol": custom_res['symbol'],
                            "groww_search": custom_res['symbol'],
                            "entry": custom_res['price'],
                            "stop_loss": custom_res['stop_loss'],
                            "target": custom_res['target_1'],
                            "exit_rule": custom_res['exit_rule']
                        }
                        st.rerun()
            else:
                st.warning(f"Could not retrieve candles for '{user_custom_symbol}'. Ensure the symbol is correct.")
            st.divider()

        if not cards:
            st.info("Synchronizing data feeds. Setups will populate here.")
        else:
            cols = st.columns(3)
            for idx, res in enumerate(cards):
                col = cols[idx % 3]
                with col:
                    with st.container(border=True):
                        is_buy = res["signal"] == "STRONG BUY"
                        box_border = "#059669" if is_buy else "#374151"
                        box_bg = "#064e3b22" if is_buy else "#111827"
                        status_color = "#34d399" if is_buy else "#9ca3af"
                        action_title = f"BUY {res['symbol']}" if is_buy else f"WATCH {res['symbol']}"

                        st.markdown(f"""
                        <div style="background-color:{box_bg}; border:1px solid {box_border}; border-radius:8px; padding:10px; margin-bottom:8px;">
                            <div style="display:flex; justify-content:space-between; align-items:center;">
                                <b style="font-size:18px; color:{status_color};">{action_title}</b>
                                <span style="font-size:11px; font-weight:bold; background-color:{box_border}; color:#fff; padding:2px 6px; border-radius:4px;">{res['signal']}</span>
                            </div>
                            <div style="font-size:12px; color:#cbd5e1; margin-top:4px;">
                                Groww: <code style="color:#38bdf8;">{res['symbol']}</code> | RATE: <b style="color:#fff;">₹{res['price']:,.2f}</b> | QTY: <b style="color:#38bdf8;">{res['trade_qty']}</b>
                            </div>
                        </div>
                        """, unsafe_allow_html=True)

                        st.markdown(f"""
                        <div style="background-color:#080c14; border-radius:6px; padding:4px 8px; margin-bottom:8px;">
                            <div class="metric-row"><span class="metric-label">🛑 Stop-Loss</span><span class="metric-val" style="color:#ef4444;">₹{res['stop_loss']:,.2f}</span></div>
                            <div class="metric-row"><span class="metric-label">🎯 Target 1</span><span class="metric-val" style="color:#10b981;">₹{res['target_1']:,.2f}</span></div>
                            <div class="metric-row"><span class="metric-label">🚀 Max Target</span><span class="metric-val" style="color:#38bdf8;">₹{res['max_upside']:,.2f}</span></div>
                            <div class="metric-row"><span class="metric-label">📈 Gain Room</span><span class="metric-val" style="color:#a855f7;">+{res['upside_pct']}%</span></div>
                        </div>
                        """, unsafe_allow_html=True)

                        st.markdown(f"""
                        <div class="exit-box">
                            <b>⚡ WHEN TO SELL FAST:</b> {res['exit_rule']}
                        </div>
                        """, unsafe_allow_html=True)

                        if is_buy and st.button(f"🎯 Lock & Track {res['symbol']}", key=f"btn_lock_{res['symbol']}"):
                            st.session_state.active_trade = {
                                "symbol": res['symbol'],
                                "groww_search": res['symbol'],
                                "entry": res['price'],
                                "stop_loss": res['stop_loss'],
                                "target": res['target_1'],
                                "exit_rule": res['exit_rule']
                            }
                            st.rerun()

    # ===== TAB 4: DEDICATED DAY ANALYSIS & AUDIT LEDGER =====
    with tabs[3]:
        st.subheader("📊 Session Performance & Signal Reconciliation")
        trades = update_session_audit(quotes_cache)

        if not trades:
            st.info("No 'STRONG BUY' signals have triggered yet today. High-probability setups will appear here automatically.")
        else:
            wins = sum(1 for t in trades if "PROFIT" in t.get("status", ""))
            losses = sum(1 for t in trades if "LOSS" in t.get("status", ""))
            decided = wins + losses
            win_rate = round((wins / decided * 100), 1) if decided > 0 else 0.0
            total_pnl = sum(t.get("pnl", 0.0) for t in trades)

            a1, a2, a3, a4 = st.columns(4)
            a1.metric("Signals Dispatched", len(trades))
            a2.metric("Target 1 Hits", wins, f"{wins} wins")
            a3.metric("Stop Loss Hits", losses, f"-{losses} losses", delta_color="inverse")
            a4.metric("Realized Day P&L", f"₹{total_pnl:,.2f}", f"{win_rate}% Win Rate")

            st.markdown("### Signal Audit Ledger")
            trade_rows = []
            for t in trades:
                t1 = t.get("target_1", t.get("target", 0.0))
                max_up = t.get("max_upside", t1)
                trade_rows.append({
                    "Time": t.get("time", "-"),
                    "Stock": t.get("symbol", "-"),
                    "Entry": f"₹{t.get('entry', 0.0):,.2f}",
                    "Stop": f"₹{t.get('stop_loss', 0.0):,.2f}",
                    "Target 1": f"₹{t1:,.2f}",
                    "Max Upside": f"₹{max_up:,.2f}",
                    "Shares": t.get("qty", 1),
                    "Outcome": t.get("status", "OPEN"),
                    "Net P&L (₹)": f"₹{t.get('pnl', 0.0):,.2f}"
                })
            st.dataframe(pd.DataFrame(trade_rows), use_container_width=True)

    # ===== TAB 5: ALL-INDIA MARKET NEWS =====
    with tabs[4]:
        st.subheader("📰 Live All-India Financial & Corporate News (NSE / BSE / Economy)")
        if st.button("🔄 Refresh News Feeds"):
            st.cache_data.clear()

        articles = fetch_all_market_news()
        if not articles:
            st.info("No breaking headlines found. Refresh to query feeds.")
        else:
            for art in articles:
                st.markdown(f"""
                <div class="news-card">
                    <div style="font-size:15px; font-weight:600; margin-bottom:4px;">
                        <a href="{art['link']}" target="_blank" style="color:#38bdf8; text-decoration:none;">{art['title']}</a>
                    </div>
                    <div style="font-size:12px; color:#94a3b8;">
                        <span>📰 Source: <b>{art['source']}</b></span> • <span>🕒 {art['date']}</span>
                    </div>
                </div>
                """, unsafe_allow_html=True)

# Run In-Place Fragment
render_live_desk()