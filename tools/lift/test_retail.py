#!/usr/bin/env python3
"""Retail-DLL integration tests against the installed Garry's Mod.

These are skipped when the retail install is absent. They prove the real
filesystem DLL can initialize and serve installed game files, not that the
game is playable.
"""
import os
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
GMOD = os.path.expanduser('~/.steam/steam/steamapps/common/GarrysMod')
HAVE = os.path.isdir(GMOD) and os.path.exists(os.path.join(HERE, 'out/filesystem-thread.json'))


@unittest.skipUnless(HAVE, 'retail Garry\'s Mod install / lifted DLLs not present')
class RetailFilesystem(unittest.TestCase):
    def test_ifilesystem_reads_installed_map(self):
        out = subprocess.run(['node', 'host/filesystem-init.mjs', GMOD], cwd=HERE,
                             capture_output=True, text=True, timeout=180).stdout
        self.assertIn('PASS retail IFileSystem served gm_construct.bsp bytes', out)
        self.assertIn('INIT_OK', out)
        self.assertIn('"VBSP"', out)
        size = os.path.getsize(os.path.join(GMOD, 'garrysmod/maps/gm_construct.bsp'))
        self.assertIn(f'Size(handle) -> {size}', out)


@unittest.skipUnless(HAVE, 'retail Garry\'s Mod install / lifted DLLs not present')
class RetailVpk(unittest.TestCase):
    def test_vpk_only_files_served_through_retail_filesystem(self):
        out = subprocess.run(['node', 'host/filesystem-vpk-test.mjs', GMOD], cwd=HERE,
                             capture_output=True, text=True, timeout=180).stdout
        self.assertIn('PASS AddSearchPath returned for GAME and MOD', out)
        for name in ('scripts/game_sounds_manifest.txt', 'materials/console/background01.vtf', 'scripts/kb_act.lst'):
            self.assertIn(f'PASS VPK-only {name}', out)


@unittest.skipUnless(HAVE and os.path.exists(os.path.join(HERE, 'out/server-multi.json')),
                     'retail server lift not present')
class RetailServerInterfaces(unittest.TestCase):
    def test_retail_server_and_engine_interfaces(self):
        out = subprocess.run(['node', 'host/server-tests.mjs'], cwd=HERE,
                             capture_output=True, text=True, timeout=180).stdout
        for name in ('ServerGameDLL009', 'ServerGameClients004', 'ServerGameEnts001',
                     'VEngineServer021', 'VEngineClient015'):
            self.assertIn(f'PASS {name}', out)
        self.assertIn('PASS unknown interface rejection', out)


if __name__ == '__main__':
    unittest.main()
