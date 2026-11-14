#!/usr/bin/env python3
"""Exercise shared guest memory and isolated CPU globals across real workers."""
import os
import subprocess
import tempfile
import unittest

from keystone import Ks, KS_ARCH_X86, KS_MODE_32
from build_dll import BIN, FEATURES
from difftest import CODE, FakeImg
from lift import Func, module


class SharedMemoryTests(unittest.TestCase):
    def test_thread_local_fs_and_fpu_backing(self):
        code = bytes(Ks(KS_ARCH_X86, KS_MODE_32).asm('mov eax, fs:[0x18]; fld dword ptr [eax]; ret', addr=CODE)[0])
        wat = module([Func(FakeImg(code), CODE, export=True, shared_memory=True).lift()], import_mem=True, shared_memory=True)
        with tempfile.TemporaryDirectory(prefix='lift-threadlocal-', dir='/tmp/opencode') as d:
            src, dst = os.path.join(d, 'thread.wat'), os.path.join(d, 'thread.wasm')
            with open(src, 'w') as f: f.write(wat)
            subprocess.run([os.path.join(BIN, 'wasm-as'), *FEATURES, '-o', dst, src], check=True)
            subprocess.run(['node', os.path.join(os.path.dirname(__file__), 'host/test-thread-local.mjs'), dst], check=True)

    def test_separate_worker_cpu_and_shared_guest_memory(self):
        code = bytes(Ks(KS_ARCH_X86, KS_MODE_32).asm(
            'mov eax, dword ptr [0x10000]; add eax, ecx; mov dword ptr [0x10000], eax; ret', addr=CODE)[0])
        wat = module([Func(FakeImg(code), CODE, export=True).lift()], import_mem=True, shared_memory=True)
        with tempfile.TemporaryDirectory(prefix='lift-shared-', dir='/tmp/opencode') as d:
            src, dst = os.path.join(d, 'shared.wat'), os.path.join(d, 'shared.wasm')
            with open(src, 'w') as f:
                f.write(wat)
            subprocess.run([os.path.join(BIN, 'wasm-as'), *FEATURES, '-o', dst, src], check=True)
            subprocess.run(['node', os.path.join(os.path.dirname(__file__), 'host/test-shared.mjs'), dst], check=True)


if __name__ == '__main__':
    unittest.main()
