#!/usr/bin/env python3
"""
FDF · недельный и месячный KPI-отчёт по KeyCRM.

    python3 -m sales_agent.periodic week  [--dry-run] [--demo]
    python3 -m sales_agent.periodic month [--dry-run] [--demo]

Неделя — прошлая полная неделя (Пн–Вс), месяц — прошлый календарный месяц.
Сравнение с предыдущим периодом и с нормами из config.py.
"""

import json
import os
import statistics as st
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from html import escape
from pathlib import Path

from . import config as C
from . import dashboard as D
from . import demo_data, keycrm
from .main import telegram

fmt = D.fmt
OK, BAD = "✅", "⚠️"


def pct(a, b):
    return round(100 * a / b, 1) if b else 0.0


def p1(v):
    return f"{v:.1f}".replace(".", ",")


def delta(cur, prev):
    if not prev:
        return ""
    d = (cur - prev) / prev
    return f"{'▲' if d >= 0 else '▼'}{abs(d):.0%}"


def periods(kind, now):
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if kind == "week":
        end = today - timedelta(days=today.weekday())
        start = end - timedelta(days=7)
        return start, end, start - timedelta(days=7), start, \
            f"неделя {start:%d.%m}–{end - timedelta(days=1):%d.%m.%Y}"
    end = today.replace(day=1)
    start = (end - timedelta(days=1)).replace(day=1)
    pstart = (start - timedelta(days=1)).replace(day=1)
    names = "январь февраль март апрель май июнь июль август сентябрь октябрь ноябрь декабрь".split()
    return start, end, pstart, start, f"{names[start.month - 1]} {start.year}"


def protein(sku):
    s = (sku or "").upper()
    for p, g in (("PUP", "Цуценята"), ("FCF", "Коти (FCF)"), ("BEEF", "Яловичина"), ("TURK", "Індичка"),
                 ("LAMB", "Баранина"), ("CHI", "Курка"), ("FISHOIL", "Олія"), ("FISH", "Риба"),
                 ("SM", "Ласощі (SM)"), ("СЗ", "Індив. рецепти (СЗ)"), ("AP", "Апетайзери"),
                 ("BOX", "Бокси"), ("OPT", "Ваговий (OPT)")):
        if s.startswith(p):
            return g
    return "Інше"


def stats(lst, first):
    live = [o for o in lst if o["live"]]
    rev = sum(o["total"] for o in live)
    disc = sum(o["discount"] for o in live)
    buyers = {o["buyer_id"] for o in live if o["buyer_id"]}
    ship = sorted((o["shipped"] - o["created"]).total_seconds() / 3600 for o in live
                  if o["shipped"] and o["shipped"] > o["created"])
    return {
        "orders": len(lst), "live": len(live), "rev": round(rev),
        "aov": round(rev / len(live)) if live else 0,
        "cancel": pct(sum(o["cancel"] for o in lst), len(lst)),
        "disc": pct(disc, rev + disc),
        "buyers": len(buyers),
        "new": sum(1 for b in buyers if first.get(b) and first[b] >= min(o["created"] for o in lst)),
        "ship_med": round(st.median(ship)) if ship else None,
        "ship_p90": round(st.quantiles(ship, n=10)[8]) if len(ship) >= 10 else None,
        "errors": sum(1 for o in lst if o["status_l"] == "помилка fdf"),
        "returns": sum(1 for o in lst if o["is_return"]),
        "unassigned": sum(1 for o in lst if not o["manager_id"]),
    }


def mgr_name(o):
    n = o["manager"] or "Без менеджера"
    return "Інші / без менеджера" if n in C.EXCLUDE_MANAGERS or n == "Без менеджера" else n


def build(kind, orders, now):
    start, end, pstart, pend, title = periods(kind, now)
    cur = [o for o in orders if start <= o["created"] < end]
    prev = [o for o in orders if pstart <= o["created"] < pend]
    first = {}
    for o in sorted((o for o in orders if o["live"] and o["buyer_id"]), key=lambda x: x["created"]):
        first.setdefault(o["buyer_id"], o["created"])
    S, P = stats(cur, first), stats(prev, first) if prev else stats([], first)
    if not prev:
        P = {k: 0 for k in S}
    # новые клиенты: первый заказ внутри периода
    S["new"] = sum(1 for b, t in first.items() if start <= t < end)
    P["new"] = sum(1 for b, t in first.items() if pstart <= t < pend)

    site = [o for o in cur if o["source"].strip().lower() in C.SITE_SOURCES]
    site_cancel = pct(sum(o["cancel"] for o in site), len(site))

    # источники
    src = defaultdict(list)
    for o in cur:
        src[o["source"]].append(o)
    sources = sorted(({"name": k, **stats(v, first)} for k, v in src.items()), key=lambda r: -r["rev"])

    # менеджеры
    mg = defaultdict(list)
    for o in cur:
        mg[mgr_name(o)].append(o)
    managers = []
    for k, v in mg.items():
        s = stats(v, first)
        s["new"] = len({o["buyer_id"] for o in v if o["live"] and first.get(o["buyer_id"]) and
                        start <= first[o["buyer_id"]] < end})
        managers.append({"name": k, **s})
    managers.sort(key=lambda r: (r["name"].startswith("Інші"), -r["rev"]))

    # клиенты на конец периода
    per_b = defaultdict(list)
    for o in orders:
        if o["live"] and o["buyer_id"] and o["created"] < end:
            per_b[o["buyer_id"]].append(o)
    seg = Counter()
    at_risk = defaultdict(list)
    for b, l in per_b.items():
        l.sort(key=lambda x: x["created"])
        days = (end - l[-1]["created"]).days
        seg["≤30" if days <= 30 else "31–60" if days <= 60 else "61–90" if days <= 90 else "90+"] += 1
        lo, hi = C.AT_RISK_DAYS
        if lo <= (now - l[-1]["created"]).days <= hi:
            gaps = [(y["created"] - x["created"]).days for x, y in zip(l, l[1:])]
            at_risk[mgr_name(l[-1])].append({
                "buyer": l[-1]["buyer"], "days": (now - l[-1]["created"]).days, "orders": len(l),
                "ltv": round(sum(o["total"] for o in l)), "interval": round(st.median(gaps)) if gaps else None,
                "last": l[-1]["id"]})
    for v in at_risk.values():
        v.sort(key=lambda r: -r["ltv"])
    repeat = pct(sum(1 for l in per_b.values() if len(l) > 1), len(per_b))
    ltv = sorted((sum(o["total"] for o in l) for l in per_b.values()), reverse=True)
    top20 = pct(sum(ltv[:max(1, len(ltv) // 5)]), sum(ltv))

    R = {"kind": kind, "title": title, "generated": now.strftime("%d.%m.%Y %H:%M"),
         "S": S, "P": P, "site_cancel": site_cancel, "site_orders": len(site),
         "sources": sources[:10], "managers": managers, "segments": dict(seg),
         "at_risk": dict(at_risk), "repeat": repeat, "top20": top20,
         "active30": seg["≤30"]}

    if kind == "month":
        def retention(c_start, c_end, n_end):
            coh = {b for b, t in first.items() if c_start <= t < c_end}
            back = {o["buyer_id"] for o in orders if o["live"] and o["buyer_id"] in coh
                    and c_end <= o["created"] < n_end}
            return len(coh), pct(len(back), len(coh))
        R["retention"] = retention(pstart, pend, end)                         # новые прошлого месяца → вернулись в этом
        pp = (pstart - timedelta(days=1)).replace(day=1)
        R["retention_prev"] = retention(pp, pstart, pend)
        pr = Counter()
        for o in cur:
            if o["live"]:
                for p in o["products"]:
                    pr[protein(p["sku"])] += p["sum"]
        tot = sum(pr.values())
        R["products"] = [(k, round(v), pct(v, tot)) for k, v in pr.most_common(12)]
    return R


def flags(R):
    S, out = R["S"], []
    def chk(ok, text):
        out.append((OK if ok else BAD) + " " + text)
    chk(S["cancel"] <= C.NORM_CANCEL, f"Отмены {p1(S['cancel'])}% (норма ≤{p1(C.NORM_CANCEL)}%)")
    if R["site_orders"]:
        chk(R["site_cancel"] <= C.NORM_CANCEL_SITE, f"Отмены сайта {p1(R['site_cancel'])}% (≤{p1(C.NORM_CANCEL_SITE)}%)")
    norm_new = C.NORM_NEW_WEEK if R["kind"] == "week" else round(C.NORM_NEW_WEEK * 4.3)
    chk(S["new"] >= norm_new, f"Новые клиенты {S['new']} (норма ≥{norm_new})")
    if S["ship_p90"] is not None:
        chk(S["ship_p90"] <= C.NORM_SHIP_P90_H,
            f"Отправка: медиана {S['ship_med']} ч, 90% до {S['ship_p90']} ч (норма ≤{C.NORM_SHIP_P90_H})")
    chk(S["disc"] <= C.NORM_DISCOUNT, f"Скидки {p1(S['disc'])}% (≤{p1(C.NORM_DISCOUNT)}%)")
    errs = S["errors"] + S["returns"]
    norm_err = C.NORM_ERRORS_WEEK if R["kind"] == "week" else C.NORM_ERRORS_WEEK * 4
    chk(errs <= norm_err, f"«Помилка FDF» {S['errors']} + возвраты {S['returns']} (норма ≤{norm_err})")
    chk(S["unassigned"] == 0, f"Заказов без менеджера: {S['unassigned']}")
    if R["kind"] == "month":
        n, r = R["retention"]
        chk(r >= C.NORM_RETENTION, f"Удержание: из {n} новых вернулись {p1(r)}% (норма ≥{C.NORM_RETENTION}%)")
        chk(R["repeat"] >= C.NORM_REPEAT, f"Повторные покупатели {p1(R['repeat'])}% (≥{C.NORM_REPEAT}%)")
    return out


def telegram_text(R):
    S, P = R["S"], R["P"]
    icon = "📈" if R["kind"] == "week" else "🗓"
    lines = [f"{icon} FDF · {R['title']}", "",
             f"Выручка: {fmt(S['rev'])} грн {delta(S['rev'], P['rev'])} · заказов {S['orders']} · чек {fmt(S['aov'])}",
             f"Покупателей {S['buyers']} · новых {S['new']} {delta(S['new'], P['new'])} · активная база (30 дн) {R['active30']}",
             ""] + flags(R) + [""]
    for m in R["managers"]:
        lines.append(f"👤 {m['name']}: {m['orders']} зак. / {fmt(m['rev'])} грн · чек {fmt(m['aov'])} · "
                     f"отмены {p1(m['cancel'])}% · новых {m['new']} · скидки {p1(m['disc'])}%")
    risk = sum(len(v) for v in R["at_risk"].values())
    if risk and R["kind"] == "week":
        lines += ["", f"📞 Клиенты «на грани» ({C.AT_RISK_DAYS[0]}–{C.AT_RISK_DAYS[1]} дн без заказа): {risk}"]
        for k, v in R["at_risk"].items():
            lines.append(f"• {k}: {len(v)}, оборот {fmt(sum(r['ltv'] for r in v))} грн")
        lines.append("Списки для обзвона — в файле ниже.")
    return "\n".join(lines)[:4000]


def render(R):
    S, P = R["S"], R["P"]
    def row(name, cur, prev, norm=""):
        return [D._td(escape(name)), D._td(cur, True), D._td(prev, True), D._td(escape(norm))]
    kpi = [row("Выручка, грн", fmt(S["rev"]), fmt(P["rev"])),
           row("Заказов", fmt(S["orders"]), fmt(P["orders"])),
           row("Средний чек, грн", fmt(S["aov"]), fmt(P["aov"])),
           row("Покупателей", fmt(S["buyers"]), fmt(P["buyers"])),
           row("Новых клиентов", fmt(S["new"]), fmt(P["new"]),
               f"≥{C.NORM_NEW_WEEK if R['kind'] == 'week' else round(C.NORM_NEW_WEEK * 4.3)}"),
           row("Отмены, %", p1(S["cancel"]), p1(P["cancel"]), f"≤{p1(C.NORM_CANCEL)}"),
           row("Скидки, % оборота", p1(S["disc"]), p1(P["disc"]), f"≤{p1(C.NORM_DISCOUNT)}"),
           row("Отправка: 90% заказов за, ч", str(S["ship_p90"] or "—"), str(P["ship_p90"] or "—"),
               f"≤{C.NORM_SHIP_P90_H}"),
           row("«Помилка FDF»", str(S["errors"]), str(P["errors"])),
           row("Возвраты", str(S["returns"]), str(P["returns"])),
           row("Без менеджера", str(S["unassigned"]), str(P["unassigned"]), "0")]
    mg = [[D._td(escape(m["name"])), D._td(fmt(m["orders"]), True), D._td(fmt(m["rev"]), True),
           D._td(fmt(m["aov"]), True),
           D._td(D._pill("red" if m["cancel"] > C.NORM_CANCEL else "ok", p1(m["cancel"]) + "%"), True),
           D._td(str(m["new"]), True), D._td(p1(m["disc"]) + "%", True)] for m in R["managers"]]
    src = [[D._td(escape(s["name"])), D._td(fmt(s["orders"]), True), D._td(fmt(s["rev"]), True),
            D._td(fmt(s["aov"]), True),
            D._td(D._pill("red" if s["cancel"] >= 7 else "yellow" if s["cancel"] >= 4 else "ok",
                          p1(s["cancel"]) + "%"), True)] for s in R["sources"]]
    seg = R["segments"]
    seg_rows = [[D._td(k), D._td(fmt(seg.get(k, 0)), True)] for k in ("≤30", "31–60", "61–90", "90+")]
    risk_html = ""
    for k, v in R["at_risk"].items():
        rows = [[D._td(escape(r["buyer"])), D._td(D._oid(r["last"])), D._td(f"{r['days']} дн", True),
                 D._td(f"раз в {r['interval']} дн" if r["interval"] else "1 заказ"),
                 D._td(str(r["orders"]), True), D._td(fmt(r["ltv"]), True)] for r in v[:40]]
        risk_html += (f'<div class="panel"><h3>{escape(k)} — {len(v)} клиентов</h3>'
                      + D._table(["Клиент", "Последний заказ", ">Без заказа", "Обычно", ">Заказов", ">Оборот, ₴"],
                                 rows, "Нет") + "</div>")
    extra = ""
    if R["kind"] == "month":
        n, r = R["retention"]
        n2, r2 = R["retention_prev"]
        pr = [[D._td(escape(k)), D._td(fmt(v), True), D._td(p1(s) + "%", True)] for k, v, s in R["products"]]
        extra = f"""
  <section class="grid2">
    <div class="panel"><h3>Удержание новых клиентов</h3>
      <p style="margin:0">Новых в прошлом месяце: <b>{n}</b>, вернулись в этом: <b>{p1(r)}%</b>
      (месяцем ранее {p1(r2)}% из {n2}). Норма ≥{C.NORM_RETENTION}%.</p>
      <p style="margin:0">Повторные покупатели: <b>{p1(R['repeat'])}%</b> · доля топ-20% клиентов в выручке: <b>{p1(R['top20'])}%</b></p></div>
    <div class="panel"><h3>Выручка по линейкам</h3>{D._table(["Линейка", ">Выручка, ₴", ">Доля"], pr, "Нет данных о товарах")}</div>
  </section>"""
    fl = "".join(f"<li>{escape(f)}</li>" for f in flags(R))
    body = f"""
<div class="wrap">
  <header><div><div class="eyebrow">FDF · KPI</div><h1>{escape(R['title'].capitalize())}</h1></div>
    <div class="muted num">сформирован {R['generated']}</div></header>
  <section class="panel brief">
    <div><span class="eyebrow">Отклонения от норм</span><ul>{fl}</ul></div>
    <div><span class="eyebrow">Клиентская база на конец периода</span>
      {D._table(["Дней с последнего заказа", ">Клиентов"], seg_rows, "")}</div>
  </section>
  <section><h2>Показатели</h2><div class="panel">{D._table(["Показатель", ">Период", ">Прошлый", "Норма"], kpi, "")}</div></section>
  <section><h2>Менеджеры</h2><div class="panel">{D._table(["Менеджер", ">Заказов", ">Выручка, ₴", ">Ср. чек", ">Отмены", ">Новых", ">Скидки"], mg, "Нет заказов")}</div></section>
  <section><h2>Источники</h2><div class="panel">{D._table(["Источник", ">Заказов", ">Выручка, ₴", ">Ср. чек", ">Отмены"], src, "Нет")}</div></section>
  {extra}
  {"" if R["kind"] != "week" else ""}<section{' hidden' if R["kind"] != "week" else ''}><h2>Клиенты «на грани»: {C.AT_RISK_DAYS[0]}–{C.AT_RISK_DAYS[1]} дней без заказа</h2>
    <p class="muted" style="margin:0">Обычно клиенты заказывают раз в 20 дней. Позвонить или написать до того, как они уйдут к конкурентам. Сортировка по обороту.</p>
    {risk_html or '<div class="empty">Нет</div>'}</section>
</div>"""
    return ('<!doctype html><html lang="ru"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>FDF KPI · {escape(R["title"])}</title>'
            '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Onest:wght@400;600'
            '&family=Unbounded:wght@600&display=swap">'
            f"<style>{D.CSS}</style></head><body>{body}</body></html>")


def main(argv):
    kind = "month" if "month" in argv else "week"
    now = datetime.now(keycrm.KYIV)
    if "--demo" in argv:
        raw = demo_data.generate(now)
    else:
        key = os.environ.get("KEYCRM_API_KEY") or sys.exit("ERROR: KEYCRM_API_KEY не задан")
        raw = keycrm.fetch(key, now, days=150 if kind == "week" else 400, products=kind == "month")
    R = build(kind, keycrm.normalize(raw), now)
    out = Path(os.environ.get("SALES_OUT_DIR", "out"))
    out.mkdir(exist_ok=True)
    path = out / f"fdf_kpi_{kind}_{now:%Y-%m-%d}.html"
    path.write_text(render(R), encoding="utf-8")
    (out / f"fdf_kpi_{kind}_{now:%Y-%m-%d}.json").write_text(
        json.dumps(R, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    text = telegram_text(R)
    print("---- TELEGRAM ----\n" + text + "\n------------------\nФайл:", path)
    if "--dry-run" in argv or ("--demo" in argv and "--send" not in argv):
        return
    telegram("sendMessage", {"text": text, "disable_web_page_preview": "true"})
    telegram("sendDocument", {"caption": f"KPI · {R['title']}"}, (path.name, path.read_bytes()))
    print("Отправлено в Telegram")


if __name__ == "__main__":
    main(sys.argv[1:])
