"""
Unified Ingress Router Client.
Manages zero-downtime routing handoffs across Cloudflare Workers and Vercel Edge.
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional, Tuple


class RouterError(Exception):
    pass


class CloudflareWorkerRouter:
    """
    Updates Cloudflare Worker routing target so api.yourdomain.com
    immediately proxies to the active Render service URL.
    """

    def __init__(
        self,
        account_id: str,
        api_token: str,
        kv_namespace_id: Optional[str] = None,
        script_name: Optional[str] = None,
    ):
        self.account_id = account_id.strip()
        self.api_token = api_token.strip()
        self.kv_namespace_id = kv_namespace_id.strip() if kv_namespace_id else None
        self.script_name = script_name.strip() if script_name else None

    def _cf_request(
        self,
        endpoint: str,
        method: str = "GET",
        data: Optional[Any] = None,
        content_type: str = "application/json"
    ) -> Dict[str, Any]:
        url = f"https://api.cloudflare.com/client/v4{endpoint}"
        headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": content_type,
        }

        body = None
        if data is not None:
            if isinstance(data, (dict, list)):
                body = json.dumps(data).encode("utf-8")
            elif isinstance(data, str):
                body = data.encode("utf-8")
            else:
                body = data

        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                resp_text = resp.read().decode("utf-8")
                return json.loads(resp_text) if resp_text else {}
        except urllib.error.HTTPError as e:
            err_msg = e.read().decode("utf-8", errors="replace")
            raise RouterError(f"Cloudflare API error {e.code}: {err_msg}") from e
        except Exception as e:
            raise RouterError(f"Cloudflare network error: {str(e)}") from e

    def update_target(self, target_url: str) -> bool:
        """
        Updates the routing target.
        Prefers KV if namespace ID configured; otherwise sets worker secret/var.
        """
        clean_url = target_url.strip()
        if not clean_url.startswith("http"):
            clean_url = f"https://{clean_url}"

        # 1. Update via KV
        if self.kv_namespace_id:
            ep = f"/accounts/{self.account_id}/storage/kv/namespaces/{self.kv_namespace_id}/values/ACTIVE_BACKEND_URL"
            res = self._cf_request(ep, method="PUT", data=clean_url, content_type="text/plain")
            if res.get("success", False):
                return True

        # 2. Update via Worker Secret / Variable
        if self.script_name:
            ep = f"/accounts/{self.account_id}/workers/scripts/{self.script_name}/secrets"
            payload = {
                "name": "ACTIVE_BACKEND_URL",
                "text": clean_url,
                "type": "secret_text"
            }
            res = self._cf_request(ep, method="PUT", data=payload)
            if res.get("success", False):
                return True

        raise RouterError("Neither kv_namespace_id nor script_name configured for Cloudflare router.")


class VercelEdgeRouter:
    """
    Updates Vercel Edge Config so Vercel edge middleware / rewrite rules
    route incoming frontend /api/* calls to the active Render backend.
    """

    def __init__(self, token: str, edge_config_id: str, team_id: Optional[str] = None):
        self.token = token.strip()
        self.edge_config_id = edge_config_id.strip()
        self.team_id = team_id.strip() if team_id else None

    def update_target(self, target_url: str) -> bool:
        clean_url = target_url.strip()
        if not clean_url.startswith("http"):
            clean_url = f"https://{clean_url}"

        url = f"https://api.vercel.com/v1/edge-config/{self.edge_config_id}/items"
        if self.team_id:
            url += f"?teamId={self.team_id}"

        payload = {
            "items": [
                {
                    "operation": "upsert",
                    "key": "active_render_url",
                    "value": clean_url
                }
            ]
        }

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json"
            },
            method="PATCH"
        )

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("status") == "ok" or data.get("ok", False)
        except Exception as e:
            raise RouterError(f"Failed to update Vercel Edge Config: {str(e)}") from e


def test_router_endpoint(public_router_url: str, timeout: int = 10) -> Tuple[bool, str]:
    """Test if the public router URL is responding."""
    if not public_router_url.startswith("http"):
        public_router_url = f"https://{public_router_url}"
    req = urllib.request.Request(public_router_url, headers={"User-Agent": "render-pool/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 400, f"HTTP {resp.status}"
    except Exception as e:
        return False, str(e)
