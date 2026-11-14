import assert from 'node:assert/strict';
import {WorkerBackend} from './lift/host/d3d9-backend.mjs';
const messages=[];
const backend=new WorkerBackend({postMessage:m=>messages.push(m)});
Atomics.store(backend.sync,0,2);
backend.ops=[{o:'present'}];backend.presented=[];
const load=Atomics.load,wait=Atomics.wait;
let raced=false,waits=0;
try {
 Atomics.load=(a,i)=>{
  const value=load(a,i);
  if(a===backend.sync&&i===1&&!raced){raced=true;Atomics.store(a,1,2);}
  return value;
 };
 Atomics.wait=(a,i,expected)=>{
  waits++;
  assert.notEqual(expected,load(a,i),'must not sleep after the renderer has already freed a slot');
  return 'not-equal';
 };
 backend.flush();
 assert.equal(waits,1);
 assert.equal(messages.at(-1).type,'ops');
} finally {Atomics.load=load;Atomics.wait=wait;}
console.log('PASS renderer finishing between the capacity check and wait cannot stall the guest');
