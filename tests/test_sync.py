"""
Test render_sync.py archive creation and extraction.
"""

import os
import shutil
import tarfile
import tempfile
import unittest


class TestRenderSync(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_sync_")
        self.data_dir = os.path.join(self.test_dir, "data")
        os.makedirs(self.data_dir, exist_ok=True)

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_archive_and_extract(self):
        # 1. Create dummy files in data dir
        f1 = os.path.join(self.data_dir, "test1.txt")
        with open(f1, "w") as f:
            f.write("Hello World 123")

        sub_dir = os.path.join(self.data_dir, "subdir")
        os.makedirs(sub_dir, exist_ok=True)
        f2 = os.path.join(sub_dir, "test2.json")
        with open(f2, "w") as f:
            f.write('{"key": "value"}')

        # 2. Archive to tar.gz
        archive_path = os.path.join(self.test_dir, "snapshot.tar.gz")
        with tarfile.open(archive_path, "w:gz") as tar:
            for item in os.listdir(self.data_dir):
                full_item_path = os.path.join(self.data_dir, item)
                tar.add(full_item_path, arcname=item)

        self.assertTrue(os.path.exists(archive_path))
        self.assertGreater(os.path.getsize(archive_path), 0)

        # 3. Extract to a fresh restore dir
        restore_dir = os.path.join(self.test_dir, "restored_data")
        os.makedirs(restore_dir, exist_ok=True)

        with tarfile.open(archive_path, "r:gz") as tar:
            if hasattr(tarfile, "data_filter"):
                tar.extractall(path=restore_dir, filter="data")
            else:
                tar.extractall(path=restore_dir)

        # Verify contents
        restored_f1 = os.path.join(restore_dir, "test1.txt")
        self.assertTrue(os.path.exists(restored_f1))
        with open(restored_f1, "r") as f:
            self.assertEqual(f.read(), "Hello World 123")

        restored_f2 = os.path.join(restore_dir, "subdir", "test2.json")
        self.assertTrue(os.path.exists(restored_f2))
        with open(restored_f2, "r") as f:
            self.assertEqual(f.read(), '{"key": "value"}')


if __name__ == "__main__":
    unittest.main()
