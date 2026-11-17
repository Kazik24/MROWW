"""Bounded offline helper alignment search. No changes to production C or compiler flags.

Commands: init PLAN; build SEED PLAN CACHE MANIFEST; schedule MANIFEST JOBS;
aggregate JOBS RESULTS OUTPUT; rank OUTPUT AGGREGATE...; mutate PLAN RANK OUTPUT;
confirm MANIFEST JOBS (a manifest containing frozen finalists and both controls);
selftest. JOBS.modules.json is the existing runners' --modules input.
"""
import ctypes
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import random
import re
import shutil
import statistics
import struct
import subprocess
import sys
import time
import unittest

from experiment_movement_kernels import replace_once, text_bytes

SEED = 2026111604
VALUES = (0, 16, 32, 64, 128)
HELPERS = ("apply_movement_flip", "set_arranged", "execute_output", "drain")
CONTROLS = {"production": (0, 0, 0, 0), "flip64": (64, 0, 0, 0)}
FLAGS = ['-std=c23', '-O3', '-ffreestanding', '-fno-builtin', '-Werror',
         '-DMCHPRS_EMBEDDED_PROGRAM=1', '--target=x86_64-pc-windows-msvc']
ANCHORS = (
    ('llvm_movement.c', 'static inline uint32_t apply_movement_flip(NEEngine *e, const uint32_t *flip, uint32_t switched_count) {'),
    ('llvm_movement.c', 'static void set_arranged(NEEngine *e, uint32_t index, uint32_t target, uint32_t side, uint8_t value, uint32_t analog) {'),
    ('engine.c', 'static uint32_t execute_output(NEEngine *engine, uint32_t component, uint8_t powered) {'),
    ('engine.c', 'static uint32_t drain(NEEngine *engine) {'),
)


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + '\n')


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def key(config):
    assert len(config) == 4 and all(type(v) is int and v in VALUES for v in config)
    return '-'.join(map(str, config))


def propose():
    rng = random.Random(SEED)
    seen = set(CONTROLS.values())
    configs, duplicates = [], []
    while len(configs) < 24:
        row = tuple(rng.choice(VALUES) for _ in HELPERS)
        if row in seen:
            duplicates.append(row)
        else:
            seen.add(row)
            configs.append(row)
    return dict(seed=SEED, helpers=HELPERS, values=VALUES, controls=CONTROLS,
                candidates=configs, duplicate_attempts=duplicates, budget=32)


def annotated(sources, config):
    key(config)
    result = sources.copy()
    for (file, anchor), value in zip(ANCHORS, config):
        # Even default annotations must detect a changed or duplicated source anchor.
        changed = anchor.replace('static ', f'static __attribute__((aligned({value}))) ', 1) if value else anchor
        result[file] = replace_once(result[file], anchor, changed)
    return result


def run_cost(command, cwd, log):
    """Windows reports the process peak; sample while alive, including short compiler runs."""
    assert os.name == 'nt', 'Memory measurement currently supports the tested Windows host only'
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in ('PeakWorkingSetSize', 'WorkingSetSize',
            'QuotaPeakPagedPoolUsage', 'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
            'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage')]

    query = ctypes.WinDLL('psapi').GetProcessMemoryInfo
    query.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    query.restype = wintypes.BOOL
    peak = 0
    started = time.perf_counter()
    with Path(log).open('wb') as output:
        process = subprocess.Popen(command, cwd=cwd, stdout=output, stderr=subprocess.STDOUT)
        while True:
            counters = Counters()
            counters.cb = ctypes.sizeof(counters)
            if query(int(process._handle), ctypes.byref(counters), counters.cb):
                peak = max(peak, counters.PeakWorkingSetSize)
            status = process.poll()
            if status is not None:
                break
            if time.perf_counter() - started > 180:
                process.kill()
                process.wait()
                raise TimeoutError(command)
            time.sleep(.01)
    assert status == 0, f'{command} failed: {Path(log).read_text(errors="replace")}'
    assert peak, 'Missing peak working-set measurement'
    return dict(ms=1000 * (time.perf_counter() - started), peak_working_set_bytes=peak)


def pe_text(data):
    pe, = struct.unpack_from('<I', data, 60)
    count, = struct.unpack_from('<H', data, pe + 6)
    size, = struct.unpack_from('<H', data, pe + 20)
    for pos in range(pe + 24 + size, pe + 24 + size + count * 40, 40):
        if data[pos:pos + 8].rstrip(b'\0') == b'.text':
            length, rva, _, offset = struct.unpack_from('<IIII', data, pos + 8)
            return rva, data[offset:offset + length]
    raise AssertionError('Missing .text')


def build(seed, plan_path, cache, manifest):
    seed, cache = Path(seed).resolve(), Path(cache).resolve()
    plan = read(plan_path)
    configs = dict(plan['controls']) | {key(row): row for row in plan['candidates']}
    assert len(plan['candidates']) <= 32 and len(configs) == len(plan['candidates']) + 2
    names = ['engine.c', 'llvm_movement.c', 'command_engine.c', 'abi.h', 'program.h']
    names += sorted(p.name for p in seed.glob('*.bin'))
    sources = {name: (seed / name).read_bytes().decode() for name in names if name.endswith('.c')}
    # Normalize the previous experiment's no-op dispatcher out by requiring original source seeds.
    assert 'movement_kernels.c' not in sources['llvm_movement.c'], 'Use an unmodified production seed'
    clang, linker = os.environ['MCHPRS_LLVM_CLANG'], os.environ['MCHPRS_LLVM_LINKER']
    dump = str(Path(clang).with_name('llvm-objdump.exe'))
    identity = dict(files={name: digest(seed / name) for name in names}, flags=FLAGS,
                    tools={p: digest(p) for p in (clang, linker, dump)}, script=digest(__file__))
    base_hash = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    rows = {}
    for label, config in configs.items():
        config_key = key(config)
        directory = cache / base_hash / config_key
        metadata = directory / 'alignment.json'
        if metadata.exists():
            row = read(metadata)
            assert row['dll_sha256'] == digest(directory / 'engine.dll'), 'Corrupt cached DLL'
        else:
            assert not directory.exists(), f'Incomplete artifact: {directory}'
            directory.mkdir(parents=True)
            for name in names:
                # Immutable large tables are shared; never rewrite these links.
                if name.endswith('.bin'):
                    os.link(seed / name, directory / name)
                else:
                    shutil.copy2(seed / name, directory / name)
            changes = annotated(sources, config)
            for name, value in changes.items():
                (directory / name).write_bytes(value.encode())
            compile_cost = run_cost([clang, *FLAGS, '-c', 'engine.c', '-o', 'engine.obj'], directory, directory / 'clang.log')
            link_cost = run_cost([linker, '/dll', '/noentry', '/nodefaultlib', '/machine:x64',
                                 '/out:engine.dll', '/map:engine.map', 'engine.obj'], directory, directory / 'link.log')
            dll = directory / 'engine.dll'
            data = dll.read_bytes()
            rva, code = pe_text(data)
            map_text = (directory / 'engine.map').read_text()
            layout = {}
            for helper, requested in zip(HELPERS, config):
                match = re.search(r'0001:([0-9a-fA-F]+)\s+' + helper + r'\s+([0-9a-fA-F]+)\s', map_text)
                address = int(match[2], 16) if match else None
                layout[helper] = dict(requested=requested, emitted=bool(match), linked_address=address,
                    rva=rva + int(match[1], 16) if match else None,
                    address_modulo={str(v): address % v for v in VALUES[1:]} if match else {})
                assert not requested or not match or address % requested == 0
            with (directory / 'linked.asm').open('w') as assembly:
                subprocess.run([dump, '--disassemble', '--no-show-raw-insn', str(dll)], stdout=assembly, check=True)
            row = dict(config=config, directory=str(directory), identity=identity, cache_identity=base_hash,
                compile=compile_cost, link=link_cost, module_bytes=len(data), text_bytes=text_bytes(data),
                text_sha256=hashlib.sha256(code).hexdigest(), dll_sha256=digest(dll), layout=layout,
                disassembly_sha256=digest(directory / 'linked.asm'), source_sha256={name: digest(directory / name) for name in changes})
            write(metadata, row)
        rows[label] = row
        print(label, row['text_bytes'], flush=True)
    base = rows['production']
    assert base['text_sha256'] == hashlib.sha256(pe_text((seed / 'engine.dll').read_bytes())[1]).hexdigest(), 'Baseline code drift'
    for row in rows.values():
        row['text_growth'] = row['text_bytes'] - base['text_bytes']
        row['eligible'] = row['text_growth'] <= 4096
        row['same_text_as_production'] = row['text_sha256'] == base['text_sha256']
    write(manifest, dict(seed=str(seed), plan=str(Path(plan_path).resolve()), artifacts=rows))


def schedule(manifest, output, confirmation=False):
    artifacts = read(manifest)['artifacts']
    rng = random.Random(SEED + 1)
    jobs = []
    for label in ('production', 'flip64', 'production'):
        jobs.append(dict(kind='warmup', config=label))
    candidates = [label for label in artifacts if label not in CONTROLS and artifacts[label]['eligible']]
    plan = read(read(manifest)['plan'])
    if 'mutation_candidates' in plan and not confirmation:
        candidates = [label for label in candidates if label in {key(row) for row in plan['mutation_candidates']}]
    for repeat in range(5 if confirmation else 3):
        rng.shuffle(candidates)
        for number, label in enumerate(['flip64', *candidates, 'flip64']):
            for reference in (['production', 'flip64'] if confirmation and label != 'flip64' else ['production']):
                labels = [reference, label] if repeat % 2 == 0 else [label, reference]
                for item in labels:
                    jobs.append(dict(kind='paired', pair=f'{repeat}-{number}-{reference}', repeat=repeat,
                                     candidate=label, reference=reference, config=item))
    for job in jobs:
        job['module'] = str(Path(artifacts[job['config']]['directory']) / 'engine.dll')
    write(output, dict(seed=SEED + 1, manifest=str(Path(manifest).resolve()), jobs=jobs))
    write(str(output) + '.modules.json', [job['module'] for job in jobs])


def aggregate(schedule_path, results_path, output):
    jobs, results = read(schedule_path)['jobs'], read(results_path)
    if isinstance(results, dict):
        results = results['samples']
    assert len(jobs) == len(results), 'Incomplete screen'
    pairs = {}
    snapshots = set()
    for job, row in zip(jobs, results):
        assert Path(job['module']) == Path(row['module'])
        snapshot = row['native_state_sha256']
        assert snapshot, 'Semantic snapshot missing'
        snapshots.add(snapshot)
        assert math.isfinite(row['tps']) and row['tps'] > 0
        if job['kind'] == 'paired':
            pair = pairs.setdefault(job['pair'], dict(candidate=job['candidate'], reference=job['reference'], samples=[]))
            pair['samples'].append(row)
    assert len(snapshots) == 1, 'Semantic mismatch: reject immediately'
    ratios = {}
    for pair in pairs.values():
        baseline = next(row for row in pair['samples'] if Path(row['module']).parent.name == key(CONTROLS[pair['reference']]))
        candidate = next(row for row in pair['samples'] if row is not baseline)
        pair['ratio'] = candidate['tps'] / baseline['tps']
        label = pair['candidate'] if pair['reference'] == 'production' else pair['candidate'] + ':vs-flip64'
        ratios.setdefault(label, []).append(pair['ratio'])
    summaries = {label: dict(ratios=values, median=statistics.median(values),
                             min=min(values), max=max(values), pairs=len(values)) for label, values in ratios.items()}
    write(output, dict(schedule=str(schedule_path), results=str(results_path), snapshots=list(snapshots), pairs=pairs, summary=summaries))


def rank(paths):
    workloads = {str(path): read(path)['summary'] for path in paths}
    labels = set.intersection(*(set(rows) for rows in workloads.values()))
    assert labels, 'No common candidates'
    rows = []
    for label in labels:
        medians = {name: data[label]['median'] for name, data in workloads.items()}
        rows.append(dict(config=label, geometric_mean=math.exp(statistics.mean(math.log(v) for v in medians.values())),
                         worst=min(medians.values()), workloads=medians))
    return sorted(rows, key=lambda row: (-row['geometric_mean'], row['config']))


def mutate(plan, ranking):
    rng = random.Random(SEED + 2)
    seen = {tuple(row) for row in plan['candidates']} | set(CONTROLS.values())
    leaders = [row['config'] for row in ranking if row['config'] not in CONTROLS][:4]
    proposals, duplicates = [], []
    for leader in itertools.cycle(leaders):
        row = list(map(int, leader.split('-')))
        slot = rng.randrange(4)
        row[slot] = rng.choice([value for value in VALUES if value != row[slot]])
        if tuple(row) in seen:
            duplicates.append(row)
        else:
            seen.add(tuple(row))
            proposals.append(row)
        if len(proposals) == 8 or len(duplicates) > 100:
            break
    result = dict(plan)
    result['provisional_leaders'] = leaders
    result['mutation_candidates'] = proposals
    result['mutation_duplicates'] = duplicates
    result['candidates'] = plan['candidates'] + proposals
    assert len(result['candidates']) <= 32
    return result


class SelfTest(unittest.TestCase):
    def test_proposals(self):
        self.assertEqual(propose(), propose())
        rows = propose()['candidates']
        self.assertEqual(len(set(rows)), 24)
        self.assertTrue(all(key(row) for row in rows))
        self.assertFalse(set(rows) & set(CONTROLS.values()))
        with self.assertRaises(AssertionError): key([1, 0, 0, 0])

    def test_source_drift_and_only_annotations(self):
        sources = {name: '' for name, _ in ANCHORS}
        for name, anchor in ANCHORS: sources[name] += anchor + '\r\n}\r\n'
        changed = annotated(sources, [64, 16, 32, 128])
        for name in sources:
            self.assertEqual(re.sub(r'__attribute__\(\(aligned\(\d+\)\)\) ', '', changed[name]), sources[name])
        sources['engine.c'] += ANCHORS[-1][1]
        with self.assertRaises(AssertionError): annotated(sources, [0] * 4)

    def test_mutations(self):
        plan = propose()
        ranking = [dict(config=key(row)) for row in plan['candidates']]
        result = mutate(plan, ranking)
        self.assertEqual(result, mutate(plan, ranking))
        self.assertEqual(len(set(map(tuple, result['candidates']))), 32)
        for proposal in result['mutation_candidates']:
            self.assertTrue(any(sum(a != b for a, b in zip(proposal, map(int, leader.split('-')))) == 1
                                for leader in result['provisional_leaders']))

    def test_aggregation(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            a, b = Path(temp) / 'a.json', Path(temp) / 'b.json'
            write(a, dict(summary={'x': dict(median=2), 'y': dict(median=1)}))
            write(b, dict(summary={'x': dict(median=.5), 'y': dict(median=1)}))
            self.assertAlmostEqual(rank([a, b])[0]['geometric_mean'], 1)
            self.assertEqual(next(row for row in rank([a, b]) if row['config'] == 'x')['worst'], .5)

    def test_pairs_and_correctness_rejection(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            paths = [Path(temp) / name for name in ('jobs.json', 'rows.json', 'out.json')]
            modules = ['/0-0-0-0/engine.dll', '/64-0-0-0/engine.dll']
            jobs = [dict(module=m, kind='paired', pair='0', reference='production', candidate='flip64') for m in modules]
            rows = [dict(module=m, native_state_sha256='same', tps=t) for m, t in zip(modules, (10, 11))]
            write(paths[0], dict(jobs=jobs)); write(paths[1], rows)
            aggregate(*paths)
            self.assertEqual(read(paths[2])['summary']['flip64']['median'], 1.1)
            rows[1]['native_state_sha256'] = 'different'
            write(paths[1], rows)
            with self.assertRaises(AssertionError): aggregate(*paths)
            write(paths[1], rows[:1])
            with self.assertRaises(AssertionError): aggregate(*paths)


if __name__ == '__main__':
    command, *args = sys.argv[1:]
    if command == 'selftest': unittest.main(argv=[sys.argv[0]])
    elif command == 'init': write(args[0], propose())
    elif command == 'build': build(*args)
    elif command == 'schedule': schedule(*args)
    elif command == 'confirm': schedule(*args, confirmation=True)
    elif command == 'aggregate': aggregate(*args)
    elif command == 'rank': write(args[0], rank(args[1:]))
    elif command == 'mutate': write(args[2], mutate(read(args[0]), read(args[1])))
    else: raise SystemExit(__doc__)
