import os
import sys
import tempfile
import unittest
from datetime import date, timedelta
from unittest import mock

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

TMP_DIR = tempfile.mkdtemp(prefix="lifeflow-test-")
os.environ["LIFEFLOW_DB"] = os.path.join(TMP_DIR, "test.db")
os.environ["LIFEFLOW_SECRET"] = "test-secret"

import app as lifeflow  # noqa: E402

lifeflow.app.config.update(TESTING=True)

MONTH = "2026-10"


class LifeFlowTestCase(unittest.TestCase):
    def setUp(self):
        conn = lifeflow.get_db()
        with conn:
            conn.execute("DELETE FROM expenses")
            conn.execute("DELETE FROM tasks")
            conn.execute("UPDATE settings SET budget = 0 WHERE id = 1")
        conn.close()
        self.client = lifeflow.app.test_client()

    def add_expense(self, kind, category, amount, record_date, note=""):
        return self.client.post(
            "/add_expense",
            data={
                "kind": kind,
                "category": category,
                "amount": str(amount),
                "note": note,
                "record_date": record_date,
                "month": MONTH,
            },
            follow_redirects=True,
        )

    def add_task(self, title, due_date=""):
        return self.client.post(
            "/add_task",
            data={"title": title, "due_date": due_date, "month": MONTH},
            follow_redirects=True,
        )

    def count(self, table):
        row = lifeflow.fetch_one(f"SELECT COUNT(*) AS total FROM {table}")
        return row["total"]

    def test_index_loads(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("LifeFlow".encode(), response.data)
        self.assertIn("每日支出趨勢".encode(), response.data)

    def test_add_and_delete_record(self):
        response = self.add_expense("收入", "生活費", 15000, "2026-10-01", "本月")
        self.assertIn("已新增收入".encode(), response.data)
        self.assertIn("15,000.00".encode(), response.data)
        self.assertEqual(self.count("expenses"), 1)

        response = self.add_expense("支出", "飲食", 8000, "2026-10-05")
        self.assertIn("已新增支出".encode(), response.data)
        self.assertEqual(self.count("expenses"), 2)

        row = lifeflow.fetch_one("SELECT id FROM expenses WHERE kind = '支出'")
        response = self.client.post(
            f"/delete_expense/{row['id']}", data={"month": MONTH}, follow_redirects=True
        )
        self.assertIn("已刪除紀錄".encode(), response.data)
        self.assertEqual(self.count("expenses"), 1)

    def test_budget_usage_and_warning(self):
        self.add_expense("收入", "生活費", 15000, "2026-10-01")
        self.add_expense("支出", "飲食", 8000, "2026-10-05")

        response = self.client.post(
            "/set_budget", data={"budget": "10000", "month": MONTH}, follow_redirects=True
        )
        self.assertIn("10,000.00".encode(), response.data)
        self.assertIn("80.0%".encode(), response.data)
        self.assertNotIn("預算超額提醒".encode(), response.data)

        response = self.add_expense("支出", "購物", 3000, "2026-10-20")
        self.assertIn("預算超額提醒".encode(), response.data)

    def test_invalid_inputs_are_rejected(self):
        cases = [
            ({"kind": "支出", "category": "飲食", "amount": "-50", "record_date": "2026-10-20"}, "金額必須大於 0"),
            ({"kind": "支出", "category": "  ", "amount": "100", "record_date": "2026-10-20"}, "請輸入分類"),
            ({"kind": "支出", "category": "飲食", "amount": "abc", "record_date": "2026-10-20"}, "金額必須是數字"),
            ({"kind": "支出", "category": "飲食", "amount": "100", "record_date": "2026-02-30"}, "日期不存在"),
            ({"kind": "支出", "category": "飲食", "amount": "100", "record_date": "2026/10/20"}, "日期格式"),
            ({"kind": "其他", "category": "飲食", "amount": "100", "record_date": "2026-10-20"}, "收支類型不正確"),
        ]
        for payload, expected in cases:
            with self.subTest(payload=payload):
                response = self.client.post(
                    "/add_expense", data={**payload, "month": MONTH}, follow_redirects=True
                )
                self.assertIn(expected.encode(), response.data)
        self.assertEqual(self.count("expenses"), 0)

    def test_task_flow_and_reminders(self):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        response = self.add_task("繳交網路費", yesterday)
        self.assertIn("繳交網路費".encode(), response.data)
        self.assertIn("逾期".encode(), response.data)
        self.assertIn("到期提醒".encode(), response.data)

        row = lifeflow.fetch_one("SELECT id FROM tasks")
        response = self.client.post(
            f"/toggle_task/{row['id']}", data={"month": MONTH}, follow_redirects=True
        )
        self.assertIn("已更新待辦狀態".encode(), response.data)
        self.assertEqual(lifeflow.fetch_one("SELECT done FROM tasks")["done"], 1)

        response = self.client.post(
            f"/delete_task/{row['id']}", data={"month": MONTH}, follow_redirects=True
        )
        self.assertIn("已刪除待辦事項".encode(), response.data)
        self.assertEqual(self.count("tasks"), 0)

    def test_month_filter(self):
        self.add_expense("支出", "飲食", 100, "2026-09-15")
        self.add_expense("支出", "交通", 200, "2026-10-15")

        response = self.client.get("/?month=2026-09")
        self.assertIn("100.00".encode(), response.data)
        self.assertNotIn("200.00".encode(), response.data)

        response = self.client.get("/?month=2026-08")
        self.assertIn("這個月份還沒有收支紀錄".encode(), response.data)

    def test_export_csv(self):
        self.add_expense("收入", "生活費", 15000, "2026-10-01")
        self.add_expense("支出", "飲食", 300, "2026-10-02", "=1+1")

        response = self.client.get(f"/export?month={MONTH}")
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response.headers["Content-Disposition"])
        text = response.data.decode("utf-8-sig")
        self.assertIn("日期,類型,分類,金額,備註", text)
        self.assertIn("生活費", text)
        self.assertIn("總支出,300.00", text)
        self.assertIn("'=1+1", text)

    def test_report_page_and_chart_data(self):
        self.add_expense("收入", "生活費", 15000, "2026-10-01")
        self.add_expense("支出", "飲食", 8000, "2026-10-05")
        self.client.post("/set_budget", data={"budget": "10000", "month": MONTH})

        response = self.client.get(f"/report?month={MONTH}")
        self.assertEqual(response.status_code, 200)
        self.assertIn("支出分類佔比".encode(), response.data)
        self.assertIn("近六個月收支".encode(), response.data)
        self.assertIn("window.LIFEFLOW_REPORT".encode(), response.data)
        self.assertIn("前五大支出".encode(), response.data)

    def test_report_export_csv(self):
        self.add_expense("支出", "飲食", 500, "2026-10-05")
        response = self.client.get(f"/report/export?month={MONTH}")
        self.assertEqual(response.status_code, 200)
        text = response.data.decode("utf-8-sig")
        self.assertIn("LifeFlow 月報表", text)
        self.assertIn("支出分類,金額,筆數,占比", text)
        self.assertIn("飲食,500.00", text)

    def test_api_reminders(self):
        current = date.today().strftime("%Y-%m")
        self.add_expense("支出", "飲食", 500, f"{current}-01")
        self.client.post("/set_budget", data={"budget": "100", "month": current})
        due = date.today().isoformat()
        self.add_task("今天要完成的事", due)

        response = self.client.get("/api/reminders")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["over_budget"])
        self.assertEqual(len(data["due_today"]), 1)
        self.assertEqual(data["due_today"][0]["title"], "今天要完成的事")

    def test_send_reminders_without_smtp_settings(self):
        empty_env = {
            "LIFEFLOW_SMTP_HOST": "",
            "LIFEFLOW_SMTP_USER": "",
            "LIFEFLOW_SMTP_PASSWORD": "",
            "LIFEFLOW_SMTP_FROM": "",
            "LIFEFLOW_SMTP_TO": "",
        }
        with mock.patch.dict(os.environ, empty_env):
            result = lifeflow.app.test_cli_runner().invoke(args=["send-reminders"])
        self.assertIn("尚未設定 SMTP", result.output)


if __name__ == "__main__":
    unittest.main(verbosity=2)
