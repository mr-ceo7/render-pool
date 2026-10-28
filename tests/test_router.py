import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from render_pool.router import CloudflareWorkerRouter, VercelEdgeRouter, RouterError


class TestRouters(unittest.TestCase):
    @patch("urllib.request.urlopen")
    def test_cloudflare_kv_update(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"success": True}).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        router = CloudflareWorkerRouter(
            account_id="cf_acc_123",
            api_token="cf_tok_abc",
            kv_namespace_id="kv_ns_456"
        )
        success = router.update_target("https://app-2.onrender.com")
        self.assertTrue(success)
        mock_urlopen.assert_called_once()

    @patch("urllib.request.urlopen")
    def test_vercel_edge_config_update(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"status": "ok"}).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        router = VercelEdgeRouter(
            token="v_tok_123",
            edge_config_id="ecfg_456"
        )
        success = router.update_target("https://app-2.onrender.com")
        self.assertTrue(success)
        mock_urlopen.assert_called_once()


if __name__ == "__main__":
    unittest.main()
