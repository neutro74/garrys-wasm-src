#!/usr/bin/env python3
import os
import subprocess
import tempfile
import unittest


class FileTests(unittest.TestCase):
    def test_read_only_volume_and_shared_handles(self):
        with tempfile.TemporaryDirectory(prefix='lift-files-', dir='/tmp/opencode') as temp:
            root = os.path.join(temp, 'root')
            os.makedirs(os.path.join(root, 'Folder'))
            with open(os.path.join(root, 'Folder', 'data.bin'), 'wb') as f: f.write(b'0123456789')
            with open(os.path.join(temp, 'outside.txt'), 'wb') as f: f.write(b'outside')
            os.symlink(os.path.join(temp, 'outside.txt'), os.path.join(root, 'escape.txt'))
            subprocess.run(['node', os.path.join(os.path.dirname(__file__), 'host/test-files.mjs'), root], check=True)


if __name__ == '__main__':
    unittest.main()
