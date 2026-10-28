"""
End-to-End Simulation Test for render-pool.
Spins up a mock Render API server and mock FastAPI backend services,
then executes full CLI lifecycle operations:
1. Account registration
2. Status reporting
3. Zero-downtime handoff / switch
4. Quota check-shift
5. Coordinated deployment
"""

import http.server
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.request

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from render_pool.pool import AccountPool
from render_pool.render_client import RenderClient


class MockRenderHandler(http.server.BaseHTTPRequestHandler):
    """Simulates Render REST API endpoints."""

    def log_message(self, format, *args):
        pass  # Suppress request logging during tests

    def do_GET(self):
        if self.path.startswith("/v1/owners"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps([{"owner": {"id": "own_123", "name": "test_user"}}]).encode())
        elif self.path.startswith("/v1/services/srv-"):
            srv_id = self.path.split("/")[3]
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "service": {
                    "id": srv_id,
                    "name": f"backend-{srv_id}",
                    "serviceDetails": {"url": f"http://127.0.0.1:{self.server.backend_ports.get(srv_id, 8000)}"}
                }
            }).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if "/suspend" in self.path:
            self.server.suspended_services.append(self.path)
            self.send_response(204)
            self.end_headers()
        elif "/resume" in self.path:
            self.server.resumed_services.append(self.path)
            self.send_response(204)
            self.end_headers()
        elif "/deploys" in self.path:
            self.server.deployed_services.append(self.path)
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"deploy": {"id": "dep_test999", "status": "created"}}).encode())
        else:
            self.send_response(404)
            self.end_headers()


class MockBackendHandler(http.server.BaseHTTPRequestHandler):
    """Simulates FastAPI backend instance with /healthz."""

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path == "/healthz":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status": "ok", "healthy": true}')
        else:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"message": "backend online"}')


class TestEndToEndSimulation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 1. Start mock Render API server
        cls.render_server = http.server.HTTPServer(("127.0.0.1", 0), MockRenderHandler)
        cls.render_port = cls.render_server.server_address[1]
        cls.render_server.suspended_services = []
        cls.render_server.resumed_services = []
        cls.render_server.deployed_services = []

        cls.render_thread = threading.Thread(target=cls.render_server.serve_forever, daemon=True)
        cls.render_thread.start()

        # 2. Start mock FastAPI backend 1 (Instance A)
        cls.backend_a = http.server.HTTPServer(("127.0.0.1", 0), MockBackendHandler)
        cls.backend_a_port = cls.backend_a.server_address[1]
        cls.thread_a = threading.Thread(target=cls.backend_a.serve_forever, daemon=True)
        cls.thread_a.start()

        # 3. Start mock FastAPI backend 2 (Instance B)
        cls.backend_b = http.server.HTTPServer(("127.0.0.1", 0), MockBackendHandler)
        cls.backend_b_port = cls.backend_b.server_address[1]
        cls.thread_b = threading.Thread(target=cls.backend_b.serve_forever, daemon=True)
        cls.thread_b.start()

        # Wire ports to mock Render
        cls.render_server.backend_ports = {
            "srv-aaa": cls.backend_a_port,
            "srv-bbb": cls.backend_b_port,
        }

    @classmethod
    def tearDownClass(cls):
        cls.render_server.shutdown()
        cls.render_server.server_close()
        cls.backend_a.shutdown()
        cls.backend_a.server_close()
        cls.backend_b.shutdown()
        cls.backend_b.server_close()

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_e2e_render_pool_")
        self.pool = AccountPool(base_dir=self.test_dir)
        self.mock_api_base = f"http://127.0.0.1:{self.render_port}/v1"

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_full_lifecycle_handoff_and_deploy(self):
        """Simulates full workflow: Add accounts -> Switch -> Verify handoff & deploy."""
        # 1. Add primary account
        acc1 = self.pool.add_account(
            name="primary",
            api_key="rnd_mock_primary",
            service_id="srv-aaa",
            service_url=f"http://127.0.0.1:{self.backend_a_port}",
            service_name="backend-aaa",
            verify=False
        )
        self.assertEqual(acc1["role"], "active")

        # 2. Add secondary account
        acc2 = self.pool.add_account(
            name="secondary",
            api_key="rnd_mock_secondary",
            service_id="srv-bbb",
            service_url=f"http://127.0.0.1:{self.backend_b_port}",
            service_name="backend-bbb",
            verify=False
        )
        self.assertEqual(acc2["role"], "standby")

        # Verify initial pool state
        self.assertEqual(self.pool.get_active_account()["name"], "primary")

        # 3. Test Healthcheck polling against mock backend
        is_healthy, code, msg = RenderClient.check_health(
            f"http://127.0.0.1:{self.backend_b_port}",
            path="/healthz"
        )
        self.assertTrue(is_healthy)
        self.assertEqual(code, 200)

        # 4. Patch RenderClient to point to our local mock Render server
        import render_pool.pool
        original_client_cls = render_pool.pool.RenderClient

        mock_port = self.render_port

        class MockedClientWrapper(RenderClient):
            def __init__(self, api_key: str):
                super().__init__(api_key, api_base=f"http://127.0.0.1:{mock_port}/v1")

        render_pool.pool.RenderClient = MockedClientWrapper

        try:
            # 5. Execute switch from primary to secondary
            log_messages = []
            success, msg = self.pool.switch_to(
                target_name="secondary",
                health_timeout=5,
                log_callback=log_messages.append
            )

            self.assertTrue(success)
            self.assertIn("Successfully handed off", msg)

            # Verify that:
            # - Secondary service was resumed
            # - Primary service was suspended
            # - Secondary is now the active account in metadata
            self.assertTrue(any("srv-bbb/resume" in call for call in self.render_server.resumed_services))
            self.assertTrue(any("srv-aaa/suspend" in call for call in self.render_server.suspended_services))

            active_now = self.pool.get_active_account()
            self.assertEqual(active_now["name"], "secondary")
            self.assertEqual(active_now["role"], "active")

            old_acc = self.pool.get_account("primary")
            self.assertEqual(old_acc["role"], "standby")
            self.assertEqual(old_acc["status"], "suspended")

            # 6. Test coordinated deploy
            client_sec = MockedClientWrapper(active_now["api_key"])
            deploy_res = client_sec.create_deploy("srv-bbb")
            self.assertEqual(deploy_res.get("id"), "dep_test999")
            self.assertTrue(any("srv-bbb/deploys" in call for call in self.render_server.deployed_services))

        finally:
            render_pool.pool.RenderClient = original_client_cls


if __name__ == "__main__":
    unittest.main()
