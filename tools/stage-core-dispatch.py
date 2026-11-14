#!/usr/bin/env python3
"""Stage complete artifacts with a fast path for core-local indirect dispatch."""
import argparse,json,os,pathlib,shutil,subprocess,sys,tempfile
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent/'lift'))
from core_dispatch import transform
p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--out',required=True);a=p.parse_args()
src=pathlib.Path(a.source).resolve();out=pathlib.Path(a.out).resolve();out.mkdir(exist_ok=False)
files=set()
for m in src.glob('*-thread.json'):
 meta=json.loads(m.read_text());files.update([m.name,m.stem+'.img',meta['core'],*meta['chunks']])
for name in files:shutil.copy2(src/name,out/name)
opt=pathlib.Path(__file__).resolve().parent.parent/'third_party/binaryen/wasm-opt'
for dll in ['client','engine','matsys','shaderdx9','lua','tier0']:
 meta=out/(dll+'-thread.json');m=json.loads(meta.read_text());core=out/m['core']
 with tempfile.TemporaryDirectory(prefix='core-dispatch-') as temp:
  wat=pathlib.Path(temp)/'core.wat';patched=pathlib.Path(temp)/'patched.wat'
  def run(args):subprocess.run(['systemd-run','--user','--scope','--quiet','-p','MemoryMax=2200M','-p','MemorySwapMax=0','--','env','BINARYEN_CORES=2','taskset','-c','4-15',str(opt),*map(str,args),'--all-features'],check=True)
  run([core,'-S','-o',wat]);patched.write_text(transform(wat.read_text()));run([patched,'-o',core])
 m['core_dispatch_hash']=True;meta.write_text(json.dumps(m,indent=1)+'\n');print('PATCHED',dll,flush=True)
print('ALL_DONE',flush=True)
