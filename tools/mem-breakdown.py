#!/usr/bin/env python3
"""Resident + swapped memory of the game renderer (largest test-profile renderer) by mapping kind, from /proc/<pid>/smaps."""
import collections, os, re, subprocess, sys
profile = os.path.realpath(sys.argv[1] if len(sys.argv) > 1 else 'run/chrome-profile')
pids = [p for p in subprocess.run(['pgrep', '-f', '--', f'--type=renderer.*user-data-dir={profile}'], capture_output=True, text=True).stdout.split()]
def rss(p):
    try: return int(re.search(r'VmRSS:\s+(\d+)', open(f'/proc/{p}/status').read()).group(1))
    except Exception: return 0
if not pids: sys.exit('no test renderer')
pid = max(pids, key=rss)
kinds = collections.Counter(); counts = collections.Counter()
cur = None
for line in open(f'/proc/{pid}/smaps'):
    if re.match(r'^[0-9a-f]+-[0-9a-f]+ ', line):
        f = line.split(); lo, hi = (int(x, 16) for x in f[0].split('-')); size = hi - lo
        perm, name = f[1], (f[5] if len(f) > 5 else '')
        if size >= 3 << 30: cur = 'guest memory (4 GiB wasm memory)'
        elif 'x' in perm and not name: cur = 'jit code (wasm/js, anonymous exec)'
        elif name.startswith('/memfd') or 'shmem' in name or name.startswith('/dev/shm'): cur = 'shared memory (memfd/shm)'
        elif name == '[heap]': cur = 'malloc heap'
        elif name.startswith('['): cur = name
        elif name: cur = 'file-backed (libraries, fonts)'
        else: cur = 'anonymous rw (V8 heaps, zones, worker stacks)'
        counts[cur] += 1
    elif (line.startswith('Rss:') or line.startswith('Swap:')) and cur:
        kinds[cur] += int(line.split()[1])
total = sum(kinds.values())
print(f'renderer {pid}: {total/1024:.0f} MB resident')
for k, v in kinds.most_common(): print(f'  {v/1024:7.0f} MB  {counts[k]:5d} maps  {k}')

# Contiguous regions (adjacent mappings merged) ranked by resident size: the guest's wasm memory is one region
# of ~4-10 GiB virtual, V8 code space another, worker heaps many small ones.
runs = []; cur = None
for line in open(f'/proc/{pid}/smaps'):
    if re.match(r'^[0-9a-f]+-[0-9a-f]+ ', line):
        f = line.split(); lo, hi = (int(x, 16) for x in f[0].split('-')); name = f[5] if len(f) > 5 else ''
        if cur and lo == cur[1] and not name: cur[1] = hi; cur[3].add(f[1])
        else:
            cur = [lo, hi, 0, {f[1]}, name]; runs.append(cur)
    elif line.startswith('Rss:') or line.startswith('Swap:'):
        cur[2] += int(line.split()[1])
print('largest regions:')
for lo, hi, r, perms, name in sorted(runs, key=lambda x: -x[2])[:8]:
    print(f'  {r/1024:7.0f} MB resident  {(hi-lo)/2**20:9.0f} MB virtual  {"/".join(sorted(perms))} {name}')
