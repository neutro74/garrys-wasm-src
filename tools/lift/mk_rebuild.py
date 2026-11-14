#!/usr/bin/env python3
"""Writes run_some.sh: rebuild the named DLLs (taken from run_all.sh), four at a time."""
import sys

lines = open('run_all.sh').read().splitlines()
head = lines[:10]
out = head[:]
builds = []
for n in sys.argv[1:]:
    builds += [l.rstrip(' &') for l in lines if l.startswith(f'build {n} ')]
for i, b in enumerate(builds):
    out.append(b + ' &')
    if i % 4 == 3:
        out.append('wait')
out += ['wait', 'echo done > out/some.done']
open('run_some.sh', 'w').write('\n'.join(out) + '\n')
print(len(builds), 'builds')
