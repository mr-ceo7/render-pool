"""
Render REST API and CLI integration client.
Zero external dependencies - standard library urllib only.
"""

import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

RENDER_API_BASE = "https://api.render.com/v1"


class RenderAPIError(Exception):
    def __init__(self, message: str, status_code: Optional[int] = None, response_body: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class RenderClient:
    def __init__(self, api_key: str, api_base: str = RENDER_API_BASE):
        self.api_key = api_key.strip()
        self.api_base = api_base.rstrip("/")

    def _request(
        self,
        endpoint: str,
        method: str = "GET",
        data: Optional[Dict[str, Any]] = None,
        timeout: int = 30
    ) -> Any:
        url = f"{self.api_base}{endpoint}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "render-pool/1.0.0"
        }

        body = None
        if data is not None:
            body = json.dumps(data).encode("utf-8")

        req = urllib.request.Request(url, data=body, headers=headers, method=method)

        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                status = response.status
                if status == 204:
                    return None
                resp_text = response.read().decode("utf-8")
                if not resp_text.strip():
                    return None
                return json.loads(resp_text)
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            raise RenderAPIError(
                f"Render API error {e.code} on {method} {endpoint}: {err_body}",
                status_code=e.code,
                response_body=err_body
            ) from e
        except urllib.error.URLError as e:
            raise RenderAPIError(f"Network error connecting to Render API: {e.reason}") from e

    # -------------------------------------------------------------------------
    # Core API Endpoints
    # -------------------------------------------------------------------------

    def get_user(self) -> Dict[str, Any]:
        """Validate API key by querying /owners (the standard whoami endpoint)."""
        res = self._request("/owners?limit=1")
        if isinstance(res, list) and len(res) > 0:
            return res[0].get("owner", {})
        return {}

    def list_services(self, limit: int = 20) -> List[Dict[str, Any]]:
        """List services in workspace."""
        res = self._request(f"/services?limit={limit}")
        if isinstance(res, list):
            return [item.get("service", item) for item in res]
        return []

    def get_service(self, service_id: str) -> Dict[str, Any]:
        """Fetch single service details."""
        res = self._request(f"/services/{service_id}")
        return res.get("service", res) if isinstance(res, dict) else {}

    def suspend_service(self, service_id: str) -> bool:
        """
        Suspend a web service or background worker.
        Suspended services consume 0 instance hours on free tier.
        """
        try:
            self._request(f"/services/{service_id}/suspend", method="POST")
            return True
        except RenderAPIError as e:
            # If already suspended, Render returns 400 with specific message
            if e.status_code == 400 and "already suspended" in (e.response_body or "").lower():
                return True
            raise

    def resume_service(self, service_id: str) -> bool:
        """
        Resume a suspended web service or background worker.
        """
        try:
            self._request(f"/services/{service_id}/resume", method="POST")
            return True
        except RenderAPIError as e:
            if e.status_code == 400 and "not suspended" in (e.response_body or "").lower():
                return True
            raise

    def create_deploy(self, service_id: str, clear_cache: bool = False) -> Dict[str, Any]:
        """Trigger a deployment on a service."""
        payload = {
            "clearCache": "clear" if clear_cache else "do_not_clear"
        }
        res = self._request(f"/services/{service_id}/deploys", method="POST", data=payload)
        return res.get("deploy", res) if isinstance(res, dict) else {}

    def get_deploy(self, service_id: str, deploy_id: str) -> Dict[str, Any]:
        """Get deploy status."""
        res = self._request(f"/services/{service_id}/deploys/{deploy_id}")
        return res.get("deploy", res) if isinstance(res, dict) else {}

    def list_deploys(self, service_id: str, limit: int = 5) -> List[Dict[str, Any]]:
        """List recent deploys."""
        res = self._request(f"/services/{service_id}/deploys?limit={limit}")
        if isinstance(res, list):
            return [item.get("deploy", item) for item in res]
        return []

    # -------------------------------------------------------------------------
    # Health Probing
    # -------------------------------------------------------------------------

    @staticmethod
    def check_health(service_url: str, path: str = "/healthz", timeout: int = 10) -> Tuple[bool, int, str]:
        """
        Poll a service URL to check if it's healthy.
        Returns (is_healthy, status_code, message).
        """
        if not service_url.startswith("http"):
            service_url = f"https://{service_url}"
        clean_url = f"{service_url.rstrip('/')}{path}"

        req = urllib.request.Request(
            clean_url,
            headers={"User-Agent": "render-pool-healthcheck/1.0"},
            method="GET"
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                status = response.status
                is_healthy = 200 <= status < 300
                return is_healthy, status, "OK" if is_healthy else f"HTTP {status}"
        except urllib.error.HTTPError as e:
            return False, e.code, f"HTTP {e.code}"
        except Exception as e:
            return False, 0, str(e)

    # -------------------------------------------------------------------------
    # Render CLI Passthrough
    # -------------------------------------------------------------------------

    def run_cli(self, args: List[str], interactive: bool = False) -> subprocess.CompletedProcess:
        """
        Execute Render CLI command with this account's API key.
        """
        env = os.environ.copy()
        env["RENDER_API_KEY"] = self.api_key

        cmd = ["render"] + args
        if interactive:
            return subprocess.run(cmd, env=env)
        return subprocess.run(cmd, env=env, capture_output=True, text=True)
