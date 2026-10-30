import calendar
import csv
import io
import json
import os
import re
import smtplib
import sqlite3
from datetime import date, datetime
from email.message import EmailMessage

from flask import Flask, Response, flash, jsonify, redirect, render_template, request, url_for

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get("LIFEFLOW_DB", os.path.join(BASE_DIR, "lifeflow.db"))

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
KINDS = ("收入", "支出")
MAX_AMOUNT = 10_000_000
WEEKDAYS = "一二三四五六日"

app = Flask(__name__)
app.secret_key = os.environ.get("LIFEFLOW_SECRET", "lifeflow-dev-secret-change-me")


def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def fetch_all(sql, params=()):
    conn = get_db()
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def fetch_one(sql, params=()):
    conn = get_db()
    try:
        return conn.execute(sql, params).fetchone()
    finally:
        conn.close()


def run_sql(sql, params=()):
    conn = get_db()
    try:
        with conn:
            return conn.execute(sql, params).lastrowid
    finally:
        conn.close()


def init_db():
    conn = get_db()
    try:
        with conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS expenses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    category TEXT NOT NULL,
                    amount REAL NOT NULL,
                    note TEXT DEFAULT '',
                    record_date TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    due_date TEXT,
                    done INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    id INTEGER PRIMARY KEY,
                    budget REAL NOT NULL DEFAULT 0
                )
                """
            )
            conn.execute("INSERT OR IGNORE INTO settings (id, budget) VALUES (1, 0)")
    finally:
        conn.close()


def parse_amount(raw):
    try:
        value = float(str(raw).replace(",", "").strip())
    except (TypeError, ValueError):
        raise ValueError("金額必須是數字。")
    if value <= 0:
        raise ValueError("金額必須大於 0。")
    if value > MAX_AMOUNT:
        raise ValueError("金額過大，請重新確認。")
    return round(value, 2)


def parse_budget(raw):
    try:
        value = float(str(raw).replace(",", "").strip())
    except (TypeError, ValueError):
        raise ValueError("預算必須是數字。")
    if value < 0:
        raise ValueError("預算不可為負數。")
    if value > MAX_AMOUNT:
        raise ValueError("預算過大，請重新確認。")
    return round(value, 2)


def parse_date(raw, required=False):
    value = (raw or "").strip()
    if not value:
        if required:
            raise ValueError("請選擇日期。")
        return None
    if not DATE_RE.match(value):
        raise ValueError("日期格式需為 YYYY-MM-DD。")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise ValueError("日期不存在，請重新確認。")
    return value


def parse_month(raw):
    value = (raw or "").strip()
    if MONTH_RE.match(value):
        try:
            datetime.strptime(value, "%Y-%m")
            return value
        except ValueError:
            pass
    return date.today().strftime("%Y-%m")


def shift_month(month, delta):
    year, mon = (int(part) for part in month.split("-"))
    index = year * 12 + (mon - 1) + delta
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def month_label(month):
    year, mon = (int(part) for part in month.split("-"))
    return f"{year} 年 {mon} 月"


def month_days(month):
    year, mon = (int(part) for part in month.split("-"))
    return calendar.monthrange(year, mon)[1]


def back(month=None):
    if MONTH_RE.match(month or ""):
        return redirect(url_for("index", month=month))
    return redirect(url_for("index"))


def safe_cell(value):
    text = "" if value is None else str(value)
    if text[:1] in ("=", "+", "-", "@"):
        return "'" + text
    return text


def current_budget():
    row = fetch_one("SELECT budget FROM settings WHERE id = 1")
    return float(row["budget"]) if row else 0.0


def month_summary(month):
    like = month + "%"
    row = fetch_one(
        """
        SELECT
            COALESCE(SUM(CASE WHEN kind = '收入' THEN amount END), 0) AS income,
            COALESCE(SUM(CASE WHEN kind = '支出' THEN amount END), 0) AS spending,
            COUNT(*) AS records
        FROM expenses
        WHERE record_date LIKE ?
        """,
        (like,),
    )
    budget = current_budget()
    income = round(row["income"], 2)
    spending = round(row["spending"], 2)
    return {
        "label": month_label(month),
        "income": income,
        "spending": spending,
        "balance": round(income - spending, 2),
        "budget": budget,
        "usage": round(spending / budget * 100, 1) if budget > 0 else 0.0,
        "over_budget": budget > 0 and spending > budget,
        "remaining_budget": round(budget - spending, 2) if budget > 0 else 0.0,
        "records": row["records"],
    }


def category_breakdown(month):
    rows = fetch_all(
        """
        SELECT category, ROUND(SUM(amount), 2) AS total, COUNT(*) AS count
        FROM expenses
        WHERE kind = '支出' AND record_date LIKE ?
        GROUP BY category
        ORDER BY total DESC
        """,
        (month + "%",),
    )
    return [dict(row) for row in rows]


def daily_series(month):
    rows = fetch_all(
        """
        SELECT substr(record_date, 9, 2) AS day, ROUND(SUM(amount), 2) AS total
        FROM expenses
        WHERE kind = '支出' AND record_date LIKE ?
        GROUP BY day
        """,
        (month + "%",),
    )
    totals = {int(row["day"]): row["total"] for row in rows if row["day"].isdigit()}
    days = month_days(month)
    return {
        "labels": [f"{int(month[5:7])}/{day:02d}" for day in range(1, days + 1)],
        "totals": [round(totals.get(day, 0.0), 2) for day in range(1, days + 1)],
    }


def six_month_trend(month):
    labels = []
    income = []
    spending = []
    for offset in range(5, -1, -1):
        target = shift_month(month, -offset)
        summary = month_summary(target)
        labels.append(target[2:].replace("-", "/"))
        income.append(summary["income"])
        spending.append(summary["spending"])
    return {"labels": labels, "income": income, "spending": spending}


def task_overview():
    today = date.today().isoformat()
    tasks = fetch_all(
        """
        SELECT * FROM tasks
        ORDER BY done ASC, COALESCE(due_date, '9999-12-31') ASC, id DESC
        """
    )
    pending = [task for task in tasks if not task["done"]]
    overdue = [task for task in pending if task["due_date"] and task["due_date"] < today]
    due_today = [task for task in pending if task["due_date"] == today]
    return {
        "tasks": tasks,
        "pending": pending,
        "overdue": overdue,
        "due_today": due_today,
    }


def reminder_payload():
    month = date.today().strftime("%Y-%m")
    summary = month_summary(month)
    tasks = task_overview()
    return {
        "month": month,
        "over_budget": summary["over_budget"],
        "usage": summary["usage"],
        "spending": summary["spending"],
        "budget": summary["budget"],
        "overdue": [
            {"id": task["id"], "title": task["title"], "due_date": task["due_date"]}
            for task in tasks["overdue"]
        ],
        "due_today": [
            {"id": task["id"], "title": task["title"], "due_date": task["due_date"]}
            for task in tasks["due_today"]
        ],
    }


def budget_advice(summary, categories):
    if summary["records"] == 0:
        return "本月還沒有任何紀錄，先新增幾筆收支，系統就能開始分析。"
    if summary["budget"] <= 0:
        suggestion = round(summary["spending"] * 0.9 / 100) * 100
        return f"尚未設定預算。依本月支出推算，建議先設定每月預算 {suggestion:,.0f} 元。"
    if summary["over_budget"]:
        top = categories[0] if categories else None
        if top:
            return (
                f"已超出預算 {summary['spending'] - summary['budget']:,.2f} 元，"
                f"最大支出分類為「{top['category']}」（{top['total']:,.2f} 元），可優先檢視這部分。"
            )
        return f"已超出預算 {summary['spending'] - summary['budget']:,.2f} 元，建議減少非必要支出。"
    if summary["usage"] >= 80:
        return f"預算已使用 {summary['usage']}%，剩下 {summary['remaining_budget']:,.2f} 元，請留意後續支出。"
    return f"預算控制良好，已使用 {summary['usage']}%，還剩 {summary['remaining_budget']:,.2f} 元。"


def export_response(buffer, filename):
    data = "\ufeff" + buffer.getvalue()
    return Response(
        data,
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.context_processor
def inject_helpers():
    return {"month_label": month_label, "shift_month": shift_month}


@app.route("/")
def index():
    month = parse_month(request.args.get("month"))
    summary = month_summary(month)
    categories = category_breakdown(month)
    category_max = max((row["total"] for row in categories), default=0)
    tasks = task_overview()
    today = date.today().isoformat()
    today_day = date.today().day if month == date.today().strftime("%Y-%m") else None

    expenses = fetch_all(
        "SELECT * FROM expenses WHERE record_date LIKE ? ORDER BY record_date DESC, id DESC",
        (month + "%",),
    )

    return render_template(
        "index.html",
        active="index",
        month=month,
        today=today,
        today_day=today_day,
        expenses=expenses,
        categories=categories,
        category_max=category_max,
        daily_chart=daily_series(month),
        **summary,
        **tasks,
    )


@app.route("/report")
def report():
    month = parse_month(request.args.get("month"))
    summary = month_summary(month)
    previous = month_summary(shift_month(month, -1))
    categories = category_breakdown(month)
    total_spending = summary["spending"] or 0

    breakdown = []
    for row in categories:
        share = round(row["total"] / total_spending * 100, 1) if total_spending else 0
        breakdown.append({**row, "share": share})

    top_expenses = fetch_all(
        """
        SELECT * FROM expenses
        WHERE kind = '支出' AND record_date LIKE ?
        ORDER BY amount DESC, id DESC
        LIMIT 5
        """,
        (month + "%",),
    )
    top_income = fetch_all(
        """
        SELECT category, ROUND(SUM(amount), 2) AS total
        FROM expenses
        WHERE kind = '收入' AND record_date LIKE ?
        GROUP BY category
        ORDER BY total DESC
        """,
        (month + "%",),
    )

    diff = {
        "income": round(summary["income"] - previous["income"], 2),
        "spending": round(summary["spending"] - previous["spending"], 2),
        "balance": round(summary["balance"] - previous["balance"], 2),
    }

    tasks = task_overview()
    task_stats = fetch_one(
        """
        SELECT
            COALESCE(SUM(CASE WHEN done = 1 THEN 1 END), 0) AS done,
            COALESCE(SUM(CASE WHEN done = 0 THEN 1 END), 0) AS pending
        FROM tasks
        """
    )

    return render_template(
        "report.html",
        active="report",
        month=month,
        previous=previous,
        diff=diff,
        breakdown=breakdown,
        top_expenses=top_expenses,
        top_income=top_income,
        advice=budget_advice(summary, categories),
        task_stats=task_stats,
        report_chart={
            "categories": {
                "labels": [row["category"] for row in breakdown],
                "totals": [row["total"] for row in breakdown],
            },
            "trend": six_month_trend(month),
        },
        **summary,
        **tasks,
    )


@app.route("/report/export")
def report_export():
    month = parse_month(request.args.get("month"))
    summary = month_summary(month)
    categories = category_breakdown(month)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["LifeFlow 月報表", summary["label"]])
    writer.writerow(["總收入", f"{summary['income']:.2f}"])
    writer.writerow(["總支出", f"{summary['spending']:.2f}"])
    writer.writerow(["結餘", f"{summary['balance']:.2f}"])
    writer.writerow(["每月預算", f"{summary['budget']:.2f}"])
    writer.writerow(["預算使用率", f"{summary['usage']:.1f}%"])
    writer.writerow([])
    writer.writerow(["支出分類", "金額", "筆數", "占比"])
    for row in categories:
        share = round(row["total"] / summary["spending"] * 100, 1) if summary["spending"] else 0
        writer.writerow([safe_cell(row["category"]), f"{row['total']:.2f}", row["count"], f"{share}%"])
    writer.writerow([])
    writer.writerow(["建議", budget_advice(summary, categories)])
    return export_response(buffer, f"lifeflow_report_{month}.csv")


@app.route("/add_expense", methods=["POST"])
def add_expense():
    month = request.form.get("month", "")
    try:
        kind = (request.form.get("kind") or "").strip()
        if kind not in KINDS:
            raise ValueError("收支類型不正確。")
        category = (request.form.get("category") or "").strip()
        if not category:
            raise ValueError("請輸入分類，例如：飲食。")
        if len(category) > 20:
            raise ValueError("分類名稱不可超過 20 字。")
        amount = parse_amount(request.form.get("amount"))
        note = (request.form.get("note") or "").strip()
        if len(note) > 100:
            raise ValueError("備註不可超過 100 字。")
        record_date = parse_date(request.form.get("record_date"), required=True)
        run_sql(
            "INSERT INTO expenses (kind, category, amount, note, record_date) VALUES (?, ?, ?, ?, ?)",
            (kind, category, amount, note, record_date),
        )
        flash(f"已新增{kind}：{category} {amount:,.2f} 元。", "success")
        month = record_date[:7]
    except ValueError as err:
        flash(str(err), "error")
    return back(month)


@app.route("/delete_expense/<int:expense_id>", methods=["POST"])
def delete_expense(expense_id):
    month = request.form.get("month", "")
    row = fetch_one("SELECT id FROM expenses WHERE id = ?", (expense_id,))
    if row is None:
        flash("找不到這筆紀錄，可能已被刪除。", "error")
    else:
        run_sql("DELETE FROM expenses WHERE id = ?", (expense_id,))
        flash("已刪除紀錄。", "success")
    return back(month)


@app.route("/set_budget", methods=["POST"])
def set_budget():
    month = request.form.get("month", "")
    try:
        budget = parse_budget(request.form.get("budget"))
        run_sql("UPDATE settings SET budget = ? WHERE id = 1", (budget,))
        flash(f"每月預算已設定為 {budget:,.2f} 元。", "success")
    except ValueError as err:
        flash(str(err), "error")
    return back(month)


@app.route("/add_task", methods=["POST"])
def add_task():
    month = request.form.get("month", "")
    try:
        title = (request.form.get("title") or "").strip()
        if not title:
            raise ValueError("請輸入待辦事項。")
        if len(title) > 60:
            raise ValueError("待辦事項不可超過 60 字。")
        due_date = parse_date(request.form.get("due_date"))
        run_sql("INSERT INTO tasks (title, due_date) VALUES (?, ?)", (title, due_date))
        flash(f"已新增待辦事項：{title}", "success")
    except ValueError as err:
        flash(str(err), "error")
    return back(month)


@app.route("/toggle_task/<int:task_id>", methods=["POST"])
def toggle_task(task_id):
    month = request.form.get("month", "")
    row = fetch_one("SELECT done FROM tasks WHERE id = ?", (task_id,))
    if row is None:
        flash("找不到這項待辦事項。", "error")
    else:
        run_sql("UPDATE tasks SET done = ? WHERE id = ?", (0 if row["done"] else 1, task_id))
        flash("已更新待辦狀態。", "success")
    return back(month)


@app.route("/delete_task/<int:task_id>", methods=["POST"])
def delete_task(task_id):
    month = request.form.get("month", "")
    row = fetch_one("SELECT id FROM tasks WHERE id = ?", (task_id,))
    if row is None:
        flash("找不到這項待辦事項。", "error")
    else:
        run_sql("DELETE FROM tasks WHERE id = ?", (task_id,))
        flash("已刪除待辦事項。", "success")
    return back(month)


@app.route("/export")
def export():
    month = request.args.get("month", "").strip()
    sql = "SELECT record_date, kind, category, amount, note FROM expenses"
    params = ()
    suffix = "all"
    if MONTH_RE.match(month):
        sql += " WHERE record_date LIKE ?"
        params = (month + "%",)
        suffix = month
    sql += " ORDER BY record_date ASC, id ASC"

    rows = fetch_all(sql, params)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["日期", "類型", "分類", "金額", "備註"])
    income = 0.0
    spending = 0.0
    for row in rows:
        if row["kind"] == "收入":
            income += row["amount"]
        else:
            spending += row["amount"]
        writer.writerow(
            [
                safe_cell(row["record_date"]),
                safe_cell(row["kind"]),
                safe_cell(row["category"]),
                f"{row['amount']:.2f}",
                safe_cell(row["note"]),
            ]
        )
    writer.writerow([])
    writer.writerow(["總收入", f"{income:.2f}"])
    writer.writerow(["總支出", f"{spending:.2f}"])
    writer.writerow(["結餘", f"{income - spending:.2f}"])
    return export_response(buffer, f"lifeflow_{suffix}.csv")


@app.route("/api/reminders")
def api_reminders():
    return jsonify(reminder_payload())


@app.cli.command("send-reminders")
def send_reminders_command():
    """寄送到期與預算提醒 Email。需設定 LIFEFLOW_SMTP_* 環境變數。"""
    host = os.environ.get("LIFEFLOW_SMTP_HOST")
    port = int(os.environ.get("LIFEFLOW_SMTP_PORT", "465"))
    username = os.environ.get("LIFEFLOW_SMTP_USER")
    password = os.environ.get("LIFEFLOW_SMTP_PASSWORD")
    sender = os.environ.get("LIFEFLOW_SMTP_FROM", username)
    recipient = os.environ.get("LIFEFLOW_SMTP_TO")

    if not all([host, username, password, sender, recipient]):
        print("尚未設定 SMTP：請設定 LIFEFLOW_SMTP_HOST / USER / PASSWORD / FROM / TO 環境變數。")
        return

    payload = reminder_payload()
    lines = [f"LifeFlow 生活提醒（{payload['month']}）", ""]
    if payload["over_budget"]:
        lines.append(
            f"- 預算超額：本月支出 {payload['spending']:,.2f} 元，"
            f"預算 {payload['budget']:,.2f} 元（使用率 {payload['usage']}%）。"
        )
    for task in payload["overdue"]:
        lines.append(f"- 已逾期：{task['title']}（到期日 {task['due_date']}）")
    for task in payload["due_today"]:
        lines.append(f"- 今日到期：{task['title']}")

    if len(lines) <= 2:
        print("目前沒有需要提醒的項目。")
        return

    message = EmailMessage()
    message["Subject"] = f"LifeFlow 提醒：{len(lines) - 2} 項待處理"
    message["From"] = sender
    message["To"] = recipient
    message.set_content("\n".join(lines))

    with smtplib.SMTP_SSL(host, port) as smtp:
        smtp.login(username, password)
        smtp.send_message(message)
    print(f"已寄出提醒至 {recipient}。")


init_db()


if __name__ == "__main__":
    app.run(debug=True)
