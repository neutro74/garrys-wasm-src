#!/usr/bin/env node
// Turns a READ_LOG (web/server.mjs) from one boot-to-map run into tools/cdn-warm.json: the ordered [path, chunkIndex]
// pairs of game data the run read. package-cdn.mjs lists those chunks in the CDN manifest so the page can fetch
// them in parallel instead of the engine paying one network round trip per chunk.
//   READ_LOG=run/reads.log node tools/web-profile.mjs ...; node tools/make-warm-list.mjs run/reads.log dist/gmod-web
import fs from 'node:fs';import path from 'node:path';import {fileURLToPath} from 'node:url';
const [log,pkg]=process.argv.slice(2);if(!log||!pkg){console.error('usage: make-warm-list.mjs reads.log package-dir');process.exit(1);}
const CHUNK=1048576,MAX_WHOLE=8*CHUNK;   // an unranged read of a huge file is a probe/stream; do not warm all of it
const seen=new Set(),out=[];
// MERGE=1 keeps the existing list (and its order) and appends chunks this log adds, so several scenarios can be combined.
const dst=path.join(path.dirname(fileURLToPath(import.meta.url)),'cdn-warm.json');
if(process.env.MERGE&&fs.existsSync(dst))for(const [rel,c] of JSON.parse(fs.readFileSync(dst,'utf8'))){seen.add(rel+'\t'+c);out.push([rel,c]);}
const before=out.length;
const size=rel=>{try{return fs.statSync(path.join(pkg,rel)).size;}catch{return -1;}};
for(const line of fs.readFileSync(log,'utf8').split('\n')){
 const [url,range]=line.split('\t');if(!url)continue;
 const rel=url.replace(/^\//,'');
 if(!/^(game|asset|out)\//.test(rel)||/\.(m?js)$/.test(rel))continue;
 const total=size(rel);if(total<=0)continue;
 let start=0,end=total-1;
 const m=/^bytes=(\d*)-(\d*)$/.exec(range??'');
 if(m){if(m[1]===''){start=Math.max(0,total-Number(m[2]));}else{start=Number(m[1]);if(m[2])end=Math.min(end,Number(m[2]));}}
 else if(total>MAX_WHOLE)continue;
 if(start>end)continue;
 for(let c=Math.floor(start/CHUNK);c<=Math.floor(end/CHUNK);c++){const key=rel+'\t'+c;if(!seen.has(key)){seen.add(key);out.push([rel,c]);}}
}
fs.writeFileSync(dst,JSON.stringify(out));
console.log(JSON.stringify({chunks:out.length,added:out.length-before}));
