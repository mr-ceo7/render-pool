import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from render_pool.templates import scaffold_project


class TestTemplatesAndInit(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_render_init_")

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_scaffold_project(self):
        created = scaffold_project(self.test_dir, include_router=True)
        self.assertTrue(len(created) > 0)

        # Check required files
        expected_files = [
            "Dockerfile",
            "entrypoint.sh",
            "render_sync.py",
            "requirements.txt",
            "main.py",
            "render_pool_router/cloudflare-worker.js",
            "render_pool_router/wrangler.toml",
            "render_pool_router/vercel-middleware.ts",
            "render_pool_router/vercel.json"
        ]

        for rel_path in expected_files:
            full_path = os.path.join(self.test_dir, rel_path)
            self.assertTrue(os.path.exists(full_path), f"Expected file {rel_path} was not created")

        # Check entrypoint is executable
        entrypoint = os.path.join(self.test_dir, "entrypoint.sh")
        self.assertTrue(os.access(entrypoint, os.X_OK))


if __name__ == "__main__":
    unittest.main()
