"""SSE/SSE2 lifting onto wasm SIMD. xmm0-7 are v128 locals ($xmm0..); MXCSR is assumed round-to-nearest,
no-trap, DAZ/FTZ off."""
from capstone.x86 import X86_OP_IMM, X86_OP_MEM, X86_OP_REG
from lift import Unsupported
from lift_ext import xr, lane0_f32, lane0_f64, vec_src, trunc_i32
from lift_mmx import is_mmx, mmx_insn

ZERO = '(v128.const i32x4 0 0 0 0)'


def sse_insn(self, i, mn, ops):
    # MMX shares mnemonics with SSE but works on the 64-bit mm0-7 registers (lift_mmx.py).
    if is_mmx(self, ops):
        return mmx_insn(self, mn, ops)
    o = []
    d = ops[0] if ops else None
    s = ops[1] if len(ops) > 1 else None
    dn = xr(self, d) if d else None
    sn = xr(self, s) if s else None

    # ---- moves ----
    if mn in ('movaps', 'movups', 'movapd', 'movupd', 'movdqa', 'movdqu', 'lddqu', 'movntps', 'movntpd', 'movntdq'):
        if dn:
            return [f'(local.set ${dn} {vec_src(self, s)})'], None
        return [f'(v128.store align=1 {self.addr(d)} (local.get ${sn}))'], None
    if mn == 'movss':
        if dn and sn:
            return [f'(local.set ${dn} (f32x4.replace_lane 0 (local.get ${dn}) (f32x4.extract_lane 0 (local.get ${sn}))))'], None
        if dn:
            return [f'(local.set ${dn} (v128.load32_zero {self.addr(s)}))'], None
        return [f'(f32.store {self.addr(d)} (f32x4.extract_lane 0 (local.get ${sn})))'], None
    if mn == 'movsd' and (dn or sn):
        if dn and sn:
            return [f'(local.set ${dn} (f64x2.replace_lane 0 (local.get ${dn}) (f64x2.extract_lane 0 (local.get ${sn}))))'], None
        if dn:
            return [f'(local.set ${dn} (v128.load64_zero {self.addr(s)}))'], None
        return [f'(f64.store {self.addr(d)} (f64x2.extract_lane 0 (local.get ${sn})))'], None
    if mn == 'movd':
        if dn:
            return [f'(local.set ${dn} (i32x4.replace_lane 0 {ZERO} {self.load(s, 4)}))'], None
        return [self.store(d, f'(i32x4.extract_lane 0 (local.get ${sn}))', 4)], None
    if mn == 'movq' and (dn or sn):
        if dn and sn:
            return [f'(local.set ${dn} (i64x2.replace_lane 0 {ZERO} (i64x2.extract_lane 0 (local.get ${sn}))))'], None
        if dn:
            return [f'(local.set ${dn} (v128.load64_zero {self.addr(s)}))'], None
        return [f'(i64.store {self.addr(d)} (i64x2.extract_lane 0 (local.get ${sn})))'], None
    if mn in ('movlps', 'movlpd', 'movhps', 'movhpd'):
        lane = 0 if 'l' in mn[3] else 1
        if dn and not sn:
            return [f'(local.set ${dn} (i64x2.replace_lane {lane} (local.get ${dn}) (i64.load {self.addr(s)})))'], None
        if dn and sn:  # movhlps/movlhps encodings decode as movlps/movhps reg,reg
            return [f'(local.set ${dn} (i64x2.replace_lane {lane} (local.get ${dn}) (i64x2.extract_lane {1 - lane} (local.get ${sn}))))'], None
        return [f'(i64.store {self.addr(d)} (i64x2.extract_lane {lane} (local.get ${sn})))'], None
    if mn == 'movhlps':
        return [f'(local.set ${dn} (i64x2.replace_lane 0 (local.get ${dn}) (i64x2.extract_lane 1 (local.get ${sn}))))'], None
    if mn == 'movlhps':
        return [f'(local.set ${dn} (i64x2.replace_lane 1 (local.get ${dn}) (i64x2.extract_lane 0 (local.get ${sn}))))'], None
    # ---- scalar float arithmetic ----
    sc = {'addss': 'add', 'subss': 'sub', 'mulss': 'mul', 'divss': 'div'}
    if mn in sc:
        r = f'(f32.{sc[mn]} (f32x4.extract_lane 0 (local.get ${dn})) {lane0_f32(self, s)})'
        return [f'(local.set ${dn} (f32x4.replace_lane 0 (local.get ${dn}) {r}))'], None
    sd = {'addsd': 'add', 'subsd': 'sub', 'mulsd': 'mul', 'divsd': 'div'}
    if mn in sd:
        r = f'(f64.{sd[mn]} (f64x2.extract_lane 0 (local.get ${dn})) {lane0_f64(self, s)})'
        return [f'(local.set ${dn} (f64x2.replace_lane 0 (local.get ${dn}) {r}))'], None
    if mn in ('minss', 'maxss'):
        o.append(f'(local.set $ts {lane0_f32(self, s)})')
        a = f'(f32x4.extract_lane 0 (local.get ${dn}))'
        cmp = 'lt' if mn == 'minss' else 'gt'
        r = f'(select {a} (local.get $ts) (f32.{cmp} {a} (local.get $ts)))'
        o.append(f'(local.set ${dn} (f32x4.replace_lane 0 (local.get ${dn}) {r}))')
        return o, None
    if mn in ('minsd', 'maxsd'):
        o.append(f'(local.set $tf {lane0_f64(self, s)})')
        a = f'(f64x2.extract_lane 0 (local.get ${dn}))'
        cmp = 'lt' if mn == 'minsd' else 'gt'
        r = f'(select {a} (local.get $tf) (f64.{cmp} {a} (local.get $tf)))'
        o.append(f'(local.set ${dn} (f64x2.replace_lane 0 (local.get ${dn}) {r}))')
        return o, None
    if mn == 'sqrtss':
        return [f'(local.set ${dn} (f32x4.replace_lane 0 (local.get ${dn}) (f32.sqrt {lane0_f32(self, s)})))'], None
    if mn == 'sqrtsd':
        return [f'(local.set ${dn} (f64x2.replace_lane 0 (local.get ${dn}) (f64.sqrt {lane0_f64(self, s)})))'], None
    if mn == 'rsqrtss':
        return [f'(local.set ${dn} (f32x4.replace_lane 0 (local.get ${dn}) (f32.div (f32.const 1) (f32.sqrt {lane0_f32(self, s)}))))'], None
    if mn == 'rcpss':
        return [f'(local.set ${dn} (f32x4.replace_lane 0 (local.get ${dn}) (f32.div (f32.const 1) {lane0_f32(self, s)})))'], None
    # ---- packed float arithmetic ----
    pk = {'addps': 'f32x4.add', 'subps': 'f32x4.sub', 'mulps': 'f32x4.mul', 'divps': 'f32x4.div',
          'addpd': 'f64x2.add', 'subpd': 'f64x2.sub', 'mulpd': 'f64x2.mul', 'divpd': 'f64x2.div',
          'andps': 'v128.and', 'andpd': 'v128.and', 'orps': 'v128.or', 'orpd': 'v128.or',
          'xorps': 'v128.xor', 'xorpd': 'v128.xor', 'pand': 'v128.and', 'por': 'v128.or', 'pxor': 'v128.xor',
          'paddb': 'i8x16.add', 'paddw': 'i16x8.add', 'paddd': 'i32x4.add', 'paddq': 'i64x2.add',
          'psubb': 'i8x16.sub', 'psubw': 'i16x8.sub', 'psubd': 'i32x4.sub', 'psubq': 'i64x2.sub',
          'pcmpeqb': 'i8x16.eq', 'pcmpeqw': 'i16x8.eq', 'pcmpeqd': 'i32x4.eq',
          'pcmpgtb': 'i8x16.gt_s', 'pcmpgtw': 'i16x8.gt_s', 'pcmpgtd': 'i32x4.gt_s',
          'pmullw': 'i16x8.mul', 'pmulld': 'i32x4.mul',
          'pminub': 'i8x16.min_u', 'pmaxub': 'i8x16.max_u', 'pminsw': 'i16x8.min_s', 'pmaxsw': 'i16x8.max_s',
          'paddusb': 'i8x16.add_sat_u', 'psubusb': 'i8x16.sub_sat_u', 'paddsw': 'i16x8.add_sat_s'}
    if mn in pk:
        return [f'(local.set ${dn} ({pk[mn]} (local.get ${dn}) {vec_src(self, s)}))'], None
    if mn == 'packssdw':
        return [f'(local.set ${dn} (i16x8.narrow_i32x4_s (local.get ${dn}) {vec_src(self, s)}))'], None
    if mn == 'pmulhw':
        # Widen signed words before multiplication, then retain each product's high word.
        # Snapshot the source so aliased operands and memory forms have identical semantics.
        lanes = '2 3 6 7 10 11 14 15 18 19 22 23 26 27 30 31'
        return [f'(local.set $tv {vec_src(self, s)})',
                f'(local.set ${dn} (i8x16.shuffle {lanes} '
                f'(i32x4.extmul_low_i16x8_s (local.get ${dn}) (local.get $tv)) '
                f'(i32x4.extmul_high_i16x8_s (local.get ${dn}) (local.get $tv))))'], None
    if mn == 'psrldq':
        count = min(s.imm & 0xff, 16)
        lanes = ' '.join(str(min(k + count, 16)) for k in range(16))
        return [f'(local.set ${dn} (i8x16.shuffle {lanes} (local.get ${dn}) {ZERO}))'], None
    if mn in ('andnps', 'andnpd', 'pandn'):
        return [f'(local.set ${dn} (v128.andnot {vec_src(self, s)} (local.get ${dn})))'], None
    if mn in ('minps', 'maxps', 'minpd', 'maxpd'):
        w = 'f32x4' if mn.endswith('ps') else 'f64x2'
        f = 'pmin' if mn.startswith('min') else 'pmax'
        return [f'(local.set ${dn} ({w}.{f} {vec_src(self, s)} (local.get ${dn})))'], None
    if mn in ('sqrtps', 'sqrtpd'):
        w = 'f32x4' if mn.endswith('ps') else 'f64x2'
        return [f'(local.set ${dn} ({w}.sqrt {vec_src(self, s)}))'], None
    if mn in ('rcpps', 'rsqrtps'):
        # These instructions always treat denormal inputs as signed zero and flush
        # denormal outputs, independent of MXCSR. Normal results may be more
        # accurate than the hardware approximation (whose error is bounded).
        sign = '(v128.const i32x4 0x80000000 0x80000000 0x80000000 0x80000000)'
        magnitude = '(v128.const i32x4 0x7fffffff 0x7fffffff 0x7fffffff 0x7fffffff)'
        min_normal = '(v128.const i32x4 0x00800000 0x00800000 0x00800000 0x00800000)'
        def flush(v):
            return (f'(v128.bitselect (v128.and {v} {sign}) {v} '
                    f'(i32x4.lt_u (v128.and {v} {magnitude}) {min_normal}))')
        divisor = '(f32x4.sqrt (local.get $tv))' if mn == 'rsqrtps' else '(local.get $tv)'
        return [f'(local.set $tv {vec_src(self, s)})',
                f'(local.set $tv {flush("(local.get $tv)")})',
                f'(local.set $tv (f32x4.div (f32x4.splat (f32.const 1)) {divisor}))',
                f'(local.set ${dn} {flush("(local.get $tv)")})'], None
    # ---- compares to flags ----
    if mn in ('comiss', 'ucomiss', 'comisd', 'ucomisd'):
        if mn.endswith('ss'):
            a, b = f'(f32x4.extract_lane 0 (local.get ${dn}))', lane0_f32(self, s)
            t = 'f32'
        else:
            a, b = f'(f64x2.extract_lane 0 (local.get ${dn}))', lane0_f64(self, s)
            t = 'f64'
        if t == 'f32':
            o += [f'(local.set $ts {a})', f'(local.set $tt {b})']
            A, B = '(local.get $ts)', '(local.get $tt)'
        else:
            o += [f'(local.set $tf {a})', f'(local.set $tg {b})']
            A, B = '(local.get $tf)', '(local.get $tg)'
        o.append(f'(local.set $tx (i32.eqz (i32.and ({t}.eq {A} {A}) ({t}.eq {B} {B}))))')
        o.append('(local.set $pf (local.get $tx))')
        o.append(f'(local.set $zf (i32.or (local.get $tx) ({t}.eq {A} {B})))')
        o.append(f'(local.set $cf (i32.or (local.get $tx) ({t}.lt {A} {B})))')
        o.append('(local.set $of (i32.const 0))')
        o.append('(local.set $sf (i32.const 0))')
        return o, None
    # ---- conversions ----
    if mn in ('cvtsi2ss', 'cvtsi2sd'):
        if mn == 'cvtsi2ss':
            return [f'(local.set ${dn} (f32x4.replace_lane 0 (local.get ${dn}) (f32.convert_i32_s {self.load(s, 4)})))'], None
        return [f'(local.set ${dn} (f64x2.replace_lane 0 (local.get ${dn}) (f64.convert_i32_s {self.load(s, 4)})))'], None
    if mn in ('cvttss2si', 'cvttsd2si', 'cvtss2si', 'cvtsd2si'):
        ty = 'f32' if mn.endswith('ss2si') or mn == 'cvttss2si' else 'f64'
        src = lane0_f32(self, s) if ty == 'f32' else lane0_f64(self, s)
        setter, val = trunc_i32(src, ty, 'nearest' if mn in ('cvtss2si', 'cvtsd2si') else None)
        return [setter, self.store(d, val)], None
    if mn == 'cvtss2sd':
        return [f'(local.set ${dn} (f64x2.replace_lane 0 (local.get ${dn}) (f64.promote_f32 {lane0_f32(self, s)})))'], None
    if mn == 'cvtsd2ss':
        return [f'(local.set ${dn} (f32x4.replace_lane 0 (local.get ${dn}) (f32.demote_f64 {lane0_f64(self, s)})))'], None
    if mn == 'cvtdq2pd':
        return [f'(local.set $tv {vec_src(self, s)})', f'(local.set ${dn} (f64x2.convert_low_i32x4_s (local.get $tv)))'], None
    if mn == 'cvtps2pd':
        return [f'(local.set $tv {vec_src(self, s)})', f'(local.set ${dn} (f64x2.promote_low_f32x4 (local.get $tv)))'], None
    if mn == 'cvtpd2ps':
        return [f'(local.set $tv {vec_src(self, s)})', f'(local.set ${dn} (f32x4.demote_f64x2_zero (local.get $tv)))'], None
    if mn in ('cvttps2dq', 'cvtps2dq'):
        # Out-of-range and NaN lanes produce the x86 "integer indefinite" 0x80000000, not wasm's saturated value.
        src = vec_src(self, s)
        if mn == 'cvtps2dq':
            src = f'(f32x4.nearest {src})'
        big = '(v128.const f32x4 2147483648 2147483648 2147483648 2147483648)'
        return [f'(local.set $tv {src})',
                f'(local.set ${dn} (v128.bitselect (v128.const i32x4 0x80000000 0x80000000 0x80000000 0x80000000) (i32x4.trunc_sat_f32x4_s (local.get $tv)) '
                f'(v128.or (f32x4.ne (local.get $tv) (local.get $tv)) (f32x4.ge (f32x4.abs (local.get $tv)) {big}))))'], None
    if mn == 'cvtdq2ps':
        return [f'(local.set ${dn} (f32x4.convert_i32x4_s {vec_src(self, s)}))'], None
    # ---- shuffles ----
    if mn == 'shufps':
        imm = s.imm if False else ops[2].imm
        a = [(imm >> (2 * k)) & 3 for k in range(4)]
        lanes = []
        for k in range(4):
            base = a[k] * 4 + (0 if k < 2 else 16)
            lanes += [base, base + 1, base + 2, base + 3]
        return [f'(local.set ${dn} (i8x16.shuffle {" ".join(map(str, lanes))} (local.get ${dn}) {vec_src(self, ops[1])}))'], None
    if mn in ('unpcklps', 'unpckhps'):
        idx = [0, 20, 4, 24] if False else None
        pairs = [(0, 0), (1, 0), (0, 1), (1, 1)] if mn == 'unpcklps' else [(0, 2), (1, 2), (0, 3), (1, 3)]
        lanes = []
        for srcsel, ln in [(0, 0 if mn == 'unpcklps' else 2), (1, 0 if mn == 'unpcklps' else 2),
                           (0, 1 if mn == 'unpcklps' else 3), (1, 1 if mn == 'unpcklps' else 3)]:
            b = ln * 4 + (16 if srcsel else 0)
            lanes += [b, b + 1, b + 2, b + 3]
        return [f'(local.set ${dn} (i8x16.shuffle {" ".join(map(str, lanes))} (local.get ${dn}) {vec_src(self, s)}))'], None
    if mn == 'pshufd':
        imm = ops[2].imm
        lanes = []
        for k in range(4):
            b = ((imm >> (2 * k)) & 3) * 4
            lanes += [b, b + 1, b + 2, b + 3]
        src = vec_src(self, s)
        return [f'(local.set $tv {src})', f'(local.set ${dn} (i8x16.shuffle {" ".join(map(str, lanes))} (local.get $tv) (local.get $tv)))'], None
    shifts = {'psrlq': 'i64x2.shr_u', 'psllq': 'i64x2.shl', 'psrld': 'i32x4.shr_u', 'pslld': 'i32x4.shl',
              'psrlw': 'i16x8.shr_u', 'psllw': 'i16x8.shl', 'psrad': 'i32x4.shr_s', 'psraw': 'i16x8.shr_s'}
    if mn in shifts:
        if s.type == X86_OP_IMM:
            cnt = f'(i32.const {s.imm & 0xff})'
        else:
            cnt = f'(i32.wrap_i64 (i64x2.extract_lane 0 {vec_src(self, s)}))'
        bits = {'q': 64, 'd': 32, 'w': 16}[mn[-1]]
        sat = (f'(select (v128.const i32x4 0 0 0 0) ({shifts[mn]} (local.get ${dn}) {cnt}) (i32.ge_u {cnt} (i32.const {bits})))'
               if 'shr_s' not in shifts[mn] else f'({shifts[mn]} (local.get ${dn}) (select (i32.const {bits - 1}) {cnt} (i32.ge_u {cnt} (i32.const {bits}))))')
        return [f'(local.set ${dn} {sat})'], None
    if mn in ('unpcklpd', 'unpckhpd', 'punpcklbw', 'punpcklwd', 'punpckldq', 'punpcklqdq', 'punpckhbw', 'punpckhwd', 'punpckhdq', 'punpckhqdq'):
        e = 8 if mn.endswith(('qdq', 'pd')) else 4 if mn.endswith('dq') else 2 if mn.endswith('wd') else 1
        base = 8 if mn.startswith(('punpckh', 'unpckh')) else 0
        lanes = []
        for k in range(8 // e):
            for b in range(e):
                lanes.append(base + k * e + b)
            for b in range(e):
                lanes.append(16 + base + k * e + b)
        return [f'(local.set ${dn} (i8x16.shuffle {" ".join(map(str, lanes))} (local.get ${dn}) {vec_src(self, s)}))'], None
    if mn.startswith('cmp') and mn[-2:] in ('ps', 'pd', 'ss', 'sd') and (len(mn) > 5 or len(ops) == 3) and dn:
        preds = ['eq', 'lt', 'le', 'unord', 'neq', 'nlt', 'nle', 'ord']
        pr = ops[2].imm & 7 if len(ops) == 3 else preds.index(mn[3:-2])
        w = 'f32x4' if mn[-1] == 's' and mn[-2] in 'ps' and mn[-2:] in ('ps', 'ss') else 'f64x2'
        wide = w == 'f32x4'
        scalar = mn[-2:] in ('ss', 'sd')
        if scalar:
            src = (f'(local.get ${sn})' if sn else (f'(v128.load32_zero {self.addr(s)})' if wide else f'(v128.load64_zero {self.addr(s)})'))
        else:
            src = vec_src(self, s)
        o.append(f'(local.set $tv {src})')
        A, B = f'(local.get ${dn})', '(local.get $tv)'
        ordm = f'(v128.and ({w}.eq {A} {A}) ({w}.eq {B} {B}))'
        res = {0: f'({w}.eq {A} {B})', 1: f'({w}.lt {A} {B})', 2: f'({w}.le {A} {B})', 3: f'(v128.not {ordm})',
               4: f'({w}.ne {A} {B})', 5: f'(v128.not ({w}.lt {A} {B}))', 6: f'(v128.not ({w}.le {A} {B}))', 7: ordm}[pr]
        if scalar:
            lane = 'i32x4' if wide else 'i64x2'
            o.append(f'(local.set ${dn} ({lane}.replace_lane 0 {A} ({lane}.extract_lane 0 {res})))')
        else:
            o.append(f'(local.set ${dn} {res})')
        return o, None
    if mn == 'pextrw':
        return [self.store(d, f'(i32x4.extract_lane 0 (i16x8.replace_lane 0 {ZERO} (i16x8.extract_lane_u {ops[2].imm & 7} (local.get ${sn}))))')], None
    if mn == 'pinsrw':
        return [f'(local.set ${dn} (i16x8.replace_lane {ops[2].imm & 7} (local.get ${dn}) {self.load(s, 2) if s.type != X86_OP_REG else self.rget(self.reg_name(s.reg))}))'], None
    if mn == 'palignr':
        n = ops[2].imm
        if n < 32:
            return [f'(local.set $tv {vec_src(self, s)})', f'(local.set ${dn} (i8x16.shuffle {" ".join(str(n + k) for k in range(16))} (local.get $tv) (local.get ${dn})))'], None
    if mn in ('pshuflw', 'pshufhw'):
        imm = ops[2].imm
        lanes = list(range(16))
        base = 0 if mn == 'pshuflw' else 8
        for k in range(4):
            w = ((imm >> (2 * k)) & 3) * 2 + base
            lanes[base + 2 * k], lanes[base + 2 * k + 1] = w, w + 1
        return [f'(local.set $tv {vec_src(self, s)})', f'(local.set ${dn} (i8x16.shuffle {" ".join(map(str, lanes))} (local.get $tv) (local.get $tv)))'], None
    if mn == 'movmskps':
        return [self.store(d, f'(i32x4.bitmask (local.get ${sn}))')], None
    if mn == 'pmovmskb':
        return [self.store(d, f'(i8x16.bitmask (local.get ${sn}))')], None
    raise Unsupported(mn)
