#!/usr/bin/env python3
"""Generate Steam interface vtable arity tables from Steamworks SDK headers.

Each entry maps a Steam interface version string to the number of 4-byte stack
words consumed by every virtual method, in vtable order. The host Steam shim
uses this to emulate the x86 thiscall ABI exactly (the callee pops its args).
"""
import re
import sys
from pathlib import Path

HEADER_DIR = Path(sys.argv[1] if len(sys.argv) > 1 else '/tmp/opencode')
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else 'host/steam-abi.mjs')

# Interfaces the Source engine / GMod reference, plus their version macros.
VERSIONS = {
    'ISteamUser': 'STEAMUSER_INTERFACE_VERSION',
    'ISteamFriends': 'STEAMFRIENDS_INTERFACE_VERSION',
    'ISteamUtils': 'STEAMUTILS_INTERFACE_VERSION',
    'ISteamClient': 'STEAMCLIENT_INTERFACE_VERSION',
    'ISteamApps': 'STEAMAPPS_INTERFACE_VERSION',
    'ISteamUserStats': 'STEAMUSERSTATS_INTERFACE_VERSION',
    'ISteamMatchmaking': 'STEAMMATCHMAKING_INTERFACE_VERSION',
    'ISteamMatchmakingServers': 'STEAMMATCHMAKINGSERVERS_INTERFACE_VERSION',
    'ISteamNetworking': 'STEAMNETWORKING_INTERFACE_VERSION',
    'ISteamRemoteStorage': 'STEAMREMOTESTORAGE_INTERFACE_VERSION',
    'ISteamGameCoordinator': 'STEAMGAMECOORDINATOR_INTERFACE_VERSION',
    'ISteamGameServer': 'STEAMGAMESERVER_INTERFACE_VERSION',
    'ISteamGameServerStats': 'STEAMGAMESERVERSTATS_INTERFACE_VERSION',
    'ISteamUGC': 'STEAMUGC_INTERFACE_VERSION',
    'ISteamInventory': 'STEAMINVENTORY_INTERFACE_VERSION',
    'ISteamHTTP': 'STEAMHTTP_INTERFACE_VERSION',
    'ISteamController': 'STEAMCONTROLLER_INTERFACE_VERSION',
    'ISteamInput': 'STEAMINPUT_INTERFACE_VERSION',
    'ISteamScreenshots': 'STEAMSCREENSHOTS_INTERFACE_VERSION',
    'ISteamMusic': 'STEAMMUSIC_INTERFACE_VERSION',
    'ISteamMusicRemote': 'STEAMMUSICREMOTE_INTERFACE_VERSION',
    'ISteamHTMLSurface': 'STEAMHTMLSURFACE_INTERFACE_VERSION',
    'ISteamVideo': 'STEAMVIDEO_INTERFACE_VERSION',
    'ISteamParentalSettings': 'STEAMPARENTALSETTINGS_INTERFACE_VERSION',
    'ISteamNetworkingSockets': 'STEAMNETWORKINGSOCKETS_INTERFACE_VERSION',
    'ISteamNetworkingMessages': 'STEAMNETWORKINGMESSAGES_INTERFACE_VERSION',
    'ISteamNetworkingUtils': 'STEAMNETWORKINGUTILS_INTERFACE_VERSION',
    'ISteamRemotePlay': 'STEAMREMOTEPLAY_INTERFACE_VERSION',
    'ISteamParties': 'STEAMPARTIES_INTERFACE_VERSION',
    'ISteamTimeline': 'STEAMTIMELINE_INTERFACE_VERSION',
}

# Header files that define each interface class.
HEADERS = [
    'isteamuser.h', 'isteamfriends.h', 'isteamutils.h', 'isteamclient.h',
    'isteamapps.h', 'isteamuserstats.h', 'isteammatchmaking.h',
    'isteamnetworking.h', 'isteamremotestorage.h', 'isteamgamecoordinator.h',
    'isteamgameserver.h', 'isteamgameserverstats.h', 'isteamugc.h',
    'isteaminventory.h', 'isteamhttp.h', 'isteamcontroller.h', 'isteaminput.h',
    'isteamscreenshots.h', 'isteammusic.h', 'isteammusicremote.h',
    'isteamhtmlsurface.h', 'isteamvideo.h', 'isteamparentalsettings.h',
    'isteamnetworking.h', 'isteamremoteplay.h', 'isteamparties.h',
    'isteamtimeline.h',
]

# Types genuinely passed as two 32-bit stack words (8 bytes by value).
# NOTE: HAuthTicket / HSteamPipe / HSteamUser / AppId_t / AccountID_t are all
# 32-bit integers and must stay one word.
U64 = ('uint64', 'int64', 'CSteamID', 'CGameID', 'SteamItemInstanceID_t',
       'PublishedFileId_t', 'PublishedFileUpdateHandle_t',
       'UGCFileWriteStreamHandle_t', 'UGCHandle_t',
       # 64-bit typedefs defined in headers outside the interface headers
       'SteamAPICall_t', 'SteamInventoryUpdateHandle_t', 'SteamNetworkingMicroseconds')


TYPE_KEYWORDS = {
    'const', 'volatile', 'struct', 'class', 'enum', 'unsigned', 'signed',
    'int', 'char', 'short', 'long', 'float', 'double', 'bool', 'void',
    'uint8', 'uint16', 'uint32', 'uint64', 'int8', 'int16', 'int32', 'int64',
    'size_t', 'time_t',
}


def type_words(decl):
    """Return the number of 4-byte stack words a parameter occupies on x86.

    `decl` is a full parameter declaration such as
    ``CSteamID steamIDGameServer`` or ``const char *pchName``.
    """
    d = decl.strip()
    # Default arguments and array params.
    if '=' in d:
        d = d.split('=', 1)[0].strip()
    if '[' in d:
        return 1  # array parameter decays to pointer
    if '*' in d or '&' in d:
        return 1  # pointer / reference (also CUtlString, const&)
    tokens = d.split()
    # Drop the trailing parameter name (it is not part of the type).
    while len(tokens) > 1:
        last = tokens[-1]
        if last in TYPE_KEYWORDS or last.endswith('_t') or last.endswith('::'):
            break
        tokens.pop()
    base = ' '.join(t for t in tokens
                    if t not in ('const', 'volatile', 'struct', 'class', 'enum'))
    if base in ('double', 'int64', 'uint64', 'long long', 'unsigned long long',
                'int64_t', 'uint64_t'):
        return 2
    if base in U64:
        return 2  # CSteamID / CGameID / 64-bit handles are passed by value
    # Everything else (handles, enums, 32-bit typedefs) is one stack word.
    # The Steam API only passes CSteamID/CGameID and the 64-bit file handles
    # above by value; larger structs always arrive by pointer/reference.
    return 1


# Classes returned by value: MSVC passes a hidden result pointer as the first stack argument (the callee
# pops it and returns it in EAX), so these methods take one more word than their declared parameters.
SRET_TYPES = {'CSteamID': 8, 'CGameID': 8, 'SteamIPAddress_t': 20}   # type -> sizeof; other class returns are refused below


def returns_by_value_class(decl_head):
    """decl_head is the text between `virtual` and the parameter list: return type plus method name."""
    tokens = [t for t in decl_head.replace('*', ' * ').replace('&', ' & ').split() if t not in ('const', 'struct', 'class')]
    if len(tokens) < 2 or '*' in tokens or '&' in tokens:
        return 0
    return SRET_TYPES.get(' '.join(tokens[:-1]), 0)


def find_version_macro(text, macro):
    m = re.search(r'#\s*define\s+' + macro + r'\s+"([^"]+)"', text)
    return m.group(1) if m else None


def parse_class(text, cls):
    # Find the class body, tolerating preprocessor noise.
    m = re.search(r'class\s+' + cls + r'\b[^{]*\{', text)
    if not m:
        return None
    i = m.end()
    depth = 1
    while i < len(text) and depth:
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
        i += 1
    body = text[m.end():i - 1]
    # Drop comments so commas inside them don't confuse the split.
    body = re.sub(r'//[^\n]*', '', body)
    body = re.sub(r'/\*.*?\*/', '', body, flags=re.S)
    methods = []
    names = []
    sret = {}
    for vm in re.finditer(r'virtual\s+([^;{}]+?)\s*\(([^;]*?)\)\s*(?:=\s*0\s*)?;', body):
        params = vm.group(2).strip()
        if not params or params == 'void':
            n = 0
        else:
            parts, cur, d = [], '', 0
            for ch in params:
                if ch in '(<[':
                    d += 1
                elif ch in ')>]':
                    d -= 1
                if ch == ',' and d == 0:
                    parts.append(cur)
                    cur = ''
                else:
                    cur += ch
            if cur.strip():
                parts.append(cur)
            n = sum(type_words(p) for p in parts)
        size = returns_by_value_class(vm.group(1))
        if size:
            n += 1
            sret[len(methods)] = size
        methods.append(n)
        names.append(re.findall(r'(\w+)\s*$', vm.group(1).strip())[0])
    return methods, sret, names


def main():
    global U64
    headers = {p.name: p.read_text(errors='replace') for p in HEADER_DIR.glob('*.h')}
    all_text = '\n'.join(headers.values())
    # Any other `typedef uint64 Name;` in the headers is also passed as two words.
    found = re.findall(r'typedef\s+(?:unsigned\s+)?(?:uint64|int64|long long|unsigned long long|uint64_t|int64_t)\s+(\w+)', all_text)
    U64 = tuple(sorted(set(U64) | set(found)))
    tables = {}
    sret_tables = {}
    name_tables = {}
    missing = []
    for cls, macro in VERSIONS.items():
        version = find_version_macro(all_text, macro)
        if not version:
            missing.append(f'{cls} ({macro})')
            continue
        body = headers.get(cls_to_header(cls))
        if body is None:
            body = all_text
        parsed = parse_class(body, cls)
        if parsed is None:
            missing.append(f'{cls} class')
            continue
        tables[version] = parsed[0]
        sret_tables[version] = parsed[1]
        name_tables[version] = parsed[2]
    lines = ['// Generated by tools/lift/steam_abi.py from Steamworks SDK headers.',
             '// version -> vtable arities (4-byte stack words) in declaration order.',
             'export const STEAM_VTABLES = {']
    for version in sorted(tables):
        lines.append(f'  {version!r}: [{", ".join(str(n) for n in tables[version])}],')
    lines.append('};')
    lines += ['', '// version -> { method index: result size in bytes } for methods returning a class by value (hidden first argument).',
              'export const STEAM_SRET = {']
    for version in sorted(sret_tables):
        if sret_tables[version]:
            lines.append(f'  {version!r}: {{{", ".join(f"{i}: {n}" for i, n in sret_tables[version].items())}}},')
    lines.append('};')
    lines += ['', '// version -> method names in vtable order (as declared in the SDK header).', 'export const STEAM_NAMES = {']
    for version in sorted(name_tables):
        lines.append(f'  {version!r}: [{", ".join(repr(n) for n in name_tables[version])}],')
    lines.append('};')
    OUT.write_text('\n'.join(lines) + '\n')
    print(f'wrote {OUT} ({len(tables)} interfaces)')
    for v in sorted(tables):
        print(f'  {v}: {len(tables[v])} methods')
    if missing:
        print('MISSING: ' + ', '.join(missing))


def cls_to_header(cls):
    return {
        'ISteamUser': 'isteamuser.h', 'ISteamFriends': 'isteamfriends.h',
        'ISteamUtils': 'isteamutils.h', 'ISteamClient': 'isteamclient.h',
        'ISteamApps': 'isteamapps.h', 'ISteamUserStats': 'isteamuserstats.h',
        'ISteamMatchmaking': 'isteammatchmaking.h',
        'ISteamNetworking': 'isteamnetworking.h',
        'ISteamRemoteStorage': 'isteamremotestorage.h',
        'ISteamGameServer': 'isteamgameserver.h',
        'ISteamUGC': 'isteamugc.h', 'ISteamInventory': 'isteaminventory.h',
        'ISteamHTTP': 'isteamhttp.h', 'ISteamController': 'isteamcontroller.h',
        'ISteamInput': 'isteaminput.h', 'ISteamScreenshots': 'isteamscreenshots.h',
        'ISteamMusic': 'isteammusic.h', 'ISteamMusicRemote': 'isteammusicremote.h',
        'ISteamHTMLSurface': 'isteamhtmlsurface.h', 'ISteamVideo': 'isteamvideo.h',
        'ISteamParentalSettings': 'isteamparentalsettings.h',
        'ISteamRemotePlay': 'isteamremoteplay.h', 'ISteamParties': 'isteamparties.h',
        'ISteamTimeline': 'isteamtimeline.h',
    }.get(cls, cls.lower()[1:] + '.h')


if __name__ == '__main__':
    main()
