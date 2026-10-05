#!/usr/bin/env python3
"""MP Dev - Canadian wealth & tech bot. 100% free stack.

Every run (every 15 min) it looks for:
  1. BREAKING: new StatCan / Bank of Canada releases.
  2. WHALES:   big single orders on BTC, ETH, SOL, XRP (Kraken public trades).
  3. MOVERS:   big moves in crypto majors, forex majors and day-trade stocks.
  4. CONTENT:  scheduled snapshots (rates, yields, banks, crypto, forex, stocks, tips, news).
Each one is pushed to your phone (ntfy) as a ready-to-paste PROMPT (no AI key needed):
  long-press the message -> copy -> paste into Claude -> copy the tweet -> post on X.
Optional: set GEMINI_API_KEY and it sends finished drafts instead (tap -> X opens filled in).
If Gemini ever fails or runs out, it falls back to prompts by itself.

Test without keys:   python bot.py --facts
"""
import argparse
import datetime as dt
import difflib
import html
import json
import os
import re
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

# ---------------------------------------------------------------- config
HERE = Path(__file__).parent
HISTORY_FILE = HERE / "history.json"
TZ = ZoneInfo("America/Toronto")
TIMEOUT = 20
UA = {"User-Agent": "mpdev-bot/1.0"}
MAX_CHARS = 270          # X limit is 280; emojis count double, keep a margin
KEEP_TWEETS = 80
KEEP_SEEN = 1500
MAX_BREAKING = 3         # per run; the rest wait for the next run
CONTENT_HOURS = (9, 11, 13, 15, 17, 19, 21, 23)   # Toronto time; 8 content drafts/day
MAX_WHALES = 2           # per run, biggest first
MAX_MOVERS = 3           # per run, biggest first
MAX_ALERTS_PER_DAY = 30  # protects your phone and the free AI quota
KEEP_IDS = 400
WHALE_WINDOW_MIN = 35    # each run looks back this far; runs overlap, duplicates are filtered
WHALE_MAX_PAGES = 10
KRAKEN = "https://api.kraken.com/0/public/Trades"
# coin -> (Kraken pair, minimum size in USD for ONE order to count as a whale). Tune after you see how often it fires.
WHALE_PAIRS = {
    "BTC": ("XBTUSD", 1_500_000),
    "ETH": ("ETHUSD", 1_000_000),
    "SOL": ("SOLUSD", 500_000),
    "XRP": ("XRPUSD", 500_000),
}
MOVE_PCT = {"crypto": 5.0, "forex": 0.7, "stock": 4.0}  # alert when a symbol moves at least this much

GEMINI = "https://generativelanguage.googleapis.com/v1beta"
# Free-tier models, tried in order. If none work, the bot auto-discovers a working Flash model.
MODELS = [m.strip() for m in os.getenv(
    "GEMINI_MODELS", "gemini-3.5-flash-lite,gemini-3.1-flash-lite,gemini-3.5-flash"
).split(",") if m.strip()]
NTFY_SERVER = os.getenv("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
CLAUDE_URL = "https://claude.ai/new"
MAX_PROMPT_BYTES = 3800  # ntfy messages max out at 4096 bytes
RULES_SHORT = (
    "Rules: max 270 characters. One tweet only. No links or domain names. Use ONLY numbers that appear in the FACTS, copied exactly. "
    "Never invent stats or dates. Never say why something moved unless the FACTS say so. No buy/sell advice, no predictions, no hype, "
    "never claim secret or insider info. At most 1 emoji and 1 hashtag. Canadian spelling. "
    "Voice: sharp, plain-English, friendly, a little edgy, like a smart friend who read the fine print. "
    "Brand: Canada is changing, inflation is eating savings, the banks are winning, and we turn public data into insights. "
    "Credit the source in your own words (never copy a headline). Opinions are fine if clearly opinion."
)

MARKETS = {
    "TSX Composite": "^GSPTSE",
    "XEQT (iShares all-equity ETF)": "XEQT.TO",
    "VFV (Vanguard S&P 500 ETF, CAD)": "VFV.TO",
    "Oil, WTI (USD/barrel)": "CL=F",
    "Gold (USD/oz)": "GC=F",
    "Bitcoin (CAD)": "BTC-CAD",
}
BANKS = {
    "RBC": "RY.TO",
    "TD": "TD.TO",
    "BMO": "BMO.TO",
    "Scotiabank": "BNS.TO",
    "CIBC": "CM.TO",
}
CRYPTO = {"Bitcoin (USD)": "BTC-USD", "Ethereum (USD)": "ETH-USD", "Solana (USD)": "SOL-USD", "XRP (USD)": "XRP-USD"}
FOREX = {  # the majors
    "EUR/USD": "EURUSD=X", "GBP/USD": "GBPUSD=X", "USD/JPY": "USDJPY=X", "USD/CAD": "USDCAD=X",
    "AUD/USD": "AUDUSD=X", "USD/CHF": "USDCHF=X", "NZD/USD": "NZDUSD=X",
}
STOCKS = {  # liquid, day-trader favourites
    "Tesla": "TSLA", "Nvidia": "NVDA", "AMD": "AMD", "Apple": "AAPL", "Microsoft": "MSFT", "Amazon": "AMZN",
    "Meta": "META", "Google": "GOOGL", "Palantir": "PLTR", "Coinbase": "COIN", "Strategy (MSTR)": "MSTR",
    "Shopify": "SHOP.TO", "SPY (S&P 500 ETF)": "SPY", "QQQ (Nasdaq 100 ETF)": "QQQ",
}
NEWS_FEEDS = {
    "CBC Business": "https://www.cbc.ca/webfeed/rss/rss-business",
    "Financial Post": "https://financialpost.com/feed/",
}
WATCH_FEEDS = {  # official sources: where the data lands first
    "Statistics Canada - Labour": "https://www150.statcan.gc.ca/n1/rss/dai-quo/14-eng.atom",
    "Statistics Canada - Prices": "https://www150.statcan.gc.ca/n1/rss/dai-quo/18-eng.atom",
    "Statistics Canada - Economy": "https://www150.statcan.gc.ca/n1/rss/dai-quo/36-eng.atom",
    "Statistics Canada - Income and wealth": "https://www150.statcan.gc.ca/n1/rss/dai-quo/11-eng.atom",
    "Bank of Canada": "https://www.bankofcanada.ca/content_type/press-releases/feed/",
}
BOC_RELEVANT = re.compile(r"rate|inflation|monetary|policy|mortgage|bond|dollar|econom|financial|projection|forecast", re.I)
VALET = "https://www.bankofcanada.ca/valet/observations/{}/json"
YIELDS = {"2-year": "BD.CDN.2YR.DQ.YLD", "5-year": "BD.CDN.5YR.DQ.YLD", "10-year": "BD.CDN.10YR.DQ.YLD"}

# VERIFY these against canada.ca every January. The AI may only quote numbers
# that appear here or in the live data.
CANADA_FACTS = [
    "TFSA annual contribution limit (2026): $7,000. Withdrawals are added back to your room the next calendar year.",
    "FHSA: $8,000 per year, $40,000 lifetime. Contributions are deductible, qualifying withdrawals for a first home are tax-free.",
    "Home Buyers' Plan: you can withdraw up to $60,000 from your RRSP for a first home, repaid over 15 years.",
    "RRSP room is 18% of last year's earned income up to a yearly maximum. Check CRA My Account for your exact room.",
    "RESP: the government CESG grant is 20% on the first $2,500 contributed per year (max $500/yr, $7,200 lifetime).",
]

SYSTEM = """You write tweets for @MPdev (MP Dev), a Canada-based page about money and tech.
Brand: "Canada is changing. Inflation is eating your savings, and the banks are winning. We turn public data
and AI into insights regular Canadians can use. Follow to get the edge." Build. Scale. Profit.
Voice: sharp, confident, plain-English, a little edgy, Canadian spelling. A smart friend who read the fine print, not a bank.
Hard rules:
- Max 270 characters. One tweet only. No threads.
- NO links, URLs or domain names.
- Use ONLY numbers that appear in the FACTS. Copy them exactly. Never invent stats, prices or dates.
- The edge is speed and clarity on PUBLIC data. Never claim secret, insider or non-public information.
- Never tell people to buy or sell a specific investment, and never promise returns. Educate, don't advise.
- Never say WHY something moved unless the FACTS say so. Prices can be delayed ~15 minutes: say "at last check".
- Whale alerts are one big order on one exchange, not total market flow. Never predict price.
- Opinions are fine when framed as opinion. No accusations of crime or fraud. No party politics.
- No hype words (guaranteed, get rich, can't lose). At most 1 emoji and at most 1 hashtag (optional).
- If you use news, say it in your own words and credit the source ("via CBC", "per StatCan"). Never copy a headline.
Reply with ONLY the tweet text. No quotes, no preface."""

# name -> (needs live data?, instruction)
TOPICS = {
    "markets": (True, "Market snapshot for Canadians. Lead with the most interesting move, then one plain-English takeaway."),
    "rates": (True, "Rates, bond yields and the loonie. Explain what the Bank of Canada rate, the 5-year bond yield or USD/CAD means for everyday Canadians (mortgages, savings, travel, shopping)."),
    "banks": (True, "The Big 5 banks' stocks today. Who moved most, and one sharp, fair point about the banks vs your savings or mortgage. Don't tell anyone to buy or sell."),
    "tip": (False, "One practical money tip for Canadians (TFSA, RRSP, FHSA, RESP, emergency fund, HISA, credit score). Specific and actionable."),
    "news": (True, "Pick the single most useful money story below and give a short, original take on why it matters to Canadians."),
    "tech": (False, "One AI or tech tool/habit a Canadian can use to save time, cut costs, or earn on the side. No price claims."),
    "mistake": (False, "A common money mistake Canadians make and the simple fix. Start with the mistake."),
    "poll": (False, "An engagement tweet: a this-or-that question about Canadian money (TFSA vs RRSP, rent vs buy, GIC vs stocks). Invite replies."),
    "crypto": (True, "Crypto majors snapshot (BTC, ETH, SOL, XRP). Lead with the biggest mover. No predictions, no buy/sell calls."),
    "forex": (True, "Forex majors snapshot. Lead with the biggest move; tie it to Canadians if USD/CAD is in the facts (travel, shopping, US-priced stuff)."),
    "stocks": (True, "Day-trader watchlist: which of these liquid names are moving most today. Facts only, no buy/sell calls, no reasons for the move."),
    "housing": (False, "One plain-English point about renting, buying or mortgage renewals in Canada. No numbers."),
    "hot": (False, "A sharp, defensible hot take on how everyday money works in Canada (bank fees, savings rates, mortgage renewals, inflation eating cash). Clearly an opinion, no invented numbers."),
}
ORDER = ["markets", "rates", "banks", "crypto", "forex", "stocks", "tip", "news", "tech", "mistake", "poll", "housing", "hot"]  # 13 topics vs 8 slots: rotates daily
MARKET_TOPICS = {"markets", "banks", "stocks", "forex"}  # skipped on weekends

BREAKING_TASK = (
    "BREAKING: an official release just dropped. Start with what was released (you may open with one siren emoji), "
    "give the headline number exactly as written in the FACTS, then one sentence on what it means for Canadians' wallets "
    "(rates, jobs, prices, mortgages). Credit the source in plain words."
)


WHALE_TASK = (
    "WHALE ALERT: a very large single order just hit the market. Lead with the whale emoji and the coin, say whether it was a buy or sell, "
    "the size and dollar value exactly as written in the FACTS, then one plain-English sentence of context. No predictions, no advice."
)
MOVE_TASK = (
    "MOVER ALERT: one symbol just made a big move. Say what moved and by how much (copy the numbers exactly), at last check, "
    "plus one line on why traders watch it. No reasons for the move unless in the FACTS. No predictions, no buy/sell calls."
)


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def need(name):
    v = os.getenv(name, "").strip()
    if not v:
        raise SystemExit(f"Missing secret/env var: {name}")
    return v


# ---------------------------------------------------------------- text + feeds
def strip_html(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def parse_feed(content):
    """RSS or Atom -> [{title, link, id, summary}], newest first as published."""
    root = ET.fromstring(content)
    items = []
    for el in root.iter():
        if _local(el.tag) not in ("item", "entry"):
            continue
        f = {}
        for c in el:
            name = _local(c.tag)
            if name == "link":
                href = c.get("href") or (c.text or "").strip()
                if href and c.get("rel") in (None, "alternate"):
                    f.setdefault("link", href)
            elif name in ("title", "summary", "description", "content", "id", "guid"):
                f.setdefault(name, strip_html("".join(c.itertext())))
        title = f.get("title", "")
        if not title:
            continue
        items.append({
            "title": title,
            "link": f.get("link", ""),
            "id": f.get("id") or f.get("guid") or f.get("link") or title,
            "summary": f.get("summary") or f.get("description") or f.get("content") or "",
        })
    return items


def load_feed(url):
    r = requests.get(url, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    return parse_feed(r.content)


def page_text(url, limit=1200):
    """First few real paragraphs of a release page (for key figures)."""
    if not url:
        return ""
    try:
        r = requests.get(url, headers=UA, timeout=TIMEOUT)
        r.raise_for_status()
        body = re.sub(r"(?is)<(script|style|nav|header|footer)[^>]*>.*?</\1>", " ", r.text)
        paras = [strip_html(p) for p in re.findall(r"(?is)<p[^>]*>(.*?)</p>", body)]
        return " ".join([p for p in paras if len(p) > 60][:5])[:limit]
    except Exception as e:
        log(f"page_text failed for {url}: {e}")
        return ""


# ---------------------------------------------------------------- live data
def valet(series, recent):
    """Bank of Canada Valet API -> [(date, float)] oldest first."""
    r = requests.get(VALET.format(series), params={"recent": recent}, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    rows = []
    for o in r.json().get("observations", []):
        v = (o.get(series) or {}).get("v")
        if v not in (None, ""):
            rows.append((o["d"], float(v)))
    return rows


def fetch_rates():
    out = []
    try:
        rows = valet("V39079", 400)  # target for the overnight rate
        date, cur = rows[-1]
        changed = None
        for i in range(len(rows) - 1, 0, -1):
            if rows[i][1] != rows[i - 1][1]:
                changed = (rows[i][0], rows[i - 1][1])
                break
        line = f"Bank of Canada policy rate: {cur:.2f}% (as of {date})"
        if changed:
            line += f"; last change took effect {changed[0]}, from {changed[1]:.2f}%"
        else:
            line += f"; no change since at least {rows[0][0]}"
        out.append(line)
    except Exception as e:
        log(f"rates policy failed: {e}")
    try:
        rows = valet("FXUSDCAD", 6)
        (d, last), (_, prev) = rows[-1], rows[-2]
        out.append(
            f"USD/CAD: 1 USD = {last:.4f} CAD (1 CAD = {1 / last:.4f} USD), "
            f"{(last / prev - 1) * 100:+.2f}% vs previous observation (as of {d})"
        )
    except Exception as e:
        log(f"rates fx failed: {e}")
    got_yield = False
    for label, sid in YIELDS.items():
        try:
            rows = valet(sid, 5)
            (d, last), (_, prev) = rows[-1], rows[-2]
            out.append(f"Government of Canada {label} bond yield: {last:.2f}% ({last - prev:+.2f} points vs previous day, as of {d})")
            got_yield = True
        except Exception as e:
            log(f"rates yield {label} failed: {e}")
    if got_yield:
        out.append("Note: the 5-year bond yield is the main driver of fixed mortgage rates.")
    return out


def quotes(tickers, dp=2):
    out = []
    try:
        import yfinance as yf
    except Exception as e:
        log(f"yfinance import failed: {e}")
        return out
    for name, sym in tickers.items():
        try:
            closes = yf.Ticker(sym).history(period="10d")["Close"].dropna()
            if len(closes) < 2:
                continue
            last, prev = float(closes.iloc[-1]), float(closes.iloc[-2])
            day = closes.index[-1].strftime("%Y-%m-%d")
            out.append(f"{name}: {last:,.{dp}f} ({(last / prev - 1) * 100:+.2f}% vs previous close, as of {day})")
        except Exception as e:
            log(f"quotes {sym} failed: {e}")
    return out


def fetch_news():
    out = []
    for src, url in NEWS_FEEDS.items():
        try:
            for it in load_feed(url)[:6]:
                out.append(f"[{src}] {it['title']}")
        except Exception as e:
            log(f"news {src} failed: {e}")
    return out


FETCHERS = {
    "markets": lambda: quotes(MARKETS),
    "banks": lambda: quotes(BANKS),
    "crypto": lambda: quotes(CRYPTO),
    "forex": lambda: quotes(FOREX, dp=4),
    "stocks": lambda: quotes(STOCKS),
    "rates": fetch_rates,
    "news": fetch_news,
}


def gather(topic):
    """Return (topic_used, facts). Falls back to 'tip' if live data is unavailable."""
    now = dt.datetime.now(TZ)
    header = f"Today (Toronto time): {now:%A, %B} {now.day}, {now.year}"
    if TOPICS[topic][0]:
        data = FETCHERS[topic]()
        if data:
            return topic, [header] + data
        log(f"no data for '{topic}', falling back to 'tip'")
        topic = "tip"
    return topic, [header] + CANADA_FACTS


def pick_topic(n, weekday):
    for i in range(len(ORDER)):
        name = ORDER[(n + i) % len(ORDER)]
        if name in MARKET_TOPICS and weekday >= 5:  # stock + forex markets are closed on weekends
            continue
        return name
    return "tip"


# ---------------------------------------------------------------- breaking watch
def fetch_watch():
    """-> {source: [items]} for the feeds that loaded."""
    feeds = {}
    for src, url in WATCH_FEEDS.items():
        try:
            feeds[src] = load_feed(url)
        except Exception as e:
            log(f"watch {src} failed: {e}")
    return feeds


def find_new(hist, persist=True):
    """New, relevant official releases since last run. First ever run just sets a baseline."""
    feeds = fetch_watch()
    if "seen" not in hist:
        if persist:
            hist["seen"] = [it["id"] for items in feeds.values() for it in items][-KEEP_SEEN:]
            log(f"baseline set: {len(hist['seen'])} existing items marked as seen")
        return []
    seen = set(hist["seen"])
    fresh = []
    for src, items in feeds.items():
        for it in reversed(items):  # oldest first
            if it["id"] in seen:
                continue
            if src == "Bank of Canada" and not BOC_RELEVANT.search(it["title"]):
                if persist:
                    hist["seen"].append(it["id"])
                continue
            fresh.append((src, it))
    return fresh


# ---------------------------------------------------------------- whales + movers
def kraken_trades(pair, since_ns):
    """Recent public trades from Kraken. Row: [price, volume, time, 'b'/'s' (taker side), ...]."""
    rows, since = [], str(since_ns)
    for _ in range(WHALE_MAX_PAGES):
        r = requests.get(KRAKEN, params={"pair": pair, "since": since, "count": 1000}, headers=UA, timeout=TIMEOUT)
        r.raise_for_status()
        j = r.json()
        if j.get("error"):
            raise RuntimeError(f"kraken: {j['error']}")
        res = j["result"]
        page = res[next(k for k in res if k != "last")]
        rows += page
        since = res["last"]
        if len(page) < 1000:
            break
        time.sleep(1)
    return rows


def find_whales(hist, now_ts):
    """Single large orders (fills in the same second are grouped), biggest first."""
    seen = set(hist.get("whales_seen", []))
    since_ns = int((now_ts - WHALE_WINDOW_MIN * 60) * 1e9)
    out = []
    for coin, (pair, min_usd) in WHALE_PAIRS.items():
        try:
            rows = kraken_trades(pair, since_ns)
        except Exception as e:
            log(f"whales {coin} failed: {e}")
            continue
        groups = {}
        for row in rows:
            price, vol, ts, side = float(row[0]), float(row[1]), float(row[2]), row[3]
            g = groups.setdefault((int(ts), side), [0.0, 0.0])
            g[0] += vol
            g[1] += vol * price
        for (sec, side), (qty, usd) in groups.items():
            wid = f"{coin}-{sec}-{side}"
            if usd < min_usd or wid in seen:
                continue
            when = dt.datetime.fromtimestamp(sec, TZ)
            out.append({
                "id": wid, "sym": coin, "side": "buy" if side == "b" else "sell", "usd": usd,
                "facts": [
                    f"{coin}: a single
