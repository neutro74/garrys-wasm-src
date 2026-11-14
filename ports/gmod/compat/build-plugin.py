#!/usr/bin/env python3
"""Build an independent Source side module using the current HL2 compile ABI."""
from pathlib import Path
import json, os, subprocess
here=Path(__file__).resolve().parent
engine=here.parents[2]/'engine'
subprocess.run(['python3',str(here/'build.py')],check=True)
out=here/'build'
emcc=os.environ.get('EMCC',str(Path.home()/'.emsdk/upstream/emscripten/emcc'))
sources=[p for p in sorted((out/'lua').glob('*.c')) if p.name not in ('linit.c','liolib.c','loslib.c','loadlib.c')]
objects=[]
for p in [here/'runtime.c',*sources]:
    obj=out/(p.stem+'.o')
    subprocess.run([emcc,'-O2','-fPIC','-pthread','-I'+str(out/'lua'),'-c',str(p),'-o',str(obj)],check=True)
    objects.append(str(obj))
commands=json.loads((engine/'build/compile_commands.json').read_text())
entry=next(x for x in commands if x['file'].endswith('/baseentity.cpp'))
flags=[]
for arg in entry['arguments'][1:]:
    if arg in ('-c','-MMD') or arg==entry['file'] or arg.startswith('-o'):continue
    if arg.startswith('-Dstrncpy=') or arg.startswith('-D_snprintf='):continue
    flags.append(arg)
obj=out/'source-plugin.o'
subprocess.run([emcc.replace('emcc','em++'),'-I'+str(out/'lua'),*flags,'-c',str(here/'source-plugin.cpp'),'-o',str(obj)],cwd=entry['directory'],check=True)
subprocess.run([emcc.replace('emcc','em++'),'-O2','-pthread','-sSIDE_MODULE=1','-sSHARED_MEMORY=1',str(obj),*objects,'-o',str(out/'gamebox_gmod.so')],check=True)
