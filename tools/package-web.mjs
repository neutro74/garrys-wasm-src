#!/usr/bin/env node
// Build a standalone static directory from the local game install. No network downloads.
import fs from 'node:fs';import path from 'node:path';import {fileURLToPath} from 'node:url';
import {GameFs} from './lib/vpk.mjs';
import {isUserData} from './lib/user-data.mjs';
import {gameRoot} from './lib/game-root.mjs';
const repo=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
export function packageWeb({game,out,artifacts=path.join(repo,'tools/lift/out'),extended=false}){
 game=path.resolve(game);out=path.resolve(out);artifacts=path.resolve(artifacts);
 if(fs.existsSync(out))throw Error('Output already exists; choose a new output directory');
 if(!fs.existsSync(path.join(game,'garrysmod/maps/gm_construct.bsp')))throw Error('Missing gm_construct in game install');
 if(!fs.existsSync(path.join(artifacts,'launcher-thread.json')))throw Error('Lifted artifacts are missing');
 for(const input of [repo,game,artifacts])if(input===out||input.startsWith(out+path.sep))throw Error('Output contains an input directory');
 const copy=(a,b)=>{fs.mkdirSync(path.dirname(b),{recursive:true});fs.copyFileSync(a,b,fs.constants.COPYFILE_FICLONE);};
 const tree=(a,b,filter=()=>true)=>{fs.mkdirSync(b,{recursive:true});for(const e of fs.readdirSync(a,{withFileTypes:true})){if(!filter(e))continue;const src=path.join(a,e.name),dst=path.join(b,e.name);if(e.isDirectory())tree(src,dst,filter);else if(e.isFile())copy(src,dst);}};
 tree(path.join(repo,'web'),path.join(out,'web'),e=>e.name!=='dev'&&e.name!=='server.mjs');copy(path.join(repo,'web/index.html'),path.join(out,'index.html'));
 tree(path.join(repo,'runtime/d3d9'),path.join(out,'runtime/d3d9'),e=>e.isDirectory()||e.name.endsWith('.js'));
 tree(path.join(repo,'tools/lift/host'),path.join(out,'tools/lift/host'),e=>e.isDirectory()||e.name.endsWith('.mjs'));
 copy(path.join(repo,'tools/lib/vpk.mjs'),path.join(out,'tools/lib/vpk.mjs'));
 copy(path.join(repo,'ports/gmod/compat/addons.mjs'),path.join(out,'ports/gmod/compat/addons.mjs'));
 tree(artifacts,path.join(out,'out'),e=>e.isFile()&&/\.(wasm|json|img)$/.test(e.name));
 // Local addons stay out of the package (an empty addons folder remains).
 tree(game,path.join(out,'game'),e=>!(e.name.toLowerCase()==='addons'&&path.basename(e.parentPath??e.path??'').toLowerCase()==='garrysmod'));
 // Per-user state (addons, saved data, caches, databases, logs) is never packaged.
 for(const u of ['garrysmod/addons','garrysmod/data','garrysmod/cache','garrysmod/saves','garrysmod/screenshots','garrysmod/demos','garrysmod/dupes','garrysmod/download','garrysmod/downloads','garrysmod/workshop','garrysmod/GameState.txt','garrysmod/voice_ban.dt','chromium.log'])fs.rmSync(path.join(out,'game',u),{recursive:true,force:true});
 (function sweep(dir,rel){for(const e of fs.readdirSync(dir,{withFileTypes:true})){const r=rel?rel+'/'+e.name:e.name;if(isUserData(r))fs.rmSync(path.join(dir,e.name),{recursive:true,force:true});else if(e.isDirectory())sweep(path.join(dir,e.name),r);}})(path.join(out,'game'),'');
 for(const d of ['garrysmod/addons','garrysmod/data'])fs.mkdirSync(path.join(out,'game',d),{recursive:true});
 // The desktop config.cfg holds desktop-only settings (see web/server.mjs); the browser keeps its own.
 fs.rmSync(path.join(out,'game/garrysmod/cfg/config.cfg'),{force:true});
 const index=dir=>Object.fromEntries(fs.readdirSync(dir,{withFileTypes:true}).filter(e=>e.isFile()||e.isDirectory()).map(e=>[e.name,e.isDirectory()?index(path.join(dir,e.name)):fs.statSync(path.join(dir,e.name)).size]));
 fs.writeFileSync(path.join(out,'game-index.json'),JSON.stringify(index(path.join(out,'game'))));
 // Extended edition (tools/build-extended.mjs): other games' folders and public Workshop addons, served from game/ext/
 // and merged over the base index at run time. Only this tree may carry addons; the base game never does.
 const ext=path.join(repo,'packs/extended/root');
 if(extended&&fs.existsSync(ext)){tree(ext,path.join(out,'game/ext'));fs.writeFileSync(path.join(out,'game-ext-index.json'),JSON.stringify(index(ext)));}
 const content=new GameFs(['garrysmod','sourceengine','platform'].map(d=>path.join(game,d)));
 let assetCount=0;
 for(const name of content.list(''))if(/\.(png|jpe?g|gif|svg|ico|ttf|woff2?|css|js|html)$/i.test(name)&&!isUserData('garrysmod/'+name)){
  const data=content.read(name);if(!data)continue;const dst=path.join(out,'asset/garrysmod',name);fs.mkdirSync(path.dirname(dst),{recursive:true});fs.writeFileSync(dst,data);assetCount++;
 }
 // Thumbnails for every map the menu can list: base maps plus, in the Extended edition, the other games' maps and addon maps.
 const sources=[{content,dir:path.join(game,'garrysmod')}];
 if(extended&&fs.existsSync(ext))for(const g of fs.readdirSync(ext)){const gd=path.join(ext,g);if(!fs.statSync(gd).isDirectory())continue;const ad=path.join(gd,'addons');const dirs=[gd,...(fs.existsSync(ad)?fs.readdirSync(ad).map(a=>path.join(ad,a)):[])];for(const d of dirs)sources.push({content:new GameFs([d]),dir:d});}
 const done=new Set();
 for(const src of sources){
  const mapsDir=path.join(src.dir,'maps');
  const names=new Set(fs.existsSync(mapsDir)?fs.readdirSync(mapsDir).filter(n=>n.endsWith('.bsp')).map(n=>n.slice(0,-4)):[]);
  for(const n of src.content.list('maps')??[]){const m=/^maps\/([^/]+)\.bsp$/i.exec(n);if(m)names.add(m[1]);}
  for(const name of names){
   const data=src.content.read(`maps/thumb/${name}.png`)??src.content.read(`maps/thumb/${name}.jpg`)??content.read(`maps/thumb/${name}.png`)??content.read(`maps/thumb/${name}.jpg`);
   if(!data&&done.has(name))continue;
   const img=data??content.read('materials/gui/noicon.png')??fs.readFileSync(path.join(out,'asset/garrysmod/materials/gui/noicon.png'));
   if(data||!done.has(name)){done.add(name);for(const ext of ['', '.png','.jpg']){const dst=path.join(out,'asset/mapimage',name+ext);fs.mkdirSync(path.dirname(dst),{recursive:true});fs.writeFileSync(dst,img);}}
  }
 }
 copy(path.join(game,'garrysmod/resource/game.ico'),path.join(out,'favicon.ico'));
 fs.writeFileSync(path.join(out,'_headers'),'/*\n  Cross-Origin-Opener-Policy: same-origin\n  Cross-Origin-Embedder-Policy: require-corp\n  Cross-Origin-Resource-Policy: same-origin\n');
 fs.writeFileSync(path.join(out,'nginx.conf.example'),`server {\n  listen 443 ssl;\n  # Configure your certificate and root pointing to this directory.\n  include mime.types;\n  add_header Cross-Origin-Opener-Policy same-origin always;\n  add_header Cross-Origin-Embedder-Policy require-corp always;\n  add_header Cross-Origin-Resource-Policy same-origin always;\n  location / { try_files $uri $uri/ =404; }\n}\n`);
 fs.writeFileSync(path.join(out,'README.txt'),'Serve this directory over HTTPS with COOP same-origin and COEP require-corp. Range requests and application/wasm MIME support are required. _headers and nginx.conf.example are included. Game files are copied from your local installation. Local addons and saves use browser OPFS and are specific to the hosting origin. Use Local files to back them up before changing hosts. No server-side game process is required.\n');
 return {out,assets:assetCount,maps:maps.length};
}
if(process.argv[1]&&fileURLToPath(import.meta.url)===path.resolve(process.argv[1])){
 const args=process.argv.slice(2),arg=(name,defaultValue)=>{const at=args.indexOf('--'+name);return at<0?defaultValue:args[at+1];};
 try{console.log(JSON.stringify(packageWeb({game:arg('game',gameRoot()),out:arg('out',path.join(repo,'dist/gmod-web')),artifacts:arg('artifacts',path.join(repo,'tools/lift/out')),extended:args.includes('--with-extended')})));}catch(e){console.error(e.message);process.exitCode=1;}
}
