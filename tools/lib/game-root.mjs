import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const repo=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
// A Steam switch to Linux/64-bit must not mix those assets with older lifted PE
// modules when the matching Windows package is already available locally.
export function gameRoot({root=repo,env=process.env}={}){
 if(env.GMOD_ROOT)return env.GMOD_ROOT;
 const steam=path.join(env.HOME,'.steam/steam/steamapps/common/GarrysMod');
 const cached=path.join(root,'dist/gmod-web/game');
 const client=p=>fs.existsSync(path.join(p,'garrysmod/bin/client.dll'));
 return client(steam)||!client(cached)?steam:cached;
}
