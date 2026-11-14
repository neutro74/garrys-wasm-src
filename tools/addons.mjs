#!/usr/bin/env node
// Manage uploaded addons from the command line: node tools/addons.mjs add <file.gma|folder>... | list | remove <name>
// Addons live in the game's overlay (GMOD_OVERLAY, default run/overlay) under garrysmod/addons; the Steam install
// is never modified.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const overlay = process.env.GMOD_OVERLAY || fileURLToPath(new URL('../run/overlay', import.meta.url));
const dir = path.join(overlay, 'garrysmod', 'addons');
const [cmd, ...args] = process.argv.slice(2);

const size = (p) => { const st = fs.statSync(p); return st.isDirectory() ? fs.readdirSync(p).reduce((n, f) => n + size(path.join(p, f)), 0) : st.size; };
const kb = (n) => (n > 1 << 20 ? `${(n / (1 << 20)).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);

if (cmd === 'add' && args.length) {
  fs.mkdirSync(dir, { recursive: true });
  for (const src of args) {
    if (!fs.existsSync(src)) { console.error(`no such file: ${src}`); process.exitCode = 1; continue; }
    const st = fs.statSync(src), name = path.basename(src);
    if (!st.isDirectory() && !name.toLowerCase().endsWith('.gma')) { console.error(`${name}: only .gma files and addon folders are supported`); process.exitCode = 1; continue; }
    fs.cpSync(src, path.join(dir, name), { recursive: true });
    console.log(`added ${name} (${kb(size(src))})`);
  }
} else if (cmd === 'list') {
  for (const n of fs.existsSync(dir) ? fs.readdirSync(dir).sort() : []) console.log(`${n}\t${kb(size(path.join(dir, n)))}`);
} else if (cmd === 'remove' && args.length) {
  for (const n of args) { if (n.includes('/') || n === '..') { console.error(`bad name ${n}`); continue; } fs.rmSync(path.join(dir, n), { recursive: true, force: true }); console.log(`removed ${n}`); }
} else {
  console.error('usage: addons.mjs add <file.gma|folder>... | list | remove <name>...'); process.exitCode = 1;
}
