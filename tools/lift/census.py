#!/usr/bin/env python3
"""Instruction census + function discovery for 32-bit PE DLLs.

Discovers function entry points from exports, relocation-tagged pointers into .text (vtables, callbacks),
and direct call targets reachable from those; then recursively disassembles them. Reports mnemonic
frequency and how many functions/instructions a given supported-set covers.
"""
import argparse, collections, json, struct, sys
import pefile
from capstone import Cs, CS_ARCH_X86, CS_MODE_32, CS_GRP_JUMP, CS_GRP_CALL, CS_GRP_RET, CS_OP_IMM, CS_OP_MEM
from capstone.x86 import X86_OP_IMM, X86_OP_MEM


class Image:
    def __init__(self, path):
        self.pe = pe = pefile.PE(path)
        self.base = pe.OPTIONAL_HEADER.ImageBase
        self.size = pe.OPTIONAL_HEADER.SizeOfImage
        self.mem = bytearray(self.size)
        self.mem[:len(pe.header)] = pe.header
        self.text = []
        for s in pe.sections:
            data = s.get_data()
            self.mem[s.VirtualAddress:s.VirtualAddress + len(data)] = data
            if s.Characteristics & 0x20000000:
                self.text.append((s.VirtualAddress, s.VirtualAddress + max(s.Misc_VirtualSize, len(data))))
        self.relocs = []
        for rel in getattr(pe, 'DIRECTORY_ENTRY_BASERELOC', []):
            for e in rel.entries:
                if e.type == 3:  # HIGHLOW
                    self.relocs.append(e.rva)
        self.reloc_set = set(self.relocs)

    def rebase(self, new_base):
        """Apply base relocations so the image is valid at new_base; code immediates become final."""
        delta = (new_base - self.base) & 0xffffffff
        for rva in self.relocs:
            struct.pack_into('<I', self.mem, rva, (self.u32(rva) + delta) & 0xffffffff)
        self.pref_base = self.base
        self.base = new_base

    def in_text(self, va):
        rva = va - self.base
        return any(a <= rva < b for a, b in self.text)

    def u32(self, rva):
        return struct.unpack_from('<I', self.mem, rva)[0]


def tls_metadata(img):
    """Return relocated PE static TLS template, index location and callbacks."""
    entry = getattr(img.pe, 'DIRECTORY_ENTRY_TLS', None)
    if not entry:
        return None
    t = entry.struct
    preferred = img.pe.OPTIONAL_HEADER.ImageBase
    def relocated(va):
        return img.base + va - preferred if va else 0
    start, end = relocated(t.StartAddressOfRawData), relocated(t.EndAddressOfRawData)
    index, cb = relocated(t.AddressOfIndex), relocated(t.AddressOfCallBacks)
    if end < start or (start and not (img.base <= start <= end <= img.base + img.size)):
        raise ValueError('invalid PE TLS template')
    if not (img.base <= index <= img.base + img.size - 4):
        raise ValueError('invalid PE TLS index')
    callbacks = []
    if cb:
        for offset in range(0, 4096, 4):
            rva = cb - img.base + offset
            if rva < 0 or rva + 4 > img.size:
                raise ValueError('invalid PE TLS callback array')
            addr = img.u32(rva)
            if not addr:
                break
            callbacks.append(addr)
        else:
            raise ValueError('unterminated PE TLS callback array')
    return dict(start=start, size=end - start, zero_fill=t.SizeOfZeroFill,
                index=index, callbacks=callbacks)


def discover(img: Image):
    seeds = set()
    for sym in getattr(getattr(img.pe, 'DIRECTORY_ENTRY_EXPORT', None), 'symbols', []):
        if img.in_text(img.base + sym.address):
            seeds.add(img.base + sym.address)
    if img.pe.OPTIONAL_HEADER.AddressOfEntryPoint:
        seeds.add(img.base + img.pe.OPTIONAL_HEADER.AddressOfEntryPoint)
    ptr_seeds = 0
    for rva in img.relocs:
        v = img.u32(rva)
        if img.in_text(v):
            # Pointer *stored* in data (not in code operand): vtable slot / callback / jump-table entry.
            seeds.add(v)   # data pointer (vtable/callback) or relocated immediate in code (push offset fn)
            ptr_seeds += 1
    return seeds, ptr_seeds


def sweep(img: Image, seeds):
    md = Cs(CS_ARCH_X86, CS_MODE_32)
    md.detail = True
    funcs = {}
    work = list(seeds)
    seen_insn = collections.Counter()
    seen_pcs = set()
    while work:
        f = work.pop()
        if f in funcs:
            continue
        funcs[f] = []
        blocks = [f]
        visited = set()
        while blocks:
            pc = blocks.pop()
            while pc not in visited and pc not in seen_pcs and img.in_text(pc):
                visited.add(pc)
                rva = pc - img.base
                insns = list(md.disasm(bytes(img.mem[rva:rva + 16]), pc, 1))
                if not insns:
                    break
                i = insns[0]
                seen_pcs.add(pc)
                seen_insn[i.mnemonic] += 1
                funcs[f].append(pc)
                op0 = i.operands[0] if i.operands else None
                if CS_GRP_CALL in i.groups:
                    if op0 is not None and op0.type == X86_OP_IMM and img.in_text(op0.imm):
                        work.append(op0.imm)
                elif CS_GRP_JUMP in i.groups:
                    if op0 is not None and op0.type == X86_OP_IMM and img.in_text(op0.imm):
                        blocks.append(op0.imm)
                    if i.mnemonic == 'jmp':
                        break
                    # jmp [table + reg*4] -> unresolved here; handled by lifter via reloc'd jump tables
                elif CS_GRP_RET in i.groups or i.mnemonic in ('int3', 'ud2', 'hlt'):
                    break
                pc += i.size
    return funcs, seen_insn


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('dll')
    ap.add_argument('--top', type=int, default=60)
    ap.add_argument('--json')
    a = ap.parse_args()
    img = Image(a.dll)
    seeds, ptr = discover(img)
    funcs, insns = sweep(img, seeds)
    counts = insns
    groups = collections.Counter()
    for m, c in counts.items():
        k = 'x87' if m.startswith('f') and m not in ('fs',) else 'sse/avx' if (
            m.endswith(('ss', 'sd', 'ps', 'pd')) or m.startswith(('p', 'v', 'mov', 'cvt'))) and m not in (
            'push', 'pop', 'popal', 'pushal', 'pushfd', 'popfd', 'movsb', 'movsd', 'movsw', 'movzx', 'movsx', 'mov', 'movs', 'pause') else 'integer'
        groups[k] += c
    total = sum(counts.values())
    print(f'{a.dll}: {len(img.relocs):,} relocs, {len(seeds):,} seeds ({ptr:,} data-pointer), '
          f'{len(funcs):,} functions, {total:,} instructions, {len(counts)} distinct mnemonics')
    print('  groups:', dict(groups))
    cum = 0
    for m, c in counts.most_common(a.top):
        cum += c
        print(f'  {m:<10}{c:>9,}  cum {cum / total:6.1%}')
    if a.json:
        json.dump({'functions': len(funcs), 'instructions': total, 'mnemonics': counts}, open(a.json, 'w'))


if __name__ == '__main__':
    main()
