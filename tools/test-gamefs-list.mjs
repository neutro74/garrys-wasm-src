import assert from 'node:assert/strict';
import fs from 'node:fs';import os from 'node:os';import path from 'node:path';
import {GameFs} from './lib/vpk.mjs';
const root=fs.mkdtempSync(path.join(os.tmpdir(),'gmod-assets-'));
try{
 const first=path.join(root,'first'),second=path.join(root,'second'),name='html/js/menu/menuApp.js';
 for(const dir of [first,second])fs.mkdirSync(path.join(dir,'html/js/menu'),{recursive:true});
 fs.writeFileSync(path.join(first,name),'primary');fs.writeFileSync(path.join(second,'html/js/menu/MENUAPP.js'),'secondary');
 const content=new GameFs([first,second]),files=content.list('').filter(n=>n.toLowerCase().endsWith('menuapp.js'));
 assert.deepEqual(files,[name],'asset enumeration must preserve the actual loose-file spelling and search priority');
 assert.equal(content.read(files[0]).toString(),'primary','every listed loose asset must be readable for packaging');
 assert.ok(content.list('html/js/menu').includes(name));
 console.log('PASS mixed-case loose asset enumeration, readable export paths and folder priority');
}finally{fs.rmSync(root,{recursive:true,force:true});}
