"""
render-pool CLI: Unified command-line interface.
"""

import argparse
import datetime
import getpass
import os
import subprocess
import sys
import time
from typing import List, Optional

from render_pool.pool import AccountPool
from render_pool.render_client import RenderClient, RenderAPIError
from render_pool.router import test_router_endpoint
from render_pool.templates import scaffold_project

VERSION = "1.0.0"


def format_table(headers: List[str], rows: List[List[str]]) -> str:
    """Renders a clean ASCII table."""
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, val in enumerate(row):
            if i < len(col_widths):
                col_widths[i] = max(col_widths[i], len(str(val)))

    sep = "+-" + "-+-".join("-" * w for w in col_widths) + "-+"
    header_str = "| " + " | ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers)) + " |"

    lines = [sep, header_str, sep]
    for row in rows:
        line_str = "| " + " | ".join(str(val).ljust(col_widths[i]) for i, val in enumerate(row)) + " |"
        lines.append(line_str)
    lines.append(sep)
    return "\n".join(lines)


def cmd_status(pool: AccountPool, args: argparse.Namespace):
    """Displays current pool status, active shifts, and router info."""
    data = pool.get_data()
    accounts = pool.list_accounts()
    active_name = data.get("active_account")
    shift_info = pool.check_shift_status()
    router = data.get("router", {})

    print(f"\n=======================================================")
    print(f" render-pool status (v{VERSION})")
    print(f"=======================================================")
    print(f"Accounts in Pool:   {len(accounts)}")
    print(f"Active Account:     {active_name or 'None (Run: render-pool account add)'}")
    print(f"Current Shift Date: {datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d')} (UTC)")
    print(f"Scheduled Active:   {shift_info.get('scheduled_account')}")
    print(f"Shift Status:       {'⚠️  SWITCH DUE (Run: render-pool switch)' if shift_info.get('needs_switch') else '✓ On Schedule'}")

    # Router info
    rtype = router.get("type", "none")
    pub_url = router.get("public_url") or "None"
    print(f"Ingress Router:     {rtype.upper()} ({pub_url})")

    if not accounts:
        print("\nNo Render accounts added yet. Add your first account with:")
        print("  render-pool account add primary\n")
        return

    print("\n[ Accounts Pool ]")
    headers = ["Name", "Role", "Service ID", "Service URL", "Status"]
    rows = []
    for acc in accounts:
        name = acc["name"]
        role = acc.get("role", "standby").upper()
        if name == active_name:
            role = f"★ {role}"
        srv_id = acc.get("service_id", "-")
        srv_url = acc.get("service_url", "-")
        # Abbreviate long URL
        if len(srv_url) > 35:
            srv_url = srv_url[:32] + "..."
        status = acc.get("status", "unknown")
        rows.append([name, role, srv_id, srv_url, status])

    print(format_table(headers, rows))
    print("")


def cmd_account_add(pool: AccountPool, args: argparse.Namespace):
    """Interactively or via flags registers a Render account."""
    name = args.name.strip()
    api_key = args.api_key
    service_id = args.service_id
    service_url = args.service_url

    if not api_key:
        print(f"\nAdding Render Account: {name}")
        print("You can create an API key in Render Dashboard -> Account Settings -> API Keys")
        api_key = getpass.getpass("Enter Render API Key (rnd_...): ").strip()
        if not api_key:
            print("Error: API Key is required.", file=sys.stderr)
            sys.exit(1)

    print(f"Validating API Key with Render API...")
    client = RenderClient(api_key)
    try:
        user = client.get_user()
        print(f"✓ Authenticated successfully with Render (Owner: {user.get('name', 'ok')})")
    except Exception as e:
        print(f"Warning: Could not fetch user details: {e}")

    if not service_id:
        print("\nFetching existing services in workspace...")
        try:
            services = client.list_services(limit=10)
            if services:
                print("\nAvailable Services:")
                for idx, s in enumerate(services):
                    s_id = s.get("id")
                    s_name = s.get("name")
                    s_type = s.get("type")
                    print(f"  [{idx + 1}] {s_name} ({s_type}) - ID: {s_id}")
                choice = input("\nSelect service number (or paste custom Service ID): ").strip()
                if choice.isdigit() and 1 <= int(choice) <= len(services):
                    selected = services[int(choice) - 1]
                    service_id = selected.get("id")
                    service_url = selected.get("serviceDetails", {}).get("url") or selected.get("url")
                else:
                    service_id = choice
            else:
                service_id = input("Enter Render Service ID (srv-...): ").strip()
        except Exception as e:
            service_id = input(f"Enter Render Service ID (srv-...): ").strip()

    if not service_url and service_id:
        try:
            srv = client.get_service(service_id)
            service_url = srv.get("serviceDetails", {}).get("url") or srv.get("url")
        except Exception:
            pass

    if not service_url:
        service_url = input("Enter Service Public URL (e.g. https://my-app.onrender.com): ").strip()

    try:
        acc = pool.add_account(
            name=name,
            api_key=api_key,
            service_id=service_id,
            service_url=service_url,
            verify=False
        )
        print(f"\n✓ Account '{name}' successfully registered in pool!")
        print(f"  Assigned Service: {acc.get('service_id')} ({acc.get('service_url')})")
        print(f"  Role: {acc.get('role')}")
    except Exception as e:
        print(f"Error adding account: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_account_remove(pool: AccountPool, args: argparse.Namespace):
    """Removes an account from pool."""
    name = args.name.strip()
    if pool.remove_account(name):
        print(f"✓ Account '{name}' removed from pool.")
    else:
        print(f"Error: Account '{name}' not found.", file=sys.stderr)
        sys.exit(1)


def cmd_switch(pool: AccountPool, args: argparse.Namespace):
    """Executes the safe handoff protocol."""
    target_name = args.target if args.target else None
    print(f"\n=== Initiating render-pool Safe Handoff ===")
    if target_name:
        print(f"Target Account: {target_name}")
    else:
        print("Auto-selecting next account in shift rotation...")

    def logger(msg: str):
        print(f"  {msg}")

    success, msg = pool.switch_to(
        target_name=target_name,
        health_timeout=args.timeout,
        force=args.force,
        log_callback=logger
    )

    if success:
        print(f"\n✓ {msg}")
    else:
        print(f"\n❌ Hand-off failed: {msg}", file=sys.stderr)
        sys.exit(1)


def cmd_deploy(pool: AccountPool, args: argparse.Namespace):
    """Deploys code across accounts in pool."""
    accounts = pool.list_accounts()
    if not accounts:
        print("Error: No accounts in pool.", file=sys.stderr)
        sys.exit(1)

    targets = []
    if args.active_only:
        active = pool.get_active_account()
        if not active:
            print("Error: No active account found.", file=sys.stderr)
            sys.exit(1)
        targets = [active]
    else:
        # Default: deploy to all so paired standby instance has identical code ready
        targets = accounts

    print(f"\n=== Triggering Deploy on {len(targets)} instance(s) ===")
    for acc in targets:
        name = acc["name"]
        srv_id = acc.get("service_id")
        if not srv_id:
            print(f"Skipping '{name}': No service ID registered.")
            continue

        print(f"\nTriggering deploy on '{name}' (Service ID: {srv_id})...")
        client = RenderClient(acc["api_key"])
        try:
            dep = client.create_deploy(srv_id, clear_cache=args.clear_cache)
            dep_id = dep.get("id", "ok")
            print(f"✓ Deploy triggered on '{name}'. Deploy ID: {dep_id}")
        except Exception as e:
            print(f"❌ Failed to trigger deploy on '{name}': {e}")


def cmd_check_shift(pool: AccountPool, args: argparse.Namespace):
    """Checks shift and optionally executes auto-switch."""
    status = pool.check_shift_status()
    needs_switch = status["needs_switch"]
    scheduled = status["scheduled_account"]
    active = status["active_account"]

    if needs_switch:
        print(f"Shift change detected! Active: {active} -> Scheduled: {scheduled}")
        if args.auto_switch:
            print("Auto-switch enabled. Running switch protocol...")
            success, msg = pool.switch_to(target_name=scheduled, log_callback=lambda m: print(f"  {m}"))
            if success:
                print(f"✓ {msg}")
                sys.exit(0)
            else:
                print(f"❌ Auto-switch failed: {msg}", file=sys.stderr)
                sys.exit(1)
        else:
            print("Run 'render-pool switch' to rotate shifts.")
            sys.exit(2)
    else:
        print(f"Shift is up to date. Active: {active} (Scheduled: {scheduled})")


def cmd_init(args: argparse.Namespace):
    """Scaffolds Firebase sync and Dockerfile into target directory."""
    target_dir = args.directory or "."
    print(f"\nScaffolding render-pool templates into: {target_dir}")
    created = scaffold_project(target_dir, include_router=True)
    for c in created:
        print(f"  ✓ Created: {c}")

    print("\nNext steps:")
    print("1. Set your FIREBASE_STORAGE_BUCKET in your environment or Render dashboard.")
    print("2. If using Cloudflare Worker router, deploy the files in ./render_pool_router/")
    print("3. Deploy to Render: render-pool deploy\n")


def cmd_router_cf(pool: AccountPool, args: argparse.Namespace):
    """Configures Cloudflare Worker integration."""
    account_id = args.account_id or input("Cloudflare Account ID: ").strip()
    api_token = args.api_token or getpass.getpass("Cloudflare API Token: ").strip()
    script_name = args.script_name or input("Worker Script Name (optional, press Enter to skip): ").strip()
    kv_id = args.kv_id or input("KV Namespace ID (optional, press Enter to skip): ").strip()
    public_url = args.public_url or input("Public API Domain (e.g. https://api.example.com): ").strip()

    pool.configure_router_cloudflare(
        account_id=account_id,
        api_token=api_token,
        kv_namespace_id=kv_id or None,
        script_name=script_name or None,
        public_url=public_url or None
    )
    print("✓ Cloudflare Worker router settings saved.")


def cmd_router_vercel(pool: AccountPool, args: argparse.Namespace):
    """Configures Vercel Edge integration."""
    token = args.token or getpass.getpass("Vercel API Token: ").strip()
    edge_config_id = args.edge_config_id or input("Edge Config ID (ecfg_...): ").strip()
    team_id = args.team_id or input("Team ID (optional, press Enter to skip): ").strip()
    public_url = args.public_url or input("Frontend Public URL: ").strip()

    pool.configure_router_vercel(
        token=token,
        edge_config_id=edge_config_id,
        team_id=team_id or None,
        public_url=public_url or None
    )
    print("✓ Vercel Edge router settings saved.")


def cmd_ssh(pool: AccountPool, args: argparse.Namespace):
    """Opens SSH session into active (or specified) Render service."""
    account_name = args.account
    if not account_name:
        active = pool.get_active_account()
        if not active:
            print("Error: No active account found.", file=sys.stderr)
            sys.exit(1)
        acc = active
    else:
        acc = pool.get_account(account_name)
        if not acc:
            print(f"Error: Account '{account_name}' not found.", file=sys.stderr)
            sys.exit(1)

    srv_id = acc.get("service_id")
    if not srv_id:
        print(f"Error: No service ID registered for '{acc['name']}'.", file=sys.stderr)
        sys.exit(1)

    client = RenderClient(acc["api_key"])
    print(f"Connecting to {acc['name']} (Service: {srv_id}) via Render SSH...")
    res = client.run_cli(["ssh", srv_id], interactive=True)
    sys.exit(res.returncode)


def cmd_logs(pool: AccountPool, args: argparse.Namespace):
    """Streams live logs from active (or specified) Render service."""
    account_name = args.account
    if not account_name:
        active = pool.get_active_account()
        if not active:
            print("Error: No active account found.", file=sys.stderr)
            sys.exit(1)
        acc = active
    else:
        acc = pool.get_account(account_name)
        if not acc:
            print(f"Error: Account '{account_name}' not found.", file=sys.stderr)
            sys.exit(1)

    srv_id = acc.get("service_id")
    client = RenderClient(acc["api_key"])
    print(f"Streaming logs for {acc['name']} ({srv_id})...")
    res = client.run_cli(["logs", srv_id], interactive=True)
    sys.exit(res.returncode)


def cmd_cron_install(args: argparse.Namespace):
    """Installs or prints crontab entry for daily shift checks."""
    render_pool_bin = os.path.expanduser("~/.local/bin/render-pool")
    cron_line = f"0 0 * * * {render_pool_bin} check-shift --auto-switch >> ~/.render_accounts/shift.log 2>&1"

    print("\nTo automate Day 1 / Day 16 shift handoffs, add this line to your crontab:")
    print(f"  {cron_line}\n")
    print("Run: crontab -e and paste the line above.")


def main():
    parser = argparse.ArgumentParser(
        prog="render-pool",
        description="Pair multiple Render instances into a single quota-surviving VPS target."
    )
    parser.add_argument("-v", "--version", action="version", version=f"render-pool {VERSION}")

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # status
    p_status = subparsers.add_parser("status", help="Show pool status, shifts, and router info")

    # account
    p_acc = subparsers.add_parser("account", help="Manage Render accounts")
    acc_sub = p_acc.add_subparsers(dest="account_command")
    
    p_acc_add = acc_sub.add_parser("add", help="Add a Render account to the pool")
    p_acc_add.add_argument("name", help="Identifier for this account (e.g. primary, backup)")
    p_acc_add.add_argument("--api-key", help="Render API key")
    p_acc_add.add_argument("--service-id", help="Render Service ID (srv-...)")
    p_acc_add.add_argument("--service-url", help="Public service URL")

    p_acc_rm = acc_sub.add_parser("remove", help="Remove an account from pool")
    p_acc_rm.add_argument("name", help="Account name to remove")

    p_acc_ls = acc_sub.add_parser("list", help="List accounts")

    # switch
    p_switch = subparsers.add_parser("switch", help="Trigger safe zero-downtime handoff to another account")
    p_switch.add_argument("target", nargs="?", help="Target account name (defaults to next in shift rotation)")
    p_switch.add_argument("--timeout", type=int, default=75, help="Health check timeout in seconds")
    p_switch.add_argument("--force", action="store_true", help="Force switch even if health checks return warnings")

    # deploy
    p_deploy = subparsers.add_parser("deploy", help="Deploy project across pool instances")
    p_deploy.add_argument("--active-only", action="store_true", help="Deploy only to the currently active instance")
    p_deploy.add_argument("--clear-cache", action="store_true", help="Clear build cache before deploying")

    # check-shift
    p_check = subparsers.add_parser("check-shift", help="Check if a shift change is due")
    p_check.add_argument("--auto-switch", action="store_true", help="Automatically execute switch if shift is due")

    # init
    p_init = subparsers.add_parser("init", help="Scaffold Firebase sync and Dockerfile into a project")
    p_init.add_argument("directory", nargs="?", default=".", help="Target project directory (default: current dir)")

    # router
    p_router = subparsers.add_parser("router", help="Configure ingress router")
    r_sub = p_router.add_subparsers(dest="router_command")
    
    p_r_cf = r_sub.add_parser("set-cloudflare", help="Configure Cloudflare Worker router")
    p_r_cf.add_argument("--account-id", help="Cloudflare Account ID")
    p_r_cf.add_argument("--api-token", help="Cloudflare API Token")
    p_r_cf.add_argument("--script-name", help="Worker Script Name")
    p_r_cf.add_argument("--kv-id", help="KV Namespace ID")
    p_r_cf.add_argument("--public-url", help="Public Domain (e.g. api.yourdomain.com)")

    p_r_vc = r_sub.add_parser("set-vercel", help="Configure Vercel Edge Config router")
    p_r_vc.add_argument("--token", help="Vercel API Token")
    p_r_vc.add_argument("--edge-config-id", help="Vercel Edge Config ID")
    p_r_vc.add_argument("--team-id", help="Vercel Team ID")
    p_r_vc.add_argument("--public-url", help="Public Frontend Domain")

    # ssh & logs
    p_ssh = subparsers.add_parser("ssh", help="SSH into active (or specified) Render instance")
    p_ssh.add_argument("account", nargs="?", help="Account name (default: active account)")

    p_logs = subparsers.add_parser("logs", help="Stream logs from active (or specified) Render instance")
    p_logs.add_argument("account", nargs="?", help="Account name (default: active account)")

    # cron
    p_cron = subparsers.add_parser("cron", help="Manage automated shift cron schedule")
    cron_sub = p_cron.add_subparsers(dest="cron_command")
    cron_sub.add_parser("install", help="Show crontab command for daily shift checking")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    pool = AccountPool()

    if args.command == "status":
        cmd_status(pool, args)
    elif args.command == "account":
        if args.account_command == "add":
            cmd_account_add(pool, args)
        elif args.account_command == "remove":
            cmd_account_remove(pool, args)
        elif args.account_command == "list":
            cmd_status(pool, args)
        else:
            p_acc.print_help()
    elif args.command == "switch":
        cmd_switch(pool, args)
    elif args.command == "deploy":
        cmd_deploy(pool, args)
    elif args.command == "check-shift":
        cmd_check_shift(pool, args)
    elif args.command == "init":
        cmd_init(args)
    elif args.command == "router":
        if args.router_command == "set-cloudflare":
            cmd_router_cf(pool, args)
        elif args.router_command == "set-vercel":
            cmd_router_vercel(pool, args)
        else:
            p_router.print_help()
    elif args.command == "ssh":
        cmd_ssh(pool, args)
    elif args.command == "logs":
        cmd_logs(pool, args)
    elif args.command == "cron":
        cmd_cron_install(args)


if __name__ == "__main__":
    main()
