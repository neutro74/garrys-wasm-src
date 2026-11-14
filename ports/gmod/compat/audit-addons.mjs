// Offline audit: parses real installed GMA archives and compiles their Lua text.
// Compilation success is not an assertion that an addon runs or has its APIs bound.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {readGma} from './addons.mjs';
import {createRealm} from './host.mjs';
const root=process.argv[2] || path.join(os.homedir(),'.local/share/Steam/steamapps/workshop/content/4000');
const output=path.join(import.meta.dirname,'evidence');fs.mkdirSync(output,{recursive:true});
const r=await createRealm('server',{print:()=>{},printErr:()=>{}});
const report={kind:'archive-and-syntax-only',addons:[],luaFiles:0,compiled:0,errors:0};
function walk(dir) {
  const out=[];
  for(const entry of fs.readdirSync(dir,{withFileTypes:true}).sort((a,b)=>a.name.localeCompare(b.name))) {
    const file=path.join(dir,entry.name);
    if(entry.isDirectory())out.push(...walk(file));else if(entry.isFile()&&file.endsWith('.gma'))out.push(file);
  }
  return out;
}
try {
  for(const file of walk(root)) {
    const entry={id:path.basename(path.dirname(file)),file:path.basename(file),bytes:fs.statSync(file).size,compiled:0,syntaxErrors:[]};
    report.addons.push(entry);
    if(entry.bytes>256*1024*1024){entry.archiveError='exceeds current 256 MiB in-memory archive limit';continue;}
    try {
      const addon=readGma(fs.readFileSync(file));entry.name=addon.name;entry.files=addon.files.size;
      for(const [name,data] of addon.files)if(name.endsWith('.lua')) {
        report.luaFiles++;
        try {r.compile(new TextDecoder().decode(data),'@'+name);entry.compiled++;report.compiled++;}
        catch(error){entry.syntaxErrors.push({path:name,error:error.message});report.errors++;}
      }
    }catch(error){entry.archiveError=error.message;}
    console.log(`${entry.id}: ${entry.name||entry.file}: ${entry.archiveError||`${entry.compiled} Lua compiled; ${entry.syntaxErrors.length} syntax failures`}`);
    if(entry.syntaxErrors.length)console.log('  '+entry.syntaxErrors[0].error);
    fs.writeFileSync(path.join(output,'addon-audit.json'),JSON.stringify(report,null,2));
  }
}finally{r.close();}
fs.writeFileSync(path.join(output,'addon-audit.json'),JSON.stringify(report,null,2));
console.log(JSON.stringify({addons:report.addons.length,archiveFailures:report.addons.filter(a=>a.archiveError).length,luaFiles:report.luaFiles,compiled:report.compiled,syntaxErrors:report.errors}));
