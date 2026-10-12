"""Чтение данных из KeyCRM OpenAPI (только GET) и приведение заказов к единому виду."""

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import config as C

BASE = "https://openapi.keycrm.app/v1"
KYIV = ZoneInfo(C.TZ)


def _get(path, params, key):
    url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {key}", "Accept": "application/json"})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:
                time.sleep(5 * (attempt + 1))
                continue
            raise
        except urllib.error.URLError:
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"KeyCRM не отвечает: {path}")


def get_all(path, key, params=None):
    """Все страницы метода. Лимит API ~60 запросов/мин — держим паузу."""
    params = dict(params or {}, limit=50)
    out, page = [], 1
    while True:
        params["page"] = page
        data = _get(path, params, key)
        if isinstance(data, list):
            return data
        out.extend(data.get("data", []))
        if page >= int(data.get("last_page") or 1):
            return out
        page += 1
        time.sleep(1.1)


def fetch(key, now, days=None, products=False):
    """Сырые данные из KeyCRM: статусы, пользователи, заказы за days (по умолчанию HISTORY_DAYS)."""
    start = (now - timedelta(days=days or C.HISTORY_DAYS)).strftime("%Y-%m-%d 00:00:00")
    end = now.strftime("%Y-%m-%d 23:59:59")
    statuses = get_all("/order/status", key)
    try:
        users = get_all("/users", key)
    except Exception as e:  # у ключа может не быть прав на список пользователей
        print(f"[keycrm] /users недоступен: {e}", file=sys.stderr)
        users = []
    orders = get_all("/order", key, {
        "include": "buyer,manager,payments,shipping,status" + (",products" if products else ""),
        "filter[created_between]": f"{start}, {end}",
    })
    try:
        sources = get_all("/order/source", key)
    except Exception as e:
        print(f"[keycrm] /order/source недоступен: {e}", file=sys.stderr)
        sources = []
    return {"statuses": statuses, "users": users, "orders": orders, "sources": sources}


# ----------------------------- нормализация -----------------------------

def parse_dt(v):
    if not v:
        return None
    s = str(v).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        try:
            dt = datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KYIV)
    return dt.astimezone(KYIV)


def _num(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _person_name(p):
    if not p:
        return None
    return (p.get("full_name") or " ".join(
        x for x in (p.get("first_name"), p.get("last_name")) if x) or p.get("username")
        or p.get("email"))


def _paid_amount(o):
    pays = o.get("payments")
    if isinstance(pays, list) and pays:
        return sum(_num(p.get("amount")) for p in pays
                   if str(p.get("status", "paid")).lower() in ("paid", "success", "1"))
    if o.get("payments_total") is not None:
        return _num(o.get("payments_total"))
    return _num(o.get("grand_total")) if o.get("payment_status") == "paid" else 0.0


def normalize(raw):
    """Приводит сырой ответ KeyCRM к списку плоских заказов."""
    groups = {s.get("id"): s.get("group_id") for s in raw.get("statuses", [])}
    names = {s.get("id"): s.get("name") for s in raw.get("statuses", [])}
    users = {u.get("id"): _person_name(u) for u in raw.get("users", [])}
    sources = {x.get("id"): x.get("name") or x.get("alias") for x in raw.get("sources", [])}
    out = []
    for o in raw.get("orders", []):
        st = o.get("status") or {}
        sid = o.get("status_id") or st.get("id")
        gid = o.get("status_group_id") or st.get("group_id") or groups.get(sid)
        mid = o.get("manager_id") or (o.get("manager") or {}).get("id")
        buyer = o.get("buyer") or {}
        created = parse_dt(o.get("ordered_at")) or parse_dt(o.get("created_at"))
        total = _num(o.get("grand_total"))
        out.append({
            "id": o.get("id"),
            "created": created,
            "stage_since": (parse_dt(o.get("status_changed_at"))
                            or parse_dt(o.get("updated_at")) or created),
            "closed": parse_dt(o.get("closed_at")),
            "group": int(gid) if gid else None,
            "status": st.get("name") or names.get(sid) or C.GROUP_NAMES.get(gid, "—"),
            "payment_status": o.get("payment_status"),
            "manager_id": mid,
            "manager": _person_name(o.get("manager")) or users.get(mid),
            "buyer_id": buyer.get("id") or o.get("client_id"),
            "buyer": _person_name(buyer) or "Без имени",
            "has_phone": bool(buyer.get("phone")),
            "total": total,
            "discount": _num(o.get("total_discount")),
            "paid": _paid_amount(o),
            "has_shipping": bool(o.get("shipping")),
            "comment": (o.get("manager_comment") or "")[:300],
            "source": sources.get(o.get("source_id")) or (f"#{o.get('source_id')}" if o.get("source_id") else "—"),
            "shipped": parse_dt((o.get("shipping") or {}).get("shipping_date_actual")),
            "products": [{"sku": p.get("sku") or (p.get("offer") or {}).get("sku") or "",
                          "sum": _num(p.get("price_sold") or p.get("price")) * _num(p.get("quantity") or 1)}
                         for p in (o.get("products") or [])],
        })
    res = []
    for o in out:
        sl = o["status"].strip().lower()
        if not o["created"] or sl in C.IGNORE_STATUSES:
            continue
        o["status_l"] = sl
        o["debt_status"] = sl in C.DEBT_STATUSES
        o["is_return"] = sl in C.RETURN_STATUSES
        # продажа: не отмена (кроме долга) и не возврат
        o["live"] = (o["group"] != C.G_CANCEL or o["debt_status"]) and not o["is_return"]
        # отмена, за которую отвечает менеджер
        o["cancel"] = o["group"] == C.G_CANCEL and sl not in C.NOT_MANAGER_CANCEL
        res.append(o)
    return res
