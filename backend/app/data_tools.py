"""Agent tools over the user's imported data (email / calendar / messenger /
bank notifications). Each wraps a deterministic function from
personal_analytics.py — the model picks the tool and phrases the answer,
but never computes sums, free slots or rankings itself.
"""
from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool, tool

from . import personal_analytics as analytics
from . import personal_data, proactive
from .injection_guard import UNTRUSTED_NOTE

DATA_TOOL_NAMES = {
    "data_overview", "calendar_events", "schedule_analysis", "recurring_schedule", "search_emails",
    "get_email_thread", "inbox_overview", "search_messages", "top_contacts", "finance_report", "monthly_cashflow",
    "proactive_suggestions", "recurring_payments",
}

_PERIOD_HELP = (
    "period — today, tomorrow, yesterday, this_week, next_week, last_week, this_month, last_month "
    "(прошлый месяц), month_before_last (позапрошлый), last_N_days (например last_3_days), all; "
    "либо date_from/date_to в формате YYYY-MM-DD (тогда period не нужен)."
)


def _wrap(result: dict[str, Any]) -> dict[str, Any]:
    return {"note_for_assistant": UNTRUSTED_NOTE, **result}


def _safe(fn, *args, **kwargs) -> dict[str, Any]:
    try:
        return _wrap(fn(*args, **kwargs))
    except ValueError as exc:
        return {"error": str(exc)}


def _tool(description: str):
    def decorate(fn):
        fn.__doc__ = description
        return tool(fn)
    return decorate


def build_data_tools(user_id: str) -> list[BaseTool]:

    @_tool("""Обзор загруженных данных пользователя: кто он (имя, email, часовой пояс), сколько писем/событий/
        сообщений/банковских операций и за какие даты, список чатов с участниками. Вызывай, когда не ясно,
        какие данные вообще есть, или нужно узнать имя/почту/часовой пояс пользователя.""")
    def data_overview() -> dict:
        return _wrap(personal_data.data_overview(user_id))

    @_tool("""События календаря пользователя за период (с временем, участниками и их email, местом, важностью
        importance: critical/high/medium/low/personal). Используй для «что у меня завтра/на неделе», «какие
        встречи», «кого уведомить об отмене», «с кем встреча». query — необязательный фильтр по тексту.
        """ + _PERIOD_HELP)
    def calendar_events(period: str | None = None, date_from: str | None = None, date_to: str | None = None,
                        query: str | None = None) -> dict:
        return _safe(analytics.calendar_events, user_id, period, date_from, date_to, query)

    @_tool("""Точный расчёт по расписанию: свободные окна нужной длины по дням, первый подходящий слот, загрузка
        каждого дня в минутах и самый загруженный день. ОБЯЗАТЕЛЬНО используй для «найди N минут/час»,
        «когда я свободен», «самый загруженный день», «окна под тренировки» — никогда не считай окна сам.
        min_free_minutes — минимальная длина окна; day_start/day_end — границы рабочего дня (не указывай — они
        определятся по расписанию пользователя; для утра/вечера вне работы расширь, например 06:30–22:00); not_before/not_after — «после
        11:00», «до 16:00». """ + _PERIOD_HELP)
    def schedule_analysis(period: str | None = None, date_from: str | None = None, date_to: str | None = None,
                          min_free_minutes: int = 30, day_start: str | None = None, day_end: str | None = None,
                          not_before: str | None = None, not_after: str | None = None,
                          include_weekends: bool = False) -> dict:
        return _safe(analytics.schedule_analysis, user_id, period, date_from, date_to, min_free_minutes,
                     day_start, day_end, not_before, not_after, include_weekends)

    @_tool("""Регулярные активности пользователя: повторяющиеся события календаря (дни недели, время, период) и
        регулярности, видимые только по почте (например, еженедельные подтверждения тренировок с днём и
        временем). Используй для «какие у меня регулярные события/привычки/тренировки».""")
    def recurring_schedule() -> dict:
        return _wrap(analytics.recurring_schedule(user_id))

    @_tool("""Полнотекстовый поиск по загруженной почте пользователя (входящие и отправленные) — по теме, тексту
        и отправителю. Используй, чтобы найти письмо по теме/человеку/компании, найти факт в письмах (дата
        рождения, адрес, договорённость) или перед подготовкой ответа на письмо. direction: "in" или "out".
        Для полного текста письма и всей переписки вызови get_email_thread. """ + _PERIOD_HELP)
    def search_emails(query: str | None = None, period: str | None = None, date_from: str | None = None,
                      date_to: str | None = None, sender: str | None = None, direction: str | None = None,
                      limit: int = 10) -> dict:
        return _safe(analytics.search_emails, user_id, query, period, date_from, date_to, sender, direction, limit)

    @_tool("""Полный текст письма и всей ветки переписки по id письма (id бери из search_emails/inbox_overview).""")
    def get_email_thread(email_id: str) -> dict:
        return _safe(analytics.get_email_thread, user_id, email_id)

    @_tool("""Разбор входящих за период: каждое письмо с типом (work/external/service_notification/newsletter),
        отметкой, ответил ли пользователь (user_replied), и найденным дедлайном со статусом (просрочено/сегодня/
        впереди). Используй для «что во входящих», «на что я не ответила», «что срочного в почте».
        Банковские уведомления по умолчанию не перечисляются (их считает finance_report). """ + _PERIOD_HELP)
    def inbox_overview(period: str | None = "last_3_days", date_from: str | None = None, date_to: str | None = None,
                       include_notifications: bool = False) -> dict:
        return _safe(analytics.inbox_overview, user_id, period, date_from, date_to, include_notifications)

    @_tool("""Поиск по переписке в мессенджере (Telegram) с контекстом соседних сообщений. Используй для фактов
        из личного общения: откуда пользователь, когда переехал, где учился, как его называют друзья и
        семья, хобби, планы, спорт с друзьями. Сообщения с пометкой «(пользователь)» написал сам
        пользователь. chat/sender — фильтры (названия чатов есть в data_overview). """ + _PERIOD_HELP)
    def search_messages(query: str | None = None, chat: str | None = None, sender: str | None = None,
                        period: str | None = None, date_from: str | None = None, date_to: str | None = None,
                        limit: int = 15) -> dict:
        return _safe(analytics.search_messages, user_id, query, chat, sender, period, date_from, date_to, limit)

    @_tool("""Рейтинг людей, с которыми пользователь больше всего взаимодействует: совместные встречи в календаре
        + входящие письма + сообщения в чатах, с разбивкой. scope: "work" (по работе, по умолчанию),
        "personal" (личное общение), "all". Используй для «с кем я чаще всего общаюсь/работаю».""")
    def top_contacts(limit: int = 10, scope: str = "work") -> dict:
        return _safe(analytics.top_contacts, user_id, limit, scope)

    @_tool("""Точные суммы трат/доходов по банковским операциям пользователя: итог, по месяцам, по категориям,
        топ мест. ОБЯЗАТЕЛЬНО используй для любых вопросов «сколько я потратила на X», «сравни траты
        месяцев» — никогда не суммируй сам. kind: "expense" | "income" | "all". categories — список категорий
        (точные названия есть в categories_available ответа); для «кафе/рестораны/еда вне дома» передай все
        категории общепита, например ["Кофейни", "Кафе и рестораны"]. Для сравнения двух месяцев вызови
        дважды (last_month и month_before_last) или один раз за оба месяца и смотри by_month. """ + _PERIOD_HELP)
    def finance_report(period: str | None = None, date_from: str | None = None, date_to: str | None = None,
                       categories: list[str] | None = None, kind: str = "expense", merchant: str | None = None) -> dict:
        return _safe(analytics.finance_report, user_id, period, date_from, date_to, categories, kind, merchant)

    @_tool("""Доходы, расходы и остаток (сколько удаётся отложить) по каждому месяцу, среднее по полным месяцам,
        источники дохода (регулярные и разовые/новые). Используй для вопросов про накопления, «сколько я
        могу откладывать», «успею ли накопить на цель к дате».""")
    def monthly_cashflow() -> dict:
        return _wrap(analytics.monthly_cashflow(user_id))

    @_tool("""Регулярные ежемесячные платежи и подписки по банковским уведомлениям: что, сколько, в какой день
        месяца, когда следующее списание (next_expected) и что спишется до конца текущего месяца
        (upcoming_this_month), плюс списания, о которых заранее предупредили письмом (announced_in_email). На вопрос
        «какие подписки спишутся до конца месяца» перечисли ВСЕ пункты SUBSCRIPTIONS_DUE_BEFORE_MONTH_END. Используй
        для «какие подписки спишутся», «автосписания», «регулярные платежи», «на что я подписан».""")
    def recurring_payments() -> dict:
        return _wrap(analytics.recurring_payments(user_id))

    @_tool("""Что ассистент сам заметил в данных пользователя и может предложить сделать: неотвеченные письма с
        горящим сроком, подозрительные письма, накладки и перегруз в календаре, конфликт с регулярными
        активностями, всплеск трат, новый доход — у каждого пункта готовое действие. Вызывай, когда пользователь
        здоровается, спрашивает «что нового», «на что обратить внимание», «что посоветуешь», или начинает разговор
        без конкретной задачи.""")
    def proactive_suggestions() -> dict:
        return _wrap(proactive.scan(user_id))

    return [
        data_overview, calendar_events, schedule_analysis, recurring_schedule, search_emails, get_email_thread,
        inbox_overview, search_messages, top_contacts, finance_report, monthly_cashflow, proactive_suggestions,
        recurring_payments,
    ]
