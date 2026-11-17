"""Predeclared, bounded PM1 image-null crossover; stdlib only.

plan MANIFEST OWNED_ROOT PLAN_JSON; run PLAN_JSON; analyze PLAN_JSON OUTPUT_JSON;
selftest. Run/check builds before timing. No alignment configurations are selected.
"""
import ctypes
import hashlib
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

from experiment_alignment import digest, read, write

SEED = 2026111608
FACTORS = ('mapping','load','execute','allocate','path_set','warm','comparison_first')
BOOTSTRAPS = 10000
LENGTHS = (2,4,5)


def schedule(seed):
    # Paley order-20 Hadamard: seven balanced, pairwise orthogonal binary columns.
    residues = {i*i % 19 for i in range(1,19)}
    h = [[1]*20]
    for i in range(19):
        h.append([1]+[-1 if i == j else (1 if (i-j)%19 in residues else -1) for j in range(19)])
    rng = random.Random(seed)
    rows = [{key:(row[i+1]+1)//2 for i,key in enumerate(FACTORS)} for row in h]
    rng.shuffle(rows)
    trials = []
    for block,row in enumerate(rows):
        comparisons = ['same','separate'] if row['comparison_first'] == 0 else ['separate','same']
        trials.extend(dict(block=block,comparison=name,**row) for name in comparisons)
    return trials


def plan(manifest_path, root, output):
    manifest = read(manifest_path)
    artifact = manifest['artifacts']['production']
    source = Path(artifact['directory'])/'engine.dll'
    assert digest(source) == artifact['dll_sha256']
    root = Path(root).resolve()
    assert not root.exists(), 'Use a new owned root; nothing is cleaned up automatically'
    root.mkdir(parents=True)
    data = source.read_bytes()
    pe, = struct.unpack_from('<I',data,60)
    image_base, = struct.unpack_from('<Q',data,pe+24+24)
    text = (source.parent/'engine.map').read_text()
    tick = re.search(r'0001:[0-9a-fA-F]+\s+ne_tick\s+([0-9a-fA-F]+)\s',text)
    assert tick
    sessions = []
    for number in range(2):
        directory = root/f'session{number}'
        directory.mkdir()
        masters = []
        for identity in range(2):
            target = directory/f'master{identity}.dll'
            shutil.copyfile(source,target) # independent files, identical bytes INCLUDING timestamp
            assert digest(target) == artifact['dll_sha256']
            masters.append(str(target))
        assert os.stat(masters[0]).st_ino != os.stat(masters[1]).st_ino
        sessions.append(dict(root=str(directory),masters=masters,seed=SEED+number,
                             trials=schedule(SEED+number),output=str(directory/'results.json')))
    result = dict(schema=1,seed=SEED,artifact_sha256=artifact['dll_sha256'],source=str(source.resolve()),
        source_manifest=str(Path(manifest_path).resolve()),tick_rva=int(tick[1],16)-image_base,
        helpers={name:row['rva'] for name,row in artifact['layout'].items() if row['emitted']},
        sessions=sessions,analysis=dict(pairs_per_comparison_per_session=20,bootstrap_resamples=BOOTSTRAPS,
            circular_block_lengths=LENGTHS,primary='geometric mean B/A throughput ratio, unadjusted; adjacent log paired differences',
            ci='percentile circular moving-block bootstrap; preserve same/separate ratios in each temporal block; report envelope of lengths 2,4,5',
            order_model='OLS log ratio on all seven signed factors plus centered block time; factor contrast is 2*coefficient; same temporal block resamples',
            pass_gate='separate AND same-image 95% CI envelopes entirely within [0.99,1.01] in both sessions; no factor with same-sign 95% CI excluding zero in both sessions',
            outliers='retain all; correctness/address/file-identity failure invalidates session, not individual timing rows',
            resample_rank='retain all observations; skip singular OLS resamples only, report count; at most 30000 draws for 10000 valid fits',
            drift='adjacent pairs + randomized interleaved comparisons; temporal blocks, not ticks or individual halves, are resampling units; linear time trend and block-length sensitivity reported',
            no_correction='never subtract null gains from candidate results; no alignment search'),
        conditions=dict(cpu='strict PM1 current saved protocol',ticks=12070,preparation_ticks=0,affinity=256,
            priority='High',publication='io',flush_every=0,clock='existing benchmark_tick_batch(0,0) before Start',
            warmup='one complete fresh execution per label per pair; both modules pinned; identical for A/A and separate',
            lifetime='one fresh process per session; one seed graph; per-pair module anchors, warmup and measured executors; all measured executors allocated before timing; no validation between halves'),
        preregistration='Frozen before first timed crossover session. Two sessions; 20 pairs/comparison/session. Stop if inconclusive. A demonstrated harness defect permits one documented amendment and repeat of both sessions.',
        fixture_hashes={p:digest(p) for p in ['test_data/pm1_redpilator.schem','test_data/cpu-references/pm1_redpilator_protocol.json','test_data/cpu-references/pm1_redpilator_reference.json']})
    write(output,result)
    print('Saved preregistered schedule and analysis:',output)


def run(plan_path):
    p = read(plan_path)
    binary = Path('target/release/examples/bench_pm1_compare.exe').resolve()
    for index,session in enumerate(p['sessions']):
        output = Path(session['output'])
        assert not output.exists(), 'Refuse to overwrite results'
        env = os.environ.copy()
        env.pop('MCHPRS_LLVM_DLL',None)
        assert Path(env['MCHPRS_LLVM_CLANG']).is_file() and Path(env['MCHPRS_LLVM_LINKER']).is_file()
        env['MCHPRS_LLVM_WORK_DIR'] = str(Path(session['root'])/'owned-llvm')
        env['MCHPRS_REDPILATOR_COMPILE_TIMEOUT_SECS'] = '1200'
        command = [str(binary),'--image-crossover',str(Path(plan_path).resolve()),str(index),str(output)]
        started = time.time()
        with (output.parent/'stdout.log').open('wb') as stdout, (output.parent/'stderr.log').open('wb') as stderr:
            process = subprocess.Popen(command,env=env,stdout=stdout,stderr=stderr,creationflags=subprocess.CREATE_NO_WINDOW)
            api = ctypes.WinDLL('kernel32',use_last_error=True)
            api.SetProcessAffinityMask.argtypes=[ctypes.c_void_p,ctypes.c_size_t]
            api.SetPriorityClass.argtypes=[ctypes.c_void_p,ctypes.c_uint32]
            assert api.SetProcessAffinityMask(int(process._handle),256)
            assert api.SetPriorityClass(int(process._handle),0x80)
            write(output.parent/'command.json',dict(command=command,plan_sha256=digest(plan_path),
                binary_sha256=digest(binary),started_unix=started,work_dir=env['MCHPRS_LLVM_WORK_DIR'],
                tools={key:dict(path=env[key],sha256=digest(env[key])) for key in ('MCHPRS_LLVM_CLANG','MCHPRS_LLVM_LINKER')}))
            status = process.wait(timeout=7200)
        assert status == 0, f'Session {index} failed: {output.parent / "stderr.log"}'
        print('Completed session',index,flush=True)


def ols(x,y):
    size = len(x[0])
    a = [[sum(row[i]*row[j] for row in x) for j in range(size)]+[sum(row[i]*v for row,v in zip(x,y))] for i in range(size)]
    for col in range(size):
        pivot = max(range(col,size),key=lambda r:abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-9: raise ValueError('rank deficient')
        a[col],a[pivot] = a[pivot],a[col]
        scale = a[col][col]
        a[col] = [v/scale for v in a[col]]
        for row in range(size):
            if row != col:
                scale = a[row][col]
                a[row] = [v-scale*w for v,w in zip(a[row],a[col])]
    return [row[-1] for row in a]


def percentile(values, probability):
    values = sorted(values)
    at = (len(values)-1)*probability
    lower = int(at)
    return values[lower]+(values[min(lower+1,len(values)-1)]-values[lower])*(at-lower)


def infer(rows, seed, draws=BOOTSTRAPS):
    y = [r['log_ratio'] for r in rows]
    x = [[1]+[2*r[k]-1 for k in FACTORS]+[(r['block']-9.5)/19] for r in rows]
    coefficients = ols(x,y)
    intervals, effects = {}, {}
    for length in LENGTHS:
        rng = random.Random(seed+length)
        means, fitted = [], []
        skipped = 0
        for attempt in range(3*draws):
            indices = []
            while len(indices) < 20:
                start = rng.randrange(20)
                indices.extend((start+i)%20 for i in range(length))
            indices = indices[:20]
            # The same original temporal coordinates are retained when blocks are resampled.
            try: fit = ols([x[i] for i in indices],[y[i] for i in indices])
            except ValueError:
                skipped += 1
                continue
            means.append(statistics.mean(y[i] for i in indices))
            fitted.append(fit)
            if len(means) == draws: break
        assert len(means) == draws, 'Insufficient nondegenerate temporal resamples'
        intervals[str(length)] = dict(ci95=[math.exp(percentile(means,q)) for q in (.025,.975)],singular_draws=skipped)
        for index,key in enumerate(FACTORS,1):
            effects.setdefault(key,[]).append([math.expm1(2*percentile([v[index] for v in fitted],q)) for q in (.025,.975)])
        effects.setdefault('linear_time',[]).append([math.expm1(percentile([v[-1] for v in fitted],q)) for q in (.025,.975)])
    effect_results = {}
    for index,key in enumerate((*FACTORS,'linear_time'),1):
        effect_results[key] = dict(effect=math.expm1(coefficients[index]*(1 if key == 'linear_time' else 2)),
            ci95_envelope=[min(v[0] for v in effects[key]),max(v[1] for v in effects[key])])
    return dict(geometric_mean=math.exp(statistics.mean(y)),median=math.exp(statistics.median(y)),
        paired_ratios=[math.exp(v) for v in y],ci95_envelope=[min(v['ci95'][0] for v in intervals.values()),max(v['ci95'][1] for v in intervals.values())],
        temporal_bootstrap=intervals,order_effects=effect_results)


def analyze(plan_path, output):
    p = read(plan_path)
    sessions = []
    for index,session in enumerate(p['sessions']):
        raw = read(session['output'])
        assert len(raw['pairs']) == 40
        groups = {name:[] for name in ('same','separate')}
        for expected,pair in zip(session['trials'],raw['pairs']):
            assert all(pair[k] == expected[k] for k in ('block','comparison',*FACTORS))
            a,b = sorted(pair['samples'],key=lambda r:r['label'])
            assert a['snapshot_sha256'] == b['snapshot_sha256']
            assert a['artifact_sha256'] == b['artifact_sha256'] == p['artifact_sha256']
            assert (a['module_base']==b['module_base']) == (pair['comparison']=='same')
            assert a['cpu'] == b['cpu'] == 8
            groups[pair['comparison']].append(dict(pair,log_ratio=math.log(a['elapsed_ns']/b['elapsed_ns'])))
        sessions.append({name:infer(sorted(rows,key=lambda r:r['block']),SEED+100+index) for name,rows in groups.items()})
    reproducible = {}
    for comparison in ('same','separate'):
        for factor in (*FACTORS,'linear_time'):
            ci = [s[comparison]['order_effects'][factor]['ci95_envelope'] for s in sessions]
            if all(v[0]>0 for v in ci) or all(v[1]<0 for v in ci): reproducible[f'{comparison}:{factor}'] = ci
    within = all(.99<=s[k]['ci95_envelope'][0] and s[k]['ci95_envelope'][1]<=1.01 for s in sessions for k in ('same','separate'))
    write(output,dict(plan_sha256=digest(plan_path),sessions=sessions,reproducible_effects=reproducible,
        decision='calibrated' if within and not reproducible else 'inconclusive',
        criterion='all same/separate CI envelopes within ±1% in both sessions and no reproduced identity/order/time effect',
        no_null_gain_subtraction=True))
    print(read(output)['decision'])


class SelfTest(unittest.TestCase):
    def test_schedule(self):
        a = schedule(SEED)
        self.assertEqual(a,schedule(SEED))
        self.assertNotEqual(a,schedule(SEED+1))
        self.assertEqual(len(a),40)
        rows = a[::2]
        for factor in FACTORS: self.assertEqual(sum(r[factor] for r in rows),10)
        for i,left in enumerate(FACTORS):
            for right in FACTORS[i+1:]:
                self.assertEqual(sum((2*r[left]-1)*(2*r[right]-1) for r in rows),0)
        for block in range(20): self.assertEqual({r['comparison'] for r in a if r['block']==block},{'same','separate'})

    def test_order_fit(self):
        rows = schedule(SEED)[::2]
        x = [[1]+[2*r[k]-1 for k in FACTORS]+[(r['block']-9.5)/19] for r in rows]
        y = [.003+.02*v[3]+.01*v[-1] for v in x]
        fit = ols(x,y)
        self.assertAlmostEqual(fit[0],.003)
        self.assertAlmostEqual(fit[3],.02)
        self.assertAlmostEqual(fit[-1],.01)

    def test_temporal_intervals(self):
        rows = [dict(r,log_ratio=.002*(r['block']//4-2)) for r in schedule(SEED)[::2]]
        result = infer(rows,42,draws=100)
        self.assertLess(result['ci95_envelope'][0],1)
        self.assertGreater(result['ci95_envelope'][1],1)
        self.assertEqual(len(result['paired_ratios']),20)


if __name__ == '__main__':
    command,*args = sys.argv[1:]
    if command == 'selftest': unittest.main(argv=[sys.argv[0]])
    elif command == 'plan': plan(*args)
    elif command == 'run': run(*args)
    elif command == 'analyze': analyze(*args)
    else: raise SystemExit(__doc__)
