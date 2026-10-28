"""
Account Pool Manager for render-pool.
Handles isolated account credentials, atomic JSON metadata updates,
half-month shift rotation math, and zero-downtime handoffs.
"""

import calendar
import datetime
import fcntl
import json
import os
import shutil
import time
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Tuple

from render_pool.render_client import RenderClient, RenderAPIError
from render_pool.router import CloudflareWorkerRouter, VercelEdgeRouter, RouterError

DEFAULT_POOL_DIR = os.getenv(
    "RENDER_ACCOUNTS_DIR",
    os.path.join(os.path.expanduser("~"), ".render_accounts")
)


class AccountPool:
    def __init__(self, base_dir: str = DEFAULT_POOL_DIR):
        self.base_dir = os.path.abspath(base_dir)
        self.metadata_file = os.path.join(self.base_dir, "accounts.json")
        os.makedirs(self.base_dir, exist_ok=True)
        self._ensure_initialized()

    @contextmanager
    def _file_lock(self):
        lock_path = self.metadata_file + ".lock"
        try:
            lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except Exception:
                pass
            try:
                os.close(lock_fd)
            except Exception:
                pass

    def _ensure_initialized(self):
        with self._file_lock():
            if not os.path.exists(self.metadata_file):
                initial_data = {
                    "version": "1.0.0",
                    "active_account": None,
                    "shift_type": "half_month",
                    "last_switch_at": None,
                    "router": {
                        "type": "none",
                        "public_url": None,
                        "cf_account_id": None,
                        "cf_api_token": None,
                        "cf_kv_id": None,
                        "cf_script_name": None,
                        "vercel_token": None,
                        "vercel_edge_config_id": None,
                    },
                    "accounts": {}
                }
                self._write_metadata_unlocked(initial_data)

    def _read_metadata_unlocked(self) -> Dict[str, Any]:
        if not os.path.exists(self.metadata_file):
            return {"version": "1.0.0", "active_account": None, "accounts": {}, "router": {}}
        try:
            with open(self.metadata_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"version": "1.0.0", "active_account": None, "accounts": {}, "router": {}}

    def _write_metadata_unlocked(self, data: Dict[str, Any]):
        tmp_path = self.metadata_file + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        shutil.move(tmp_path, self.metadata_file)

    def get_data(self) -> Dict[str, Any]:
        with self._file_lock():
            return self._read_metadata_unlocked()

    def get_account(self, name: str) -> Optional[Dict[str, Any]]:
        with self._file_lock():
            data = self._read_metadata_unlocked()
            return data.get("accounts", {}).get(name)

    def list_accounts(self) -> List[Dict[str, Any]]:
        with self._file_lock():
            data = self._read_metadata_unlocked()
            accounts = list(data.get("accounts", {}).values())
            # Sort by shift_index or name
            accounts.sort(key=lambda x: (x.get("shift_index", 0), x.get("name", "")))
            return accounts

    def get_active_account(self) -> Optional[Dict[str, Any]]:
        with self._file_lock():
            data = self._read_metadata_unlocked()
            active_name = data.get("active_account")
            if not active_name:
                return None
            return data.get("accounts", {}).get(active_name)

    # -------------------------------------------------------------------------
    # Account Registration & Management
    # -------------------------------------------------------------------------

    def add_account(
        self,
        name: str,
        api_key: str,
        service_id: str,
        service_url: Optional[str] = None,
        service_name: Optional[str] = None,
        workspace_id: Optional[str] = None,
        verify: bool = True
    ) -> Dict[str, Any]:
        """Register a Render account and associate its service."""
        clean_name = name.strip()
        client = RenderClient(api_key)

        if verify:
            user = client.get_user()
            if not user and not client.list_services(limit=1):
                raise RenderAPIError("Failed to verify Render API key. Check token permissions.")
            if service_id:
                srv = client.get_service(service_id)
                if not service_url and srv:
                    service_url = srv.get("serviceDetails", {}).get("url") or srv.get("url")
                if not service_name and srv:
                    service_name = srv.get("name")

        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        with self._file_lock():
            data = self._read_metadata_unlocked()
            accounts = data.setdefault("accounts", {})

            existing_count = len(accounts)
            is_first = existing_count == 0

            account_entry = {
                "name": clean_name,
                "api_key": api_key.strip(),
                "service_id": service_id.strip(),
                "service_name": service_name or clean_name,
                "service_url": service_url.strip() if service_url else "",
                "workspace_id": workspace_id or "",
                "shift_index": existing_count,
                "role": "active" if is_first else "standby",
                "status": "unknown",
                "created_at": now_iso,
                "last_resumed_at": now_iso if is_first else None,
                "last_suspended_at": None,
                "estimated_hours_this_month": 0
            }

            accounts[clean_name] = account_entry
            if is_first:
                data["active_account"] = clean_name
                data["last_switch_at"] = now_iso

            self._write_metadata_unlocked(data)
            return account_entry

    def remove_account(self, name: str) -> bool:
        with self._file_lock():
            data = self._read_metadata_unlocked()
            accounts = data.get("accounts", {})
            if name not in accounts:
                return False

            del accounts[name]
            if data.get("active_account") == name:
                data["active_account"] = next(iter(accounts.keys())) if accounts else None
            # Reindex shift_index
            for idx, acc in enumerate(accounts.values()):
                acc["shift_index"] = idx

            self._write_metadata_unlocked(data)
            return True

    # -------------------------------------------------------------------------
    # Shift Scheduling Math
    # -------------------------------------------------------------------------

    def compute_scheduled_account(self, now: Optional[datetime.datetime] = None) -> Optional[str]:
        """
        Determines which account should be active right now based on half-month shifts.
        For 2 accounts: Days 1-15 -> Account 0, Days 16-End -> Account 1.
        For N accounts: Month is sliced evenly across N accounts.
        """
        accounts = self.list_accounts()
        if not accounts:
            return None
        if len(accounts) == 1:
            return accounts[0]["name"]

        if now is None:
            now = datetime.datetime.now(datetime.timezone.utc)

        current_day = now.day
        _, days_in_month = calendar.monthrange(now.year, now.month)

        num_accounts = len(accounts)
        if num_accounts == 2:
            midpoint = days_in_month // 2
            target_index = 0 if current_day <= midpoint else 1
        else:
            days_per_shift = days_in_month / num_accounts
            target_index = min(int((current_day - 1) / days_per_shift), num_accounts - 1)

        return accounts[target_index]["name"]

    def check_shift_status(self) -> Dict[str, Any]:
        """Returns current shift state and whether a switch is due."""
        now = datetime.datetime.now(datetime.timezone.utc)
        scheduled_name = self.compute_scheduled_account(now)
        active_acc = self.get_active_account()
        active_name = active_acc["name"] if active_acc else None

        needs_switch = scheduled_name is not None and scheduled_name != active_name

        return {
            "current_time_utc": now.isoformat(),
            "active_account": active_name,
            "scheduled_account": scheduled_name,
            "needs_switch": needs_switch,
            "accounts_count": len(self.list_accounts())
        }

    # -------------------------------------------------------------------------
    # Router Settings
    # -------------------------------------------------------------------------

    def configure_router_cloudflare(
        self,
        account_id: str,
        api_token: str,
        kv_namespace_id: Optional[str] = None,
        script_name: Optional[str] = None,
        public_url: Optional[str] = None
    ):
        with self._file_lock():
            data = self._read_metadata_unlocked()
            router = data.setdefault("router", {})
            router["type"] = "cloudflare"
            router["cf_account_id"] = account_id.strip()
            router["cf_api_token"] = api_token.strip()
            router["cf_kv_id"] = kv_namespace_id.strip() if kv_namespace_id else None
            router["cf_script_name"] = script_name.strip() if script_name else None
            if public_url:
                router["public_url"] = public_url.strip()
            self._write_metadata_unlocked(data)

    def configure_router_vercel(
        self,
        token: str,
        edge_config_id: str,
        team_id: Optional[str] = None,
        public_url: Optional[str] = None
    ):
        with self._file_lock():
            data = self._read_metadata_unlocked()
            router = data.setdefault("router", {})
            router["type"] = "vercel"
            router["vercel_token"] = token.strip()
            router["vercel_edge_config_id"] = edge_config_id.strip()
            router["vercel_team_id"] = team_id.strip() if team_id else None
            if public_url:
                router["public_url"] = public_url.strip()
            self._write_metadata_unlocked(data)

    def _sync_router_to_active(self, target_url: str) -> Tuple[bool, str]:
        """Pushes target URL to configured router."""
        data = self.get_data()
        router = data.get("router", {})
        rtype = router.get("type", "none")

        if rtype == "cloudflare":
            cf = CloudflareWorkerRouter(
                account_id=router.get("cf_account_id", ""),
                api_token=router.get("cf_api_token", ""),
                kv_namespace_id=router.get("cf_kv_id"),
                script_name=router.get("cf_script_name")
            )
            try:
                cf.update_target(target_url)
                return True, "Cloudflare Worker route updated."
            except RouterError as e:
                return False, f"Cloudflare update failed: {str(e)}"

        elif rtype == "vercel":
            vc = VercelEdgeRouter(
                token=router.get("vercel_token", ""),
                edge_config_id=router.get("vercel_edge_config_id", ""),
                team_id=router.get("vercel_team_id")
            )
            try:
                vc.update_target(target_url)
                return True, "Vercel Edge Config route updated."
            except RouterError as e:
                return False, f"Vercel Edge update failed: {str(e)}"

        return True, "No external router configured (direct URLs used)."

    # -------------------------------------------------------------------------
    # Zero-Downtime Handoff (The Switch Protocol)
    # -------------------------------------------------------------------------

    def switch_to(
        self,
        target_name: Optional[str] = None,
        health_timeout: int = 75,
        force: bool = False,
        log_callback=None
    ) -> Tuple[bool, str]:
        """
        Executes safe handoff from active account to target_name.
        1. Resume target service.
        2. Poll healthcheck.
        3. Update router.
        4. Suspend old service.
        5. Update accounts.json.
        """
        def log(msg: str):
            if log_callback:
                log_callback(msg)

        accounts = self.list_accounts()
        if not accounts:
            return False, "No accounts in pool."

        active = self.get_active_account()
        if not target_name:
            if not active:
                target_name = accounts[0]["name"]
            else:
                curr_idx = active.get("shift_index", 0)
                next_idx = (curr_idx + 1) % len(accounts)
                target_name = accounts[next_idx]["name"]

        if active and active["name"] == target_name and not force:
            return True, f"Account '{target_name}' is already active."

        target_acc = self.get_account(target_name)
        if not target_acc:
            return False, f"Target account '{target_name}' not found."

        target_client = RenderClient(target_acc["api_key"])
        target_srv_id = target_acc["service_id"]
        target_url = target_acc.get("service_url")

        # Step 1: Resume target service
        log(f"Resuming standby service on '{target_name}' (ID: {target_srv_id})...")
        try:
            target_client.resume_service(target_srv_id)
        except RenderAPIError as e:
            if not force:
                return False, f"Failed to resume target service: {str(e)}"
            log(f"Warning: resume returned error ({str(e)}), continuing due to --force")

        # Step 2: Healthcheck polling
        if target_url:
            log(f"Waiting for '{target_name}' healthcheck at {target_url}/healthz (timeout {health_timeout}s)...")
            start_time = time.time()
            healthy = False
            last_err = ""
            while time.time() - start_time < health_timeout:
                is_ok, code, msg = RenderClient.check_health(target_url, path="/healthz", timeout=5)
                if is_ok:
                    healthy = True
                    log(f"✓ Health check passed (HTTP {code}) in {int(time.time() - start_time)}s.")
                    break
                last_err = msg
                time.sleep(3)

            if not healthy and not force:
                return False, f"Health check failed on target service after {health_timeout}s: {last_err}"

        # Step 3: Update router
        if target_url:
            log(f"Updating Ingress Router to point to {target_url}...")
            r_ok, r_msg = self._sync_router_to_active(target_url)
            log(f"Router status: {r_msg}")

        # Step 4: Suspend old active service
        if active and active["name"] != target_name and active.get("service_id"):
            old_client = RenderClient(active["api_key"])
            old_srv_id = active["service_id"]
            log(f"Suspending previous service on '{active['name']}' (ID: {old_srv_id}) to preserve quota...")
            try:
                old_client.suspend_service(old_srv_id)
                log(f"✓ Service '{active['name']}' suspended (0 billable hours).")
            except Exception as e:
                log(f"Warning: Failed to suspend old service: {str(e)}")

        # Step 5: Update metadata
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self._file_lock():
            data = self._read_metadata_unlocked()
            accs = data.get("accounts", {})

            if active and active["name"] in accs:
                accs[active["name"]]["role"] = "standby"
                accs[active["name"]]["status"] = "suspended"
                accs[active["name"]]["last_suspended_at"] = now_iso

            if target_name in accs:
                accs[target_name]["role"] = "active"
                accs[target_name]["status"] = "running"
                accs[target_name]["last_resumed_at"] = now_iso

            data["active_account"] = target_name
            data["last_switch_at"] = now_iso
            self._write_metadata_unlocked(data)

        return True, f"Successfully handed off active shift to '{target_name}'."
