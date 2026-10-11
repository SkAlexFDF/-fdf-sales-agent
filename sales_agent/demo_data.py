"""Синтетические данные в формате ответа KeyCRM — для проверки без доступа к СРМ."""

import random
from datetime import timedelta

from . import config as C

STATUSES = [
    {"id": 1, "name": "Новий", "group_id": C.G_NEW},
    {"id": 2, "name": "Підтверджено", "group_id": C.G_APPROVAL},
    {"id": 3, "name": "Комплектація", "group_id": C.G_PRODUCTION},
    {"id": 4, "name": "Відправлено", "group_id": C.G_DELIVERY},
    {"id": 5, "name": "Виконано", "group_id": C.G_DONE},
    {"id": 6, "name": "Скасовано", "group_id": C.G_CANCEL},
]
MANAGERS = [{"id": 11, "full_name": "Олена (демо)"}, {"id": 12, "full_name": "Ігор (демо)"}]
NAMES = ["Марія К.", "Андрій П.", "Зоомагазин «Лапа»", "Ірина С.", "Олег Т.", "Наталія В.",
         "Груминг «Пухнастик»", "Сергій Д.", "Юлія М.", "Ветклініка «Айболить»", "Тетяна Р.",
         "Дмитро Л.", "Катерина Б.", "Розплідник «Самоєд»", "Віктор Н.", "Оксана Г."]


def generate(now, seed=7):
    rnd = random.Random(seed)
    buyers = []
    for i in range(140):
        name = rnd.choice(NAMES) + ("" if i < len(NAMES) else f" #{i}")
        buyers.append({"id": 1000 + i, "full_name": name,
                       "phone": "" if rnd.random() < 0.04 else "+38067" + str(1000000 + i),
                       "mgr": MANAGERS[i % 2 if rnd.random() < 0.85 else (i + 1) % 2],
                       "interval": rnd.choice([7, 10, 14, 14, 21, 30]),
                       "churned": rnd.random() < 0.12, "b2b": "«" in name})
    orders, oid = [], 50000
    start = now - timedelta(days=C.HISTORY_DAYS)
    for b in buyers:
        t = start + timedelta(days=rnd.uniform(0, b["interval"]))
        stop = now - timedelta(days=rnd.randint(35, 60)) if b["churned"] else now
        while t < stop:
            t = t.replace(hour=rnd.randint(8, 21), minute=rnd.randint(0, 59))
            age = (now - t).days
            mgr = b["mgr"]
            r = rnd.random()
            if age > 9:
                gid = C.G_CANCEL if r < (0.13 if mgr["id"] == 12 else 0.06) else C.G_DONE
            elif age > 3:
                gid = C.G_DELIVERY if r < 0.5 else C.G_DONE
            elif age > 1:
                gid = rnd.choice([C.G_PRODUCTION, C.G_DELIVERY, C.G_DELIVERY])
            else:
                gid = rnd.choice([C.G_NEW, C.G_APPROVAL, C.G_PRODUCTION])
            total = round(rnd.uniform(4000, 18000) if b["b2b"] else rnd.uniform(900, 4200), -1)
            disc = round(total * rnd.choice([0, 0, 0, 0.05, 0.1, 0.2 if mgr["id"] == 12 else 0.05]))
            changed = t + timedelta(hours=rnd.uniform(0.5, 30) if gid != C.G_DONE else age * 12)
            if gid == C.G_DELIVERY and rnd.random() < 0.1:
                changed = t  # «застрял» в доставке
            changed = min(changed, now)
            unpaid = gid == C.G_DONE and b["b2b"] and rnd.random() < (0.5 if age < 40 else 0.08)
            orders.append({
                "id": oid, "status_id": gid, "manager_id": mgr["id"], "manager": mgr,
                "buyer": {"id": b["id"], "full_name": b["full_name"], "phone": b["phone"]},
                "ordered_at": t.strftime("%Y-%m-%d %H:%M:%S"),
                "status_changed_at": changed.strftime("%Y-%m-%d %H:%M:%S"),
                "closed_at": changed.strftime("%Y-%m-%d %H:%M:%S") if gid in (5, 6) else None,
                "grand_total": total - disc, "total_discount": disc,
                "payments": [] if unpaid else [{"amount": total - disc, "status": "paid"}],
                "shipping": {} if rnd.random() < 0.03 else {"delivery_service_id": 1},
            })
            oid += 1
            t += timedelta(days=max(2, rnd.gauss(b["interval"], b["interval"] * 0.25)))
    # пара заказов, которые «забыли» в новых
    for o in rnd.sample([o for o in orders if o["status_id"] == C.G_NEW], k=2):
        o["ordered_at"] = o["status_changed_at"] = (now - timedelta(days=2)).strftime("%Y-%m-%d 10:15:00")
    orders.append({"id": oid, "status_id": 1, "manager_id": None,
                   "buyer": {"id": 9999, "full_name": "Новий клієнт з Instagram", "phone": "+380500000000"},
                   "ordered_at": (now - timedelta(hours=20)).strftime("%Y-%m-%d %H:%M:%S"),
                   "grand_total": 1850, "payments": [], "shipping": {}})
    return {"statuses": STATUSES, "users": MANAGERS, "orders": orders}
