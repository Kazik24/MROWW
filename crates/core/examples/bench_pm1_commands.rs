//! Alternating paired LLVM runs with PM1 command blocks enabled or compiled out.
#[path = "../test_support/cpus.rs"]
#[allow(dead_code)]
mod cpus;
#[path = "../test_support/pm1.rs"]
#[allow(dead_code)]
mod pm1;

use mchprs_core::{plot::benchmark, redpilator::CompilerOptions, world::World};
use serde_json::{Value, json};
use std::{path::PathBuf, time::Instant};

fn median(values: &mut [f64]) -> f64 {
    values.sort_by(f64::total_cmp);
    (values[(values.len() - 1) / 2] + values[values.len() / 2]) / 2.0
}

fn main() {
    let args: Vec<_> = std::env::args().skip(1).collect();
    let output = PathBuf::from(args.first().expect("output JSON path required"));
    assert!(!output.exists(), "refusing to overwrite a benchmark report");
    let samples: usize = args.get(1).map_or(6, |value| value.parse().unwrap());
    assert!(samples > 0);
    let relaxed = args.iter().any(|arg| arg == "--relaxed");
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../test_data/cpu-references");
    let protocol = pm1::protocol(&root.join("pm1_redpilator_protocol.json"));
    let reference: Value = serde_json::from_slice(&std::fs::read(root.join("pm1_redpilator_reference.json")).unwrap()).unwrap();
    assert_eq!(reference["fixture_sha256"], pm1::SHA256);
    assert_eq!(reference["ticks"], protocol.ticks);
    let cpu = pm1::cpu(&protocol);
    let mut seeds = Vec::new();
    for ignored in [false, true] {
        let flags = format!(
            "--llvm --publish-io {} {}",
            if relaxed { "--relaxed" } else { "" },
            if ignored { "--ignore-command-blocks" } else { "" }
        );
        let seed = cpus::compile_cpu_with_options(pm1::load(&protocol).0, cpu, CompilerOptions::parse(&flags).unwrap()).1;
        eprintln!(
            "PM1 commands ignored={ignored}: components={}, compile={:?}",
            seed.stats().unwrap().components,
            seed.stats().unwrap().duration
        );
        seeds.push(seed);
    }
    benchmark::pin_cpu(24);
    let mut rows = Vec::new();
    let mut times = [Vec::new(), Vec::new()];
    let mut gains = Vec::new();
    // The first pair warms both native modules and is excluded from the report.
    for pair in 0..=samples {
        let mut pair_times = [0.0; 2];
        for variant in if pair % 2 == 0 { [0, 1] } else { [1, 0] } {
            let (mut world, bounds) = pm1::load(&protocol);
            let original_commands: Vec<_> = world
                .get_chunks()
                .iter()
                .flat_map(|chunk| {
                    chunk.block_entities.iter().filter_map(move |(&pos, entity)| match entity {
                        mchprs_blocks::block_entities::BlockEntity::CommandBlock(entity) => Some((
                            mchprs_blocks::BlockPos::new(chunk.x * 16 + pos.x, pos.y, chunk.z * 16 + pos.z),
                            entity.clone(),
                        )),
                        _ => None,
                    })
                })
                .collect();
            assert_eq!(original_commands.len(), 60, "frozen PM1 command-block count changed");
            let mut compiler = seeds[variant].benchmark_restart(&world, bounds, vec![]).unwrap();
            compiler.try_on_use_block(cpu.origin + cpu.start).unwrap();
            let started = Instant::now();
            for _ in 0..protocol.ticks {
                compiler.tick().unwrap();
            }
            let seconds = started.elapsed().as_secs_f64();
            compiler.try_reset(&mut world, bounds).unwrap();
            assert_eq!(pm1::outputs(&world, &protocol), reference["outputs"], "physical PM1 outputs differ");
            assert!(pm1::quiet(&world), "PM1 did not halt");
            let commands: Vec<_> = world.command_output().collect();
            if variant == 0 {
                assert_eq!(json!(commands), reference["commands"], "ordered messages differ");
            } else {
                assert!(commands.is_empty(), "disabled command blocks emitted messages");
                for (pos, entity) in &original_commands {
                    let Some(mchprs_blocks::block_entities::BlockEntity::CommandBlock(stored)) = world.get_block_entity(*pos) else {
                        panic!("disabled command data lost");
                    };
                    assert_eq!(stored, entity, "disabled command data changed");
                }
            }
            println!(
                "PM1_COMMANDS pair={pair} ignored={} relaxed={relaxed} tps={:.1} messages={}",
                variant == 1,
                f64::from(protocol.ticks) / seconds,
                commands.len()
            );
            if pair != 0 {
                pair_times[variant] = seconds;
                times[variant].push(seconds);
                rows.push(json!({"pair":pair,"ignore_command_blocks":variant==1,"seconds":seconds,
                    "tps":f64::from(protocol.ticks)/seconds,"messages":commands.len(),
                    "outputs_match_reference":true,"quiet":true}));
            }
        }
        if pair != 0 {
            gains.push((pair_times[0] / pair_times[1] - 1.0) * 100.0);
        }
    }
    let normal = median(&mut times[0]);
    let ignored = median(&mut times[1]);
    let paired_gain = median(&mut gains);
    let report = json!({"fixture_sha256":pm1::SHA256,"ticks":protocol.ticks,"relaxed":relaxed,
        "scope":"Headless LLVM, single tick calls, CPU 24, no timed publication; one warmup pair excluded; alternating paired runs. Compile, load and reset excluded.",
        "command_blocks_enabled":{"median_tps":f64::from(protocol.ticks)/normal,"components":seeds[0].stats().unwrap().components},
        "command_blocks_ignored":{"median_tps":f64::from(protocol.ticks)/ignored,"components":seeds[1].stats().unwrap().components},
        "median_tps_gain_percent":(normal/ignored-1.0)*100.0,"median_paired_gain_percent":paired_gain,
        "paired_gain_percent":gains,"samples":rows});
    std::fs::write(output, serde_json::to_vec_pretty(&report).unwrap()).unwrap();
    println!("PM1_COMMANDS median paired gain={paired_gain:.2}%");
}
