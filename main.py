#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bithumb KRW '초입 신호(개선 버전)' — 간이 스캐너
- 15분/1시간 변동률 기반 Top1 선정
- 과열 회피(최근 30분 +10%면 경고/스킵)
- 2시간 내 동일 종목 중복 전송 금지 (.state.json 사용)
- BEL, ICP 포함 (제외하지 않음)
- 24h vs 7d, OI, Funding은 공개 REST만으로 즉시 산출 어려워 N/A 처리

필요 환경변수(Secrets):
- TELEGRAM_BOT_TOKEN
- TELEGRAM_CHAT_ID
"""
import os, time, json, requests
from datetime import datetime, timedelta, timezone

BASE = "https://api.bithumb.com/v1"
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
STATE_PATH = ".state.json"

def notify(text: str):
    if not TOKEN or not CHAT_ID:
        print("⚠️ TELEGRAM_* env not set")
        return
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    r = requests.post(url, json={"chat_id": CHAT_ID, "text": text}, timeout=20)
    print("notify:", r.status_code, r.text[:200])

def get_krw_markets():
    r = requests.get(f"{BASE}/market/all", params={"isDetails":"false"}, timeout=20)
    r.raise_for_status()
    data = r.json()
    return [x["market"] for x in data if isinstance(x, dict) and str(x.get("market","")).startswith("KRW-")]

def get_minute_candles(market, n=60):
    r = requests.get(f"{BASE}/candles/minutes/1", params={"market":market, "count":n}, timeout=20)
    r.raise_for_status()
    return r.json()

def pct(cur, ref):
    if ref in (None, 0): return None
    return (cur - ref) / ref * 100.0

def load_state():
    try:
        with open(STATE_PATH,"r",encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_state(state):
    with open(STATE_PATH,"w",encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def overheat_30min(closes):
    if len(closes) < 31: return False
    change = pct(closes[0], closes[30]) or 0.0
    return change >= 10.0

def scan_once():
    krw = get_krw_markets()
    best = None
    for m in krw:
        try:
            cs = get_minute_candles(m, 60)
            if not cs or len(cs) < 60: 
                continue
            closes = [c.get("trade_price") for c in cs if isinstance(c, dict)]
            if len(closes) < 60:
                continue
            c_now, c_15, c_60 = closes[0], closes[15], closes[59]
            chg15, chg60 = pct(c_now, c_15), pct(c_now, c_60)
            score = (max(chg15,0) if chg15 else 0)*0.6 + (max(chg60,0) if chg60 else 0)*0.4
            item = {"m": m, "price": c_now, "chg15": chg15, "chg60": chg60,
                    "score": score, "overheat": overheat_30min(closes)}
            if (best is None) or (item["score"] > best["score"]):
                best = item
            time.sleep(0.02)
        except Exception as e:
            print("scan error:", m, e)
            continue
    return best

def format_line(item, reason):
    price_s = f"{int(item['price']):,}" if item['price'] else "N/A"
    p15 = f"{item['chg15']:.2f}%" if item['chg15'] is not None else "N/A"
    p60 = f"{item['chg60']:.2f}%" if item['chg60'] is not None else "N/A"
    return f"[초입신호] {item['m']} / {price_s} / {p15} / {p60} / N/A / N/A / N/A / {reason}"

def main():
    state = load_state()
    best = scan_once()
    if not best:
        notify("[초입신호] 후보 없음 / N/A / N/A / N/A / N/A / N/A / N/A / 데이터 부족")
        return
    now = datetime.now(timezone.utc)

    # 2시간 중복 방지
    last = state.get("last", {})
    if last:
        last_sym = last.get("symbol")
        last_ts = last.get("ts")
        try:
            last_dt = datetime.fromisoformat(last_ts.replace("Z","+00:00"))
        except Exception:
            last_dt = now - timedelta(hours=3)
        if last_sym == best["m"] and (now - last_dt) < timedelta(hours=2):
            print("skip duplicate within 2h:", best["m"])
            return

    reason = "15분·1시간 모멘텀 상위"
    if best["overheat"]:
        reason = "모멘텀 상위(과열 경고)"

    notify(format_line(best, reason))
    state["last"] = {"symbol": best["m"], "ts": now.strftime("%Y-%m-%dT%H:%M:%SZ")}
    save_state(state)

if __name__ == "__main__":
    main()
