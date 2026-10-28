#!/usr/bin/env python3
"""
render_sync.py - Firebase Storage synchronization layer for Render Ephemeral Disks.

Ensures /data filesystem persistence across Render instance suspensions,
redeployments, and account rotations.

Usage:
  python render_sync.py pull     # On container startup (downloads /data archive)
  python render_sync.py push     # On SIGTERM / graceful shutdown (uploads /data archive)
  python render_sync.py periodic # Background sync daemon
"""

import os
import sys
import tarfile
import tempfile
import time
import signal
from pathlib import Path

# Data directory inside container to persist
DATA_DIR = os.getenv("PERSISTENT_DATA_DIR", "/app/data")
FIREBASE_BUCKET = os.getenv("FIREBASE_STORAGE_BUCKET")
FIREBASE_ARCHIVE_NAME = os.getenv("FIREBASE_ARCHIVE_NAME", "render_pool_data.tar.gz")
FIREBASE_SERVICE_ACCOUNT_JSON = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")


def get_bucket():
    """Initializes Firebase Admin SDK and returns the Storage bucket."""
    if not FIREBASE_BUCKET:
        print("[render_sync] WARNING: FIREBASE_STORAGE_BUCKET not set. Sync skipped.", file=sys.stderr)
        return None

    try:
        import firebase_admin
        from firebase_admin import credentials, storage

        if not firebase_admin._apps:
            if FIREBASE_SERVICE_ACCOUNT_JSON and os.path.exists(FIREBASE_SERVICE_ACCOUNT_JSON):
                cred = credentials.Certificate(FIREBASE_SERVICE_ACCOUNT_JSON)
                firebase_admin.initialize_app(cred, {"storageBucket": FIREBASE_BUCKET})
            else:
                # Uses Application Default Credentials or env
                firebase_admin.initialize_app(options={"storageBucket": FIREBASE_BUCKET})

        return storage.bucket(FIREBASE_BUCKET)
    except ImportError:
        print("[render_sync] ERROR: firebase-admin package is required for storage sync.", file=sys.stderr)
        print("[render_sync] Install with: pip install firebase-admin", file=sys.stderr)
        return None
    except Exception as e:
        print(f"[render_sync] ERROR: Failed to initialize Firebase: {e}", file=sys.stderr)
        return None


def pull_snapshot():
    """Downloads snapshot archive from Firebase Storage and extracts into DATA_DIR."""
    bucket = get_bucket()
    if not bucket:
        os.makedirs(DATA_DIR, exist_ok=True)
        return False

    blob = bucket.blob(FIREBASE_ARCHIVE_NAME)
    if not blob.exists():
        print(f"[render_sync] No existing snapshot found in bucket '{FIREBASE_BUCKET}'. Starting fresh.")
        os.makedirs(DATA_DIR, exist_ok=True)
        return True

    os.makedirs(DATA_DIR, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp_file:
        tmp_path = tmp_file.name

    try:
        print(f"[render_sync] Downloading snapshot from Firebase Storage ({FIREBASE_ARCHIVE_NAME})...")
        blob.download_to_filename(tmp_path)
        print(f"[render_sync] Extracting snapshot to {DATA_DIR}...")
        with tarfile.open(tmp_path, "r:gz") as tar:
            tar.extractall(path=DATA_DIR)
        print("[render_sync] ✓ Snapshot restored successfully.")
        return True
    except Exception as e:
        print(f"[render_sync] ERROR: Failed to restore snapshot: {e}", file=sys.stderr)
        return False
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def push_snapshot():
    """Archives DATA_DIR and uploads it to Firebase Storage."""
    bucket = get_bucket()
    if not bucket:
        return False

    if not os.path.exists(DATA_DIR) or not os.listdir(DATA_DIR):
        print(f"[render_sync] Data directory {DATA_DIR} is empty, skipping upload.")
        return True

    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp_file:
        tmp_path = tmp_file.name

    try:
        print(f"[render_sync] Archiving {DATA_DIR}...")
        with tarfile.open(tmp_path, "w:gz") as tar:
            for item in os.listdir(DATA_DIR):
                full_item_path = os.path.join(DATA_DIR, item)
                tar.add(full_item_path, arcname=item)

        size_kb = os.path.getsize(tmp_path) / 1024
        print(f"[render_sync] Uploading {size_kb:.1f} KB snapshot to Firebase Storage...")
        blob = bucket.blob(FIREBASE_ARCHIVE_NAME)
        blob.upload_from_filename(tmp_path, content_type="application/gzip")
        print("[render_sync] ✓ Snapshot uploaded successfully to Firebase Storage.")
        return True
    except Exception as e:
        print(f"[render_sync] ERROR: Failed to upload snapshot: {e}", file=sys.stderr)
        return False
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def main():
    if len(sys.argv) < 2:
        print("Usage: python render_sync.py [pull|push]")
        sys.exit(1)

    action = sys.argv[1].lower()
    if action == "pull":
        success = pull_snapshot()
        sys.exit(0 if success else 1)
    elif action == "push":
        success = push_snapshot()
        sys.exit(0 if success else 1)
    else:
        print(f"Unknown action: {action}")
        sys.exit(1)


if __name__ == "__main__":
    main()
