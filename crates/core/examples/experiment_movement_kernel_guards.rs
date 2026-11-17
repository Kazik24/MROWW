//! Synthetic remaining-budget checks around one profiled canonical movement range.
//! Use only the offline guard DLLs produced by tools/experiment_movement_kernels.py.
#[path = "../test_support/cpus.rs"]
#[allow(dead_code)]
mod cpus;

use mchprs_blocks::BlockPos;
use mchprs_core::{plot::worldedit::load_schematic, redpilator::CompilerOptions};
use serde_json::json;
use sha2::{Digest, Sha256};
use std::{path::PathBuf, time::Instant};

fn main() {
    let output = PathBuf::from(std::env::args().nth(1).expect("output JSON"));
    let cpu = cpus::CPUS[3];
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../test_data").join(cpu.schematic);
    let clip = load_schematic(std::fs::File::open(path).unwrap()).unwrap();
    let bounds = (
        cpu.origin,
        cpu.origin + BlockPos::new(clip.size_x as i32 - 1, clip.size_y as i32 - 1, clip.size_z as i32 - 1),
    );
    let (_, seed, _) = cpus::compile_cpu_with_options(
        cpus::load_cpu(cpu),
        cpu,
        CompilerOptions {
            llvm: true,
            ..Default::default()
        },
    );
    let initial = seed.benchmark_movement_profile(false).expect("requires a guard diagnostic DLL");
    assert_eq!(initial.len(), 5);
    assert_eq!(initial[0], u32::MAX as u64, "requires a fresh guard diagnostic module");
    assert!((2..=32).contains(&initial[3]));
    let mut results = Vec::new();
    for budget in 0..=initial[3] + 1 {
        let world = cpus::load_cpu(cpu);
        let mut compiler = seed
            .benchmark_restart(&world, bounds, world.scheduler().iter_entries().collect())
            .unwrap();
        compiler.try_on_use_block(cpu.origin + cpu.start).unwrap();
        for _ in 0..413 {
            compiler.tick().unwrap();
        }
        compiler.try_on_use_block(cpu.origin + BlockPos::new(207, 10, 154)).unwrap();
        compiler.benchmark_movement_profile(true);
        let started = Instant::now();
        let mut found = false;
        for tick in 1..=5000 {
            let result = compiler.tick();
            let probe = compiler.benchmark_movement_profile(false).unwrap();
            assert_eq!(probe[0], budget);
            if probe[1] == 0 {
                let snapshot = compiler.benchmark_snapshot().unwrap();
                let bytes: Vec<_> = snapshot.iter().flat_map(|word| word.to_le_bytes()).collect();
                results.push(json!({"remaining_budget":budget,"range_cost":probe[3],"run_tick":tick,
                    "injection_tick":probe[2],"specialized_calls":probe[4],"error":result.err().map(|error| error.to_string()),
                    "state_sha256":format!("{:x}",Sha256::digest(bytes)),"snapshot_words":snapshot.len()}));
                found = true;
                break;
            }
            result.unwrap();
        }
        assert!(found, "profiled range was not reached");
        eprintln!("guard budget {budget} checked in {:?}", started.elapsed());
        std::fs::write(&output, serde_json::to_vec_pretty(&results).unwrap()).unwrap();
    }
}
