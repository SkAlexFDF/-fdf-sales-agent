"""Расчёт показателей по каждому менеджеру и по компании."""

from collections import defaultdict
from datetime import datetime, time, timedelta

from . import config as C

NO_MANAGER = "Без менеджера"


def work_hours(start, end):
    """Рабочие часы между датами (Пн–Сб, WORK_START–WORK_END)."""
    if not start or end <= start:
        return 0.0
    total, day = 0.0, start.date()
    while day <= end.date():
        if day.weekday() in C.WORK_DAYS:
            ws = datetime.combine(day, time(C.WORK_START), start.tzinfo)
            we = datetime.combine(day, time(C.WORK_END), start.tzinfo)
            s, e = max(start, ws), min(end, we)
            if e > s:
                total += (e - s).total_seconds() / 3600
        day += timedelta(days=1)
    return total


def _stats(orders, first_order_at):
    live = [o for o in orders if o["live"]]
    canceled = sum(1 for o in orders if o["cancel"])
    revenue = sum(o["total"] for o in live)
    return {
        "orders": len(orders),
        "revenue": round(revenue),
        "avg_check": round(revenue / len(live)) if live else 0,
        "canceled": canceled,
        "cancel_rate": round(canceled / len(orders), 3) if orders else 0.0,
        "done": sum(1 for o in orders if o["group"] == C.G_DONE),
        "new_clients": sum(1 for o in live if o["buyer_id"]
                           and first_order_at.get(o["buyer_id"]) == o["created"]),
    }


def _in(o, start, end):
    return start <= o["created"] < end


def _limit(o):
    if o["status_l"] in C.STATUS_LIMITS:
        return C.STATUS_LIMITS[o["status_l"]]
    g = o["group"]
    if g in C.STAGE_LIMIT_WH:
        return ("wh", C.STAGE_LIMIT_WH[g])
    if g == C.G_DELIVERY:
        return ("days", C.DELIVERY_LIMIT_DAYS)
    return None


def _stuck(o, now):
    if o["group"] in (C.G_DONE, C.G_CANCEL):
        return None
    rule = _limit(o)
    if not rule:
        return None
    kind, limit = rule
    if kind == "wh":
        wh = work_hours(o["stage_since"], now)
        if wh > limit:
            return {"level": "red" if wh > 3 * limit else "yellow",
                    "age": f"{wh:.0f} раб. ч", "norm": f"{limit} раб. ч", "hours": wh}
    else:
        days = (now - o["stage_since"]).total_seconds() / 86400
        if days > limit:
            return {"level": "red" if days > 2 * limit else "yellow",
                    "age": f"{days:.0f} дн", "norm": f"{limit} дн", "hours": days * 24}
    return None


def _row(o, **extra):
    return dict({"id": o["id"], "buyer": o["buyer"], "status": o["status"],
                 "total": round(o["total"]), "created": o["created"].strftime("%d.%m %H:%M")},
                **extra)


def build(orders, now):
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    p = {
        "yesterday": (today - timedelta(days=1), today),
        "today": (today, now + timedelta(seconds=1)),
        "week": (today - timedelta(days=7), today),
        "prev_week": (today - timedelta(days=14), today - timedelta(days=7)),
        "month": (today - timedelta(days=30), today),
    }

    first_order_at = {}
    for o in sorted(orders, key=lambda x: x["created"]):
        if o["buyer_id"] and o["live"]:
            first_order_at.setdefault(o["buyer_id"], o["created"])

    by_mgr = defaultdict(list)
    names = {}
    for o in orders:
        key = o["manager_id"] or 0
        by_mgr[key].append(o)
        names[key] = o["manager"] or (NO_MANAGER if not key else f"Менеджер #{key}")

    # отток считаем по клиенту, относим к менеджеру последнего заказа
    by_buyer = defaultdict(list)
    for o in orders:
        if o["buyer_id"] and o["live"]:
            by_buyer[o["buyer_id"]].append(o)
    churn = defaultdict(list)
    for bid, lst in by_buyer.items():
        lst.sort(key=lambda x: x["created"])
        if len(lst) < C.CHURN_MIN_ORDERS:
            continue
        span = (lst[-1]["created"] - lst[0]["created"]).days
        interval = span / (len(lst) - 1)
        silent = (now - lst[-1]["created"]).days
        limit = max(C.CHURN_FACTOR * interval, C.CHURN_MIN_DAYS)
        if silent > limit:
            last = lst[-1]
            churn[last["manager_id"] or 0].append({
                "buyer": last["buyer"], "orders": len(lst),
                "ltv": round(sum(x["total"] for x in lst)),
                "interval": round(interval), "silent": silent,
                "last_order": last["id"],
            })

    managers = []
    for key, lst in by_mgr.items():
        stuck, debt, issues = [], [], []
        for o in lst:
            s = _stuck(o, now)
            if s:
                stuck.append(_row(o, **s))
            unpaid = o["total"] - o["paid"] > 1 and o["payment_status"] != "overpaid"
            if unpaid and (o["group"] == C.G_DONE and o["live"] or o["debt_status"]):
                since = o["closed"] or o["stage_since"]
                days = (now - since).days
                debt.append(_row(o, due=round(o["total"] - o["paid"]), days=days,
                                 level="red" if days > C.DEBT_RED_DAYS else
                                 "yellow" if days > C.DEBT_YELLOW_DAYS else "ok"))
            if _in(o, *p["week"]) and o["status_l"] == "помилка fdf":
                issues.append(_row(o, problem="помилка FDF"))
            if _in(o, *p["week"]) and o["live"]:
                if not o["has_phone"]:
                    issues.append(_row(o, problem="нет телефона клиента"))
                if o["total"] and o["discount"] / (o["total"] + o["discount"]) > C.DISCOUNT_WARN:
                    pct = 100 * o["discount"] / (o["total"] + o["discount"])
                    issues.append(_row(o, problem=f"скидка {pct:.0f}%"))
                if o["group"] in (C.G_DELIVERY, C.G_DONE) and not o["has_shipping"]:
                    issues.append(_row(o, problem="нет данных доставки"))
        if not key:
            issues = [_row(o, problem="заказ без менеджера") for o in lst
                      if o["group"] not in (C.G_DONE, C.G_CANCEL)] + issues

        stuck.sort(key=lambda r: -r["hours"])
        debt.sort(key=lambda r: -r["days"])
        ch = sorted(churn.get(key, []), key=lambda r: -r["ltv"])
        st = {name: _stats([o for o in lst if _in(o, *rng)], first_order_at)
              for name, rng in p.items()}
        debt_total = sum(r["due"] for r in debt)
        debt_red = sum(r["due"] for r in debt if r["level"] == "red")
        red_stuck = sum(1 for r in stuck if r["level"] == "red")

        # Индекс дисциплины: прозрачная формула, 100 — идеально (штраф по категории ограничен)
        red_debt = sum(1 for r in debt if r["level"] == "red")
        idx = 100 - min(40, 4 * red_stuck + 2 * (len(stuck) - red_stuck)) \
            - min(20, 2 * red_debt) - min(15, 2 * len(issues)) \
            - (15 if st["month"]["cancel_rate"] > C.CANCEL_RATE_WARN else 0)
        managers.append({
            "id": key, "name": names[key], "stats": st,
            "stuck": stuck, "debt": debt, "debt_total": debt_total, "debt_red": debt_red,
            "issues": issues, "churn": ch, "churn_ltv": sum(r["ltv"] for r in ch),
            "index": max(0, idx),
            "daily": _daily(lst, today),
        })
    managers.sort(key=lambda m: (m["id"] == 0, -m["stats"]["month"]["revenue"]))

    company = {name: _stats([o for o in orders if _in(o, *rng)], first_order_at)
               for name, rng in p.items()}
    return {
        "generated": now.strftime("%d.%m.%Y %H:%M"),
        "date": (today - timedelta(days=1)).strftime("%d.%m.%Y"),
        "company": company,
        "managers": managers,
        "totals": {
            "stuck": sum(len(m["stuck"]) for m in managers),
            "stuck_red": sum(1 for m in managers for r in m["stuck"] if r["level"] == "red"),
            "debt": sum(m["debt_total"] for m in managers),
            "debt_red": sum(m["debt_red"] for m in managers),
            "churn": sum(len(m["churn"]) for m in managers),
            "churn_ltv": sum(m["churn_ltv"] for m in managers),
            "issues": sum(len(m["issues"]) for m in managers),
        },
        "orders_loaded": len(orders),
    }


def _daily(lst, today):
    """Выручка по дням за 30 дней (без отмен) — для графика."""
    days = [(today - timedelta(days=i)).date() for i in range(30, 0, -1)]
    rev = defaultdict(float)
    for o in lst:
        if o["live"]:
            rev[o["created"].date()] += o["total"]
    return [[d.strftime("%d.%m"), round(rev[d])] for d in days]
