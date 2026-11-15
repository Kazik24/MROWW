//! Compile CPU fixtures in both modes and print the sampled memory/movement census.
//!
//! Run with `MCHPRS_REDPILATOR_PHASE_TIMINGS=1 cargo run -p mchprs_core --example census_sampled`.
use std::{fs::File, path::Path, sync::Arc};

use mchprs_blocks::BlockPos;
use mchprs_core::{
    plot::{
        PLOT_WIDTH, PlotWorld,
        worldedit::{load_schematic, paste_clipboard},
    },
    redpilator::{Compiler, CompilerOptions, TaskMonitor},
    world::storage::Chunk,
};

#[path = "../test_support/chungus.rs"]
mod chungus;
#[path = "../test_support/cpus.rs"]
mod cpus;

fn compile(name: &str, world: &PlotWorld, bounds: (BlockPos, BlockPos)) {
    for relaxed_mode in [false, true] {
        let started = std::time::Instant::now();
        let mut compiler = Compiler::default();
        let result = compiler.compile(
            world,
            bounds,
            CompilerOptions {
                relaxed_mode,
                ..Default::default()
            },
            world.scheduler().iter_entries().collect(),
            Arc::new(TaskMonitor::default()),
        );
        eprintln!(
            "CENSUS_FIXTURE_DONE {name} relaxed={relaxed_mode} elapsed={:?} result={result:?}",
            started.elapsed()
        );
    }
}

fn schematic_bounds(path: &Path, origin: BlockPos) -> (BlockPos, BlockPos) {
    let clip = load_schematic(File::open(path).unwrap()).unwrap();
    (
        origin,
        origin + BlockPos::new(clip.size_x as i32 - 1, clip.size_y as i32 - 1, clip.size_z as i32 - 1),
    )
}

fn main() {
    let data = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../test_data");
    for cpu in cpus::CPUS {
        let world = cpus::load_cpu(cpu);
        let path = data.join(cpu.schematic);
        compile(cpu.name, &world, schematic_bounds(&path, cpu.origin));
    }

    for fixture in [
        "pm1_redpilator.schem",
        "piston-research/test-potados-counter-revised-20261008/TEST_POTADOS_PC_COUNTER.schem",
        "piston-research/FPU_27.schem",
    ] {
        let path = data.join(fixture);
        let origin = BlockPos::new(8, 8, 8);
        let mut clip = load_schematic(File::open(&path).unwrap()).unwrap();
        clip.offset_x = 0;
        clip.offset_y = 0;
        clip.offset_z = 0;
        let chunks = (0..PLOT_WIDTH).flat_map(|x| (0..PLOT_WIDTH).map(move |z| Chunk::empty(x, z))).collect();
        let mut world = PlotWorld::from_chunks(0, 0, chunks, Default::default());
        paste_clipboard(&mut world, &clip, origin, false);
        let bounds = (
            origin,
            origin + BlockPos::new(clip.size_x as i32 - 1, clip.size_y as i32 - 1, clip.size_z as i32 - 1),
        );
        compile(fixture, &world, bounds);
    }

    let world = chungus::init_world();
    compile("Chungus", &world, world.get_corners());
}
