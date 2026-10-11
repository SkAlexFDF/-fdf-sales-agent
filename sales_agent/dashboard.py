"""HTML-дашборд: один самодостаточный файл (открывается в браузере/Telegram)."""

import os
from html import escape

from . import config as C

SERIES = ["var(--s1)", "var(--s2)", "var(--s3)", "var(--s4)"]

CSS = """
/* Layout: шапка → сводка ИИ → плитки → сравнение менеджеров → график → карточки менеджеров */
:root{
  --bg:#f3f5f1; --surface:#ffffff; --fg:#18221d; --muted:#5b6a62; --line:#dde3dc;
  --accent:#2d6a4f; --s1:#2d6a4f; --s2:#c27c2c; --s3:#4a6fa5; --s4:#8a5a9e;
  --ok:#2e7d4f; --ok-bg:#e3f1e8; --warn:#9a6412; --warn-bg:#fbf0dc; --crit:#b3261e; --crit-bg:#fbe5e3;
  --display:"Unbounded","Onest",system-ui,sans-serif; --body:"Onest",system-ui,-apple-system,"Segoe UI",sans-serif;
}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){
  --bg:#121815; --surface:#1a221e; --fg:#e6ece8; --muted:#9aa9a0; --line:#2c3731;
  --accent:#6fbf94; --s1:#6fbf94; --s2:#e0a35a; --s3:#86a8dc; --s4:#c39ad6;
  --ok:#7fd1a1; --ok-bg:#1f3a2b; --warn:#f0c071; --warn-bg:#3d3020; --crit:#ff9b92; --crit-bg:#45211f; color-scheme:dark}}
:root[data-theme="dark"]{
  --bg:#121815; --surface:#1a221e; --fg:#e6ece8; --muted:#9aa9a0; --line:#2c3731;
  --accent:#6fbf94; --s1:#6fbf94; --s2:#e0a35a; --s3:#86a8dc; --s4:#c39ad6;
  --ok:#7fd1a1; --ok-bg:#1f3a2b; --warn:#f0c071; --warn-bg:#3d3020; --crit:#ff9b92; --crit-bg:#45211f; color-scheme:dark}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--fg);font:15px/1.5 var(--body);margin:0}
.wrap{max-width:1180px;margin:0 auto;padding-inline:16px;padding-block:24px 48px;display:grid;gap:28px}
h1,h2,h3{font-family:var(--display);font-weight:600;margin:0;text-wrap:balance;letter-spacing:-.01em}
h1{font-size:1.5rem} h2{font-size:1.1rem} h3{font-size:.95rem}
.eyebrow{font-size:.72rem;text-transform:uppercase;letter-spacing:.09em;color:var(--muted);font-weight:600}
.muted{color:var(--muted)} .num{font-variant-numeric:tabular-nums;white-space:nowrap}
header{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:end;gap:12px;border-bottom:2px solid var(--fg);padding-bottom:14px}
section{display:grid;gap:14px;min-width:0}
.panel{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:18px;min-width:0}
.brief{display:grid;grid-template-columns:1.4fr 1fr;gap:18px}
.brief p{white-space:pre-line;margin:0;font-size:1rem}
ol,ul{margin:0;padding-left:1.2em;display:grid;gap:6px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px}
.tile{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:14px 16px;display:grid;gap:2px;border-top:3px solid var(--line)}
.tile.crit{border-top-color:var(--crit)} .tile.warn{border-top-color:var(--warn)} .tile.ok{border-top-color:var(--ok)}
.tile .v{font-family:var(--display);font-size:1.45rem;font-variant-numeric:tabular-nums}
.tile .d{font-size:.8rem;color:var(--muted)}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:.88rem}
th{font-size:.7rem;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-weight:600;text-align:left}
th,td{padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
td.r,th.r{text-align:right}
.pill{display:inline-block;padding:1px 8px;border-radius:99px;font-size:.75rem;font-weight:600;white-space:nowrap}
.pill.red{background:var(--crit-bg);color:var(--crit)} .pill.yellow{background:var(--warn-bg);color:var(--warn)} .pill.ok{background:var(--ok-bg);color:var(--ok)}
.swatch{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px;vertical-align:baseline}
.mgr{display:grid;gap:16px}
.mgr-head{display:flex;flex-wrap:wrap;gap:12px;justify-content:space-between;align-items:baseline}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:18px}
.cols>div{min-width:0;display:grid;gap:8px;align-content:start}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:18px}
.grid2>div{min-width:0}
.empty{color:var(--muted);font-size:.88rem;padding:8px 0}
svg text{fill:var(--muted);font:11px var(--body)}
a{color:var(--accent)} a:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
footer{font-size:.8rem;color:var(--muted);display:grid;gap:4px}
@media (max-width:760px){.brief,.cols,.grid2{grid-template-columns:1fr}}
"""


def fmt(v):
    return f"{int(round(v)):,}".replace(",", " ")


def _oid(i):
    tpl = os.environ.get("KEYCRM_ORDER_URL")  # напр. https://fdf.keycrm.app/app/orders/view/{id}
    return f'<a href="{escape(tpl.format(id=i))}">№{i}</a>' if tpl else f"№{i}"


def _pill(level, text):
    return f'<span class="pill {level}">{escape(text)}</span>'


def _table(head, rows, empty):
    if not rows:
        return f'<div class="empty">{escape(empty)}</div>'
    th = "".join(f'<th class="{"r" if h.startswith(">") else ""}">{escape(h.lstrip(">"))}</th>'
                 for h in head)
    return (f'<div class="scroll"><table><thead><tr>{th}</tr></thead><tbody>'
            + "".join("<tr>" + "".join(rows_) + "</tr>" for rows_ in rows)
            + "</tbody></table></div>")


def _td(v, right=False):
    return f'<td class="{"r num" if right else ""}">{v}</td>'


def _chart(managers):
    """Столбцы выручки по дням (30 дней), стопкой по менеджерам."""
    days = [d for d, _ in managers[0]["daily"]] if managers else []
    if not days:
        return ""
    totals = [sum(m["daily"][i][1] for m in managers) for i in range(len(days))]
    top = max(totals) or 1
    step = 10 ** (len(str(int(top))) - 1)
    for k in (1, 2, 2.5, 5, 10):
        if top / (k * step) <= 4:
            tick = k * step
            break
    ymax = tick * -(-top // tick)
    W, H, L, B, T = 900, 240, 56, 26, 10
    bw = (W - L - 8) / len(days)
    y = lambda v: T + (H - T - B) * (1 - v / ymax)
    parts = []
    v = 0
    while v <= ymax:
        parts.append(f'<line x1="{L}" x2="{W-4}" y1="{y(v):.1f}" y2="{y(v):.1f}" '
                     f'stroke="var(--line)" stroke-width="1"/>'
                     f'<text x="{L-6}" y="{y(v)+4:.1f}" text-anchor="end">{fmt(v/1000)}k</text>')
        v += tick
    for i, d in enumerate(days):
        x, base = L + i * bw + 2, 0
        for j, m in enumerate(managers):
            val = m["daily"][i][1]
            if val:
                parts.append(f'<rect x="{x:.1f}" y="{y(base+val):.1f}" width="{bw-4:.1f}" '
                             f'height="{y(base)-y(base+val):.1f}" fill="{SERIES[j % 4]}" rx="1.5">'
                             f'<title>{escape(m["name"])}, {d}: {fmt(val)} грн</title></rect>')
                base += val
        if i % 5 == 4 or i == len(days) - 1:
            parts.append(f'<text x="{x+(bw-4)/2:.1f}" y="{H-8}" text-anchor="middle">{d}</text>')
    legend = " ".join(f'<span><i class="swatch" style="background:{SERIES[j % 4]}"></i>'
                      f'{escape(m["name"])}</span>' for j, m in enumerate(managers))
    return (f'<div class="scroll"><svg viewBox="0 0 {W} {H}" width="100%" role="img" '
            f'aria-label="Выручка по дням за 30 дней" style="min-width:560px">{"".join(parts)}</svg></div>'
            f'<div class="muted" style="display:flex;gap:16px;flex-wrap:wrap;font-size:.85rem">{legend}</div>')


def _trend(cur, prev):
    if not prev:
        return "нет базы"
    d = (cur - prev) / prev
    return f'{"▲" if d >= 0 else "▼"} {abs(d):.0%} к прошлой неделе'


def render(report, ai, full=True):
    co, t = report["company"], report["totals"]
    mgr_ai = {m["name"]: m for m in ai.get("managers", [])}
    M = report["managers"]

    tiles = [
        ("Заказы вчера", fmt(co["yesterday"]["orders"]),
         f'отмен {co["yesterday"]["canceled"]} · сегодня уже {co["today"]["orders"]}', ""),
        ("Выручка вчера", fmt(co["yesterday"]["revenue"]) + " ₴",
         f'средний чек {fmt(co["yesterday"]["avg_check"])} ₴', ""),
        ("Выручка за 7 дней", fmt(co["week"]["revenue"]) + " ₴",
         _trend(co["week"]["revenue"], co["prev_week"]["revenue"]),
         "ok" if co["week"]["revenue"] >= co["prev_week"]["revenue"] else "warn"),
        ("Зависшие заказы", str(t["stuck"]), f'критичных {t["stuck_red"]}',
         "crit" if t["stuck_red"] else "warn" if t["stuck"] else "ok"),
        ("Дебиторка", fmt(t["debt"]) + " ₴", f'старше {C.DEBT_RED_DAYS} дн: {fmt(t["debt_red"])} ₴',
         "crit" if t["debt_red"] else "warn" if t["debt"] else "ok"),
        ("Риск оттока", str(t["churn"]), f'клиентов, оборот {fmt(t["churn_ltv"])} ₴',
         "warn" if t["churn"] else "ok"),
    ]
    tiles_html = "".join(
        f'<div class="tile {c}"><span class="eyebrow">{escape(a)}</span>'
        f'<span class="v">{escape(b)}</span><span class="d">{d}</span></div>'
        for a, b, d, c in tiles)

    cmp_rows = []
    for j, m in enumerate(M):
        w, mo = m["stats"]["week"], m["stats"]["month"]
        cr = mo["cancel_rate"]
        idx_level = "ok" if m["index"] >= 80 else "yellow" if m["index"] >= 60 else "red"
        cmp_rows.append([
            _td(f'<i class="swatch" style="background:{SERIES[j % 4]}"></i>{escape(m["name"])}'),
            _td(fmt(w["orders"]), True), _td(fmt(w["revenue"]), True),
            _td(_trend(w["revenue"], m["stats"]["prev_week"]["revenue"])),
            _td(fmt(mo["avg_check"]), True),
            _td(_pill("red" if cr > C.CANCEL_RATE_WARN else "ok", f"{cr:.0%}"), True),
            _td(fmt(mo["new_clients"]), True),
            _td(str(len(m["stuck"])), True), _td(fmt(m["debt_total"]), True),
            _td(str(len(m["churn"])), True), _td(str(len(m["issues"])), True),
            _td(_pill(idx_level, f'{m["index"]}/100'), True),
        ])
    cmp_html = _table(["Менеджер", ">Заказов 7д", ">Выручка 7д", "Динамика", ">Ср. чек 30д",
                       ">Отмены 30д", ">Новые кл. 30д", ">Зависло", ">Долг, ₴", ">Отток",
                       ">Ошибки", ">Индекс"], cmp_rows, "Нет данных")

    cards = []
    for j, m in enumerate(M):
        a = mgr_ai.get(m["name"], {})
        stuck = [[_td(_oid(r["id"])), _td(escape(r["buyer"])), _td(escape(r["status"])),
                  _td(_pill(r["level"], r["age"])), _td(r["norm"]), _td(fmt(r["total"]), True)]
                 for r in m["stuck"][:C.TOP_N]]
        debt = [[_td(_oid(r["id"])), _td(escape(r["buyer"])), _td(fmt(r["due"]), True),
                 _td(_pill(r["level"], f'{r["days"]} дн'))] for r in m["debt"][:C.TOP_N]]
        churn = [[_td(escape(c["buyer"])), _td(str(c["orders"]), True),
                  _td(f'раз в {c["interval"]} дн'), _td(_pill("yellow", f'{c["silent"]} дн')),
                  _td(fmt(c["ltv"]), True)] for c in m["churn"][:C.TOP_N]]
        issues = [[_td(_oid(r["id"])), _td(escape(r["buyer"])), _td(escape(r["problem"]))]
                  for r in m["issues"][:C.TOP_N]]
        recs = "".join(f"<li>{escape(x)}</li>" for x in a.get("recommendations", []))
        qs = "".join(f"<li>{escape(x)}</li>" for x in a.get("questions", []))
        y = m["stats"]["yesterday"]
        cards.append(f"""
<section class="panel mgr" id="m{j}">
  <div class="mgr-head">
    <h2><i class="swatch" style="background:{SERIES[j % 4]}"></i>{escape(m["name"])}</h2>
    <span class="muted num">вчера: {y["orders"]} заказов · {fmt(y["revenue"])} ₴ · индекс {m["index"]}/100</span>
  </div>
  {f'<p style="margin:0">{escape(a["summary"])}</p>' if a.get("summary") else ""}
  <div class="cols">
    <div><span class="eyebrow">Рекомендации</span><ol>{recs or "<li>—</li>"}</ol></div>
    <div><span class="eyebrow">Вопросы менеджеру сегодня</span><ul>{qs or "<li>—</li>"}</ul></div>
  </div>
  <div class="grid2">
    <div><h3>Зависшие заказы ({len(m["stuck"])})</h3>
      {_table(["Заказ", "Клиент", "Статус", "В стадии", "Норма", ">Сумма"], stuck, "Зависших нет")}</div>
    <div><h3>Дебиторка ({fmt(m["debt_total"])} ₴)</h3>
      {_table(["Заказ", "Клиент", ">Долг", "Возраст"], debt, "Долгов нет")}</div>
    <div><h3>Клиенты в риске оттока ({len(m["churn"])})</h3>
      {_table(["Клиент", ">Заказов", "Обычно", "Молчит", ">Оборот"], churn, "Все постоянные клиенты активны")}</div>
    <div><h3>Ошибки в заказах за 7 дней ({len(m["issues"])})</h3>
      {_table(["Заказ", "Клиент", "Проблема"], issues, "Ошибок нет")}</div>
  </div>
</section>""")

    actions = "".join(f"<li>{escape(x)}</li>" for x in ai.get("owner_actions", []))
    norms = (f'Нормы: новый заказ — {C.STAGE_LIMIT_WH[C.G_NEW]} раб. ч, согласование — '
             f'{C.STAGE_LIMIT_WH[C.G_APPROVAL]} раб. ч, производство/сборка — '
             f'{C.STAGE_LIMIT_WH[C.G_PRODUCTION]} раб. ч, доставка — {C.DELIVERY_LIMIT_DAYS} дн '
             f'(рабочее время Пн–Сб {C.WORK_START}:00–{C.WORK_END}:00). Отмены выше '
             f'{C.CANCEL_RATE_WARN:.0%} — флаг. Дебиторка — выполненные заказы без полной оплаты. '
             f'Отток — клиент молчит дольше 2× своего интервала и не меньше {C.CHURN_MIN_DAYS} дн.')
    body = f"""
<div class="wrap">
  <header>
    <div><div class="eyebrow">FDF · контроль менеджеров</div><h1>Отчёт за {report["date"]}</h1></div>
    <div class="muted num">сформирован {report["generated"]} · заказов в выборке {report["orders_loaded"]}</div>
  </header>
  <section class="panel brief">
    <div><span class="eyebrow">Главное · {escape(ai.get("source", ""))}</span><p>{escape(ai.get("telegram_brief", ""))}</p></div>
    <div><span class="eyebrow">Что сделать сегодня</span><ol>{actions}</ol></div>
  </section>
  <section class="tiles">{tiles_html}</section>
  <section><h2>Сравнение менеджеров</h2><div class="panel">{cmp_html}</div></section>
  <section><h2>Выручка по дням, 30 дней</h2><div class="panel">{_chart(M)}</div></section>
  {"".join(cards)}
  <footer><span>{escape(norms)}</span>
  <span>Индекс дисциплины = 100 − (4 за критично зависший заказ + 2 за зависший, макс. 40) − (2 за долг 30+ дн, макс. 20) − (2 за ошибку, макс. 15) − 15 при отменах выше нормы.</span></footer>
</div>"""
    head = ('<title>FDF Контроль менеджеров</title>'
            '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Onest:wght@400;600'
            '&family=Unbounded:wght@600&display=swap">'
            f"<style>{CSS}</style>")
    if not full:
        return head + body
    return ('<!doctype html><html lang="ru"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f"{head}</head><body>{body}</body></html>")
