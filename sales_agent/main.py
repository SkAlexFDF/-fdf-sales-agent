#!/usr/bin/env python3
"""
FDF · агент контроля менеджеров по продажам.

Каждое утро: данные из KeyCRM → показатели по менеджерам → рекомендации (Claude) →
короткая сводка в Telegram + полный HTML-дашборд файлом.

Запуск:
    python3 -m sales_agent.main              # боевой режим
    python3 -m sales_agent.main --dry-run    # без отправки в Telegram
    python3 -m sales_agent.main --demo       # на синтетических данных (ключи не нужны)

Переменные окружения:
    KEYCRM_API_KEY                          — обязательно (кроме --demo)
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID    — обязательно для отправки
    SALES_TELEGRAM_CHAT_ID                  — опционально, отдельный чат для этого отчёта
    ANTHROPIC_API_KEY                       — опционально; без него рекомендации по правилам
    KEYCRM_ORDER_URL                        — опционально, шаблон ссылки на заказ с {id}
"""

import json
import os
import sys
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

from . import ai, dashboard, demo_data, keycrm, metrics


def telegram(method, fields, file=None):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("SALES_TELEGRAM_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        sys.exit("ERROR: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID не заданы")
    fields = dict(fields, chat_id=chat)
    boundary = uuid.uuid4().hex
    body = b""
    for k, v in fields.items():
        body += (f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n'
                 f"{v}\r\n").encode()
    if file:
        name, data = file
        body += (f'--{boundary}\r\nContent-Disposition: form-data; name="document"; '
                 f'filename="{name}"\r\nContent-Type: text/html\r\n\r\n').encode() + data + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}", data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        resp = json.loads(r.read().decode())
    if not resp.get("ok"):
        sys.exit(f"ERROR: Telegram ответил {resp}")


def telegram_text(report, analysis):
    lines = [f"📊 FDF · менеджеры · {report['date']}", "", analysis["telegram_brief"].strip(), ""]
    for m in report["managers"]:
        w = m["stats"]["week"]
        flags = []
        if m["stuck"]:
            flags.append(f"⏳{len(m['stuck'])}")
        if m["debt_red"]:
            flags.append(f"💸{ai.fmt(m['debt_red'])}")
        if m["churn"]:
            flags.append(f"🚪{len(m['churn'])}")
        if m["issues"]:
            flags.append(f"⚠️{len(m['issues'])}")
        lines.append(f"👤 {m['name']}: 7д {w['orders']} зак. / {ai.fmt(w['revenue'])} грн · "
                     f"индекс {m['index']} {' '.join(flags)}")
    lines += ["", "✅ Сегодня:"] + [f"• {a}" for a in analysis["owner_actions"][:5]]
    lines += ["", "Полный отчёт с рекомендациями и вопросами менеджерам — в файле ниже.",
              "⏳ зависшие · 💸 долг 30+ дн · 🚪 риск оттока · ⚠️ ошибки"]
    return "\n".join(lines)[:4000]


def main(argv):
    now = datetime.now(keycrm.KYIV)
    if "--demo" in argv:
        raw = demo_data.generate(now)
    else:
        key = os.environ.get("KEYCRM_API_KEY")
        if not key:
            sys.exit("ERROR: KEYCRM_API_KEY не задан (или запустите с --demo)")
        raw = keycrm.fetch(key, now)

    orders = keycrm.normalize(raw)
    report = metrics.build(orders, now)
    analysis = ai.analyze(report)

    out = Path(os.environ.get("SALES_OUT_DIR", "out"))
    out.mkdir(exist_ok=True)
    stamp = now.strftime("%Y-%m-%d")
    html_path = out / f"fdf_managers_{stamp}.html"
    html_path.write_text(dashboard.render(report, analysis), encoding="utf-8")
    (out / f"fdf_managers_{stamp}.json").write_text(
        json.dumps({"report": report, "analysis": analysis}, ensure_ascii=False, indent=1,
                   default=str), encoding="utf-8")

    text = telegram_text(report, analysis)
    print("---- TELEGRAM ----\n" + text + "\n------------------")
    print("Дашборд:", html_path)
    if "--dry-run" in argv or "--demo" in argv and "--send" not in argv:
        return
    telegram("sendMessage", {"text": text, "disable_web_page_preview": "true"})
    telegram("sendDocument", {"caption": f"Отчёт по менеджерам за {report['date']}"},
             (html_path.name, html_path.read_bytes()))
    print("Отправлено в Telegram")


if __name__ == "__main__":
    main(sys.argv[1:])
