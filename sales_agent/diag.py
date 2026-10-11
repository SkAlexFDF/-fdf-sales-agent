"""Диагностика ответа KeyCRM: структура и количества, без персональных данных.

    python3 -m sales_agent.diag
"""

import os
import sys
from collections import Counter
from datetime import datetime

from . import keycrm


def main():
    key = os.environ.get("KEYCRM_API_KEY")
    if not key:
        sys.exit("KEYCRM_API_KEY не задан")
    raw = keycrm.fetch(key, datetime.now(keycrm.KYIV))
    print("Статусы:", [(s.get("id"), s.get("name"), s.get("group_id")) for s in raw["statuses"]])
    print("Пользователей:", len(raw["users"]))
    print("Заказов:", len(raw["orders"]))
    if raw["orders"]:
        o = raw["orders"][0]
        print("Поля заказа:", sorted(o.keys()))
        for k in ("status", "manager", "buyer", "shipping"):
            v = o.get(k)
            print(f"  {k}:", sorted(v.keys()) if isinstance(v, dict) else type(v).__name__)
        pays = o.get("payments")
        print("  payments[0]:", sorted(pays[0].keys()) if pays else pays)
        print("  даты:", {k: o.get(k) for k in ("ordered_at", "created_at", "status_changed_at",
                                               "closed_at", "updated_at")})
        print("  payment_status:", Counter(x.get("payment_status") for x in raw["orders"]))
    orders = keycrm.normalize(raw)
    print("После нормализации:", len(orders))
    print("По группам:", Counter(o["group"] for o in orders))
    print("По менеджерам:", Counter(o["manager"] for o in orders))
    print("С оплатой > 0:", sum(1 for o in orders if o["paid"] > 0),
          "| без телефона:", sum(1 for o in orders if not o["has_phone"]),
          "| без доставки:", sum(1 for o in orders if not o["has_shipping"]))


if __name__ == "__main__":
    main()
