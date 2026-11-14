import unittest
import random
from difftest import CODE, FakeImg, ks, run_case
from lift import Func

CASES = [
    'mov ecx, 9; again: add eax, edx; dec ecx; jnz again',
    'mov ecx, 9; again: test ecx, 1; jz even; add eax, edx; even: dec ecx; jnz again',
    'mov ecx, 4; outer: mov edx, 3; inner: add eax, edx; dec edx; jnz inner; dec ecx; jnz outer',
    'mov ecx, 7; first: add eax, ecx; dec ecx; jnz first; mov ecx, 5; second: sub eax, ecx; dec ecx; jnz second',
    'mov ecx, 8; again: test eax, 1; jz done; dec ecx; jnz again; done: xor edx, edx',
    'mov ecx, 5; jmp middle; again: inc eax; middle: dec ecx; jnz again',
    'mov ecx, 5; again: test ecx, 1; jz skip; inc eax; skip: dec ecx; jnz again; xor edx, edx',
    'mov ecx, 4; outer: mov edx, 3; inner: test eax, 1; jz next; add eax, edx; dec edx; jnz inner; next: dec ecx; jnz outer',
    'mov ecx, 5; jmp middle; head: dec ecx; jz done; middle: inc eax; test ecx, 1; jz head; dec ecx; jnz middle; done: nop',
]

class StructuredLoops(unittest.TestCase):
    def test_backedges_do_not_dispatch(self):
        for asm in CASES[:4]:
            code = bytes(ks.asm(asm + '; ret', addr=CODE)[0])
            wat = Func(FakeImg(code), CODE, export=True).lift()
            self.assertIn('(loop $l', wat)
            self.assertIn('(br $l', wat)

    def test_external_entry_keeps_dispatch(self):
        code = bytes(ks.asm(CASES[5] + '; ret', addr=CODE)[0])
        wat = Func(FakeImg(code), CODE, export=True).lift()
        self.assertNotIn('(loop $l', wat)

    def test_execution_matches_x86(self):
        for asm in CASES:
            with self.subTest(asm=asm):
                good, message = run_case(asm, n=40)
                self.assertTrue(good, message)

    def test_bounded_random_control_flow(self):
        rng = random.Random(4000)
        for case in range(80):
            blocks = ['mov ecx, 4']
            for n in range(6):
                a, b = rng.randrange(6), rng.randrange(6)
                blocks.append(f'L{n}: dec ecx; jz done; add eax, {rng.randrange(1, 100)}; '
                              f'test eax, {1 << rng.randrange(5)}; jnz L{a}; jmp L{b}')
            blocks.append('done: nop')
            with self.subTest(case=case):
                good, message = run_case('; '.join(blocks), n=12, seed=case)
                self.assertTrue(good, message)

if __name__ == '__main__':
    unittest.main()
