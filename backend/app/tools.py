from __future__ import annotations

from collections import defaultdict
from typing import Any

from langchain_core.tools import BaseTool, tool

from . import db, memory_bridge

MEMORY_TOOL_NAME = "search_memory"

# ============================================================
# TOPIC 1 — CALENDAR & TASKS
# ============================================================


def create_task_impl(user_id: str, title: str, due_date: str | None = None, notes: str | None = None) -> dict[str, Any]:
    row_id = db.new_id()
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO tasks (id, user_id, title, due_date, notes, done, created_at) "
            "VALUES (?, ?, ?, ?, ?, 0, ?)",
            (row_id, user_id, title, due_date, notes, db.now_iso()),
        )
    return {"id": row_id, "title": title, "due_date": due_date, "notes": notes}


def list_tasks_impl(user_id: str) -> dict[str, Any]:
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, title, due_date, notes, done, created_at FROM tasks "
            "WHERE user_id = ? ORDER BY created_at DESC LIMIT 20",
            (user_id,),
        ).fetchall()
    return {"tasks": [dict(row) for row in rows]}


# ============================================================
# TOPIC 2 — FINANCE & EXPENSES
# ============================================================


def add_expense_impl(user_id: str, amount: float, category: str, date: str, note: str | None = None) -> dict[str, Any]:
    row_id = db.new_id()
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO expenses (id, user_id, amount, category, note, date, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (row_id, user_id, amount, category, note, date, db.now_iso()),
        )
    return {"id": row_id, "amount": amount, "category": category, "date": date}


def get_expense_summary_impl(user_id: str) -> dict[str, Any]:
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT amount, category, date FROM expenses WHERE user_id = ?",
            (user_id,),
        ).fetchall()

    by_month: dict[str, float] = defaultdict(float)
    by_category: dict[str, float] = defaultdict(float)

    for row in rows:
        month = str(row["date"])[:7]
        by_month[month] += float(row["amount"])
        by_category[row["category"]] += float(row["amount"])

    return {
        "total": sum(by_month.values()),
        "by_month": dict(sorted(by_month.items())),
        "by_category": dict(sorted(by_category.items(), key=lambda kv: -kv[1])),
    }


# ============================================================
# TOPIC 3 — HEALTH & FITNESS
# ============================================================


def log_meal_impl(
    user_id: str,
    name: str,
    kcal: float,
    date: str,
    protein: float | None = None,
    fat: float | None = None,
    carbs: float | None = None,
) -> dict[str, Any]:
    row_id = db.new_id()
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO meals (id, user_id, name, kcal, protein, fat, carbs, date, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (row_id, user_id, name, kcal, protein, fat, carbs, date, db.now_iso()),
        )
    return {"id": row_id, "name": name, "kcal": kcal, "date": date}


def log_weight_impl(user_id: str, value_kg: float, date: str) -> dict[str, Any]:
    row_id = db.new_id()
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO weights (id, user_id, value_kg, date, created_at) VALUES (?, ?, ?, ?, ?)",
            (row_id, user_id, value_kg, date, db.now_iso()),
        )
    return {"id": row_id, "value_kg": value_kg, "date": date}


DAILY_KCAL_GOAL = 2200


def get_health_summary_impl(user_id: str) -> dict[str, Any]:
    with db.get_conn() as conn:
        meal_rows = conn.execute(
            "SELECT name, kcal, protein, fat, carbs, date FROM meals WHERE user_id = ? ORDER BY date DESC",
            (user_id,),
        ).fetchall()
        weight_rows = conn.execute(
            "SELECT value_kg, date FROM weights WHERE user_id = ? ORDER BY date ASC",
            (user_id,),
        ).fetchall()

    by_day: dict[str, float] = defaultdict(float)
    for row in meal_rows:
        by_day[row["date"]] += float(row["kcal"])

    today = max(by_day.keys()) if by_day else None
    today_kcal = by_day.get(today, 0.0) if today else 0.0

    return {
        "daily_kcal_goal": DAILY_KCAL_GOAL,
        "today": today,
        "today_kcal": today_kcal,
        "kcal_left_today": max(0.0, DAILY_KCAL_GOAL - today_kcal),
        "kcal_by_day": dict(sorted(by_day.items())),
        "weight_history": [dict(row) for row in weight_rows],
    }


# ============================================================
# LANGCHAIN TOOLS — bound to one user_id, handed to the LangGraph agent
# ============================================================
#
# Each request builds its own tool instances (via build_action_tools /
# build_memory_tool) closed over that request's user_id, since the raw
# functions above are keyed by user but the LLM must never see or fill in
# user_id itself. MCP-provided tools are plain LangChain BaseTool instances
# too, so agent_graph.py can merge them into the same flat list this module
# produces without any special-casing.


def build_action_tools(user_id: str) -> list[BaseTool]:
    """Task/expense/health tools exposed to the agent for one user."""

    @tool
    def create_task(title: str, due_date: str | None = None, notes: str | None = None) -> dict:
        """Создать задачу/напоминание с дедлайном. Действие на запись. due_date — YYYY-MM-DD, опционально."""
        return create_task_impl(user_id=user_id, title=title, due_date=due_date, notes=notes)

    @tool
    def list_tasks() -> dict:
        """Показать последние задачи пользователя."""
        return list_tasks_impl(user_id=user_id)

    @tool
    def add_expense(amount: float, category: str, date: str, note: str | None = None) -> dict:
        """Записать трату. Действие на запись. date — YYYY-MM-DD, используй сегодняшнюю дату, если пользователь не указал."""
        return add_expense_impl(user_id=user_id, amount=amount, category=category, date=date, note=note)

    @tool
    def get_expense_summary() -> dict:
        """Получить сводку трат по категориям и месяцам."""
        return get_expense_summary_impl(user_id=user_id)

    @tool
    def log_meal(
        name: str,
        kcal: float,
        date: str,
        protein: float | None = None,
        fat: float | None = None,
        carbs: float | None = None,
    ) -> dict:
        """Записать приём пищи с КБЖУ. Действие на запись. date — YYYY-MM-DD."""
        return log_meal_impl(
            user_id=user_id, name=name, kcal=kcal, date=date, protein=protein, fat=fat, carbs=carbs
        )

    @tool
    def log_weight(value_kg: float, date: str) -> dict:
        """Записать вес пользователя. Действие на запись. date — YYYY-MM-DD."""
        return log_weight_impl(user_id=user_id, value_kg=value_kg, date=date)

    @tool
    def get_health_summary() -> dict:
        """Получить сводку по калориям за сегодня и динамике веса."""
        return get_health_summary_impl(user_id=user_id)

    return [create_task, list_tasks, add_expense, get_expense_summary, log_meal, log_weight, get_health_summary]


def build_memory_tool(user_id: str) -> BaseTool:
    """The memory-retriever tool: the agent decides on its own, per query,
    whether to call this (and how many times) before answering."""

    @tool(MEMORY_TOOL_NAME)
    def search_memory(query: str) -> str:
        """Найти релевантные факты о пользователе в долгосрочной памяти. Передавай короткий целевой поисковый запрос (например «любимая еда» или «дедлайн проекта»), а не весь вопрос пользователя целиком. Можно вызывать несколько раз подряд с разными формулировками, если первого результата недостаточно."""
        context = memory_bridge.retrieve_memory_context(query, user_id, top_k=5)
        return context if context else "По этому запросу в памяти ничего не найдено."

    return search_memory


def get_sandbox_snapshot(user_id: str) -> dict[str, Any]:
    with db.get_conn() as conn:
        tasks = conn.execute(
            "SELECT id, title, due_date, notes, done, created_at FROM tasks "
            "WHERE user_id = ? ORDER BY created_at DESC LIMIT 20",
            (user_id,),
        ).fetchall()
        expenses = conn.execute(
            "SELECT id, amount, category, note, date, created_at FROM expenses "
            "WHERE user_id = ? ORDER BY created_at DESC LIMIT 20",
            (user_id,),
        ).fetchall()
        meals = conn.execute(
            "SELECT id, name, kcal, protein, fat, carbs, date, created_at FROM meals "
            "WHERE user_id = ? ORDER BY created_at DESC LIMIT 20",
            (user_id,),
        ).fetchall()
        weights = conn.execute(
            "SELECT id, value_kg, date, created_at FROM weights "
            "WHERE user_id = ? ORDER BY created_at DESC LIMIT 20",
            (user_id,),
        ).fetchall()

    return {
        "tasks": [dict(r) for r in tasks],
        "expenses": [dict(r) for r in expenses],
        "meals": [dict(r) for r in meals],
        "weights": [dict(r) for r in weights],
    }
