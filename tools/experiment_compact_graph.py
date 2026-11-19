"""Offline compact filtered-movement experiment on a validated Windows native build.

build SEED DEST packed|base-profile|packed-profile|base-guard|packed-guard
selftest
"""
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import time

from experiment_alignment import digest, write
from experiment_movement_kernels import replace_once, text_bytes

ID_MASK = (1 << 19) - 1
LOW_FLAGS = 0x70F
FLAGS = 0xF0000 | LOW_FLAGS
FIELDS = "static inline uint32_t apply_movement_flip(NEEngine *e, const uint32_t *flip, uint32_t switched_count) {"
REPLAY = "    for (uint32_t i=begin;i<end;++i) switched=apply_movement_flip(e,ne_static_graph_flips+(size_t)i*4u,switched);"
PROFILE = r"""
static uint64_t ne_graph_calls, ne_graph_flips, ne_graph_samples, ne_graph_sample_flips, ne_graph_cycles;
static inline uint64_t ne_graph_clock(void) {
    __asm__ __volatile__("lfence" ::: "memory");
    return __builtin_readcyclecounter();
}
MCHPRS_NATIVE_EXPORT uint32_t ne_movement_profile(uint32_t reset, uint64_t *out, uint32_t capacity) {
    if (reset) {
        ne_graph_calls = ne_graph_flips = ne_graph_samples = ne_graph_sample_flips = ne_graph_cycles = 0;
        return 0;
    }
    if (out && capacity >= 5u) {
        out[0] = ne_graph_calls; out[1] = ne_graph_flips; out[2] = ne_graph_samples;
        out[3] = ne_graph_sample_flips; out[4] = ne_graph_cycles;
    }
    return 5u;
}
"""
GUARD = r"""
static uint32_t ne_probe = UINT32_MAX, ne_armed;
static uint64_t ne_tick_at, ne_work_at;
static uint32_t ne_probe_count, ne_probe_physical, ne_probe_pending, ne_probe_replayed;
static NEEngine *ne_probe_engine;
MCHPRS_NATIVE_EXPORT uint32_t ne_movement_profile(uint32_t reset, uint64_t *out, uint32_t capacity) {
    if (reset) {
        ++ne_probe;
        ne_armed = 1;
        ne_probe_engine = 0;
        ne_probe_pending = ne_probe_replayed = 0;
        return 0;
    }
    if (out && capacity >= 12u) {
        out[0] = ne_probe; out[1] = ne_armed; out[2] = ne_tick_at; out[3] = ne_work_at;
        out[4] = ne_probe_count; out[5] = ne_probe_physical;
        out[6] = ne_probe_engine ? ne_probe_engine->graph_active : 0;
        out[7] = ne_probe_engine ? ne_probe_engine->tick_operations : 0;
        out[8] = ne_probe_engine ? ne_probe_engine->work_count : 0;
        out[9] = ne_probe_engine ? ne_probe_engine->tick_count : 0;
        out[10] = ne_probe / 5u;
        out[11] = ne_probe_replayed;
    }
    return 12u;
}
"""
INJECT = r"""
#if defined(NE_STATIC_DIRECT_RANGES)
        if (ne_armed && e->graph_active && ne_probe / 5u == (position != NONE) &&
            cost == 3u && (flips[1] >> NE_RANGE_COST_BITS) >= 2u) {
            ne_probe_engine = e;
            ne_tick_at = e->tick_count;
            ne_work_at = e->work_count;
            ne_probe_count = cost;
            ne_probe_physical = flips[1] >> NE_RANGE_COST_BITS;
            e->tick_operations = NE_MAX_TICK_OPERATIONS - ne_probe % 5u;
            ne_armed = 0;
            ne_probe_pending = 1;
        }
#endif
"""


def encode(target, source, link, flags):
    if (target | link | (source & 0x7FFFFFFF)) & ~ID_MASK or flags & ~FLAGS:
        raise ValueError(f"Unrepresentable movement flip {target:#x}, {source:#x}, {link:#x}, {flags:#x}")
    return (target | ((flags & LOW_FLAGS) << 19), source, link | ((flags >> 16) << 19))


def decode(target, source, link):
    return target & ID_MASK, source, link & ID_MASK, (target >> 19) | ((link >> 19) << 16)


def pack(data):
    result = bytearray(len(data) // 16 * 12)
    for index, original in enumerate(struct.iter_unpack("<4I", data)):
        words = encode(*original)
        struct.pack_into("<3I", result, index * 12, *words)
        assert decode(*struct.unpack_from("<3I", result, index * 12)) == original
    return result


def compact(source):
    begin = source.index(FIELDS)
    end = source.index("\n}", begin) + 2
    body = source[begin:end].replace(FIELDS, """static inline uint32_t apply_movement_flip_fields(NEEngine *e, uint32_t target, uint32_t source,
                                                        uint32_t link, uint32_t flags, uint32_t switched_count) {""")
    body = replace_once(body, "uint32_t flags = flip[3], attenuation = flags & 15u;", "uint32_t attenuation = flags & 15u;")
    for old, new in [("flip[0]", "target"), ("flip[1]", "source"), ("flip[2]", "link")]:
        body = body.replace(old, new)
    wrapper = FIELDS + """
    return apply_movement_flip_fields(e, flip[0], flip[1], flip[2], flip[3], switched_count);
}"""
    source = source[:begin] + body + "\n" + wrapper + source[end:]
    replay = """
    for (uint32_t i = begin; i < end; ++i) {
        const uint32_t *flip = ne_static_graph_flips + (size_t)i * 3u;
        uint32_t target = flip[0], link = flip[2];
        uint32_t flags = (target >> 19u) | ((link >> 19u) << 16u);
        switched = apply_movement_flip_fields(e, target & 0x7ffffu, flip[1], link & 0x7ffffu, flags, switched);
    }""".strip("\n")
    return replace_once(source, REPLAY, replay), replay


def build(seed, dest, mode):
    seed, dest = Path(seed).resolve(), Path(dest).resolve()
    assert mode in {"packed", "base-profile", "packed-profile", "base-guard", "packed-guard"}
    dest.mkdir(parents=True)
    for path in seed.iterdir():
        if path.suffix in (".c", ".h", ".bin"):
            shutil.copy2(path, dest / path.name)
    source_path = dest / "llvm_movement.c"
    source, replay = source_path.read_text(), REPLAY
    table = dest / "graph_flips.bin"
    original_bytes = table.stat().st_size
    if mode.startswith("packed"):
        source, replay = compact(source)
        table.write_bytes(pack(table.read_bytes()))
        header = dest / "program.h"
        words = f"uint8_t bytes[{original_bytes}]; uint32_t words[{original_bytes // 4}];"
        packed_bytes = table.stat().st_size
        packed_words = f"uint8_t bytes[{packed_bytes}]; uint32_t words[{packed_bytes // 4}];"
        marker = " } ne_data_graph_flips"
        header.write_text(replace_once(header.read_text(), words + marker, packed_words + marker))
    if mode.endswith("profile"):
        source = PROFILE + source
        source = replace_once(source, replay, """    uint32_t measure = ((ne_graph_calls++ & 1023u) == 0u);
    ne_graph_flips += end - begin;
    uint64_t started = measure ? ne_graph_clock() : 0;
""" + replay + """
    if (measure) {
        ne_graph_cycles += ne_graph_clock() - started;
        ne_graph_samples++;
        ne_graph_sample_flips += end - begin;
    }""")
    if mode.endswith("guard"):
        source = GUARD + source
        point = "        if (e->graph_active && cost <= NE_MAX_TICK_OPERATIONS - e->tick_operations) {"
        source = replace_once(source, point, INJECT + point + """
            if (ne_probe_pending) {
                ne_probe_replayed = 1;
                ne_probe_pending = 0;
            }""")
        source = replace_once(source, "        graph_materialize(e);", "        ne_probe_pending = 0;\n        graph_materialize(e);")
    source_path.write_text(source)
    manifest = json.loads((seed / "build.json").read_text())
    flags = ["".join(map(chr, flag["Windows"])) if isinstance(flag, dict) else flag for flag in manifest["flags"]]
    clang, linker = (os.environ[name] for name in ("MCHPRS_LLVM_CLANG", "MCHPRS_LLVM_LINKER"))
    commands = [[clang, *flags], [linker, "/dll", "/noentry", "/nodefaultlib", "/machine:x64",
                "/out:engine.dll", "/map:engine.map", "engine.obj"]]
    started = time.perf_counter()
    for command in commands:
        subprocess.run(command, cwd=dest, check=True)
    elapsed = time.perf_counter() - started
    dll = dest / "engine.dll"
    write(dest / "experiment.json", dict(mode=mode, seed=str(seed), commands=commands,
          tool_hashes={tool: digest(tool) for tool in (clang, linker)}, program_identity=manifest["program_identity"],
          original_graph_bytes=original_bytes, graph_bytes=table.stat().st_size, verified_records=original_bytes // 16,
          dll_bytes=dll.stat().st_size, text_bytes=text_bytes(dll.read_bytes()),
          baseline_sha256=digest(seed / "engine.dll"), engine_sha256=digest(dll), native_build_seconds=elapsed))
    print(dest, mode, original_bytes - table.stat().st_size, flush=True)


def selftest():
    for source in (0, ID_MASK, 0x80000000, 0x80000000 | ID_MASK):
        for flags in (0, FLAGS):
            flip = (ID_MASK, source, ID_MASK, flags)
            assert decode(*encode(*flip)) == flip
    for flip in ((ID_MASK + 1, 0, 0, 0), (0, ID_MASK + 1, 0, 0), (0, 0, ID_MASK + 1, 0), (0, 0, 0, 16)):
        try:
            encode(*flip)
        except ValueError:
            continue
        raise AssertionError(f"Unexpected representable flip: {flip}")
    assert pack(struct.pack("<4I", ID_MASK, 0x80000000, ID_MASK, FLAGS)) == struct.pack(
        "<3I", *encode(ID_MASK, 0x80000000, ID_MASK, FLAGS))
    print("Compact movement flip round-trip and rejection checks passed")


if __name__ == "__main__":
    action, *args = sys.argv[1:]
    {"build": build, "selftest": selftest}[action](*args)
