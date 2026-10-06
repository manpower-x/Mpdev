#!/usr/bin/env python3
"""MP Dev - worldwide wealth & tech bot. 100% free stack.

Every run (every 15 min) it looks for:
  1. BREAKING: new StatCan / Bank of Canada releases.
  2. MOVERS:   big moves in crypto majors, forex majors and day-trade stocks.
  3. CONTENT:  scheduled snapshots every 2 hours, 24/7 (world markets, rates, banks, crypto, forex, stocks, tips, news).
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
CONTENT_HOURS = tuple(range(1, 24, 2))   # Toronto time; a draft every 2 hours, 24/7
MAX_MOVERS = 3           # per run, biggest first
MAX_ALERTS_PER_DAY = 30  # protects your phone and the free AI quota
KEEP_IDS = 400
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
    "Style: open with the number or the surprise in the first few words, then one plain sentence on what it means for people's wallets, optional short closer. Short sentences, no jargon, no filler, no 'Breaking' unless the task says so. Voice: sharp, friendly, a little edgy, like a smart friend who read the fine print. Opinions are fine if clearly opinion. Rules: max 270 characters. One tweet only. No links or domain names. Use ONLY numbers that appear in the FACTS, copied exactly. Never invent stats or dates. Never say why something moved unless the FACTS say so. No buy/sell advice, no predictions, no hype, never claim secret or insider info. At most 1 emoji and 1 hashtag. Credit the source in your own words (never copy a headline). Prices can lag, so say 'at last check'."
)

MARKETS = {'S&P 500': '^GSPC',
 'Nasdaq': '^IXIC',
 'Dow Jones': '^DJI',
 'TSX Composite': '^GSPTSE',
 'FTSE 100': '^FTSE',
 'DAX (Germany)': '^GDAXI',
 'Nikkei 225 (Japan)': '^N225',
 'Hang Seng (Hong Kong)': '^HSI',
 'Oil, WTI (USD/barrel)': 'CL=F',
 'Gold (USD/oz)': 'GC=F',
 'Bitcoin (USD)': 'BTC-USD'}
BANKS = {'JPMorgan': 'JPM',
 'Goldman Sachs': 'GS',
 'Bank of America': 'BAC',
 'Citigroup': 'C',
 'HSBC': 'HSBC',
 'Royal Bank of Canada': 'RY.TO'}
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

SYSTEM = """You write tweets for @MPdev (MP Dev), a page about wealth, markets and tech for everyone, with a Canadian angle when it fits.
Brand: "Decoding the world's money and tech. Live data + AI on rates, crypto, forex and stocks. Follow to get the edge." Build. Scale. Profit.
Voice: sharp, confident, plain-English, a little edgy. A smart friend who read the fine print, not a bank.
Hard rules:
- Max 270 characters. One tweet only. No threads.
- NO links, URLs or domain names.
- Use ONLY numbers that appear in the FACTS. Copy them exactly. Never invent stats, prices or dates.
- The edge is speed and clarity on PUBLIC data. Never claim secret, insider or non-public information.
- Never tell people to buy or sell a specific investment, and never promise returns. Educate, don't advise.
- Never say WHY something moved unless the FACTS say so. Prices can be delayed ~15 minutes: say "at last check".
- Never predict price.
- Opinions are fine when framed as opinion. No accusations of crime or fraud. No party politics.
- No hype words (guaranteed, get rich, can't lose). At most 1 emoji and at most 1 hashtag (optional).
- If you use news, say it in your own words and credit the source ("via CNBC", "per the Fed"). Never copy a headline.
Reply with ONLY the tweet text. No quotes, no preface."""

# name -> (needs live data?, instruction)
TOPICS = {'markets': (True,
             'World market snapshot. Lead with the most interesting move across stocks, oil, gold and bitcoin, then one '
             'plain-English takeaway.'),
 'rates': (True,
           'Interest rates and bond yields. Explain what the Bank of Canada policy rate or a Canadian bond yield means for '
           "everyday people's borrowing and savings; one line on why the rest of the world watches it too."),
 'banks': (True,
           "The big global banks' stocks today. Who moved most, and one sharp, fair point about banks vs your savings or loans. "
           "Don't tell anyone to buy or sell."),
 'tip': (False,
         'One practical money tip for the Canadian corner of our audience (TFSA, RRSP, FHSA, RESP). Specific and actionable.'),
 'news': (True,
          'Pick the single most useful world money or tech story below and give a short, original take on why it matters to '
          'everyday people.'),
 'tech': (False, 'One AI or tech tool or habit anyone can use to save time, cut costs, or earn on the side. No price claims.'),
 'mistake': (False, 'A common money mistake people make and the simple fix. Start with the mistake.'),
 'poll': (False,
          'An engagement tweet: a this-or-that question about money or tech (rent vs buy, stocks vs crypto, save vs invest, AI '
          'tool A vs B). Invite replies.'),
 'crypto': (True, 'Crypto majors snapshot (BTC, ETH, SOL, XRP). Lead with the biggest mover. No predictions, no buy/sell calls.'),
 'forex': (True, 'Forex majors snapshot. Lead with the biggest move and what it means for travel, imports and big companies.'),
 'stocks': (True,
            'Day-trader watchlist: which of these liquid names are moving most today. Facts only, no buy/sell calls, no reasons '
            'for the move.'),
 'housing': (False, 'One plain-English point about renting, buying or mortgage renewals. No numbers.'),
 'hot': (False,
         'A sharp, defensible hot take on how everyday money works (bank fees, savings rates, mortgages, inflation eating cash, '
         'AI changing jobs). Clearly an opinion, no invented numbers.')}
ORDER = ["markets", "rates", "banks", "crypto", "forex", "stocks", "tip", "news", "tech", "mistake", "poll", "housing", "hot"]  # 13 topics vs 8 slots: rotates daily
MARKET_TOPICS = {"markets", "banks", "stocks", "forex"}  # skipped on weekends

BREAKING_TASK = (
    "BREAKING: an official release just dropped. Start with what was released (you may open with one siren emoji), give the headline number exactly as written in the FACTS, then one sentence on what it means for everyday people's wallets (rates, jobs, prices, mortgages). Credit the source in plain words."
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


# ---------------------------------------------------------------- movers
def detect_movers(hist, now):
    """Big moves in crypto majors (24h), forex majors and day-trade stocks (today). One alert per symbol/direction/day."""
    groups = {"crypto": CRYPTO}
    mins = now.hour * 60 + now.minute
    if now.weekday() < 5:
        groups["forex"] = FOREX
        if 9 * 60 + 30 <= mins <= 16 * 60:
            groups["stock"] = STOCKS
    try:
        import yfinance as yf
    except Exception as e:
        log(f"yfinance import failed: {e}")
        return []
    seen = set(hist.get("moves_seen", []))
    out = []
    for kind, tickers in groups.items():
        for name, sym in tickers.items():
            try:
                if kind == "crypto":
                    c = yf.Ticker(sym).history(period="3d", interval="1h")["Close"].dropna()
                    if len(c) < 25:
                        continue
                    last, ref, label = float(c.iloc[-1]), float(c.iloc[-25]), "over the last 24 hours"
                else:
                    c = yf.Ticker(sym).history(period="10d")["Close"].dropna()
                    if len(c) < 2:
                        continue
                    if kind == "stock" and c.index[-1].date() != now.date():
                        continue  # no live bar for today yet
                    last, ref, label = float(c.iloc[-1]), float(c.iloc[-2]), "today vs previous close"
                pct = (last / ref - 1) * 100
                if abs(pct) < MOVE_PCT[kind]:
                    continue
                mid = f"{sym}-{now:%Y-%m-%d}-{'up' if pct > 0 else 'down'}"
                if mid in seen:
                    continue
                dp = 4 if kind == "forex" else 2
                out.append({
                    "id": mid, "sym": name, "pct": pct,
                    "facts": [f"{name}: {last:,.{dp}f} ({pct:+.2f}% {label}, as of {c.index[-1]:%Y-%m-%d %H:%M}). "
                              "Prices can be delayed about 15 minutes."],
                })
            except Exception as e:
                log(f"movers {sym} failed: {e}")
    return sorted(out, key=lambda m: -abs(m["pct"]))


def alerts_left(hist, now):
    a = hist.setdefault("alerts", {"day": "", "n": 0})
    today = f"{now:%Y-%m-%d}"
    if a.get("day") != today:
        a["day"], a["n"] = today, 0
    return MAX_ALERTS_PER_DAY - a["n"]


# ---------------------------------------------------------------- validation
NUM = re.compile(r"(\$)?(\d[\d,]*(?:\.\d+)?)(%)?")


def _to_float(s):
    return float(s.replace(",", ""))


def allowed_numbers(facts):
    allowed = set()
    for m in NUM.finditer("\n".join(facts)):
        v = _to_float(m.group(2))
        for x in (v, round(v), round(v, 1), round(v, 2)):
            allowed.add(round(x, 4))
    return allowed


def unknown_numbers(text, facts):
    allowed = allowed_numbers(facts)
    bad = []
    for m in NUM.finditer(text):
        v = _to_float(m.group(2))
        is_money_or_pct = bool(m.group(1) or m.group(3))
        if float(v).is_integer() and v <= 10 and not is_money_or_pct:
            continue
        if round(v, 4) not in allowed:
            bad.append(m.group(0))
    return bad


def validate(text, facts, recent):
    if not text:
        return ["empty reply"]
    problems = []
    if len(text) > MAX_CHARS:
        problems.append(f"{len(text)} characters, must be {MAX_CHARS} or fewer")
    if re.search(r"https?://|www\.|\b\w+\.(com|ca|org|net|io)\b", text, re.I):
        problems.append("no links or domain names")
    bad = unknown_numbers(text, facts)
    if bad:
        problems.append("these numbers are not in the FACTS: " + ", ".join(bad))
    if re.search(r"\b(guaranteed|can'?t lose|get rich|buy now|sell now)\b", text, re.I):
        problems.append("no hype or buy/sell calls")
    for old in recent:
        if difflib.SequenceMatcher(None, text.lower(), old.lower()).ratio() > 0.7:
            problems.append("too similar to a recent tweet")
            break
    return problems


def clean(text):
    text = (text or "").strip()
    text = re.sub(r"^(tweet|draft)\s*:\s*", "", text, flags=re.I)
    if len(text) > 1 and text[0] in "\"'“" and text[-1] in "\"'”":
        text = text[1:-1].strip()
    return text


# ---------------------------------------------------------------- AI (Gemini free tier)
_state = {"model": None}


def _gemini_headers():
    return {"x-goog-api-key": need("GEMINI_API_KEY"), "Content-Type": "application/json"}


def _call(model, contents):
    return requests.post(
        f"{GEMINI}/models/{model}:generateContent",
        headers=_gemini_headers(),
        json={
            "systemInstruction": {"parts": [{"text": SYSTEM}]},
            "contents": contents,
            "generationConfig": {"temperature": 0.8, "maxOutputTokens": 2048},
        },
        timeout=60,
    )


def discover_models():
    """Ask Google which Flash models this key can use (only runs if the configured ones fail)."""
    r = requests.get(f"{GEMINI}/models", params={"pageSize": 200}, headers=_gemini_headers(), timeout=TIMEOUT)
    r.raise_for_status()
    skip = re.compile(r"tts|live|image|audio|transcribe|embed|robotics|computer|translate|veo|imagen|aqa|learnlm|gemma", re.I)
    names = [
        m["name"].split("/", 1)[-1]
        for m in r.json().get("models", [])
        if "generateContent" in m.get("supportedGenerationMethods", [])
    ]
    ok = [n for n in names if "flash" in n and not skip.search(n)]
    return sorted(ok, key=lambda n: ("lite" not in n, n))[:6]


def _text_of(data):
    cands = data.get("candidates") or []
    if not cands:
        return ""
    parts = (cands[0].get("content") or {}).get("parts") or []
    return "".join(p.get("text", "") for p in parts if not p.get("thought"))


def ask(contents):
    order = ([_state["model"]] if _state["model"] else []) + [m for m in MODELS if m != _state["model"]]
    last = "no models configured"
    for round_ in (1, 2):
        if round_ == 2:
            try:
                order = [m for m in discover_models() if m not in order]
            except Exception as e:
                log(f"model discovery failed: {e}")
                break
        for model in order:
            r = _call(model, contents)
            if r.status_code == 200:
                _state["model"] = model
                return _text_of(r.json())
            last = f"{model}: HTTP {r.status_code} {r.text[:200]}"
            log(last)
            if r.status_code in (401, 403) or (r.status_code == 400 and "API key" in r.text):
                raise RuntimeError(f"Gemini key problem - check GEMINI_API_KEY. {last}")
    raise RuntimeError(f"Gemini failed on every model. Last: {last}")


def generate(task, facts, recent):
    avoid = "\n".join(f"- {t}" for t in recent[-12:]) or "(none yet)"
    prompt = (
        f"Task: {task}\n\n"
        "FACTS (the only numbers you may use):\n" + "\n".join(facts) + "\n\n"
        f"Recent tweets (do not repeat their angle or wording):\n{avoid}\n\n"
        "Reply with ONLY the tweet text."
    )
    contents = [{"role": "user", "parts": [{"text": prompt}]}]
    for attempt in range(1, 4):
        text = clean(ask(contents))
        problems = validate(text, facts, recent)
        if not problems:
            return text
        log(f"attempt {attempt} rejected: {problems}")
        contents += [
            {"role": "model", "parts": [{"text": text or "(empty)"}]},
            {"role": "user", "parts": [{"text": "Rejected: " + "; ".join(problems) + ". Rewrite it. Reply with ONLY the tweet text."}]},
        ]
    raise RuntimeError("AI could not produce a valid tweet after 3 attempts")
# ---------------------------------------------------------------- phone push
def notify(text, label, urgent=False):
    """Push the draft via ntfy. Tapping it opens X with the tweet pre-filled."""
    channel = need("NTFY_TOPIC")
    link = "https://x.com/intent/post?text=" + urllib.parse.quote(text, safe="")
    headers = {
        "Title": f"MP Dev - {label}",
        "Click": link,
        "Actions": f"view, Post on X, {link}",
        "Tags": "rotating_light" if urgent else "memo",
    }
    if urgent:
        headers["Priority"] = "high"
    r = requests.post(f"{NTFY_SERVER}/{channel}", data=text.encode("utf-8"), headers=headers, timeout=TIMEOUT)
    r.raise_for_status()
    return link


INTRO = (
    "You write tweets for MP Dev (@MPdev), an X page about wealth, markets and tech for everyday people worldwide, with a Canadian angle when it fits. The audience is regular people (beginners to side-hustlers) who want live data explained in plain English. The page's edge is speed and clarity on PUBLIC data. Write ONE tweet."
)


def build_prompt(task, facts):
    """A complete, self-contained prompt to paste into Claude."""
    facts = list(facts)

    def make(fs):
        return (
            INTRO + "\n\n"
            f"Task: {task}\n\nFACTS:\n" + "\n".join(fs) + f"\n\n{RULES_SHORT}\nReply with ONLY the tweet text."
        )

    text = make(facts)
    while len(text.encode("utf-8")) > MAX_PROMPT_BYTES and len(facts) > 2:
        facts.pop()
        text = make(facts)
    if len(text.encode("utf-8")) > MAX_PROMPT_BYTES:
        text = text.encode("utf-8")[:MAX_PROMPT_BYTES].decode("utf-8", "ignore")
    return text


def notify_prompt(prompt, label, urgent=False):
    """Push the prompt via ntfy. Long-press to copy, then tap 'Open Claude'."""
    channel = need("NTFY_TOPIC")
    headers = {
        "Title": f"MP Dev PROMPT - {label}",
        "Actions": f"view, Open Claude, {CLAUDE_URL}",
        "Tags": "rotating_light" if urgent else "robot",
    }
    if urgent:
        headers["Priority"] = "high"
    r = requests.post(f"{NTFY_SERVER}/{channel}", data=prompt.encode("utf-8"), headers=headers, timeout=TIMEOUT)
    r.raise_for_status()


# ---------------------------------------------------------------- history
def load_history():
    try:
        h = json.loads(HISTORY_FILE.read_text())
        if isinstance(h, dict) and isinstance(h.get("tweets"), list):
            h.setdefault("n", len(h["tweets"]))
            h.setdefault("moves_seen", [])
            h.setdefault("alerts", {"day": "", "n": 0})
            return h
    except FileNotFoundError:
        pass
    except Exception as e:
        log(f"history unreadable, starting fresh: {e}")
    return {"n": 0, "tweets": [], "moves_seen": [], "alerts": {"day": "", "n": 0}}


def save_history(h):
    h["tweets"] = h["tweets"][-KEEP_TWEETS:]
    if "seen" in h:
        h["seen"] = h["seen"][-KEEP_SEEN:]
    for k in ("moves_seen",):
        h[k] = h.get(k, [])[-KEEP_IDS:]
    HISTORY_FILE.write_text(json.dumps(h, indent=1, ensure_ascii=False) + "\n")


def write_summary(lines):
    path = os.getenv("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--facts", action="store_true", help="print the data for the next topic + what alerts are waiting, then exit")
    ap.add_argument("--dry", action="store_true", help="write drafts but do not push to phone or save state")
    ap.add_argument("--topic", choices=ORDER, help="force a content topic now")
    args = ap.parse_args()
    persist = not (args.dry or args.facts)

    hist = load_history()
    now = dt.datetime.now(TZ)
    stamp = f"Today (Toronto time): {now:%A, %B} {now.day}, {now.year}"
    errors, sent = [], []

    fresh = find_new(hist, persist)
    movers = detect_movers(hist, now)
    slot = f"{now:%Y-%m-%d}-{now.hour}"
    content_due = bool(args.topic) or (now.hour in CONTENT_HOURS and hist.get("last_slot") != slot)

    if args.facts:
        topic, facts = gather(args.topic or pick_topic(hist["n"], now.weekday()))
        print(f"topic: {topic}\n" + "\n".join(facts))
        print(f"\nwaiting: {len(fresh)} official release(s), {len(movers)} mover(s)")
        for src, it in fresh:
            print(f"  [release/{src}] {it['title']}")
        for m in movers:
            print("  [mover] " + m["facts"][0])
        return

    use_ai = bool(os.getenv("GEMINI_API_KEY", "").strip())

    def recent():
        return [t["text"] for t in hist["tweets"] if t.get("text")]

    def record(topic, **extra):
        hist["tweets"].append({"t": now.isoformat(timespec="seconds"), "topic": topic, **extra})

    def produce(label, topic, task, facts, urgent):
        """AI draft if a Gemini key is set and works; otherwise a ready-to-paste prompt for Claude."""
        if use_ai:
            try:
                text = generate(task, facts, recent())
            except Exception as e:
                log(f"AI failed for '{label}', sending a prompt instead: {e}")
            else:
                if args.dry:
                    print(f"--- {label} ({len(text)} chars)\n{text}\n")
                else:
                    notify(text, label, urgent)
                sent.append((label, text))
                record(topic, text=text)
                time.sleep(4)  # stay under free-tier requests per minute
                return
        prompt = build_prompt(task, facts)
        if args.dry:
            print(f"--- {label} [prompt]\n{prompt}\n")
        else:
            notify_prompt(prompt, label, urgent)
        sent.append((f"{label} [prompt]", prompt))
        record(topic, prompt=True)

    # ---- alerts: official releases, movers
    jobs = []
    for src, it in fresh[:MAX_BREAKING]:
        details = it["summary"] or page_text(it["link"])
        facts = [stamp, f"Source: {src}", f"Release title: {it['title']}"]
        if details:
            facts.append(f"Details: {details[:1200]}")
        jobs.append({"label": f"BREAKING {src}", "topic": "breaking", "task": BREAKING_TASK, "facts": facts, "key": "seen", "id": it["id"]})
    for m in movers[:MAX_MOVERS]:
        jobs.append({"label": f"MOVER {m['sym']}", "topic": "mover", "task": MOVE_TASK, "facts": [stamp] + m["facts"], "key": "moves_seen", "id": m["id"]})

    for job in jobs:
        if alerts_left(hist, now) <= 0:
            log(f"daily alert cap ({MAX_ALERTS_PER_DAY}) reached, skipping the rest")
            break
        try:
            produce(job["label"], job["topic"], job["task"], job["facts"], urgent=True)
            hist.setdefault(job["key"], []).append(job["id"])
            hist["alerts"]["n"] += 1
            if persist:
                save_history(hist)
        except Exception as e:
            errors.append(f"{job['label']}: {e}")
            log(errors[-1])

    # ---- scheduled content
    if content_due:
        try:
            topic, facts = gather(args.topic or pick_topic(hist["n"], now.weekday()))
            produce(f"draft: {topic}", topic, TOPICS[topic][1], facts, urgent=False)
            hist["n"] += 1
            hist["last_slot"] = slot
        except Exception as e:
            errors.append(f"content: {e}")
            log(errors[-1])

    if persist:
        save_history(hist)
    if sent:
        write_summary([f"### {len(sent)} message(s) sent to phone"] + [f"- **{l}**" for l, _ in sent])
    else:
        print("nothing to send this run")
    if errors:
        raise SystemExit("ERRORS: " + " | ".join(errors))


if __name__ == "__main__":
    main()