//! Compilation ownership, Linux memory headroom, and fallible native buffers.
use super::scratch::ScratchError;

const LOW_MEMORY: &str = "Redpilator compilation stopped: insufficient server memory; free memory or compile a smaller circuit";

pub(super) fn allocation_error() -> ScratchError {
    ScratchError {
        position: None,
        reason: LOW_MEMORY,
    }
}

pub(super) fn reserve<T>(values: &mut Vec<T>, additional_elements: usize) -> Result<(), ScratchError> {
    #[cfg(target_os = "linux")]
    {
        let required = values.len().checked_add(additional_elements).ok_or_else(allocation_error)?;
        if required > values.capacity() {
            let bytes = required.checked_mul(std::mem::size_of::<T>()).ok_or_else(allocation_error)?;
            if under_pressure(bytes as u64) {
                return Err(allocation_error());
            }
        }
    }
    values.try_reserve_exact(additional_elements).map_err(|_| allocation_error())
}

pub(super) fn zeroed<T: Default + Clone>(length: usize) -> Result<Vec<T>, ScratchError> {
    let mut values = Vec::new();
    reserve(&mut values, length)?;
    values.resize(length, T::default());
    Ok(values)
}

// Large compilation phases and their native subprocesses share the server's memory limit.
static COMPILATION: std::sync::Mutex<()> = std::sync::Mutex::new(());

pub(super) struct Compilation {
    _owner: std::sync::MutexGuard<'static, ()>,
    stop: Option<std::sync::mpsc::Sender<()>>,
    watcher: Option<std::thread::JoinHandle<()>>,
}

pub(super) fn compile(monitor: &std::sync::Arc<super::TaskMonitor>) -> Result<Compilation, ScratchError> {
    use std::sync::TryLockError;
    let mut waiting = false;
    let owner = loop {
        if monitor.cancelled() {
            return Err(if monitor.memory_exhausted() {
                allocation_error()
            } else {
                ScratchError {
                    position: None,
                    reason: "compilation cancelled",
                }
            });
        }
        match COMPILATION.try_lock() {
            Ok(owner) => break owner,
            Err(TryLockError::Poisoned(error)) => break error.into_inner(),
            Err(TryLockError::WouldBlock) => {
                if !waiting {
                    monitor.set_phase("waiting for compilation memory");
                    waiting = true;
                }
                std::thread::sleep(std::time::Duration::from_millis(10));
            }
        }
    };
    #[cfg(target_os = "linux")]
    let (stop, watcher) = {
        if under_pressure(0) {
            return Err(allocation_error());
        }
        let monitor = monitor.clone();
        let (stop, stopped) = std::sync::mpsc::channel();
        let watcher = std::thread::Builder::new()
            .name("compile-memory".into())
            .spawn(move || {
                // shortcut: pressure is sampled; add accounting at unchecked allocations if bursts still exhaust the cgroup.
                while stopped.recv_timeout(std::time::Duration::from_millis(25)) == Err(std::sync::mpsc::RecvTimeoutError::Timeout) {
                    if under_pressure(0) {
                        monitor.cancel_for_memory();
                        break;
                    }
                }
            })
            .map_err(|_| ScratchError {
                position: None,
                reason: "could not start compilation memory monitor",
            })?;
        (Some(stop), Some(watcher))
    };
    #[cfg(not(target_os = "linux"))]
    let (stop, watcher) = (None, None);
    Ok(Compilation {
        _owner: owner,
        stop,
        watcher,
    })
}

impl Drop for Compilation {
    fn drop(&mut self) {
        if let Some(stop) = self.stop.take() {
            let _ = stop.send(());
        }
        if let Some(watcher) = self.watcher.take() {
            let _ = watcher.join();
        }
    }
}

#[cfg(target_os = "linux")]
fn cgroup_available(directory: &std::path::Path, v2: bool) -> Option<u64> {
    let number = |name| std::fs::read_to_string(directory.join(name)).ok()?.trim().parse::<u64>().ok();
    let limit = number(if v2 { "memory.max" } else { "memory.limit_in_bytes" })?;
    let used = number(if v2 { "memory.current" } else { "memory.usage_in_bytes" })?;
    let stats = std::fs::read_to_string(directory.join("memory.stat")).unwrap_or_default();
    let statistic = |name: &str| {
        stats
            .lines()
            .find_map(|line| {
                let (key, value) = line.split_once(' ')?;
                (key == name).then(|| value.parse::<u64>().ok()).flatten()
            })
            .unwrap_or(0)
    };
    // Clean file pages (including generated compiler input) can be reclaimed by the kernel.
    let reclaimable = statistic(if v2 { "file" } else { "total_cache" })
        .saturating_sub(statistic(if v2 { "shmem" } else { "total_shmem" }))
        .saturating_sub(statistic(if v2 { "file_dirty" } else { "total_dirty" }))
        .saturating_sub(statistic(if v2 { "file_writeback" } else { "total_writeback" }));
    Some(limit.saturating_sub(used.saturating_sub(reclaimable)))
}

#[cfg(target_os = "linux")]
fn under_pressure(additional: u64) -> bool {
    let headroom = (256u64 * 1024 * 1024).saturating_add(additional);
    if let Ok(info) = std::fs::read_to_string("/proc/meminfo") {
        if let Some(available) = info
            .lines()
            .find_map(|line| line.strip_prefix("MemAvailable:")?.split_whitespace().next()?.parse::<u64>().ok())
        {
            if available.saturating_mul(1024) < headroom {
                return true;
            }
        }
    }
    if let Ok(groups) = std::fs::read_to_string("/proc/self/cgroup") {
        for line in groups.lines() {
            let mut fields = line.splitn(3, ':');
            let _ = fields.next();
            let Some(controllers) = fields.next() else {
                continue;
            };
            let Some(path) = fields.next() else {
                continue;
            };
            let v2 = controllers.is_empty();
            if !v2 && !controllers.split(',').any(|controller| controller == "memory") {
                continue;
            }
            let root = std::path::Path::new(if v2 { "/sys/fs/cgroup" } else { "/sys/fs/cgroup/memory" });
            let mut directory = root.join(path.trim_start_matches('/'));
            if !directory.is_dir() {
                directory = root.to_owned();
            }
            while directory.starts_with(root) {
                if cgroup_available(&directory, v2).is_some_and(|available| available < headroom) {
                    return true;
                }
                if directory == root || !directory.pop() {
                    break;
                }
            }
        }
    }
    false
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn allocation_overflow_preserves_existing_values() {
        assert_eq!(zeroed::<u64>(usize::MAX).unwrap_err().reason, LOW_MEMORY);
        let mut values = vec![1u64, 2];
        assert_eq!(reserve(&mut values, usize::MAX).unwrap_err().reason, LOW_MEMORY);
        assert_eq!(values, [1, 2]);
    }
    #[test]
    fn compilation_memory_slot_is_exclusive_and_waiting_is_cancellable() {
        let owner = COMPILATION.lock().unwrap_or_else(|error| error.into_inner());
        let monitor = std::sync::Arc::new(super::super::TaskMonitor::default());
        let waiting = monitor.clone();
        let worker = std::thread::spawn(move || compile(&waiting).map(|_owner| ()));
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
        while !monitor.status().contains("waiting for compilation memory") {
            assert!(std::time::Instant::now() < deadline);
            std::thread::sleep(std::time::Duration::from_millis(1));
        }
        assert!(!worker.is_finished());
        monitor.cancel();
        assert_eq!(worker.join().unwrap().unwrap_err().reason, "compilation cancelled");
        drop(owner);
        assert!(compile(&std::sync::Arc::new(super::super::TaskMonitor::default())).is_ok());
    }

    #[test]
    #[cfg(target_os = "linux")]
    fn cgroup_headroom_accounts_for_reclaimable_pages_and_saturates() {
        let root = std::env::temp_dir().join(format!("mroww-memory-limit-{}-{}", std::process::id(), rand::random::<u64>()));
        std::fs::create_dir(&root).unwrap();
        for v2 in [false, true] {
            let limit = if v2 { "memory.max" } else { "memory.limit_in_bytes" };
            let current = if v2 { "memory.current" } else { "memory.usage_in_bytes" };
            let cache = if v2 { "file" } else { "total_cache" };
            std::fs::write(root.join(limit), "1000").unwrap();
            std::fs::write(root.join(current), "900").unwrap();
            std::fs::write(root.join("memory.stat"), format!("{cache} 200\n")).unwrap();
            assert_eq!(cgroup_available(&root, v2), Some(300));
            let (shared, dirty, writeback) = if v2 {
                ("shmem", "file_dirty", "file_writeback")
            } else {
                ("total_shmem", "total_dirty", "total_writeback")
            };
            std::fs::write(
                root.join("memory.stat"),
                format!("{cache} 200\n{shared} 50\n{dirty} 100\n{writeback} 25\n"),
            )
            .unwrap();
            assert_eq!(cgroup_available(&root, v2), Some(125));
            std::fs::write(root.join(current), "1500").unwrap();
            assert_eq!(cgroup_available(&root, v2), Some(0));
            std::fs::write(root.join(limit), "max").unwrap();
            assert_eq!(cgroup_available(&root, v2), None);
        }
        std::fs::remove_dir_all(root).unwrap();
    }
}
