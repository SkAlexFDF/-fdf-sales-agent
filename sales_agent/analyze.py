"""Глубокий анализ KeyCRM за год: агрегаты без персональных данных (печатаются в лог).

    python3 -m sales_agent.analyze [дней=365]
"""

import json
import os
import statistics as st
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from . import config as C
from . import keycrm


def med(xs):
    return round(st.median(xs), 1) if xs else None


def pct(a, b):
    return round(100 * a / b, 1) if b else 0.0


def names(key, path):
    try:
        return {x.get("id"): x.get("name") or x.get("alias") for x in keycrm.get_all(path, key)}
    except Exception as e:
        print(f"[warn] {path}: {e}", file=sys.stderr)
        return {}


def sku_group(sku):
    s = (sku or "").upper()
    for p, g in (("OPT", "OPT весовой"), ("BOX", "BOX боксы"), ("SP", "SP Small Pack"),
                 ("SM", "SM сушёные ласощі"), ("AP", "AP апетайзеры"), ("СЗ", "СЗ индив. рецепты"),
                 ("J", "J натуралка")):
        if s.startswith(p):
            return g
    return "прочее"


def main():
    key = os.environ["KEYCRM_API_KEY"]
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 365
    now = datetime.now(keycrm.KYIV)
    start = (now - timedelta(days=days)).strftime("%Y-%m-%d 00:00:00")
    raw = {
        "statuses": keycrm.get_all("/order/status", key),
        "users": keycrm.get_all("/users", key),
        "orders": keycrm.get_all("/order", key, {
            "include": "buyer,manager,payments,shipping,status,products",
            "filter[created_between]": f"{start}, {now:%Y-%m-%d 23:59:59}"}),
    }
    sources = names(key, "/order/source")
    delivery = names(key, "/order/delivery-service")
    paymeth = names(key, "/order/payment-method")
    rawby = {o["id"]: o for o in raw["orders"]}
    orders = keycrm.normalize(raw)
    for o in orders:
        r = rawby[o["id"]]
        o["source"] = sources.get(r.get("source_id"), f"#{r.get('source_id')}")
        sh = r.get("shipping") or {}
        o["delivery"] = delivery.get(sh.get("delivery_service_id"), f"#{sh.get('delivery_service_id')}")
        o["city"] = (sh.get("shipping_address_city") or "—").strip()
        o["shipped"] = keycrm.parse_dt(sh.get("shipping_date_actual"))
        o["margin"] = keycrm._num(r.get("margin_sum"))
        o["promo"] = bool(r.get("promocode"))
        o["paymeth"] = Counter(paymeth.get(p.get("payment_method_id"), f"#{p.get('payment_method_id')}")
                               for p in (r.get("payments") or []))
        o["products"] = [{"sku": p.get("sku") or (p.get("offer") or {}).get("sku"),
                          "name": p.get("name"), "qty": keycrm._num(p.get("quantity")),
                          "sum": keycrm._num(p.get("price_sold") or p.get("price")) *
                          keycrm._num(p.get("quantity") or 1)}
                         for p in (r.get("products") or [])]
    live = [o for o in orders if o["live"]]
    out = {"period_days": days, "orders_total": len(orders), "live": len(live)}

    # --- по месяцам
    by_m = defaultdict(list)
    for o in orders:
        by_m[o["created"].strftime("%Y-%m")].append(o)
    first = {}
    for o in sorted(live, key=lambda x: x["created"]):
        if o["buyer_id"]:
            first.setdefault(o["buyer_id"], o["created"])
    months = []
    for m in sorted(by_m):
        lst = by_m[m]
        lv = [o for o in lst if o["live"]]
        rev = sum(o["total"] for o in lv)
        buyers = {o["buyer_id"] for o in lv if o["buyer_id"]}
        new = {b for b in buyers if first[b].strftime("%Y-%m") == m}
        months.append({"m": m, "orders": len(lst), "live": len(lv), "rev": round(rev),
                       "aov": round(rev / len(lv)) if lv else 0,
                       "cancel%": pct(sum(o["cancel"] for o in lst), len(lst)),
                       "buyers": len(buyers), "new": len(new), "repeat": len(buyers - new),
                       "margin": round(sum(o["margin"] for o in lv)),
                       "disc%": pct(sum(o["discount"] for o in lv), rev + sum(o["discount"] for o in lv))})
    out["months"] = months

    # --- клиенты
    per_b = defaultdict(list)
    for o in live:
        if o["buyer_id"]:
            per_b[o["buyer_id"]].append(o)
    ltv = sorted((sum(o["total"] for o in l) for l in per_b.values()), reverse=True)
    top20 = sum(ltv[:max(1, len(ltv) // 5)])
    intervals = []
    for l in per_b.values():
        l.sort(key=lambda x: x["created"])
        intervals += [(b["created"] - a["created"]).days for a, b in zip(l, l[1:])]
    cnt = Counter(min(len(l), 6) for l in per_b.values())
    out["clients"] = {
        "unique": len(per_b), "avg_ltv": round(sum(ltv) / len(ltv)) if ltv else 0,
        "median_ltv": med(ltv), "top20%_share": pct(top20, sum(ltv)),
        "orders_per_client": {("6+" if k == 6 else k): v for k, v in sorted(cnt.items())},
        "repeat_rate%": pct(sum(1 for l in per_b.values() if len(l) > 1), len(per_b)),
        "interval_median_days": med(intervals),
        "interval_p25_p75": [round(st.quantiles(intervals, n=4)[0]), round(st.quantiles(intervals, n=4)[2])]
        if len(intervals) > 4 else None,
        "active_30d": sum(1 for l in per_b.values() if (now - l[-1]["created"]).days <= 30),
        "silent_31_60": sum(1 for l in per_b.values() if 30 < (now - l[-1]["created"]).days <= 60),
        "silent_61_90": sum(1 for l in per_b.values() if 60 < (now - l[-1]["created"]).days <= 90),
        "silent_90+": sum(1 for l in per_b.values() if (now - l[-1]["created"]).days > 90),
    }
    # когорты: доля клиентов, заказавших снова в месяц +1..+3
    coh = defaultdict(lambda: [set(), set(), set(), set()])
    for b, l in per_b.items():
        c = first[b].strftime("%Y-%m")
        coh[c][0].add(b)
        fm = first[b].year * 12 + first[b].month
        for o in l:
            d = o["created"].year * 12 + o["created"].month - fm
            if 1 <= d <= 3:
                coh[c][d].add(b)
    out["cohorts_ret%"] = {c: [len(v[0])] + [pct(len(v[i]), len(v[0])) for i in (1, 2, 3)]
                           for c, v in sorted(coh.items())}

    # --- источники, доставка, оплата, города
    def breakdown(field, top=12):
        g = defaultdict(list)
        for o in orders:
            g[o[field]].append(o)
        rows = []
        for k, l in g.items():
            lv = [o for o in l if o["live"]]
            rev = sum(o["total"] for o in lv)
            rows.append({"k": k, "orders": len(l), "rev": round(rev),
                         "aov": round(rev / len(lv)) if lv else 0,
                         "cancel%": pct(sum(o["cancel"] for o in l), len(l))})
        return sorted(rows, key=lambda r: -r["rev"])[:top]
    out["sources"] = breakdown("source")
    out["delivery"] = breakdown("delivery")
    out["cities"] = breakdown("city", 15)
    pm = Counter()
    for o in live:
        pm.update(o["paymeth"])
    out["payment_methods"] = pm.most_common(10)
    out["payment_status"] = Counter(o["payment_status"] for o in orders).most_common()
    out["status_mix"] = Counter(o["status"] for o in orders).most_common()

    # --- скорость
    to_ship = [(o["shipped"] - o["created"]).total_seconds() / 3600 for o in live
               if o["shipped"] and o["shipped"] > o["created"]]
    to_close = [(o["closed"] - o["created"]).total_seconds() / 86400 for o in live
                if o["closed"] and o["group"] == C.G_DONE and o["closed"] > o["created"]]
    out["speed"] = {"order_to_shipped_h_median": med(to_ship),
                    "order_to_shipped_h_p90": round(st.quantiles(to_ship, n=10)[8]) if len(to_ship) > 10 else None,
                    "order_to_completed_days_median": med(to_close),
                    "order_to_completed_days_p90": round(st.quantiles(to_close, n=10)[8], 1) if len(to_close) > 10 else None}

    # --- когда заказывают
    out["weekday"] = Counter("Пн Вт Ср Чт Пт Сб Вс".split()[o["created"].weekday()] for o in orders).most_common()
    out["hours"] = sorted(Counter(o["created"].hour for o in orders).items())

    # --- товары
    sku_rev, sku_qty, grp_rev, names_ = Counter(), Counter(), Counter(), {}
    for o in live:
        for p in o["products"]:
            s = p["sku"] or p["name"]
            sku_rev[s] += p["sum"]
            sku_qty[s] += p["qty"]
            grp_rev[sku_group(p["sku"])] += p["sum"]
            names_[s] = p["name"]
    out["top_sku"] = [(s, (names_.get(s) or "")[:40], round(r), round(sku_qty[s]))
                      for s, r in sku_rev.most_common(20)]
    out["sku_groups"] = [(g, round(r), pct(r, sum(grp_rev.values()))) for g, r in grp_rev.most_common()]
    out["items_per_order_median"] = med([len(o["products"]) for o in live if o["products"]])
    out["promo_orders%"] = pct(sum(o["promo"] for o in live), len(live))

    # --- менеджеры (последние 90 дней)
    since = now - timedelta(days=90)
    gm = defaultdict(list)
    for o in orders:
        if o["created"] >= since and (o["manager"] or "—") not in C.EXCLUDE_MANAGERS:
            gm[o["manager"] or "Без менеджера"].append(o)
    mrows = []
    for k, l in gm.items():
        lv = [o for o in l if o["live"]]
        rev = sum(o["total"] for o in lv)
        bs = {o["buyer_id"] for o in lv}
        mrows.append({"k": k, "orders": len(l), "rev": round(rev),
                      "aov": round(rev / len(lv)) if lv else 0,
                      "cancel%": pct(sum(o["cancel"] for o in l), len(l)),
                      "new_clients": sum(1 for b in bs if b in first and first[b] >= since),
                      "disc%": pct(sum(o["discount"] for o in lv), rev + sum(o["discount"] for o in lv)),
                      "unpaid_done": sum(1 for o in lv if o["group"] == C.G_DONE and o["total"] - o["paid"] > 1)})
    out["managers_90d"] = sorted(mrows, key=lambda r: -r["rev"])

    for k, v in out.items():
        print(f"## {k}: {json.dumps(v, ensure_ascii=False, default=str)}")


if __name__ == "__main__":
    main()
