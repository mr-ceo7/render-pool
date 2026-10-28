import datetime
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from render_pool.pool import AccountPool


class TestAccountPool(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_render_pool_")
        self.pool = AccountPool(base_dir=self.test_dir)

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_initialization(self):
        data = self.pool.get_data()
        self.assertEqual(data["version"], "1.0.0")
        self.assertIsNone(data["active_account"])
        self.assertEqual(len(data["accounts"]), 0)

    def test_add_and_list_accounts(self):
        acc1 = self.pool.add_account(
            name="primary",
            api_key="rnd_testkey1",
            service_id="srv-111",
            service_url="https://app1.onrender.com",
            verify=False
        )
        self.assertEqual(acc1["name"], "primary")
        self.assertEqual(acc1["role"], "active")
        self.assertEqual(self.pool.get_active_account()["name"], "primary")

        acc2 = self.pool.add_account(
            name="secondary",
            api_key="rnd_testkey2",
            service_id="srv-222",
            service_url="https://app2.onrender.com",
            verify=False
        )
        self.assertEqual(acc2["name"], "secondary")
        self.assertEqual(acc2["role"], "standby")

        accounts = self.pool.list_accounts()
        self.assertEqual(len(accounts), 2)
        self.assertEqual(accounts[0]["name"], "primary")
        self.assertEqual(accounts[1]["name"], "secondary")

    def test_half_month_shift_math(self):
        # 2 accounts: days 1-15 -> primary, days 16-31 -> secondary
        self.pool.add_account("primary", "key1", "srv-1", verify=False)
        self.pool.add_account("secondary", "key2", "srv-2", verify=False)

        # Day 5
        dt_day5 = datetime.datetime(2026, 10, 5, 12, 0, tzinfo=datetime.timezone.utc)
        self.assertEqual(self.pool.compute_scheduled_account(dt_day5), "primary")

        # Day 15
        dt_day15 = datetime.datetime(2026, 10, 15, 23, 59, tzinfo=datetime.timezone.utc)
        self.assertEqual(self.pool.compute_scheduled_account(dt_day15), "primary")

        # Day 16
        dt_day16 = datetime.datetime(2026, 10, 16, 0, 1, tzinfo=datetime.timezone.utc)
        self.assertEqual(self.pool.compute_scheduled_account(dt_day16), "secondary")

        # Day 28
        dt_day28 = datetime.datetime(2026, 10, 28, 12, 0, tzinfo=datetime.timezone.utc)
        self.assertEqual(self.pool.compute_scheduled_account(dt_day28), "secondary")

    def test_remove_account(self):
        self.pool.add_account("primary", "key1", "srv-1", verify=False)
        self.pool.add_account("secondary", "key2", "srv-2", verify=False)

        self.assertTrue(self.pool.remove_account("primary"))
        accounts = self.pool.list_accounts()
        self.assertEqual(len(accounts), 1)
        self.assertEqual(accounts[0]["name"], "secondary")
        self.assertEqual(self.pool.get_active_account()["name"], "secondary")

    def test_router_configuration(self):
        self.pool.configure_router_cloudflare(
            account_id="cf_acc_123",
            api_token="cf_tok_abc",
            kv_namespace_id="kv_ns_456",
            public_url="https://api.myproject.com"
        )
        data = self.pool.get_data()
        self.assertEqual(data["router"]["type"], "cloudflare")
        self.assertEqual(data["router"]["cf_account_id"], "cf_acc_123")
        self.assertEqual(data["router"]["public_url"], "https://api.myproject.com")


if __name__ == "__main__":
    unittest.main()
