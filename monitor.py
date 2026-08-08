#!/usr/bin/env python3
"""
FDF freezer monitor — ежедневный дайджест в Telegram.

Собирает с OLX (API) новые за сутки объявления шоковой заморозки и с Prom.ua
«лучшую новую цену сегодня» по целевым моделям, шлёт одно сообщение в бот.

Зависимостей нет — только стандартная библиотека (работает на любом GitHub Actions
runner без pip install). Токен и chat_id берутся из переменных окружения:
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
"""

import json
import os
import re
import ssl
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36")

# На GitHub Actions (Ubuntu) сертификаты в порядке и используется обычная проверка.
# На части локальных машин (macOS без certifi) проверка падает — тогда фолбэк без неё.
_CTX = ssl.create_default_context()
_CTX_INSECURE = ssl._create_unverified_context()


def _urlopen(req, timeout):
    try:
        return urllib.request.urlopen(req, timeout=timeout, context=_CTX)
    except urllib.error.URLError as e:
        if isinstance(getattr(e, "reason", None), ssl.SSLCertVerificationError):
            return urllib.request.urlopen(req, timeout=timeout, context=_CTX_INSECURE)
        raise

# --- Настройки поиска ------------------------------------------------------
# Точные модели: ищем по всей Украине, любая цена, всегда с ⭐.
EXACT_QUERIES = ["Apach ASH10", "Apach ASH10K", "Tecnodom ATT10"]
# Общие: только Киев+область, цена ≤ порога.
GENERAL_QUERIES = ["шокова заморозка", "apach заморозка", "tecnodom"]
GENERAL_PRICE_CAP = 150_000

# Prom: «лучшая новая цена сегодня» + флаг 🏷 при снижении ниже порога.
PROM_MODELS = [
    ("Apach ASH10K", "https://prom.ua/ua/search?search_term=Apach+ASH10K", 175_000),
    ("Tecnodom ATT10", "https://prom.ua/ua/search?search_term=Tecnodom+ATT10", 165_000),
]

# Киев + область: по названию региона OLX.
KYIV_REGIONS = {"київська область", "київ", "киев", "киевская область"}
# Слова, по которым отсекаем «продукты» и услуги, а не оборудование.
EXCLUDE_WORDS = ["ремонт", "запчаст", "вишн", "ягод", "полуниц", "чорниц",
                 "фрукт", "м'яс", "мясо", "риб", "оренд", "прокат", "куплю"]
FRESH_HOURS = 24


def http_get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept-Language": "uk,ru;q=0.9"})
    with _urlopen(req, timeout) as r:
        return r.read().decode("utf-8", "replace")


OLX_ERRORS = 0  # счётчик неудачных запросов к OLX за прогон


def olx_get(url):
    """GET к OLX. Если задан SCRAPER_API_KEY — идём через прокси (обход анти-бота)."""
    key = os.environ.get("SCRAPER_API_KEY")
    if key:
        url = ("https://api.scraperapi.com/?api_key=" + key +
               "&url=" + urllib.parse.quote(url, safe=""))
    return http_get(url, timeout=60 if key else 25)


def olx_search(query):
    """Возвращает список объявлений через публичный API OLX."""
    global OLX_ERRORS
    q = urllib.parse.quote(query)
    url = (f"https://www.olx.ua/api/v1/offers/?offset=0&limit=40"
           f"&query={q}&sort_by=created_at%3Adesc")
    try:
        data = json.loads(olx_get(url)).get("data", [])
    except Exception as e:
        print(f"[olx] '{query}' error: {e}", file=sys.stderr)
        OLX_ERRORS += 1
        return []
    out = []
    for o in data:
        price = None
        for p in o.get("params", []):
            if p.get("key") == "price":
                price = (p.get("value") or {}).get("value")
        loc = o.get("location", {})
        out.append({
            "title": (o.get("title") or "").strip(),
            "price": price,
            "city": (loc.get("city") or {}).get("name") or "",
            "region": ((loc.get("region") or {}).get("name") or "").lower(),
            "created": o.get("created_time") or "",
            "url": o.get("url") or "",
        })
    return out


def is_fresh(created_iso):
    try:
        dt = datetime.fromisoformat(created_iso)
    except Exception:
        return False
    return datetime.now(timezone.utc) - dt <= timedelta(hours=FRESH_HOURS)


def excluded(title):
    t = title.lower()
    return any(w in t for w in EXCLUDE_WORDS)


def collect_olx():
    seen, exact, general = set(), [], []
    # Точные модели — вся Украина, любая цена
    for q in EXACT_QUERIES:
        for it in olx_search(q):
            if it["url"] in seen or not is_fresh(it["created"]) or excluded(it["title"]):
                continue
            seen.add(it["url"])
            it["star"] = True
            exact.append(it)
    # Общие — Киев+область, ≤ cap
    for q in GENERAL_QUERIES:
        for it in olx_search(q):
            if it["url"] in seen or not is_fresh(it["created"]) or excluded(it["title"]):
                continue
            if it["region"] not in KYIV_REGIONS:
                continue
            if it["price"] is None or it["price"] > GENERAL_PRICE_CAP:
                continue
            seen.add(it["url"])
            it["star"] = False
            general.append(it)
    return exact, general


def prom_min_price(url):
    """Минимальная цена среди офферов на странице поиска Prom."""
    try:
        html = http_get(url)
    except Exception as e:
        print(f"[prom] {url} error: {e}", file=sys.stderr)
        return None
    prices = [int(x) for x in re.findall(r'data-qaprice="([0-9]{4,8})"', html)]
    return min(prices) if prices else None


def fmt_price(v):
    return f"{v:,}".replace(",", " ") if isinstance(v, int) else str(v)


def build_message():
    today = datetime.now(timezone(timedelta(hours=3))).strftime("%d.%m.%Y")
    lines = [f"🧊 FDF · шокова заморозка · {today}", ""]

    exact, general = collect_olx()
    olx_items = exact + general
    if olx_items:
        lines.append(f"🆕 Новое на OLX за сутки ({len(olx_items)}):")
        for it in olx_items:
            star = "⭐ " if it.get("star") else "• "
            price = fmt_price(it["price"]) + " грн" if it["price"] else "цена договорная"
            city = it["city"] or "—"
            lines.append(f"{star}{it['title']} — {price}, {city}")
            lines.append(it["url"])
            lines.append("")
    elif OLX_ERRORS >= len(EXACT_QUERIES) + len(GENERAL_QUERIES):
        lines.append("⚠️ OLX недоступен (не удалось проверить объявления).")
        lines.append("")
    else:
        lines.append("На OLX за сутки нового нет.")
        lines.append("")

    lines.append("🆕 Лучшая новая цена сегодня:")
    for name, url, threshold in PROM_MODELS:
        mn = prom_min_price(url)
        if mn is None:
            lines.append(f"• {name} — нет данных")
        else:
            tag = " 🏷" if mn <= threshold else ""
            lines.append(f"• {name} — от {fmt_price(mn)} грн{tag}")
            lines.append(url)
    return "\n".join(lines).strip()


def send_telegram(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("ERROR: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID не заданы", file=sys.stderr)
        sys.exit(1)
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": "true",
    }).encode()
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    req = urllib.request.Request(url, data=data)
    with _urlopen(req, 25) as r:
        resp = json.loads(r.read().decode())
    if not resp.get("ok"):
        print(f"ERROR: Telegram ответил {resp}", file=sys.stderr)
        sys.exit(1)
    print("Отправлено, message_id =", resp["result"]["message_id"])


if __name__ == "__main__":
    msg = build_message()
    print("---- MESSAGE ----")
    print(msg)
    print("-----------------")
    if "--dry-run" not in sys.argv:
        send_telegram(msg)
