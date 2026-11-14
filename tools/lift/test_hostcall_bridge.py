import os
import subprocess
import tempfile
import unittest
from test_multi_dll import build

class HostcallBridgeTests(unittest.TestCase):
    def test_real_lifted_com_call_and_fast_argument_abi(self):
        with tempfile.TemporaryDirectory(prefix='lift-hostbridge-', dir='/tmp/opencode') as d:
            build(d, 'primary', 0x400000, [('entry', 0x1000, 'call dword ptr [0x403000]; ret')],
                  [dict(dll='secondary.dll', name='com', iat=0x403000)], False, spare_thunks=1024)
            build(d, 'secondary', 0x500000, [('com', 0x1000,
                  'push 32; call dword ptr [0x503000]; push eax; mov eax, [eax]; call dword ptr [eax+16]; ret')],
                  [dict(dll='d3d9.dll', name='Direct3DCreate9', iat=0x503000)], True, spare_thunks=1024)
            subprocess.run(['node',os.path.join(os.path.dirname(__file__),'host/test-hostcall-bridge-guest.mjs'),d],check=True)

    def test_native_tls_is_thread_local(self):
        with tempfile.TemporaryDirectory(prefix='lift-native-tls-', dir='/tmp/opencode') as d:
            build(d,'primary',0x400000,[('worker',0x1000,'call dword ptr [0x403000]; ret 4')],
                  [dict(dll='secondary.dll',name='worker',iat=0x403000)],False,shared=True,spare_thunks=1024)
            build(d,'secondary',0x600000,[('worker',0x1000,
                  'push 7; call dword ptr [0x603000]; mov [0x602800], eax; '
                  'push 456; push 7; call dword ptr [0x603004]; '
                  'call dword ptr [0x603008]; mov [0x602804], eax; ret'),
                  ('read',0x1100,'push 7; call dword ptr [0x603000]; ret')],
                  [dict(dll='kernel32.dll',name='TlsGetValue',iat=0x603000),
                   dict(dll='kernel32.dll',name='TlsSetValue',iat=0x603004),
                   dict(dll='kernel32.dll',name='GetCurrentThreadId',iat=0x603008)],True,shared=True,spare_thunks=1024)
            subprocess.run(['node',os.path.join(os.path.dirname(__file__),'host/test-native-tls-thread.mjs'),d],check=True)

if __name__ == '__main__':
    unittest.main()
