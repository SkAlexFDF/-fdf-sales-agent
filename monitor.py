#!/usr/bin/env python3
"""
FDF equipment monitor — ежедневный дайджест в Telegram.

Для каждой группы товаров собирает с OLX (через API) новые за сутки объявления
и с Prom.ua «лучшую новую цену сегодня», шлёт ОДНО сообщение в бот с разделами.

Зависимостей нет — только стандартная библиотека (работает на GitHub Actions без pip).
Переменные окружения:
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID   — обязательно
    SCRAPER_API_KEY                        — опционально, обход анти-бота OLX
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

# =====================  ГРУППЫ ТОВАРОВ  =====================
# exact   — точные модели: ищутся по всей Украине, любая цена, помечаются ⭐
# general — общие запросы: только Киев+область, цена ≤ general_cap
# prom    — (название, ссылка на поиск Prom, порог 🏷, ценовой пол)
#           ценовой пол отсекает аксессуары (фильтры/мембраны/крышки) из «мин. цены»
GROUPS = [
    {
        "title": "🧊 Шокова заморозка",
        "exact": ["Apach ASH10", "Apach ASH10K", "Tecnodom ATT10"],
        "general": ["шокова заморозка", "apach заморозка", "tecnodom"],
        "general_cap": 150_000,
        "prom": [
            ("Apach ASH10K", "https://prom.ua/ua/search?search_term=Apach+ASH10K", 175_000, 100_000),
            ("Tecnodom ATT10", "https://prom.ua/ua/search?search_term=Tecnodom+ATT10", 165_000, 100_000),
        ],
    },
    {
        "title": "🧰 Вакууматори (камерні)",
        "exact": ["Hurakan HKN-VAC400E", "Vektor DZ-400", "Henkelman Boxer 52", "Henkelman Boxer 42"],
        "general": ["вакуумний пакувальник камерний"],
        "general_cap": 120_000,
        "prom": [
            ("Hurakan HKN-VAC400E", "https://prom.ua/ua/search?search_term=Hurakan+HKN-VAC400E", 40_000, 25_000),
            ("Vektor DZ-400", "https://prom.ua/ua/search?search_term=Vektor+DZ-400", 38_000, 25_000),
            ("Henkelman Boxer 52", "https://prom.ua/ua/search?search_term=Henkelman+Boxer+52", 150_000, 90_000),
            ("Henkelman Boxer 42", "https://prom.ua/ua/search?search_term=Henkelman+Boxer+42", 130_000, 90_000),
        ],
    },
]

# Киев + область (по названию региона OLX)
KYIV_REGIONS = {"київська область", "київ", "киев", "киевская область"}
# Стоп-слова: услуги, продукты, запчасти/аксессуары — не оборудование
EXCLUDE_WORDS = ["ремонт", "запчаст", "вишн", "ягод", "полуниц", "чорниц", "фрукт",
                 "м'яс", "мясо", "риб", "оренд", "прокат", "куплю",
                 "кришка", "крышка", "фільтр", "фильтр", "мембран", "олія", "масло",
                 "пакет", "плівк", "плёнк", "мішк", "скотч"]
FRESH_HOURS = 24

# На GitHub Actions сертификаты в порядке; на части локальных машин — фолбэк без проверки.
_CTX = ssl.create_default_context()
_CTX_INSECURE = ssl._create_unverified_context()


def _urlopen(req, timeout):
    try:
        return urllib.request.urlopen(req, timeout=timeout, context=_CTX)
    except urllib.error.URLError as e:
        if isinstance(getattr(e, "reason", None), ssl.SSLCertVerificationError):
            return urllib.request.urlopen(req, timeout=timeout, context=_CTX_INSECURE)
        raise


def http_get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept-Language": "uk,ru;q=0.9"})
    with _urlopen(req, timeout) as r:
        return r.read().decode("utf-8", "replace")


OLX_ERRORS = 0


def olx_get(url):
    """GET к OLX. Если задан SCRAPER_API_KEY — через прокси (обход анти-бота)."""
    key = os.environ.get("SCRAPER_API_KEY")
    if key:
        url = ("https://api.scraperapi.com/?api_key=" + key +
               "&url=" + urllib.parse.quote(url, safe=""))
    return http_get(url, timeout=70 if key else 25)


def olx_search(query):
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


def collect_olx(group, seen):
    exact, general = [], []
    for q in group["exact"]:
        for it in olx_search(q):
            if it["url"] in seen or not is_fresh(it["created"]) or excluded(it["title"]):
                continue
            seen.add(it["url"])
            it["star"] = True
            exact.append(it)
    for q in group["general"]:
        for it in olx_search(q):
            if it["url"] in seen or not is_fresh(it["created"]) or excluded(it["title"]):
                continue
            if it["region"] not in KYIV_REGIONS:
                continue
            if it["price"] is None or it["price"] > group["general_cap"]:
                continue
            seen.add(it["url"])
            it["star"] = False
            general.append(it)
    return exact + general


def prom_min_price(url, floor):
    """Мин. цена на странице поиска Prom, игнорируя всё дешевле floor (аксессуары)."""
    try:
        html = http_get(url)
    except Exception as e:
        print(f"[prom] {url} error: {e}", file=sys.stderr)
        return None
    prices = [int(x) for x in re.findall(r'data-qaprice="([0-9]{4,8})"', html)]
    prices = [p for p in prices if p >= floor]
    return min(prices) if prices else None


def fmt_price(v):
    return f"{v:,}".replace(",", " ") if isinstance(v, int) else str(v)


def build_message():
    today = datetime.now(timezone(timedelta(hours=3))).strftime("%d.%m.%Y")
    lines = [f"📋 FDF · моніторинг обладнання · {today}"]
    seen = set()

    for g in GROUPS:
        lines.append("")
        lines.append(f"━━ {g['title']} ━━")

        items = collect_olx(g, seen)
        if items:
            lines.append(f"🆕 Новое на OLX за сутки ({len(items)}):")
            for it in items:
                star = "⭐ " if it.get("star") else "• "
                price = fmt_price(it["price"]) + " грн" if it["price"] else "цена договорная"
                lines.append(f"{star}{it['title']} — {price}, {it['city'] or '—'}")
                lines.append(it["url"])
        else:
            lines.append("На OLX за сутки нового нет.")

        lines.append("🆕 Лучшая новая цена сегодня:")
        for name, url, threshold, floor in g["prom"]:
            mn = prom_min_price(url, floor)
            if mn is None:
                lines.append(f"• {name} — нет данных")
            else:
                tag = " 🏷" if mn <= threshold else ""
                lines.append(f"• {name} — от {fmt_price(mn)} грн{tag}")
                lines.append(url)

    # Если все запросы к OLX упали — честно скажем об этом отдельной строкой.
    total_queries = sum(len(g["exact"]) + len(g["general"]) for g in GROUPS)
    if OLX_ERRORS >= total_queries:
        lines.insert(1, "⚠️ OLX недоступен (не удалось проверить объявления).")

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
