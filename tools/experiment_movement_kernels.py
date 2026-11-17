"""Offline movement-kernel experiment; copies owned generated modules, never edits production C.

Dot-source test_data/cpu-references/redpilator-next-env.ps1 first.
profile SEED OUTPUT: build an instrumented copy; specialize SEED OUTPUT PROFILE: bounded kernels.
baseline SEED OUTPUT: build the same source with an empty dispatch. selftest: check generator bounds.
layout SEED OUTPUT PROFILE: retain unused generated bodies to control for compiler/code layout effects.
guard-baseline / guard-specialize SEED OUTPUT PROFILE: synthetic budget-prefix test modules.
align SEED OUTPUT: generic replay with only a 64-byte-aligned flip helper (layout confound control).
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import unittest

MAX_KERNELS = 16
MAX_FLIPS = 256
MAX_RANGE = 32
MAX_TEXT_GROWTH = 32 * 1024

PROFILE_C = r'''
/* shortcut: process-global counters require serial instances; use per-instance counters for concurrent profiling. */
typedef struct { uint32_t path, start, count; uint64_t hits, cycles; } NEProfileRow;
static NEProfileRow ne_profile_rows[131072];
static uint64_t ne_profile_overflow, ne_profile_zero[3], ne_profile_partial;
static uint32_t ne_profile_used;
static inline uint64_t ne_profile_clock(void) {
    __asm__ __volatile__("lfence" ::: "memory");
    return __builtin_readcyclecounter();
}
static void ne_profile_range(uint32_t path, uint32_t start, uint32_t count, uint64_t cycles) {
    if (!count) { ne_profile_zero[path]++; return; }
    uint32_t slot = (start * 2654435761u + count * 97u + path) & 131071u;
    for (uint32_t i = 0; i < 131072u; ++i, slot = (slot + 1u) & 131071u) {
        NEProfileRow *r = &ne_profile_rows[slot];
        if (!r->path) {
            r->path = path; r->start = start; r->count = count;
            ne_profile_used++;
        }
        if (r->path == path && r->start == start && r->count == count) {
            r->hits++; r->cycles += cycles; return;
        }
    }
    ne_profile_overflow++;
}
MCHPRS_NATIVE_EXPORT uint32_t ne_movement_profile(uint32_t reset, uint64_t *out, uint32_t capacity) {
    if (reset) {
        for (uint32_t i = 0; i < 131072u; ++i) {
            ne_profile_rows[i].path = 0u;
            ne_profile_rows[i].hits = 0u; ne_profile_rows[i].cycles = 0u;
        }
        ne_profile_used = 0u; ne_profile_overflow = 0u; ne_profile_partial = 0u;
        ne_profile_zero[1] = 0u; ne_profile_zero[2] = 0u;
        return 0u;
    }
    uint32_t words = 4u + ne_profile_used * 5u;
    if (!out || capacity < words) return words;
    out[0] = ne_profile_overflow; out[1] = ne_profile_zero[1];
    out[2] = ne_profile_zero[2]; out[3] = ne_profile_partial;
    uint32_t cursor = 4u;
    for (uint32_t i = 0; i < 131072u; ++i) {
        const NEProfileRow *r = &ne_profile_rows[i];
        if (!r->path) continue;
        out[cursor++] = r->path; out[cursor++] = r->start; out[cursor++] = r->count;
        out[cursor++] = r->hits; out[cursor++] = r->cycles;
    }
    return words;
}
'''

GRAPH_LOOP = '    for (uint32_t i=begin;i<end;++i) switched=apply_movement_flip(e,ne_static_graph_flips+(size_t)i*4u,switched);'
FLAT_LOOP = '''        for (uint32_t i = 0; i < flat_count; ++i)
            switched_count = apply_movement_flip(e, table(e, 22u) + (flat_start + i) * 4u, switched_count);'''
PARTIAL_FLIP = '            switched_count = apply_movement_flip(e, flip, switched_count);'


def replace_once(source, old, new):
    assert source.count(old) == 1, f"Source anchor changed: {old[:80]}"
    return source.replace(old, new)


def text_bytes(data):
    assert data[:2] == b'MZ'
    pe, = struct.unpack_from('<I', data, 60)
    assert data[pe:pe + 4] == b'PE\0\0'
    sections, = struct.unpack_from('<H', data, pe + 6)
    optional, = struct.unpack_from('<H', data, pe + 20)
    for at in range(pe + 24 + optional, pe + 24 + optional + 40 * sections, 40):
        if data[at:at + 8].rstrip(b'\0') == b'.text':
            return struct.unpack_from('<I', data, at + 8)[0]
    raise AssertionError('Missing code section')


def flip_records(seed, path):
    header = (seed / 'program.h').read_text()
    if path == 1:
        data = (seed / 'graph_flips.bin').read_bytes()
    else:
        assert path == 2
        match = re.search(r'offsetof\(NEStaticProgramData,table_22\) == (\d+)u', header)
        count = re.search(r'uint32_t table_22\[(\d+)\]', header)
        assert match and count
        blob = (seed / 'program.bin').read_bytes()
        offset, length = int(match[1]), int(count[1]) * 4
        data = blob[offset:offset + length]
        assert len(data) == length
    assert len(data) % 16 == 0
    return data


def select(profile):
    assert len(profile) >= 4 and (len(profile) - 4) % 5 == 0
    assert profile[0] == 0, 'Profile hash table overflow; reject incomplete profile'
    rows = [profile[i:i + 5] for i in range(4, len(profile), 5)]
    assert all(path in (1, 2) and start >= 0 and count > 0 and hits > 0
               for path, start, count, hits, cycles in rows)
    ranked = sorted(rows, key=lambda r: r[4], reverse=True)
    selected, budget = [], MAX_FLIPS
    for row in ranked:
        if 2 <= row[2] <= min(MAX_RANGE, budget) and len(selected) < MAX_KERNELS:
            selected.append(row)
            budget -= row[2]
    return rows, selected


def kernels(seed, selected):
    functions, cases = [], {}
    for index, (path, start, count, hits, cycles) in enumerate(selected):
        records = flip_records(seed, path)
        assert 0 <= start < len(records) // 16 and start + count <= len(records) // 16
        body = []
        for at in range(start * 16, (start + count) * 16, 16):
            flip = struct.unpack_from('<4I', records, at)
            # shortcut: relies on the current value-only flip primitive; revalidate if that primitive changes.
            body.append('    switched = apply_movement_flip(e, (const uint32_t[4]){' +
                        ','.join(f'{word}u' for word in flip) + '}, switched);')
        functions.append(f'static __attribute__((noinline)) uint32_t ne_kernel_{index}(NEEngine *e, uint32_t switched) {{\n' +
                         '\n'.join(body) + '\n    return switched;\n}\n')
        slot = (start * 2654435761 + count * 97 + path) & 63
        cases.setdefault(slot, []).append(f'    if (path == {path}u && start == {start}u && count == {count}u) {{ *switched = ne_kernel_{index}(e, *switched); return 1u; }}')
    dispatch = '\n'.join(f'    case {slot}u:\n' + '\n'.join(checks) + '\n    break;' for slot, checks in cases.items())
    return '\n'.join(functions) + '''
static uint32_t ne_specialized(NEEngine *e, uint32_t path, uint32_t start, uint32_t count, uint32_t *switched) {
    if (count < 2u || count > 32u) return 0u;
    switch ((start * 2654435761u + count * 97u + path) & 63u) {
''' + dispatch + '\n    }\n    return 0u;\n}\n'


def build(mode, seed, output, profile=None):
    whole_started = time.perf_counter()
    seed, output = Path(seed).resolve(), Path(output).resolve()
    assert seed != output and not output.exists(), 'Output must be a new owned directory'
    output.mkdir(parents=True)
    for name in ['engine.c', 'llvm_movement.c', 'command_engine.c', 'abi.h', 'program.h', 'build.json',
                 *(p.name for p in seed.glob('*.bin'))]:
        shutil.copy2(seed / name, output / name)
    source = (output / 'llvm_movement.c').read_text()
    selected, rows = [], []
    if mode == 'profile':
        source = replace_once(source, 'static const uint32_t *movement_step', PROFILE_C + '\nstatic const uint32_t *movement_step')
        source = replace_once(source, GRAPH_LOOP,
            '    uint64_t profile_begin = ne_profile_clock();\n' + GRAPH_LOOP +
            '\n    ne_profile_range(1u, begin, end - begin, ne_profile_clock() - profile_begin);')
        # Braces preserve the graph/canonical if/else binding.
        source = replace_once(source, FLAT_LOOP,
            '        { uint64_t profile_begin = ne_profile_clock();\n' + FLAT_LOOP +
            '\n        ne_profile_range(2u, flat_start, flat_count, ne_profile_clock() - profile_begin); }')
        source = replace_once(source, PARTIAL_FLIP, '            ne_profile_partial++;\n' + PARTIAL_FLIP)
    else:
        if profile:
            rows, selected = select(json.loads(Path(profile).read_text()))
        kernel_source = kernels(seed, [] if mode == 'guard-baseline' else selected)
        if mode == 'layout':
            kernel_source = kernel_source.split('\nstatic uint32_t ne_specialized', 1)[0].replace(
                '__attribute__((noinline))', '__attribute__((used,noinline))') + '''
static uint32_t ne_specialized(NEEngine *e, uint32_t path, uint32_t start, uint32_t count, uint32_t *switched) { return 0u; }
'''
        (output / 'movement_kernels.c').write_text(kernel_source)
        source = replace_once(source, 'static const uint32_t *movement_step', '#include "movement_kernels.c"\nstatic const uint32_t *movement_step')
        source = replace_once(source, GRAPH_LOOP,
            '    if (ne_specialized(e, 1u, begin, end - begin, &switched)) return switched;\n' + GRAPH_LOOP)
        source = replace_once(source, FLAT_LOOP,
            '        if (!ne_specialized(e, 2u, flat_start, flat_count, &switched_count))\n' + FLAT_LOOP)
    guard_target = None
    if mode == 'align':
        source = replace_once(source, 'static inline uint32_t apply_movement_flip',
            'static inline __attribute__((aligned(64))) uint32_t apply_movement_flip')
    if mode.startswith('guard-'):
        guard_target = min(selected, key=lambda row: row[2])
        path, start, count, _, _ = guard_target
        assert path == 2, 'Budget probe currently covers canonical fixed ranges only'
        guard = f'''
static uint32_t ne_guard_budget = UINT32_MAX, ne_guard_armed;
static uint64_t ne_guard_tick;
MCHPRS_NATIVE_EXPORT uint32_t ne_movement_profile(uint32_t reset, uint64_t *out, uint32_t capacity) {{
    if (reset) {{ ne_guard_budget++; ne_guard_armed = 1u; ne_guard_hits = 0u; ne_guard_fired = 0u; return 0u; }}
    if (out && capacity >= 5u) {{
        out[0] = ne_guard_budget; out[1] = ne_guard_armed; out[2] = ne_guard_tick; out[3] = {count}u;
        out[4] = ne_guard_hits;
    }}
    return 5u;
}}
'''
        source = replace_once(source, '#include "movement_kernels.c"',
            'static uint32_t ne_guard_hits, ne_guard_fired;\n#include "movement_kernels.c"')
        kernel_path = output / 'movement_kernels.c'
        kernel_source = kernel_path.read_text().replace('uint32_t switched) {\n',
            'uint32_t switched) {\n    if (ne_guard_fired) ne_guard_hits++;\n')
        kernel_path.write_text(kernel_source)
        source = replace_once(source, 'static const uint32_t *movement_step', guard + '\nstatic const uint32_t *movement_step')
        anchor = '    uint32_t flat = !e->error && flat_start != NONE && flat_count <= NE_MAX_TICK_OPERATIONS - e->tick_operations;'
        source = replace_once(source, anchor, f'''    if (ne_guard_armed && flat_start == {start}u && flat_count == {count}u) {{
        e->tick_operations = NE_MAX_TICK_OPERATIONS - ne_guard_budget;
        ne_guard_armed = 0u; ne_guard_fired = 1u; ne_guard_tick = e->tick_count;
    }}
''' + anchor)
    (output / 'llvm_movement.c').write_text(source)
    clang, linker = os.environ['MCHPRS_LLVM_CLANG'], os.environ['MCHPRS_LLVM_LINKER']
    flags = ['-std=c23', '-O3', '-ffreestanding', '-fno-builtin', '-Werror', '-DMCHPRS_EMBEDDED_PROGRAM=1', '--target=x86_64-pc-windows-msvc']
    started = time.perf_counter()
    subprocess.run([clang, *flags, '-c', 'engine.c', '-o', 'engine.obj'], cwd=output, check=True, timeout=180)
    subprocess.run([linker, '/dll', '/noentry', '/nodefaultlib', '/machine:x64', '/out:engine.dll', 'engine.obj'], cwd=output, check=True, timeout=180)
    code_bytes = text_bytes((output / 'engine.dll').read_bytes())
    seed_code_bytes = text_bytes((seed / 'engine.dll').read_bytes())
    if mode in ('specialize', 'layout', 'guard-specialize'):
        assert code_bytes - seed_code_bytes <= MAX_TEXT_GROWTH, 'Reject: native code budget exceeded'
    result = dict(mode=mode, seed=str(seed), flags=flags, clang=clang, linker=linker,
                  build_ms=1000 * (time.perf_counter() - started), selected=selected,
                  selected_flips=sum(row[2] for row in selected),
                  profile_physical_flips=sum(row[2] * row[3] for row in rows),
                  selected_physical_flips=sum(row[2] * row[3] for row in selected),
                  profile_cycles=sum(row[4] for row in rows), selected_cycles=sum(row[4] for row in selected),
                  module_bytes=(output / 'engine.dll').stat().st_size,
                  text_bytes=code_bytes, seed_text_bytes=seed_code_bytes, text_growth_budget=MAX_TEXT_GROWTH,
                  source_sha256=hashlib.sha256(source.encode()).hexdigest(),
                  program_sha256=hashlib.sha256((seed / 'program.bin').read_bytes()).hexdigest())
    result['kernel_source_sha256'] = hashlib.sha256((output / 'movement_kernels.c').read_bytes()).hexdigest() if mode != 'profile' else None
    result['guard_target'] = guard_target
    result['script_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    result['total_build_ms'] = 1000 * (time.perf_counter() - whole_started)
    if mode == 'profile':
        result['profile_header'] = ['overflow', 'zero_graph_ranges', 'zero_canonical_ranges', 'partial_scalar_flips']
        result['profile_row'] = ['path (1 graph physical, 2 canonical flat)', 'start', 'count', 'hits', 'cycles']
    (output / 'experiment.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


class SelfTest(unittest.TestCase):
    def test_budgets_and_invalid_profile(self):
        profile = [0, 0, 0, 0] + sum(([1, i * 32, 32, 100, 1000 - i] for i in range(100)), [])
        rows, selected = select(profile)
        self.assertEqual(len(rows), 100)
        self.assertLessEqual(len(selected), MAX_KERNELS)
        self.assertEqual(sum(row[2] for row in selected), MAX_FLIPS)
        with self.assertRaises(AssertionError):
            select([1, 0, 0, 0])
        self.assertEqual(select([0, 0, 0, 0, 1, 0, 1, 10, 100])[1], [])

    def test_source_drift_fails(self):
        with self.assertRaises(AssertionError):
            replace_once('x x', 'x', 'y')

    def test_code_size_reads_virtual_section_size(self):
        data = bytearray(160)
        data[:2] = b'MZ'
        struct.pack_into('<I', data, 60, 64)
        data[64:68] = b'PE\0\0'
        struct.pack_into('<H', data, 70, 1)
        data[88:93] = b'.text'
        struct.pack_into('<I', data, 96, 1234)
        self.assertEqual(text_bytes(data), 1234)

    def test_kernels_preserve_physical_record_order_and_bounds(self):
        with tempfile.TemporaryDirectory() as directory:
            seed = Path(directory)
            (seed / 'program.h').write_text('')
            (seed / 'graph_flips.bin').write_bytes(struct.pack('<8I', 1, 2, 3, 4, 5, 6, 7, 8))
            source = kernels(seed, [[1, 0, 2, 100, 200]])
            self.assertLess(source.index('{1u,2u,3u,4u}'), source.index('{5u,6u,7u,8u}'))
            with self.assertRaises(AssertionError):
                kernels(seed, [[1, 1, 2, 100, 200]])
            (seed / 'program.h').write_text('uint32_t table_22[8];\n'
                '_Static_assert(offsetof(NEStaticProgramData,table_22) == 16u, "offset");')
            data = (seed / 'graph_flips.bin').read_bytes()
            (seed / 'program.bin').write_bytes(bytes(16) + data)
            self.assertEqual(flip_records(seed, 2), data)
            (seed / 'program.bin').write_bytes(bytes(16))
            with self.assertRaises(AssertionError):
                flip_records(seed, 2)


if __name__ == '__main__':
    if sys.argv[1:] == ['selftest']:
        unittest.main(argv=[sys.argv[0]])
    else:
        assert len(sys.argv) in (4, 5) and sys.argv[1] in ('profile', 'baseline', 'specialize', 'layout', 'guard-baseline', 'guard-specialize', 'align')
        assert (sys.argv[1] in ('specialize', 'layout', 'guard-baseline', 'guard-specialize')) == (len(sys.argv) == 5)
        build(*sys.argv[1:])
