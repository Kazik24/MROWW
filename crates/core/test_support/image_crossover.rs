//! Opt-in PM1 image crossover. All loader/allocation/validation work is outside tick intervals.
use super::{cpus, pm1};
use mchprs_core::{
    plot::PlotWorld,
    redpilator::{Compiler, CompilerOptions},
};
use serde::Deserialize;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    ffi::c_void,
    fs::File,
    os::windows::{ffi::OsStrExt, io::AsRawHandle},
    path::{Path, PathBuf},
    time::Instant,
};

#[link(name = "kernel32")]
unsafe extern "system" {
    fn LoadLibraryW(path: *const u16) -> *mut c_void;
    fn FreeLibrary(module: *mut c_void) -> i32;
    fn GetModuleFileNameW(module: *mut c_void, out: *mut u16, count: u32) -> u32;
    fn GetFileInformationByHandle(file: *mut c_void, out: *mut u32) -> i32;
    fn GetCurrentProcessorNumber() -> u32;
    fn GetCurrentProcess() -> *mut c_void;
    fn GetProcessAffinityMask(process: *mut c_void, mask: *mut usize, system: *mut usize) -> i32;
    fn GetPriorityClass(process: *mut c_void) -> u32;
}

struct Image {
    handle: *mut c_void,
    path: PathBuf,
}
impl Image {
    fn load(path: &Path) -> Self {
        let wide: Vec<u16> = path.as_os_str().encode_wide().chain(Some(0)).collect();
        // SAFETY: This experiment loads only its checked, owned native images.
        let handle = unsafe { LoadLibraryW(wide.as_ptr()) };
        assert!(!handle.is_null(), "LoadLibraryW: {}", path.display());
        let mut name = vec![0u16; 32768];
        // SAFETY: Owning live handle and writable buffer of the supplied size.
        let length = unsafe { GetModuleFileNameW(handle, name.as_mut_ptr(), name.len() as u32) };
        assert!(length > 0 && length < name.len() as u32);
        let loaded = PathBuf::from(String::from_utf16(&name[..length as usize]).unwrap());
        assert_eq!(loaded, path, "loader selected another image");
        Self { handle, path: loaded }
    }
}
impl Drop for Image {
    fn drop(&mut self) {
        // SAFETY: Each successful LoadLibraryW reference is released once, after its executors.
        assert_ne!(unsafe { FreeLibrary(self.handle) }, 0);
    }
}

fn file_id(path: &Path) -> Value {
    let file = File::open(path).unwrap();
    let mut info = [0u32; 13]; // BY_HANDLE_FILE_INFORMATION: 13 DWORDs.
    // SAFETY: File owns the live handle; info covers the full Win32 output structure.
    assert_ne!(unsafe { GetFileInformationByHandle(file.as_raw_handle().cast(), info.as_mut_ptr()) }, 0);
    json!({"volume":info[7],"index":(u64::from(info[11]) << 32) | u64::from(info[12])})
}

#[derive(Deserialize)]
struct Trial {
    block: usize,
    comparison: String,
    mapping: usize,
    load: usize,
    execute: usize,
    allocate: usize,
    path_set: usize,
    warm: usize,
    comparison_first: usize,
}
#[derive(Deserialize)]
struct Session {
    root: PathBuf,
    masters: [PathBuf; 2],
    trials: Vec<Trial>,
}
#[derive(Deserialize)]
struct Plan {
    artifact_sha256: String,
    tick_rva: usize,
    helpers: BTreeMap<String, usize>,
    sessions: Vec<Session>,
}

fn fresh(seed: &Compiler, protocol: &pm1::Protocol, path: &Path) -> (PlotWorld, pm1::Bounds, Compiler) {
    let (world, bounds) = pm1::load(protocol);
    let mut compiler = seed
        .benchmark_restart_module(&world, bounds, world.scheduler().iter_entries().collect(), path)
        .unwrap();
    compiler.benchmark_tick_batch(0, 0).unwrap();
    (world, bounds, compiler)
}

fn finish(state: &mut (PlotWorld, pm1::Bounds, Compiler), protocol: &pm1::Protocol, reference: &Value, expected: &mut Option<Vec<u32>>) -> String {
    let snapshot = state.2.benchmark_snapshot().unwrap();
    if let Some(expected) = expected {
        assert!(&snapshot == expected, "semantic snapshot differs");
    } else {
        *expected = Some(snapshot.clone());
    }
    let bytes: Vec<_> = snapshot.iter().flat_map(|word| word.to_le_bytes()).collect();
    let hash = format!("{:x}", Sha256::digest(bytes));
    state.2.try_reset(&mut state.0, state.1).unwrap();
    assert_eq!(pm1::outputs(&state.0, protocol), reference["outputs"]);
    assert_eq!(json!(state.0.command_output().collect::<Vec<_>>()), reference["commands"]);
    assert!(pm1::quiet(&state.0));
    for _ in 0..2 {
        state.0.tick_interpreted();
    }
    assert!(pm1::quiet(&state.0));
    assert_eq!(pm1::outputs(&state.0, protocol), reference["outputs"]);
    assert_eq!(json!(state.0.command_output().collect::<Vec<_>>()), reference["commands"]);
    hash
}

pub fn run(args: &[String]) {
    assert_eq!(args.len(), 3, "--image-crossover PLAN SESSION OUTPUT");
    let plan: Plan = serde_json::from_slice(&std::fs::read(&args[0]).unwrap()).unwrap();
    let session_index: usize = args[1].parse().unwrap();
    let session = &plan.sessions[session_index];
    let output = Path::new(&args[2]);
    assert!(!output.exists(), "use a fresh results path");
    let ids: Vec<_> = session
        .masters
        .iter()
        .map(|path| {
            let data = std::fs::read(path).unwrap();
            assert_eq!(format!("{:x}", Sha256::digest(data)), plan.artifact_sha256);
            file_id(path)
        })
        .collect();
    assert_ne!(ids[0], ids[1], "masters must be independent copies");
    let protocol = pm1::protocol(Path::new("test_data/cpu-references/pm1_redpilator_protocol.json"));
    let reference: Value = serde_json::from_slice(&std::fs::read("test_data/cpu-references/pm1_redpilator_reference.json").unwrap()).unwrap();
    assert_eq!(reference["ticks"], protocol.ticks);
    let seed = cpus::compile_cpu_with_options(
        pm1::load(&protocol).0,
        pm1::cpu(&protocol),
        CompilerOptions::parse("--llvm --publish-io").unwrap(),
    )
    .1;
    let mut mask = 0usize;
    let mut system = 0usize;
    // SAFETY: Current-process pseudo handle and writable scalar output buffers.
    let priority = unsafe {
        assert_ne!(GetProcessAffinityMask(GetCurrentProcess(), &mut mask, &mut system), 0);
        GetPriorityClass(GetCurrentProcess())
    };
    assert_eq!(mask, 256);
    assert_eq!(priority, 0x80, "High priority required");
    let origin = Instant::now();
    let mut expected = None;
    let mut results = Vec::new();
    for trial in &session.trials {
        let pair_start = origin.elapsed().as_nanos();
        let same = trial.comparison == "same";
        assert!(same || trial.comparison == "separate");
        let slots: [PathBuf; 2] = std::array::from_fn(|slot| session.root.join(format!("paths{}/image{slot}.dll", trial.path_set)));
        for (slot, path) in slots.iter().enumerate() {
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            assert!(!path.exists(), "owned alias was not released");
            let master = if same { trial.mapping } else { slot ^ trial.mapping };
            std::fs::hard_link(&session.masters[master], path).unwrap();
        }
        let paths: [PathBuf; 2] = if same {
            [slots[0].clone(), slots[0].clone()]
        } else {
            [slots[trial.mapping].clone(), slots[1 ^ trial.mapping].clone()]
        };
        let order = |bit| if bit == 0 { [0usize, 1] } else { [1usize, 0] };
        let mut images: [Option<Image>; 2] = [None, None];
        let mut load_ns = [0u128; 2];
        for label in order(trial.load) {
            images[label] = Some(Image::load(&paths[label]));
            load_ns[label] = origin.elapsed().as_nanos();
        }
        let images = images.map(Option::unwrap);
        assert_eq!(images[0].handle == images[1].handle, same, "separate-image condition was not achieved");
        let mut warmups = [None, None];
        for label in order(trial.allocate) {
            warmups[label] = Some(fresh(&seed, &protocol, &paths[label]));
        }
        for label in order(trial.warm) {
            let state = warmups[label].as_mut().unwrap();
            state.2.try_on_use_block(pm1::cpu(&protocol).origin + pm1::cpu(&protocol).start).unwrap();
            for _ in 0..protocol.ticks {
                state.2.tick().unwrap();
            }
        }
        for label in order(trial.warm) {
            finish(warmups[label].as_mut().unwrap(), &protocol, &reference, &mut expected);
        }
        drop(warmups);
        let mut states = [None, None];
        let mut allocation_ns = [0u128; 2];
        for label in order(trial.allocate) {
            states[label] = Some(fresh(&seed, &protocol, &paths[label]));
            allocation_ns[label] = origin.elapsed().as_nanos();
        }
        let mut rows = Vec::new();
        for label in 0..2 {
            let state = states[label].as_mut().unwrap();
            let addresses = state.2.benchmark_addresses();
            let base = images[label].handle as usize;
            assert_eq!(addresses[0], base + plan.tick_rva, "executor uses another image");
            let helpers: BTreeMap<_, _> = plan.helpers.iter().map(|(name, rva)| (name, base + rva)).collect();
            let identity = file_id(&paths[label]);
            let master = if same { trial.mapping } else { label };
            assert_eq!(identity, ids[master]);
            rows.push(json!({"label":label,"master":master,"path":images[label].path,"file_id":identity,
                "module_base":base,"tick_address":addresses[0],"state_address":addresses[1],"state_bytes":addresses[2],
                "helpers":helpers,"load_rank":usize::from(label != trial.load),"load_ns":load_ns[label],
                "allocation_rank":usize::from(label != trial.allocate),"allocation_ns":allocation_ns[label],
                "artifact_sha256":plan.artifact_sha256}));
        }
        let mut elapsed = [0u128; 2];
        let mut offsets = [0u128; 2];
        let mut cpus = [0u32; 2];
        for label in order(trial.execute) {
            let state = states[label].as_mut().unwrap();
            state.2.try_on_use_block(pm1::cpu(&protocol).origin + pm1::cpu(&protocol).start).unwrap();
            // SAFETY: Read-only processor identity, outside the interval.
            cpus[label] = unsafe { GetCurrentProcessorNumber() };
            offsets[label] = origin.elapsed().as_nanos();
            let started = Instant::now();
            for _ in 0..protocol.ticks {
                state.2.tick().unwrap();
            }
            elapsed[label] = started.elapsed().as_nanos();
        }
        // No snapshot/publication/reset or allocation work between the two measured intervals.
        for label in 0..2 {
            let hash = finish(states[label].as_mut().unwrap(), &protocol, &reference, &mut expected);
            rows[label]["snapshot_sha256"] = json!(hash);
            rows[label]["cpu"] = json!(cpus[label]);
            rows[label]["start_ns"] = json!(offsets[label]);
            rows[label]["elapsed_ns"] = json!(elapsed[label]);
            rows[label]["execution_rank"] = json!(usize::from(label != trial.execute));
        }
        drop(states);
        drop(images);
        for path in &slots {
            std::fs::remove_file(path).unwrap();
        } // Only aliases created by this iteration.
        results.push(
            json!({"block":trial.block,"comparison":trial.comparison,"mapping":trial.mapping,"load":trial.load,
            "execute":trial.execute,"allocate":trial.allocate,"path_set":trial.path_set,"warm":trial.warm,
            "comparison_first":trial.comparison_first,"pair_start_ns":pair_start,"samples":rows}),
        );
        std::fs::write(
            output,
            serde_json::to_vec_pretty(&json!({"session":session_index,"pid":std::process::id(),
            "affinity":mask,"priority":priority,"master_ids":ids,"pairs":results}))
            .unwrap(),
        )
        .unwrap();
        println!(
            "CROSSOVER session={session_index} block={} comparison={} pairs={}",
            trial.block,
            trial.comparison,
            results.len()
        );
    }
}
