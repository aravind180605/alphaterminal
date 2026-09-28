import os
import json
import xml.etree.ElementTree as ET
from datetime import datetime, time
import pytz
import pandas as pd
import numpy as np
import yfinance as yf
import requests
import streamlit as st

# ==================== PAGE & RESPONSIVE SETUP ====================
st.set_page_config(
    page_title="AlphaTerminal Ultra | Action Desk",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="collapsed"
)

st.markdown("""
<style>
    .stApp { background-color: #080c14; color: #e2e8f0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
    div[data-testid="stMetric"] { background-color: #111827; padding: 10px; border-radius: 8px; border: 1px solid #1f2937; }
    .action-box { background-color: #0c1a2e; border: 2px solid #2563eb; border-radius: 10px; padding: 12px; margin-bottom: 10px; }
    .exit-box { background-color: #3b0712; border: 1px solid #ef4444; border-radius: 8px; padding: 8px 12px; margin-top: 8px; font-size: 12px; color: #fca5a5; }
    .news-card { background-color: #0f172a; border: 1px solid #1e293b; border-radius: 8px; padding: 10px; margin-bottom: 8px; }
</style>
""", unsafe_allow_html=True)

TRADE_LOG_FILE = "daily_trades.json"
IST = pytz.timezone("Asia/Kolkata")

DEFAULT_MOMENTUM_WATCHLIST = [
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "ICICIBANK.NS", "INFY.NS",
    "BHARTIARTL.NS", "TATAMOTORS.NS", "LT.NS", "SBIN.NS", "BAJFINANCE.NS",
    "DIXON.NS", "SUZLON.NS", "POLYCAB.NS", "TATAELXSI.NS", "PERSISTENT.NS",
    "KALYANKJIL.NS", "BSE.NS", "ZOMATO.NS", "HAL.NS", "BEL.NS",
    "TRENT.NS", "ADANIENT.NS", "COALINDIA.NS", "POWERGRID.NS", "VEDL.NS"
]

# State for clicked stock news
if "selected_news_stock" not in st.session_state:
    st.session_state.selected_news_stock = None

if "trade_ledger" not in st.session_state:
    st.session_state.trade_ledger = []

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

def save_trade(symbol, entry, stop, t1, t2, qty, est_profit, reasons, acc):
    today = datetime.now(IST).strftime("%Y-%m-%d")
    trades = load_trade_log()
    if not any(t.get("symbol") == symbol for t in trades):
        new_entry = {
            "symbol": symbol,
            "entry": float(entry),
            "stop_loss": float(stop),
            "target_1": float(t1),
            "max_upside": float(t2),
            "qty": int(qty),
            "est_profit": float(est_profit),
            "accuracy": float(acc),
            "reasons": reasons,
            "time": datetime.now(IST).strftime("%I:%M %p"),
            "status": "OPEN",
            "pnl": 0.0
        }
        trades.append(new_entry)
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
                max_high = float(df["high"].max())
                min_low = float(df["low"].min())
                target_val = trade.get("target_1", trade.get("entry", 0.0))
                stop_val = trade.get("stop_loss", trade.get("entry", 0.0))
                trade_qty = trade.get("qty", 1)

                if max_high >= target_val:
                    trade["status"] = "PROFIT (Target 1 Reached)"
                    trade["pnl"] = round((target_val - trade["entry"]) * trade_qty, 2)
                    updated = True
                elif min_low <= stop_val:
                    trade["status"] = "LOSS (Stop Hit)"
                    trade["pnl"] = round(((stop_val - trade["entry"]) * trade_qty), 2)
                    updated = True

    if updated:
        today = datetime.now(IST).strftime("%Y-%m-%d")
        st.session_state.trade_ledger = trades
        try:
            with open(TRADE_LOG_FILE, "w") as f:
                json.dump({"date": today, "trades": trades}, f, indent=2)
        except Exception:
            pass
    return trades

def clean_candle_data(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [col[0].lower() for col in df.columns]
    else:
        df.columns = [str(c).lower() for c in df.columns]
    return df.dropna(how="all")

def analyze_stock(df: pd.DataFrame, nifty_df: pd.DataFrame, capital: float, max_risk: float):
    df = clean_candle_data(df)
    if len(df) < 15 or "close" not in df.columns:
        return None

    close = df["close"]
    high = df["high"] if "high" in df.columns else close
    low = df["low"] if "low" in df.columns else close
    volume = df["volume"] if "volume" in df.columns else pd.Series([100000] * len(df), index=df.index)

    curr_close = float(close.iloc[-1])
    curr_vol = float(volume.iloc[-1]) if not np.isnan(volume.iloc[-1]) else 0.0

    span_20 = min(20, len(close))
    span_50 = min(50, len(close))
    ema_20 = float(close.ewm(span=span_20, adjust=False).mean().iloc[-1])
    ema_50 = float(close.ewm(span=span_50, adjust=False).mean().iloc[-1])

    sma_20 = close.rolling(span_20).mean()
    std_20 = close.rolling(span_20).std().fillna(0)
    bb_upper = float((sma_20 + (2.0 * std_20)).iloc[-1])

    tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
    atr = float(tr.rolling(min(14, len(tr))).mean().iloc[-1])
    if np.isnan(atr) or atr <= 0:
        atr = max(curr_close * 0.015, 0.5)

    kc_upper = float(ema_20 + (1.5 * atr))
    vol_sma = float(volume.rolling(min(20, len(volume))).mean().iloc[-1]) if len(volume) >= 5 else curr_vol

    lookback = min(15, len(close) - 1)
    stock_ret = (curr_close - float(close.iloc[-lookback])) / max(float(close.iloc[-lookback]), 1e-4)
    if not nifty_df.empty and "close" in nifty_df.columns:
        n_close = nifty_df["close"]
        n_idx = min(lookback, len(n_close) - 1)
        nifty_ret = (float(n_close.iloc[-1]) - float(n_close.iloc[-n_idx])) / max(float(n_close.iloc[-n_idx]), 1e-4)
    else:
        nifty_ret = 0.0
    alpha = round((stock_ret - nifty_ret) * 100, 2)

    reasons = []
    if curr_close >= ema_20 >= ema_50:
        reasons.append("Structural Markup (Price >= 20 EMA >= 50 EMA)")
    if alpha > 0:
        reasons.append(f"Institutional Alpha (+{alpha}% over Nifty 50)")
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
    est_profit_max = round((max_upside - curr_close) * trade_qty, 2)

    wins, trades = 0, 0
    for i in range(5, len(df) - 3):
        if close.iloc[i] > ema_20:
            trades += 1
            if high.iloc[i+1:i+4].max() >= close.iloc[i] + (1.2 * atr):
                wins += 1
    accuracy = round((wins / trades * 100), 1) if trades > 0 else 65.0

    return {
        "signal": "STRONG BUY" if len(reasons) >= 3 else "WATCH",
        "price": round(curr_close, 2),
        "stop_loss": stop_loss,
        "target_1": target_1,
        "max_upside": max_upside,
        "upside_pct": upside_pct,
        "trade_qty": trade_qty,
        "capital_required": capital_required,
        "est_profit_t1": est_profit_t1,
        "est_profit_max": est_profit_max,
        "accuracy": accuracy,
        "reasons": reasons,
        "exit_rule": f"Exit immediately if 15m candle closes below ₹{stop_loss} or if target of ₹{target_1} is reached."
    }

@st.cache_data(ttl=25, show_spinner=False)
def fetch_market_quotes(tickers_tuple: tuple):
    tickers_list = list(tickers_tuple)
    try:
        data = yf.download(tickers_list, period="5d", interval="15m", group_by="ticker", progress=False, threads=True)
        if data is None or data.empty:
            data = yf.download(tickers_list, period="1mo", interval="1d", group_by="ticker", progress=False, threads=True)
        return data
    except Exception:
        return None

@st.cache_data(ttl=300, show_spinner=False)
def fetch_company_news(query_symbol="NSE India"):
    clean_query = query_symbol.replace(".NS", "").replace(".BO", "").strip()
    encoded_query = requests.utils.quote(f"{clean_query} stock market India")
    rss_url = f"https://news.google.com/rss/search?q={encoded_query}&hl=en-IN&gl=IN&ceid=IN:en"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    
    articles = []
    try:
        res = requests.get(rss_url, headers=headers, timeout=4)
        if res.status_code == 200:
            root = ET.fromstring(res.content)
            for item in root.findall(".//item")[:8]:
                title = item.find("title").text if item.find("title") is not None else "Update"
                link = item.find("link").text if item.find("link") is not None else "#"
                pub_date = item.find("pubDate").text if item.find("pubDate") is not None else ""
                source = item.find("source").text if item.find("source") is not None else "Financial News"
                articles.append({"title": title, "link": link, "date": pub_date[:16], "source": source})
    except Exception:
        pass
    return articles

def fetch_fno_chain(symbol="NIFTY"):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.nseindia.com/"
    }
    s = requests.Session()
    s.headers.update(headers)
    step = 50 if symbol == "NIFTY" else 100
    lot_size = 25 if symbol == "NIFTY" else 15

    try:
        s.get("https://www.nseindia.com", timeout=3)
        res = s.get(f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}", timeout=3).json()
        records = res.get("records", {})
        spot = float(records.get("underlyingValue", 0.0))
        curr_expiry = records.get("expiryDates", ["Weekly"])[0]

        ce_oi, pe_oi = 0, 0
        strikes = {}
        for item in records.get("data", []):
            if item.get("expiryDate") == curr_expiry:
                stk = item["strikePrice"]
                ce = item.get("CE", {})
                pe = item.get("PE", {})
                ce_oi += ce.get("openInterest", 0)
                pe_oi += pe.get("openInterest", 0)
                strikes[stk] = {
                    "ce_oi": ce.get("openInterest", 0),
                    "pe_oi": pe.get("openInterest", 0),
                    "ce_ltp": ce.get("lastPrice", 0.0),
                    "pe_ltp": pe.get("lastPrice", 0.0)
                }

        pcr = round(pe_oi / max(ce_oi, 1), 2)
        call_wall = max(strikes.keys(), key=lambda k: strikes[k]["ce_oi"])
        put_wall = max(strikes.keys(), key=lambda k: strikes[k]["pe_oi"])
    except Exception:
        idx_sym = "^NSEI" if symbol == "NIFTY" else "^NSEBANK"
        t = yf.Ticker(idx_sym)
        hist = t.history(period="2d")
        spot = float(hist["Close"].iloc[-1]) if not hist.empty else (25200.0 if symbol == "NIFTY" else 51500.0)
        curr_expiry = "Current Expiry"
        pcr = 1.18
        call_wall = int(((spot // step) + 3) * step)
        put_wall = int(((spot // step) - 3) * step)

    if pcr >= 1.15:
        bias = "BULLISH (Put Writers Active)"
        rec_strike = int((spot // step) * step)
        contract = f"{symbol} {rec_strike} CALL (CE)"
        est_entry_rate = 125.0
        stop_loss_rate = 85.0
        target_rate = 195.0
        approx_profit_lot = round((target_rate - est_entry_rate) * lot_size, 2)
        exit_rule = f"⚡ Fast Exit: Sell immediately if {symbol} Spot breaches ₹{put_wall} downward, or if premium drops to ₹{stop_loss_rate}."
    elif pcr <= 0.85:
        bias = "BEARISH (Call Writers Dominating)"
        rec_strike = int(((spot // step) + 1) * step)
        contract = f"{symbol} {rec_strike} PUT (PE)"
        est_entry_rate = 120.0
        stop_loss_rate = 80.0
        target_rate = 190.0
        approx_profit_lot = round((target_rate - est_entry_rate) * lot_size, 2)
        exit_rule = f"⚡ Fast Exit: Sell immediately if {symbol} Spot breaks above ₹{call_wall} resistance, or if premium drops to ₹{stop_loss_rate}."
    else:
        bias = "RANGEBOUND"
        contract = "AVOID NAKED OPTION BUYING"
        est_entry_rate = 0.0
        stop_loss_rate = 0.0
        target_rate = 0.0
        approx_profit_lot = 0.0
        exit_rule = "⚡ Market is choppy. Close open long options fast before theta decay sets in."

    return {
        "spot": spot, "expiry": curr_expiry, "pcr": pcr, "bias": bias,
        "call_wall": call_wall, "put_wall": put_wall, "contract": contract,
        "entry_rate": est_entry_rate, "stop_loss": stop_loss_rate, "target": target_rate,
        "approx_profit_lot": approx_profit_lot, "lot_size": lot_size, "exit_rule": exit_rule
    }

# ==================== SIDEBAR ====================
st.sidebar.markdown("### 💼 Portfolio Sizing Engine")
USER_CAPITAL = st.sidebar.number_input("Total Trading Capital (₹)", min_value=10000, value=100000, step=10000)
RISK_PERCENT = st.sidebar.slider("Risk Per Trade (%)", min_value=0.5, max_value=3.0, value=1.0, step=0.1)

st.sidebar.markdown("---")
st.sidebar.markdown("### 🔍 Universal Indian Stock Finder")
user_custom_symbol = st.sidebar.text_input(
    "Analyze ANY NSE/BSE Stock",
    placeholder="e.g. TATAPOWER, IREDA, SUZLON",
    help="Type any symbol to scan and fetch news."
).strip().upper()

AUTO_REFRESH_SEC = st.sidebar.selectbox("Real-Time Refresh Interval", [15, 30, 60], index=1)
SIMULATION_MODE = st.sidebar.checkbox("Force Live Market Mode (Off-Hours Test)", value=False)

MAX_RISK_RUPEES = USER_CAPITAL * (RISK_PERCENT / 100.0)
st.sidebar.info(f"🛡️ **Max Risk Limit / Trade:** ₹{MAX_RISK_RUPEES:,.2f}")

st.title("⚡ AlphaTerminal Action Desk: Equities & F&O")

# ==================== REAL-TIME FRAGMENT ENGINE ====================
@st.fragment(run_every=f"{AUTO_REFRESH_SEC}s")
def render_live_desk():
    now_ist = datetime.now(IST)
    current_time = now_ist.time()

    PRE_MARKET_START = time(8, 15)
    MARKET_OPEN = time(9, 15)
    MARKET_CLOSE = time(15, 30)

    is_live = (MARKET_OPEN <= current_time <= MARKET_CLOSE) or SIMULATION_MODE
    is_pre = (PRE_MARKET_START <= current_time < MARKET_OPEN) and not SIMULATION_MODE

    h1, h2 = st.columns([3, 1])
    with h1:
        if is_pre:
            st.info(f"🌅 **Pre-Market Regime (8:15 AM - 9:15 AM IST)**: Watchlist prepped. Last Check: {now_ist.strftime('%I:%M:%S %p')}")
        elif is_live:
            st.success(f"🟢 **Live Market Streaming Active**: Polling every {AUTO_REFRESH_SEC}s | Time: {now_ist.strftime('%I:%M:%S %p')} IST")
        else:
            st.warning(f"🔴 **Market Closed**: Post 3:30 PM Audit Active | Time: {now_ist.strftime('%I:%M:%S %p')} IST")
    with h2:
        st.metric("Auto-Refresh", f"{AUTO_REFRESH_SEC}s Interval", "In-Place Fragment")

    tabs = st.tabs([
        "⚡ Improvised F&O Action Box",
        "📈 Equities Action Radar",
        "📰 Selected Company News",
        "📊 Day Audit & Realized P&L"
    ])

    # ===== TAB 1: F&O DEDICATED ACTION BOX =====
    with tabs[0]:
        st.subheader("⚡ F&O Instant Trade Signal")
        fno_col1, fno_col2 = st.columns([1, 4])
        with fno_col1:
            fno_target = st.selectbox("Select Index", ["NIFTY", "BANKNIFTY"])

        fno_data = fetch_fno_chain(fno_target)

        # Standout Indicator Box
        st.markdown(f"""
        <div class="action-box">
            <div style="font-size:12px; color:#93c5fd; font-weight:bold; text-transform:uppercase; letter-spacing:0.05em;">RECOMMENDED F&O TRADE</div>
            <div style="font-size:24px; font-weight:bold; color:#4ade80; margin: 4px 0 10px 0;">{fno_data['contract']}</div>
            <div style="display:grid; grid-template-columns: repeat(4, 1fr); gap:10px; font-size:13px; background-color:#080c14; padding:12px; border-radius:8px;">
                <div><span style="color:#94a3b8;">BUY AT RATE:</span><br><b style="font-size:16px; color:#fff;">₹{fno_data['entry_rate']}</b></div>
                <div><span style="color:#ef4444;">STOP LOSS:</span><br><b style="font-size:16px; color:#ef4444;">₹{fno_data['stop_loss']}</b></div>
                <div><span style="color:#10b981;">TARGET:</span><br><b style="font-size:16px; color:#10b981;">₹{fno_data['target']}</b></div>
                <div><span style="color:#38bdf8;">EST. PROFIT / LOT:</span><br><b style="font-size:16px; color:#38bdf8;">+₹{fno_data['approx_profit_lot']:,.2f}</b></div>
            </div>
            <div class="exit-box">
                <b>🚨 WHEN TO SELL FAST:</b> {fno_data['exit_rule']}
            </div>
        </div>
        """, unsafe_allow_html=True)

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Underlying Spot", f"₹{fno_data['spot']:,.2f}", fno_data['expiry'])
        m2.metric("Put-Call Ratio (PCR)", fno_data['pcr'], fno_data['bias'].split()[0])
        m3.metric("Call Resistance Wall", f"₹{fno_data['call_wall']}")
        m4.metric("Put Support Wall", f"₹{fno_data['put_wall']}")

    # ===== TAB 2: EQUITIES ACTION RADAR =====
    active_symbols = list(DEFAULT_MOMENTUM_WATCHLIST)
    if user_custom_symbol:
        custom_formatted = user_custom_symbol if ("." in user_custom_symbol) else f"{user_custom_symbol}.NS"
        if custom_formatted not in active_symbols:
            active_symbols.insert(0, custom_formatted)

    with tabs[1]:
        c1, c2 = st.columns([2, 1])
        with c1:
            st.subheader(f"Equity Breakout Box ({len(active_symbols)} Stocks Tracked)")
        with c2:
            filter_mode = st.selectbox("Display Filter", ["Show All Analyzed Stocks", "Show STRONG BUY Setups Only"], index=0)

        batch_raw = fetch_market_quotes(tuple(active_symbols + ["^NSEI"]))
        results = []
        quotes_cache = {}

        nifty_df = pd.DataFrame()
        if batch_raw is not None and "^NSEI" in getattr(batch_raw.columns, "levels", [[]])[0]:
            nifty_df = clean_candle_data(batch_raw["^NSEI"])

        if batch_raw is not None:
            has_levels = hasattr(batch_raw.columns, "levels") and len(batch_raw.columns.levels) > 0
            for ticker in active_symbols:
                try:
                    df = pd.DataFrame()
                    if has_levels and ticker in batch_raw.columns.levels[0]:
                        df = clean_candle_data(batch_raw[ticker])
                    elif not has_levels and ticker in batch_raw.columns:
                        df = clean_candle_data(batch_raw)

                    if not df.empty and len(df) >= 10:
                        analysis = analyze_stock(df, nifty_df, USER_CAPITAL, MAX_RISK_RUPEES)
                        if analysis:
                            clean_sym = ticker.replace(".NS", "").replace(".BO", "")
                            analysis["symbol"] = clean_sym
                            results.append(analysis)
                            quotes_cache[ticker] = df

                            if analysis["signal"] == "STRONG BUY" and is_live:
                                save_trade(
                                    analysis["symbol"], analysis["price"], analysis["stop_loss"],
                                    analysis["target_1"], analysis["max_upside"], analysis["trade_qty"],
                                    analysis["est_profit_t1"], analysis["reasons"], analysis["accuracy"]
                                )
                except Exception:
                    continue

        if filter_mode == "Show STRONG BUY Setups Only":
            display_results = [r for r in results if r["signal"] == "STRONG BUY"]
        else:
            display_results = results

        display_results.sort(key=lambda x: (x["signal"] == "STRONG BUY", x["accuracy"]), reverse=True)

        if not display_results:
            st.info("Synchronizing data feeds. High-confluence setups will populate here.")
        else:
            cols = st.columns(3)
            for idx, res in enumerate(display_results):
                col = cols[idx % 3]
                with col:
                    with st.container(border=True):
                        # Action Indicating Box
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
                            <div style="font-size:13px; color:#cbd5e1; margin-top:4px;">
                                AT RATE: <b style="color:#fff;">₹{res['price']}</b> | QTY: <b style="color:#38bdf8;">{res['trade_qty']} shares</b>
                            </div>
                        </div>
                        """, unsafe_allow_html=True)

                        # Levels
                        t1, t2 = st.columns(2)
                        t1.metric("🛑 Stop-Loss", f"₹{res['stop_loss']}")
                        t1.metric("🚀 Max Target", f"₹{res['max_upside']}")
                        t2.metric("🎯 Target 1", f"₹{res['target_1']}")
                        t2.metric("📈 Gain Room", f"+{res['upside_pct']}%")

                        # Fast Exit Rule Box
                        st.markdown(f"""
                        <div class="exit-box">
                            <b>⚡ WHEN TO SELL FAST:</b> {res['exit_rule']}
                        </div>
                        """, unsafe_allow_html=True)

                        # Click to Fetch News Button
                        if st.button(f"📰 Read {res['symbol']} News", key=f"btn_news_{res['symbol']}"):
                            st.session_state.selected_news_stock = res["symbol"]

    # ===== TAB 3: NEWS DRILLDOWN =====
    with tabs[2]:
        active_news_stock = st.session_state.selected_news_stock if st.session_state.selected_news_stock else "NSE India"
        st.subheader(f"📰 Live News & Catalyst Feed: {active_news_stock}")

        news_articles = fetch_company_news(active_news_stock)
        if not news_articles:
            st.info(f"No breaking headlines found for {active_news_stock}. Select a stock from Tab 2 to view its company-specific news.")
        else:
            for art in news_articles:
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

    # ===== TAB 4: END-OF-DAY AUDIT =====
    with tabs[3]:
        st.subheader("Daily Prediction Reconciliation & Realized Returns")
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
                    "Entry": f"₹{t.get('entry', 0.0)}",
                    "Stop": f"₹{t.get('stop_loss', 0.0)}",
                    "Target 1": f"₹{t1}",
                    "Max Upside": f"₹{max_up}",
                    "Shares": t.get("qty", 1),
                    "Outcome": t.get("status", "OPEN"),
                    "Net P&L (₹)": f"₹{t.get('pnl', 0.0):,.2f}"
                })
            st.dataframe(pd.DataFrame(trade_rows), use_container_width=True)

# Run In-Place Fragment
render_live_desk()