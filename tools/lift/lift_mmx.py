"""MMX lifting onto wasm SIMD.

mm0-7 are v128 locals ($mm0..) holding the register in lane 0; the upper lane is ignored. They are local to the
lifted function: the retail DLLs use MMX inside self-contained routines (the engine's audio mixer) and never pass
MMX values between functions. x87/MMX aliasing (FPU tag word, fsave of mm state) is not modelled; emms is a no-op.
"""
from capstone.x86 import X86_OP_IMM, X86_OP_REG
from lift import Unsupported
from lift_ext import xr

ZERO = '(v128.const i32x4 0 0 0 0)'

# Shuffles that give MMX results from SSE2-width operations. The SSE2 packs produce eight lanes per operand where
# MMX produces four, so take bytes 0-3 of each half; MMX punpckh interleaves the high 32 bits of 64-bit operands.
PACK_LO = '0 1 2 3 8 9 10 11 0 0 0 0 0 0 0 0'
UNPACK_HI = {1: '4 20 5 21 6 22 7 23 0 0 0 0 0 0 0 0', 2: '4 5 20 21 6 7 22 23 0 0 0 0 0 0 0 0',
             4: '4 5 6 7 20 21 22 23 0 0 0 0 0 0 0 0'}
HIGH_HALVES = '2 3 6 7 10 11 14 15 0 0 0 0 0 0 0 0'

LANEWISE = {
    'pand': 'v128.and', 'por': 'v128.or', 'pxor': 'v128.xor',
    'paddb': 'i8x16.add', 'paddw': 'i16x8.add', 'paddd': 'i32x4.add', 'paddq': 'i64x2.add',
    'psubb': 'i8x16.sub', 'psubw': 'i16x8.sub', 'psubd': 'i32x4.sub', 'psubq': 'i64x2.sub',
    'paddsb': 'i8x16.add_sat_s', 'paddsw': 'i16x8.add_sat_s', 'paddusb': 'i8x16.add_sat_u', 'paddusw': 'i16x8.add_sat_u',
    'psubsb': 'i8x16.sub_sat_s', 'psubsw': 'i16x8.sub_sat_s', 'psubusb': 'i8x16.sub_sat_u', 'psubusw': 'i16x8.sub_sat_u',
    'pcmpeqb': 'i8x16.eq', 'pcmpeqw': 'i16x8.eq', 'pcmpeqd': 'i32x4.eq',
    'pcmpgtb': 'i8x16.gt_s', 'pcmpgtw': 'i16x8.gt_s', 'pcmpgtd': 'i32x4.gt_s',
    'pmullw': 'i16x8.mul', 'pminub': 'i8x16.min_u', 'pmaxub': 'i8x16.max_u',
    'pminsw': 'i16x8.min_s', 'pmaxsw': 'i16x8.max_s', 'pavgb': 'i8x16.avgr_u', 'pavgw': 'i16x8.avgr_u',
}
PACKS = {'packsswb': 'i8x16.narrow_i16x8_s', 'packuswb': 'i8x16.narrow_i16x8_u', 'packssdw': 'i16x8.narrow_i32x4_s'}
SHIFTS = {'psrlq': 'i64x2.shr_u', 'psllq': 'i64x2.shl', 'psrld': 'i32x4.shr_u', 'pslld': 'i32x4.shl',
          'psrlw': 'i16x8.shr_u', 'psllw': 'i16x8.shl', 'psrad': 'i32x4.shr_s', 'psraw': 'i16x8.shr_s'}


def mm(f, op):
    """Name of an mm register operand, or None."""
    if op.type == X86_OP_REG:
        n = f.md.reg_name(op.reg)
        if n.startswith('mm'):
            return n
    return None


def is_mmx(f, ops):
    return any(mm(f, op) for op in ops)


def src(f, op):
    """Source operand as v128 with its 64-bit value in lane 0: mm or xmm register, or m64."""
    n = mm(f, op) or xr(f, op)
    if n:
        return f'(local.get ${n})'
    return f'(v128.load64_zero {f.addr(op)})'


def low64(v):
    return f'(i64x2.replace_lane 0 {ZERO} (i64x2.extract_lane 0 {v}))'


def mmx_insn(f, mn, ops):
    d = ops[0]
    s = ops[1] if len(ops) > 1 else None
    dn = mm(f, d)
    D = f'(local.get ${dn})' if dn else None

    def put(v):
        return [f'(local.set ${dn} {v})']

    if mn == 'movd':
        if dn:
            return put(f'(i32x4.replace_lane 0 {ZERO} {f.load(s, 4)})'), None
        return [f.store(d, f'(i32x4.extract_lane 0 (local.get ${mm(f, s)}))', 4)], None
    if mn == 'movq':
        if dn:
            return put(low64(src(f, s))), None
        if xr(f, d):                                          # movq2dq decodes as movq xmm, mm
            return [f'(local.set ${xr(f, d)} {low64(src(f, s))})'], None
        return [f'(i64.store {f.addr(d)} (i64x2.extract_lane 0 (local.get ${mm(f, s)})))'], None
    if mn == 'movdq2q':
        return put(low64(src(f, s))), None
    if mn == 'movntq':
        return [f'(i64.store {f.addr(d)} (i64x2.extract_lane 0 (local.get ${mm(f, s)})))'], None
    if mn in LANEWISE:
        return put(f'({LANEWISE[mn]} {D} {src(f, s)})'), None
    if mn == 'pandn':
        return put(f'(v128.andnot {src(f, s)} {D})'), None
    if mn in ('pmulhw', 'pmulhuw'):
        # 16x16 products of the four words, keeping each product's high half.
        sign = 's' if mn == 'pmulhw' else 'u'
        return put(f'(i8x16.shuffle {HIGH_HALVES} (i32x4.extmul_low_i16x8_{sign} {D} {src(f, s)}) {ZERO})'), None
    if mn == 'pmaddwd':
        return put(f'(i32x4.dot_i16x8_s {D} {src(f, s)})'), None
    if mn == 'pmuludq':
        return put(f'(i64x2.extmul_low_i32x4_u {D} {src(f, s)})'), None
    if mn in PACKS:
        return put(f'(i8x16.shuffle {PACK_LO} ({PACKS[mn]} {D} {src(f, s)}) {ZERO})'), None
    if mn in ('punpcklbw', 'punpcklwd', 'punpckldq'):
        e = {'bw': 1, 'wd': 2, 'dq': 4}[mn[-2:]]
        lanes = []
        for k in range(4 // e):
            lanes += [k * e + b for b in range(e)] + [16 + k * e + b for b in range(e)]
        lanes += [0] * (16 - len(lanes))
        return put(f'(i8x16.shuffle {" ".join(map(str, lanes))} {D} {src(f, s)})'), None
    if mn in ('punpckhbw', 'punpckhwd', 'punpckhdq'):
        e = {'bw': 1, 'wd': 2, 'dq': 4}[mn[-2:]]
        return put(f'(i8x16.shuffle {UNPACK_HI[e]} {D} {src(f, s)})'), None
    if mn in SHIFTS:
        bits = {'q': 64, 'd': 32, 'w': 16}[mn[-1]]
        o = []
        if s.type == X86_OP_IMM:
            cnt = f'(i32.const {s.imm & 0xff})'
        else:
            # The count is the whole 64-bit source operand; any count >= the lane width saturates.
            o.append(f'(local.set $t64 (i64x2.extract_lane 0 {src(f, s)}))')
            cnt = '(select (i32.const 255) (i32.wrap_i64 (local.get $t64)) (i64.gt_u (local.get $t64) (i64.const 255)))'
        if SHIFTS[mn].endswith('shr_s'):
            o += put(f'({SHIFTS[mn]} {D} (select (i32.const {bits - 1}) {cnt} (i32.ge_u {cnt} (i32.const {bits}))))')
        else:
            o += put(f'(select {ZERO} ({SHIFTS[mn]} {D} {cnt}) (i32.ge_u {cnt} (i32.const {bits})))')
        return o, None
    if mn == 'pshufw':
        imm = ops[2].imm
        lanes = []
        for k in range(4):
            w = ((imm >> (2 * k)) & 3) * 2
            lanes += [w, w + 1]
        lanes += [0] * 8
        return [f'(local.set $tv {src(f, s)})',
                f'(local.set ${dn} (i8x16.shuffle {" ".join(map(str, lanes))} (local.get $tv) (local.get $tv)))'], None
    if mn == 'pextrw':
        return [f.store(d, f'(i16x8.extract_lane_u {ops[2].imm & 3} (local.get ${mm(f, s)}))')], None
    if mn == 'pinsrw':
        v = f.load(s, 2) if s.type != X86_OP_REG else f.rget(f.reg_name(s.reg))
        return put(f'(i16x8.replace_lane {ops[2].imm & 3} {D} {v})'), None
    if mn in ('cvttps2pi', 'cvtps2pi'):
        # Two floats to two int32; out-of-range and NaN give the x86 "integer indefinite" 0x80000000.
        v = src(f, s) if mn == 'cvttps2pi' else f'(f32x4.nearest {src(f, s)})'
        big = '(v128.const f32x4 2147483648 2147483648 2147483648 2147483648)'
        return [f'(local.set $tv {v})',
                f'(local.set ${dn} (v128.bitselect (v128.const i32x4 0x80000000 0x80000000 0x80000000 0x80000000) '
                f'(i32x4.trunc_sat_f32x4_s (local.get $tv)) (v128.or (f32x4.ne (local.get $tv) (local.get $tv)) '
                f'(f32x4.ge (f32x4.abs (local.get $tv)) {big}))))'], None
    if mn == 'cvtpi2ps':                                       # two int32 into the low two floats; lanes 2-3 kept
        x = xr(f, d)
        return [f'(local.set ${x} (i64x2.replace_lane 0 (local.get ${x}) (i64x2.extract_lane 0 (f32x4.convert_i32x4_s {src(f, s)}))))'], None
    if mn == 'cvtpi2pd':
        return [f'(local.set ${xr(f, d)} (f64x2.convert_low_i32x4_s {src(f, s)}))'], None
    if mn == 'pmovmskb':
        return [f.store(d, f'(i32.and (i8x16.bitmask (local.get ${mm(f, s)})) (i32.const 0xff))')], None
    raise Unsupported('mmx ' + mn)
