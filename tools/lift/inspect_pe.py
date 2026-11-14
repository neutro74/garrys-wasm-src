#!/usr/bin/env python3
"""PE inspector for the GameBox lifter: sections, imports, exports, protection hints and Source
interface versions. Optionally diffs the interface versions a DLL asks for against the ones an
engine source tree defines.

  tools/lift/.venv/bin/python tools/lift/inspect_pe.py client.dll server.dll \
      --engine ports/gmod/engine --json out.json
"""
import argparse, json, math, os, re, sys
import pefile

IFACE_RE = re.compile(rb'\b((?:V|IV)?[A-Za-z_]{3,40}?)(\d{3})\b')
# Source interface identifiers all end in three digits and start with a known family.
FAMILIES = (
    b'VEngine', b'VClient', b'ServerGame', b'VModelInfo', b'VFileSystem', b'VGUI_', b'VMaterialSystem',
    b'VPhysics', b'VStudioRender', b'EngineTrace', b'VDebugOverlay', b'VClientEntityList', b'GAMEUI',
    b'VEngineCvar', b'SoundEmitter', b'VSoundEmitter', b'VDataCache', b'VInputSystem', b'VMDLCache',
    b'MDLCache', b'VCvar', b'LUASHARED', b'GMOD', b'IGMOD', b'GModDataPacks', b'Steam', b'SteamClient',
    b'STEAMAPPS', b'STEAMUSER', b'ISteam', b'AvatarMgr', b'VP2P', b'NetworkSystem', b'VClientPrediction',
    b'VStaticPropMgr', b'VEngineRandom', b'VVideoServices', b'VAvi', b'VBik', b'VQuickTime',
    b'VMaterialSystemStub', b'VVoiceServer', b'VoiceServer', b'XboxSystem', b'VENGINE', b'INETSUPPORT',
)

PACKER_SECTIONS = {b'.vmp0', b'.vmp1', b'.vmp2', b'.themida', b'.winlice', b'.enigma1', b'.aspack',
                   b'.adata', b'upx0', b'upx1', b'.petite', b'.nsp0', b'.nsp1', b'.mpress1', b'.svkp', b'.perplex'}


def entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = [0] * 256
    for b in data:
        counts[b] += 1
    n = len(data)
    return -sum(c / n * math.log2(c / n) for c in counts if c)


def interfaces(blob: bytes):
    found = set()
    for m in re.finditer(rb'[A-Za-z_][A-Za-z0-9_]{2,40}\d{3}(?![A-Za-z0-9_])', blob):
        s = m.group(0)
        if any(s.startswith(f) for f in FAMILIES):
            found.add(s.decode('ascii'))
    return sorted(found)


def inspect(path: str):
    pe = pefile.PE(path, fast_load=False)
    data = pe.__data__
    opt = pe.OPTIONAL_HEADER
    arch = {0x14c: 'x86', 0x8664: 'x64'}.get(pe.FILE_HEADER.Machine, hex(pe.FILE_HEADER.Machine))
    out = {
        'file': os.path.basename(path), 'size': len(data), 'arch': arch,
        'is_dll': bool(pe.FILE_HEADER.Characteristics & 0x2000),
        'image_base': opt.ImageBase, 'entry_rva': opt.AddressOfEntryPoint, 'image_size': opt.SizeOfImage,
        'subsystem': opt.Subsystem,
        'has_relocs': bool(getattr(pe, 'DIRECTORY_ENTRY_BASERELOC', None)),
        'has_tls': hasattr(pe, 'DIRECTORY_ENTRY_TLS'),
        'has_seh_table': bool(getattr(opt, 'DATA_DIRECTORY', None) and opt.DATA_DIRECTORY[10].VirtualAddress),
        'has_exception_dir': bool(opt.DATA_DIRECTORY[3].VirtualAddress),
        'has_delay_imports': hasattr(pe, 'DIRECTORY_ENTRY_DELAY_IMPORT'),
        'sections': [], 'imports': {}, 'delay_imports': {}, 'exports': [], 'flags': [],
    }
    for s in pe.sections:
        raw = s.get_data()
        name = s.Name.rstrip(b'\0')
        ent = entropy(raw)
        out['sections'].append({
            'name': name.decode('latin1'), 'rva': s.VirtualAddress, 'vsize': s.Misc_VirtualSize,
            'rsize': s.SizeOfRawData, 'exec': bool(s.Characteristics & 0x20000000),
            'write': bool(s.Characteristics & 0x80000000), 'entropy': round(ent, 2)})
        if name.lower() in PACKER_SECTIONS:
            out['flags'].append(f'packer-section:{name.decode("latin1")}')
        if s.Characteristics & 0x20000000 and ent > 7.2:
            out['flags'].append(f'high-entropy-code:{name.decode("latin1")}')
        if s.Characteristics & 0x20000000 and s.Characteristics & 0x80000000:
            out['flags'].append(f'rwx-section:{name.decode("latin1")}')
    for entry in getattr(pe, 'DIRECTORY_ENTRY_IMPORT', []):
        out['imports'][entry.dll.decode().lower()] = sorted(
            (i.name.decode() if i.name else f'#{i.ordinal}') for i in entry.imports)
    for entry in getattr(pe, 'DIRECTORY_ENTRY_DELAY_IMPORT', []):
        out['delay_imports'][entry.dll.decode().lower()] = sorted(
            (i.name.decode() if i.name else f'#{i.ordinal}') for i in entry.imports)
    for sym in getattr(getattr(pe, 'DIRECTORY_ENTRY_EXPORT', None), 'symbols', []):
        out['exports'].append(sym.name.decode() if sym.name else f'#{sym.ordinal}')
    out['interfaces'] = interfaces(data)
    return out


def engine_interfaces(root: str):
    """Interface version strings defined anywhere in an engine source tree."""
    pat = re.compile(r'"((?:' + '|'.join(f.decode() for f in FAMILIES) + r')[A-Za-z0-9_]*\d{3})"')
    found = {}
    for d, _, files in os.walk(root):
        if any(skip in d for skip in ('/build', '/.git', '/node_modules')):
            continue
        for f in files:
            if not f.endswith(('.h', '.hpp', '.cpp', '.c', '.cc')):
                continue
            try:
                text = open(os.path.join(d, f), errors='ignore').read()
            except OSError:
                continue
            for m in pat.finditer(text):
                found.setdefault(m.group(1), os.path.relpath(os.path.join(d, f), root))
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('files', nargs='+')
    ap.add_argument('--engine', help='engine source root to diff interface versions against')
    ap.add_argument('--json')
    ap.add_argument('--quiet', action='store_true')
    args = ap.parse_args()
    eng = engine_interfaces(args.engine) if args.engine else None
    reports = []
    for path in args.files:
        r = inspect(path)
        if eng is not None:
            # Match by family: same name different number is a version mismatch, absent is missing.
            by_family = {}
            for k in eng:
                by_family.setdefault(k[:-3], []).append(k[-3:])
            r['interface_diff'] = {'ok': [], 'version_mismatch': {}, 'missing': []}
            for i in r['interfaces']:
                if i in eng:
                    r['interface_diff']['ok'].append(i)
                elif i[:-3] in by_family:
                    r['interface_diff']['version_mismatch'][i] = by_family[i[:-3]]
                else:
                    r['interface_diff']['missing'].append(i)
        reports.append(r)
        if args.quiet:
            continue
        print(f"== {r['file']}  {r['arch']}  {'DLL' if r['is_dll'] else 'EXE'}  base=0x{r['image_base']:x}  "
              f"size={r['size']:,}  relocs={r['has_relocs']}  tls={r['has_tls']}  flags={r['flags'] or 'none'}")
        for s in r['sections']:
            print(f"   {s['name']:<8} rva={s['rva']:08x} vsize={s['vsize']:>9,} ent={s['entropy']:<5} "
                  f"{'X' if s['exec'] else '-'}{'W' if s['write'] else '-'}")
        for dll, names in r['imports'].items():
            print(f"   imports {dll}: {len(names)}")
        print(f"   exports: {len(r['exports'])}  e.g. {', '.join(r['exports'][:6])}")
        if 'interface_diff' in r:
            d = r['interface_diff']
            print(f"   interfaces: {len(r['interfaces'])}  ok={len(d['ok'])}  "
                  f"version-mismatch={len(d['version_mismatch'])}  missing-in-engine={len(d['missing'])}")
            for k, v in d['version_mismatch'].items():
                print(f"      ~ {k}: engine has {', '.join(v)}")
            for k in d['missing']:
                print(f"      - {k}")
        else:
            print('   interfaces:', ', '.join(r['interfaces']))
    if args.json:
        json.dump(reports, open(args.json, 'w'), indent=1)
    return 0


if __name__ == '__main__':
    sys.exit(main())
