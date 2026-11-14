// GMod mounts its HL2 content under sourceengine/, not hl2/. Keep this isolated
// from existing GameBox game packs while the compatibility port is developed.
import {GAMES,ContentIndex} from '../../../tools/lib/content.mjs';
import {shaderSets,compatibleShader} from '../../../tools/lib/shaders.mjs';

export class GModContent extends ContentIndex {
  constructor() {
    GAMES.gmod.tops=['garrysmod','sourceengine','platform'];
    super('gmod');
    this.overrides=new Map();
    for(const name of shaderSets().keys()) {
      const shader=compatibleShader(name);
      if(!shader)continue;
      for(const top of ['garrysmod','sourceengine'])
        this.sources.set(`${top}/shaders/fxc/${name}.vcs`,{vpk:shader.vpk,key:shader.key,shaderSet:shader.set});
    }
    // The native GMod g_sky shader/env_skypaint entity do not exist in HL2.
    // Use a real static Source sky until that renderer path is implemented.
    for(const face of ['rt','lf','ft','bk','up','dn'])
      this.overrides.set(`garrysmod/materials/skybox/painted${face}.vmt`,Buffer.from(`UnlitGeneric { "$basetexture" "skybox/sky_day01_09${face}" "$nofog" "1" "$ignorez" "1" }`));
  }
  read(file) {return this.overrides?.get(file.toLowerCase())??super.read(file);}
  size(file) {return this.overrides?.get(file.toLowerCase())?.length??super.size(file);}
}
