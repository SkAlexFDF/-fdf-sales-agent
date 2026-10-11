"""Рекомендации и вопросы менеджерам: Claude (если есть ANTHROPIC_API_KEY) или правила."""

import json
import os
import sys

from . import config as C

MODEL = "claude-opus-5-5"

SYSTEM = """Ты — операционный директор и аналитик продаж компании FDF (Fresh Dog Food):
натуральный корм для собак, Украина, розница и опт. У владельца 2 менеджера, которые ведут
клиентов во всех каналах KeyCRM и обрабатывают заказы.

Тебе дают факты из CRM в JSON. Задача — дать владельцу короткую, жёсткую и полезную
управленческую сводку, а каждому менеджеру — конкретные рекомендации и вопросы, которые
владелец задаст ему сегодня. Правила:
- Опирайся только на цифры из JSON, ничего не выдумывай. Называй номера заказов и клиентов.
- Рекомендация = действие + объект + срок («Сегодня до 12:00 дозвонить клиенту Х по заказу №…»).
- Вопросы менеджеру — открытые, по фактам, без обвинений («Что мешает закрыть заказ №… 3 дня?»).
- Сравнивай менеджеров между собой и с прошлой неделей; отмечай и хорошее.
- Пиши по-русски, деньги в грн, без воды."""

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["telegram_brief", "owner_actions", "managers"],
    "properties": {
        "telegram_brief": {"type": "string",
                           "description": "3–6 строк: главное за вчера и что горит сегодня"},
        "owner_actions": {"type": "array", "items": {"type": "string"},
                          "description": "3–5 действий владельца на сегодня"},
        "managers": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["name", "summary", "recommendations", "questions"],
            "properties": {
                "name": {"type": "string"},
                "summary": {"type": "string", "description": "оценка работы в 2–3 предложениях"},
                "recommendations": {"type": "array", "items": {"type": "string"}},
                "questions": {"type": "array", "items": {"type": "string"}},
            }}},
    },
}


def _facts(report):
    """Сжатые факты для модели (без телефонов и лишних полей)."""
    def mgr(m):
        return {
            "name": m["name"], "index": m["index"], "stats": m["stats"],
            "stuck": m["stuck"][:8], "debt_total": m["debt_total"], "debt_30plus": m["debt_red"],
            "debt_top": m["debt"][:6], "issues": m["issues"][:8],
            "churn_count": len(m["churn"]), "churn_top": m["churn"][:6],
        }
    return {"report_date": report["date"], "company": report["company"],
            "totals": report["totals"], "managers": [mgr(m) for m in report["managers"]],
            "norms": {"stage_limit_work_hours": {C.GROUP_NAMES[g]: h
                                                 for g, h in C.STAGE_LIMIT_WH.items()},
                      "delivery_days": C.DELIVERY_LIMIT_DAYS,
                      "cancel_rate_warn": C.CANCEL_RATE_WARN,
                      "debt_red_days": C.DEBT_RED_DAYS}}


def claude(report):
    import anthropic

    client = anthropic.Anthropic()
    resp = client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "medium",
                       "format": {"type": "json_schema", "schema": SCHEMA}},
        system=SYSTEM,
        messages=[{"role": "user", "content":
                   "Факты из KeyCRM:\n" + json.dumps(_facts(report), ensure_ascii=False)}],
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("модель отказалась отвечать")
    text = "".join(b.text for b in resp.content if b.type == "text")
    data = json.loads(text)
    data["source"] = "Claude"
    return data


def fmt(v):
    return f"{int(v):,}".replace(",", " ")


def rules(report):
    """Запасной вариант без ИИ: рекомендации по правилам."""
    mgrs = []
    for m in report["managers"]:
        rec, q = [], []
        for r in m["stuck"][:3]:
            rec.append(f"Сегодня закрыть зависший заказ №{r['id']} ({r['buyer']}, "
                       f"{r['status']}, {r['age']} при норме {r['norm']}).")
            q.append(f"Что мешает продвинуть заказ №{r['id']}?")
        for r in [d for d in m["debt"] if d["level"] != "ok"][:3]:
            rec.append(f"Напомнить об оплате: №{r['id']} {r['buyer']} — {fmt(r['due'])} грн, "
                       f"{r['days']} дн.")
        if m["debt_red"]:
            q.append(f"Когда будет погашено {fmt(m['debt_red'])} грн долга старше "
                     f"{C.DEBT_RED_DAYS} дней?")
        for c in m["churn"][:3]:
            rec.append(f"Связаться с {c['buyer']}: заказывал раз в {c['interval']} дн, "
                       f"молчит {c['silent']} дн (оборот {fmt(c['ltv'])} грн).")
        if m["churn"]:
            q.append(f"Почему {len(m['churn'])} постоянных клиентов перестали заказывать?")
        cr = m["stats"]["month"]["cancel_rate"]
        if cr > C.CANCEL_RATE_WARN:
            q.append(f"Отмены за 30 дней — {cr:.0%}. Какие основные причины?")
        for i in m["issues"][:2]:
            rec.append(f"Исправить заказ №{i['id']}: {i['problem']}.")
        w, pw = m["stats"]["week"]["revenue"], m["stats"]["prev_week"]["revenue"]
        trend = f"{(w - pw) / pw:+.0%}" if pw else "нет данных"
        mgrs.append({
            "name": m["name"],
            "summary": (f"Неделя: {m['stats']['week']['orders']} заказов, {fmt(w)} грн "
                        f"({trend} к прошлой). Индекс дисциплины {m['index']}/100."),
            "recommendations": rec or ["Критичных отклонений нет — держать темп."],
            "questions": q or ["Какие клиенты могут заказать больше в этом месяце?"],
        })
    t, y = report["totals"], report["company"]["yesterday"]
    brief = (f"Вчера: {y['orders']} заказов на {fmt(y['revenue'])} грн, отмен {y['canceled']}.\n"
             f"Зависших заказов: {t['stuck']} (критичных {t['stuck_red']}).\n"
             f"Дебиторка: {fmt(t['debt'])} грн, из них 30+ дней {fmt(t['debt_red'])} грн.\n"
             f"Клиенты в риске оттока: {t['churn']} (оборот {fmt(t['churn_ltv'])} грн).")
    actions = []
    if t["stuck_red"]:
        actions.append("Разобрать критичные зависшие заказы на утренней планёрке.")
    if t["debt_red"]:
        actions.append("Поставить менеджерам срок по просроченной дебиторке.")
    if t["churn"]:
        actions.append("Дать задачу обзвонить топ клиентов в риске оттока.")
    return {"telegram_brief": brief, "owner_actions": actions or ["Критичного нет."],
            "managers": mgrs, "source": "правила (без ИИ)"}


def analyze(report):
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            return claude(report)
        except Exception as e:
            print(f"[ai] Claude недоступен, использую правила: {e}", file=sys.stderr)
    return rules(report)
