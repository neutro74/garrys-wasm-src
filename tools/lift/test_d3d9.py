#!/usr/bin/env python3
"""Exercise D3D9 COM calls through real lifted x86 guest instructions."""
import os
import subprocess
import tempfile
import unittest

from test_multi_dll import build


class D3D9AbiTests(unittest.TestCase):
    def test_stdcall_com_this_and_return_stack(self):
        asm = ('push 32; call dword ptr [0x403000]; mov ebx, eax; '
               'push 0; push ebx; mov eax, [ebx]; call dword ptr [eax+60]; '
               'mov [0x402800], eax; '
               'push 0x402900; push 0; push 0; push ebx; mov eax, [ebx]; call dword ptr [eax+20]; '
               'mov [0x402804], esp; '
               'push ebx; mov eax, [ebx]; call dword ptr [eax+16]; ret')
        with tempfile.TemporaryDirectory(prefix='lift-d3d9-abi-', dir='/tmp/opencode') as d:
            build(d, 'primary', 0x400000, [('entry', 0x1000, asm)],
                  [dict(dll='d3d9.dll', name='Direct3DCreate9', iat=0x403000)],
                  False, spare_thunks=1024)
            subprocess.run(['node', os.path.join(os.path.dirname(__file__), 'host/test-d3d9-abi.mjs'), d], check=True)


if __name__ == '__main__':
    unittest.main()
