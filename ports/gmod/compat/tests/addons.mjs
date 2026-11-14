import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readGma, mountAddons, crc32 } from '../addons.mjs';

export function archive(entries, version=3) {
  const parts = [Buffer.from('GMAD'), Buffer.from([version]), Buffer.alloc(16), Buffer.from('\0Test\0{}\0Author\0'), Buffer.alloc(4)];
  entries.forEach(([name, text], i) => {
    const data=Buffer.from(text), header=Buffer.alloc(4), size=Buffer.alloc(12);
    header.writeUInt32LE(i+1); size.writeBigUInt64LE(BigInt(data.length)); size.writeUInt32LE(crc32(data),8);
    parts.push(header,Buffer.from(name+'\0'),size);
  });
  parts.push(Buffer.alloc(4), ...entries.map(([,text])=>Buffer.from(text)));
  const data=Buffer.concat(parts), footer=Buffer.alloc(4); footer.writeUInt32LE(crc32(data));
  return Buffer.concat([data,footer]);
}

test('reads GMA v3 payloads from sliced byte views and mounts ordered overlays', () => {
  const input=archive([['lua/autorun/test.lua','answer=42'],['materials/test.vmt','material']]);
  const padded=Buffer.concat([Buffer.alloc(7),input,Buffer.alloc(9)]);
  const a=readGma(padded.subarray(7,7+input.length));
  assert.equal(a.name,'Test');
  assert.equal(new TextDecoder().decode(a.files.get('lua/autorun/test.lua')),'answer=42');
  const mount=mountAddons([a,{name:'override',files:new Map([['lua/autorun/test.lua',new TextEncoder().encode('answer=43')]])}]);
  assert.equal(new TextDecoder().decode(mount.files.get('lua/autorun/test.lua')),'answer=43');
  assert.equal(mount.conflicts.length,1);
});

test('rejects traversal, duplicates, unsupported versions, truncation and bad CRCs', () => {
  for (const name of ['../evil','lua/../../evil','/root/evil','lua\\evil','C:/evil'])
    assert.throws(()=>readGma(archive([[name,'x']])), /path/);
  assert.throws(()=>readGma(archive([['lua/a.lua','x'],['LUA/A.lua','y']])), /duplicate/);
  assert.throws(()=>readGma(archive([],9)), /version/);
  const good=archive([['lua/a.lua','print(1)']]);
  for(const end of [0,5,20,good.length-5]) assert.throws(()=>readGma(good.subarray(0,end)), /truncated|magic|bounds/);
  const bad=Buffer.from(good); bad[bad.length-5]^=1;
  assert.throws(()=>readGma(bad), /CRC/);
});

test('accepts omitted zero CRC fields used by installed Workshop writers', () => {
  const bytes=archive([['lua/a.lua','x']]);
  const pathEnd=bytes.indexOf(0,bytes.indexOf(Buffer.from('lua/a.lua')));
  bytes.writeUInt32LE(0,pathEnd+1+8);
  bytes.writeUInt32LE(0,bytes.length-4);
  assert.equal(readGma(bytes).files.get('lua/a.lua')[0],120);
});

test('accepts legacy bytes in display metadata while keeping file paths strict', () => {
  const bytes=archive([['lua/a.lua','x']]);
  bytes[22]=0xff;bytes.writeUInt32LE(0,bytes.length-4);
  assert.equal(readGma(bytes).files.size,1);
});
