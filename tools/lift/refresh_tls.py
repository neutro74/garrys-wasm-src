#!/usr/bin/env python3
"""Add PE TLS metadata to an existing matching lift without rebuilding its WASM."""
import argparse
import json

from census import Image, tls_metadata

ap = argparse.ArgumentParser()
ap.add_argument('dll')
ap.add_argument('prefix')
a = ap.parse_args()
with open(a.prefix + '.json') as f:
    meta = json.load(f)
img = Image(a.dll)
img.rebase(meta['base'])
with open(a.prefix + '.img', 'rb') as f:
    if f.read() != img.mem:
        raise SystemExit('DLL does not match the lifted image; refusing to update metadata')
meta['tls'] = tls_metadata(img)
with open(a.prefix + '.json', 'w') as f:
    json.dump(meta, f, indent=1)
print(a.prefix, meta['tls'])
