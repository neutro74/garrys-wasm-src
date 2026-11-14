import assert from 'node:assert/strict';
import {launchOptions} from '../web/launch-config.mjs';
const first=launchOptions({});
assert.deepEqual(first.size,[960,540]);
assert.match(first.command,/-w 960 -h 540/);
assert.match(first.command,/\+fps_max 0(?: |$)/);
assert.equal(first.targetFps,30);
assert.match(first.command,/\+r_shadows 0(?: |$)/);
assert.match(first.command,/\+r_flashlightdepthres 512(?: |$)/);
const saved=launchOptions({saved:true});
assert.doesNotMatch(saved.command,/-w \d+|-h \d+|\+r_shadows /);
assert.ok(launchOptions({cmdline:'+fps_max 60'}).command.endsWith('+fps_max 60'));
console.log('PASS Chromebook first-run settings, saved preferences and overrides');

assert.equal(launchOptions({cmdline:'+fps_max 60'}).targetFps,0);
