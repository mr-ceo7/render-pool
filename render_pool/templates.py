"""
Template generation and project scaffolding utilities.
"""

import os
import shutil
from pathlib import Path

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"


def scaffold_project(target_dir: str, include_router: bool = True) -> list[str]:
    """
    Scaffolds render-pool support into target_dir:
    - Dockerfile with entrypoint
    - entrypoint.sh (graceful sync)
    - render_sync.py (Firebase Storage snapshot sync)
    - requirements.txt (if missing, or appends)
    - Router configurations (Cloudflare Worker & Vercel Edge)
    """
    dest = Path(target_dir).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    created_files = []

    # 1. FastAPI sync files
    fastapi_src = TEMPLATES_DIR / "fastapi"
    for filename in ["Dockerfile", "entrypoint.sh", "render_sync.py"]:
        src_file = fastapi_src / filename
        dest_file = dest / filename
        if not dest_file.exists():
            shutil.copy2(src_file, dest_file)
            if filename.endswith(".sh"):
                dest_file.chmod(0o755)
            created_files.append(str(dest_file))

    # requirements.txt
    req_file = dest / "requirements.txt"
    if not req_file.exists():
        shutil.copy2(fastapi_src / "requirements.txt", req_file)
        created_files.append(str(req_file))
    else:
        # Check if firebase-admin is present
        content = req_file.read_text(encoding="utf-8")
        if "firebase-admin" not in content:
            req_file.write_text(content + "\nfirebase-admin>=6.5.0\n", encoding="utf-8")

    # main.py if project is completely empty
    main_file = dest / "main.py"
    if not main_file.exists() and not (dest / "app.py").exists():
        shutil.copy2(fastapi_src / "main.py", main_file)
        created_files.append(str(main_file))

    # 2. Router files
    if include_router:
        router_dest = dest / "render_pool_router"
        router_dest.mkdir(parents=True, exist_ok=True)
        router_src = TEMPLATES_DIR / "router"
        for item in router_src.glob("*"):
            if item.is_file():
                target_f = router_dest / item.name
                if not target_f.exists():
                    shutil.copy2(item, target_f)
                    created_files.append(str(target_f))

    return created_files
