"""Private relaxed PM1 shared-output experiment.

profile SEED OUTPUT OFFSET builds an exclusive operation-loop diagnostic.
rank DATA RUN0 RUN1 EARLIER_CENSUS BASELINE_TIMING OUTPUT applies the profiling gate.
Run from the repository root with redpilator-next-env.ps1 loaded.
"""
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys

from experiment_alignment import digest, write

PERIOD = 256
ILLUSTRATIVE = (0, 0, 0, 2, 3, 0, 2)
EMPTY = "empty"
CLASSES = (EMPTY, "callback-only", "contribution-only", "split", "interleaved")
START = "static uint32_t execute_output(NEEngine *engine, uint32_t component, uint8_t powered) {"
PROFILE_C = r"""
typedef struct {
    uint64_t calls, samples, clocks;
} NEOutputSample;
typedef struct NEOutputProfileFrame {
    struct NEOutputProfileFrame *parent;
    uint64_t nested_start, children, loop_start;
    NEOutputSample *row;
} NEOutputProfileFrame;

static const union {
    uint8_t bytes[NE_PROFILE_MAP_BYTES];
    uint32_t words[NE_PROFILE_MAP_BYTES / 4];
} ne_output_profile_ids = {.bytes = {
#embed "profile_ids.bin"
}};
static NEOutputSample ne_output_profile_rows[NE_PROFILE_ROWS];
static NEOutputProfileFrame *ne_output_profile_parent;
static uint64_t ne_output_profile_started, ne_output_profile_underflows;

static inline uint64_t ne_output_profile_clock(void) {
    __asm__ __volatile__("lfence" ::: "memory");
    return __builtin_readcyclecounter();
}

static inline void ne_output_profile_begin(NEOutputProfileFrame *frame, uint32_t component, uint8_t powered) {
    uint32_t id = ne_output_profile_ids.words[(size_t)component * 2u + powered];
    NEOutputSample *row = &ne_output_profile_rows[id * 2u + powered];
    if ((row->calls++ & (NE_PROFILE_PERIOD - 1u)) == NE_PROFILE_OFFSET) {
        frame->row = row;
        frame->loop_start = ne_output_profile_clock();
    }
}

static inline void ne_output_profile_end(NEOutputProfileFrame *frame) {
    if (frame->row) {
        uint64_t clocks = ne_output_profile_clock() - frame->loop_start;
        if (clocks >= frame->children)
            frame->row->clocks += clocks - frame->children;
        else
            ne_output_profile_underflows++;
        frame->row->samples++;
        frame->row = 0;
    }
}

static inline uint32_t ne_output_profile_return(NEOutputProfileFrame *frame, uint32_t status) {
    ne_output_profile_parent = frame->parent;
    if (frame->nested_start)
        frame->parent->children += ne_output_profile_clock() - frame->nested_start;
    return status;
}

MCHPRS_NATIVE_EXPORT uint32_t ne_movement_profile(uint32_t reset, uint64_t *out, uint32_t capacity) {
    if (reset) {
        for (uint32_t i = 0; i < NE_PROFILE_ROWS; ++i) {
            ne_output_profile_rows[i].calls = 0;
            ne_output_profile_rows[i].samples = 0;
            ne_output_profile_rows[i].clocks = 0;
        }
        ne_output_profile_underflows = 0;
        ne_output_profile_started = ne_output_profile_clock();
        return 0u;
    }
    uint32_t words = 4u + NE_PROFILE_ROWS * 3u;
    if (!out || capacity < words)
        return words;
    out[0] = ne_output_profile_clock() - ne_output_profile_started;
    out[1] = NE_PROFILE_PERIOD;
    out[2] = NE_PROFILE_OFFSET;
    out[3] = ne_output_profile_underflows;
    for (uint32_t i = 0; i < NE_PROFILE_ROWS; ++i) {
        out[4u + i * 3u] = ne_output_profile_rows[i].calls;
        out[5u + i * 3u] = ne_output_profile_rows[i].samples;
        out[6u + i * 3u] = ne_output_profile_rows[i].clocks;
    }
    return words;
}
"""


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def classify(sequence):
    flags = [opcode < 2 for opcode in sequence]
    if not flags:
        return EMPTY
    prefix = sum(flags)
    if prefix == 0:
        return "callback-only"
    if prefix == len(flags):
        return "contribution-only"
    return "split" if all(flags[:prefix]) else "interleaved"


def census(seed):
    header = (seed / "program.h").read_text()
    value = re.search(r"ne_static_program = \{([^}]+)\}", header)[1]
    descriptor = [int(word.strip().removesuffix("u")) for word in value.split(",")]
    _, _, size, op_at, op_count, _, _, comp_at, comp_count, *_ = descriptor
    blob = (seed / "program.bin").read_bytes()
    assert len(blob) == size
    assert op_at + op_count * 8 <= comp_at and comp_at + comp_count * 32 <= size
    ranges = {}
    edges = []
    for at in range(comp_at, comp_at + comp_count * 32, 32):
        on_start, on_count, off_start, off_count = struct.unpack_from("<4I", blob, at + 16)
        for start, count in [(off_start, off_count), (on_start, on_count)]:
            if count:
                assert start <= op_count and count <= op_count - start
                if (start, count) not in ranges:
                    ranges[start, count] = tuple(blob[op_at + start * 8:op_at + (start + count) * 8:8])
            edges.append((start, count))
    sequence_keys = sorted(set(ranges.values())) + [()]
    ids = {key: index for index, key in enumerate(sequence_keys)}
    table = bytearray(len(edges) * 4)
    for at, edge in enumerate(edges):
        struct.pack_into("<I", table, at * 4, ids[ranges[edge]] if edge[1] else ids[()])
    counts = {key: 0 for key in ids}
    for sequence in ranges.values():
        counts[sequence] += 1
    return table, [
        dict(id=ids[key], sequence=key, range_count=counts[key], classification=classify(key))
        for key in sequence_keys
    ]


def replace(text, old, new):
    assert text.count(old) == 1, old
    return text.replace(old, new)


def instrument(source, size, rows, offset):
    head = source.index(START)
    tail = source.index("\n}", head) + 2
    body = source[head:tail]
    prefix = "\n".join(f"#define NE_PROFILE_{name} {value}u" for name, value in
                       [("MAP_BYTES", size), ("ROWS", rows), ("PERIOD", PERIOD), ("OFFSET", offset)])
    body = replace(body, START, START + """
    NEOutputProfileFrame *parent = ne_output_profile_parent;
    uint64_t nested_start = parent && parent->row ? ne_output_profile_clock() : 0;
    NEOutputProfileFrame frame = {parent, nested_start, 0, 0, 0};
    ne_output_profile_parent = &frame;""")
    line = "    uint32_t count = powered ? record->on_count : record->off_count;"
    body = replace(body, line, line + "\n    ne_output_profile_begin(&frame, component, powered);")
    for indent, test in [(12, "if (status)"), (8, "if (status != NE_OK)")]:
        pad = " " * indent
        body = replace(body, f"{pad}{test}\n{pad}    return status;", f"""{pad}{test} {{
{pad}    ne_output_profile_end(&frame);
{pad}    return ne_output_profile_return(&frame, status);
{pad}}}""")
    body = replace(body, "    return ne_has_reactive(engine) ? update_reactive(engine, component) : NE_OK;",
                   """    ne_output_profile_end(&frame);
    uint32_t status = ne_has_reactive(engine) ? update_reactive(engine, component) : NE_OK;
    return ne_output_profile_return(&frame, status);""")
    assert body.count("return ") == 3
    return source[:head] + prefix + "\n" + PROFILE_C + "\n" + body + source[tail:]


def profile(seed, dest, offset):
    seed, dest = Path(seed).resolve(), Path(dest).resolve()
    offset = int(offset)
    assert 0 <= offset < PERIOD
    dest.mkdir(parents=True)
    manifest = read(seed / "build.json")
    table, sequences = census(seed)
    for path in seed.iterdir():
        if path.suffix in (".c", ".h", ".bin"):
            shutil.copy2(path, dest / path.name)
    source = dest / "engine.c"
    source.write_text(instrument(source.read_text(), len(table), len(sequences) * 2, offset))
    (dest / "profile_ids.bin").write_bytes(table)
    flags = ["".join(map(chr, flag["Windows"])) if isinstance(flag, dict) else flag for flag in manifest["flags"]]
    clang, linker = (os.environ[name] for name in ("MCHPRS_LLVM_CLANG", "MCHPRS_LLVM_LINKER"))
    commands = [
        [clang, *flags],
        [linker, "/dll", "/noentry", "/nodefaultlib", "/machine:x64", "/out:engine.dll", "engine.obj"],
    ]
    for command in commands:
        subprocess.run(command, cwd=dest, check=True)
    write(dest / "profile.json", dict(seed=str(seed), offset=offset, period=PERIOD, program_identity=manifest["program_identity"],
          commands=commands, sequences=sequences, baseline_sha256=digest(seed / "engine.dll"),
          diagnostic_sha256=digest(dest / "engine.dll"), fixture_hashes={
              name: digest(Path("test_data") / name) for name in (
                  "pm1_redpilator.schem",
                  "cpu-references/pm1_redpilator_protocol.json",
                  "cpu-references/pm1_redpilator_reference.json",
              )}, tools={tool: digest(tool) for tool in (clang, linker)}))


def rank(data, run0, run1, earlier_census, baseline_timing, output):
    sequences = read(data)["sequences"]
    baseline = read(baseline_timing)["samples"]
    fastest_ms = min(row["elapsed_ms"] for row in baseline if "-candidate" not in row["module"])
    earlier = read(earlier_census)
    traces = []
    runs = []
    for result_path in map(Path, (run0, run1)):
        run, = read(result_path)["samples"]
        assert run["ticks"] == 12070 and run["quiet"] and run["reference_matched"] and len(run["commands"]) == 1534
        assert run["native_state_sha256"] == baseline[0]["native_state_sha256"]
        runs.append(run)
        total_clocks, period, offset, underflows, *words = read(result_path.with_suffix(".output-profile.json"))
        assert underflows == 0 and period == PERIOD and len(words) == len(sequences) * 6
        call_totals = {(edge, cls): [0, 0] for edge in range(2) for cls in CLASSES}
        rows = []
        # Approximate cycles per host millisecond from the same full timed interval.
        nominal_uninstrumented_clocks = total_clocks * fastest_ms / run["elapsed_ms"]
        for record in sequences:
            idx = record["id"] * 6
            edges = []
            for edge in range(2):
                calls, samples, clocks = words[idx + edge * 3:idx + edge * 3 + 3]
                assert samples == (calls + period - 1 - offset) // period
                agg = call_totals[edge, record["classification"]]
                agg[0] += calls
                agg[1] += calls * len(record["sequence"])
                edges.append(dict(calls=calls, samples=samples, clocks=clocks, records=calls * len(record["sequence"]),
                                  estimated_clocks=calls * clocks / samples if samples else 0))
            estimate = sum(edge["estimated_clocks"] for edge in edges)
            rows.append(dict(record, edges=edges, estimated_clocks=estimate,
                             upper_bound_share=estimate / nominal_uninstrumented_clocks))
        for edge in range(2):
            for j, cls in enumerate(CLASSES):
                idx = (edge * len(CLASSES) + j) * 4
                assert call_totals[edge, cls] == earlier[idx:idx + 2], (edge, cls, call_totals[edge, cls], earlier[idx:idx + 2])
        calls = sum(e["calls"] for row in rows for e in row["edges"])
        for row in rows:
            row["call_share"] = sum(edge["calls"] for edge in row["edges"]) / calls
        traces.append(dict(offset=offset, instrumented_ms=run["elapsed_ms"], total_clocks=total_clocks,
                           nominal_uninstrumented_clocks=nominal_uninstrumented_clocks,
                           rows=sorted(rows, key=lambda row: -row["estimated_clocks"])))
    assert traces[0]["offset"] != traces[1]["offset"]
    assert runs[0]["outputs"] == runs[1]["outputs"] and runs[0]["commands"] == runs[1]["commands"]
    contenders = [
        [row for row in trace["rows"] if 1 <= len(row["sequence"]) <= 16] for trace in traces
    ]
    eligible_leaders = [rows[0] for rows in contenders]
    excluded = []
    for trace in traces:
        rows = [row for row in trace["rows"] if len(row["sequence"]) > 16]
        excluded.append(dict(sequences=len(rows), ranges=sum(row["range_count"] for row in rows),
                             call_share=sum(row["call_share"] for row in rows),
                             estimated_share=sum(row["estimated_clocks"] for row in rows)
                             / trace["nominal_uninstrumented_clocks"]))
    repeatable = eligible_leaders[0]["sequence"] == eligible_leaders[1]["sequence"]
    threshold = 1 - 1 / 1.03
    stop = all(row["upper_bound_share"] < threshold for row in eligible_leaders)
    write(output, dict(fastest_baseline_ms=fastest_ms, threshold=threshold, repeatable_leader=repeatable,
          leader=eligible_leaders[0]["sequence"] if repeatable else None, eligible_leaders=eligible_leaders,
          illustrative=[next(row for row in trace["rows"] if tuple(row["sequence"]) == ILLUSTRATIVE) for trace in traces],
          over_length_coverage=excluded, stop_below_threshold=stop, traces=traces))


if __name__ == "__main__":
    action, *args = sys.argv[1:]
    if action == "profile":
        profile(*args)
    elif action == "rank":
        rank(*args)
    else:
        raise SystemExit(__doc__)
