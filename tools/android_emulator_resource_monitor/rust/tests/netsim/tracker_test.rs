use super::proc::tests::env_of;
use super::sockets::tests::{LSOF_OUT, emu_groups_with};
use super::*;
use std::time::Duration;

#[test]
fn test_netsim_tracker_standalone_and_orphaned_and_exit() {
    let dir = tempfile::tempdir().unwrap();
    let art_dir = dir.path().join("android-test").join("netsimd");
    std::fs::create_dir_all(&art_dir).unwrap();
    let log_path = art_dir.join("netsim_stderr.log");
    std::fs::write(
        &log_path,
        "netsim I 09-25 22:37:05.612 netsimd.rs:451 - Netsim Version: 1.0.24, OS: linux, Arch: x86_64\n\
         netsim I 09-25 22:37:05.683 netsimd.rs:744 - Wrote to INI file /tmp/netsim.ini\n",
    )
    .unwrap();

    let mut env = HashMap::new();
    env.insert("TMPDIR".to_string(), dir.path().to_string_lossy().to_string());
    env.insert("USER".to_string(), "test".to_string());
    let procs = vec![NetsimDaemonProc {
        pid: 5_009_001,
        parent: Some(1),
        parent_name: None,
        start_time: 1790375825,
        exe: Some(PathBuf::from("/sdk/emulator/bin/netsimd")),
        cmd: vec!["/sdk/emulator/bin/netsimd".to_string(), "--no-shutdown".to_string()],
        environ: env,
        cpu: 1.0,
        ram_mb: 30.0,
        uid: None,
        same_user: true,
    }];

    let mut tracker = NetsimTracker::new(None, 500, true, dir.path());
    tracker.host_os = HostOs::Linux;
    tracker.custom_ini_dirs = vec![dir.path().to_path_buf()];
    tracker.custom_proc_locks = Some(String::new());

    tracker.update(&procs, &HashMap::new(), &[], &HashMap::new(), 1790375826.0, 10.0);
    assert!(
        tracker
            .timeline
            .iter()
            .any(|(_, e)| e.contains("STARTED(pid=5009001, spawner=STANDALONE)"))
    );
    assert!(tracker.timeline.iter().any(|(_, e)| e.contains("READY(")));

    tracker.update(&procs, &HashMap::new(), &[], &HashMap::new(), 1790375860.0, 45.0);
    assert!(tracker.timeline.iter().any(|(_, e)| e.contains("ORPHANED(pid=5009001)")));

    let mut f = std::fs::OpenOptions::new().append(true).open(&log_path).unwrap();
    use std::io::Write;
    writeln!(f, "netsim I 09-25 22:37:50.000 netsimd.rs:800 - Shutting down gracefully").unwrap();

    tracker.update(&[], &HashMap::new(), &[], &HashMap::new(), 1790375870.0, 55.0);
    assert!(
        tracker
            .timeline
            .iter()
            .any(|(_, e)| e.contains("EXITED(pid=5009001, reason=sigterm_sigint)"))
    );
}

/// PIDs above Linux's PID_MAX_LIMIT (2^22): no `/proc` lookup or liveness
/// probe reaches a real process, so `start_wall` equals the create time.
const DAEMON_A: u32 = 5_000_100;
const DAEMON_B: u32 = 5_000_200;
const DEAD_PID: u32 = 5_000_999;
const START: u64 = 1_790_375_825;

fn write_ini(path: &Path, content: &str, mtime: u64) {
    std::fs::write(path, content).unwrap();
    std::fs::File::options()
        .write(true)
        .open(path)
        .unwrap()
        .set_modified(UNIX_EPOCH + Duration::from_secs(mtime))
        .unwrap();
}

fn netsimd(pid: u32, start: u64, tmp: &Path) -> NetsimDaemonProc {
    NetsimDaemonProc {
        pid,
        parent: Some(1),
        start_time: start,
        exe: Some(PathBuf::from("/sdk/emulator/netsimd")),
        cmd: vec!["/sdk/emulator/netsimd".to_string()],
        environ: env_of(&[("TMPDIR", tmp.to_str().unwrap()), ("USER", "test")]),
        same_user: true,
        ..Default::default()
    }
}

/// A tracker that scans only `dirs` and sees no `/proc/locks` entries, as
/// on macOS and Windows.
fn tracker_for(dirs: &[&Path], out: &Path) -> NetsimTracker {
    let mut tracker = NetsimTracker::new(None, 500, false, out);
    tracker.host_os = HostOs::Linux;
    tracker.custom_ini_dirs = if dirs.is_empty() {
        vec![out.to_path_buf()]
    } else {
        dirs.iter().map(|d| d.to_path_buf()).collect()
    };
    tracker.custom_proc_locks = Some(String::new());
    tracker
}

fn tick(tracker: &mut NetsimTracker, procs: &[NetsimDaemonProc], now_wall: u64) {
    let now_mono = (now_wall - START) as f64;
    tracker.update(procs, &HashMap::new(), &[], &HashMap::new(), now_wall as f64, now_mono);
}

fn count_events(tracker: &NetsimTracker, prefix: &str) -> usize {
    tracker.timeline.iter().filter(|(_, e)| e.starts_with(prefix)).count()
}

#[test]
fn test_pid_exists() {
    assert!(pid_exists(std::process::id()));
    assert!(!pid_exists(DEAD_PID));
}

#[cfg(unix)]
#[test]
fn test_same_file_resolves_symlinks() {
    let root = tempfile::tempdir().unwrap();
    let real = root.path().join("real");
    std::fs::create_dir_all(&real).unwrap();
    std::fs::write(real.join("netsim.ini"), "").unwrap();
    std::fs::write(root.path().join("other.ini"), "").unwrap();
    let alias = root.path().join("alias");
    std::os::unix::fs::symlink(&real, &alias).unwrap();
    assert!(same_file(&alias.join("netsim.ini"), &real.join("netsim.ini")));
    assert!(!same_file(&real.join("netsim.ini"), &root.path().join("other.ini")));
    // Missing files compare by path.
    assert!(same_file(Path::new("/nonexistent/a.ini"), Path::new("/nonexistent/a.ini")));
    assert!(!same_file(Path::new("/nonexistent/a.ini"), Path::new("/nonexistent/b.ini")));
}

#[cfg(unix)]
#[test]
fn test_discover_ini_files_dedupes_aliases_and_honors_custom_dirs() {
    let root = tempfile::tempdir().unwrap();
    let real = root.path().join("real");
    std::fs::create_dir_all(&real).unwrap();
    for name in [
        "netsim.ini",
        "netsim_2.ini",
        "netsim.ini.lock",
        "netsim_2.ini.init.lock",
        "netsim_x.ini",
        "pid_42.ini",
    ] {
        std::fs::write(real.join(name), "").unwrap();
    }
    // macOS reaches one dir as both /tmp and /private/tmp, for example.
    let alias = root.path().join("alias");
    std::os::unix::fs::symlink(&real, &alias).unwrap();
    // A daemon's own TMPDIR is not searched while custom dirs are set.
    let other = root.path().join("other");
    std::fs::create_dir_all(&other).unwrap();
    std::fs::write(other.join("netsim_3.ini"), "").unwrap();

    let tracker = tracker_for(&[&alias, &real], root.path());
    let found = tracker.discover_ini_files(&[netsimd(DAEMON_A, START, &other)]);
    // One path per file, spelled as first seen.
    assert_eq!(found, vec![alias.join("netsim.ini"), alias.join("netsim_2.ini")]);
}

#[test]
fn test_netsim_tracker_stale_ini_does_not_steal_live_daemon() {
    // A leftover INI from a dead daemon must not override the live INI,
    // whichever sorts first and whether or not netsim wrote `pid=`.
    for with_pid in [false, true] {
        for stale_first in [false, true] {
            let ctx = format!("with_pid={with_pid} stale_first={stale_first}");
            let root = tempfile::tempdir().unwrap();
            let (dir_a, dir_b) = (root.path().join("a"), root.path().join("b"));
            std::fs::create_dir_all(&dir_a).unwrap();
            std::fs::create_dir_all(&dir_b).unwrap();
            let (stale_dir, live_dir) =
                if stale_first { (&dir_a, &dir_b) } else { (&dir_b, &dir_a) };
            let (stale_ini, live_ini) = (stale_dir.join("netsim.ini"), live_dir.join("netsim.ini"));
            let pid_line = |pid: u32| if with_pid { format!("pid={pid}\n") } else { String::new() };
            write_ini(&stale_ini, &format!("{}grpc.port=1111\n", pid_line(DEAD_PID)), START - 600);
            write_ini(&live_ini, &format!("{}grpc.port=43929\n", pid_line(DAEMON_A)), START + 1);

            let mut tracker = tracker_for(&[&dir_a, &dir_b], root.path());
            let procs = [netsimd(DAEMON_A, START, live_dir)];
            for now in [START + 10, START + 20] {
                tick(&mut tracker, &procs, now);
                let d = &tracker.daemons[&(DAEMON_A, START)];
                assert_eq!(d.grpc_port, Some(43929), "{ctx}");
                assert_eq!(d.ini_path.as_deref(), Some(live_ini.as_path()), "{ctx}");
                assert!(tracker.stale_inis.contains_key(&stale_ini), "{ctx}");
                assert!(!tracker.stale_inis.contains_key(&live_ini), "{ctx}");
            }
            assert_eq!(count_events(&tracker, "STALE_INI("), 1, "{ctx}");
            assert_eq!(tracker.restart_count, 0, "{ctx}");
        }
    }
}

#[test]
fn test_netsim_tracker_rejects_ini_with_reused_pid() {
    // The INI predates the daemon whose PID it names, so that PID was
    // reused by a new netsimd and the INI is stale.
    let root = tempfile::tempdir().unwrap();
    let ini = root.path().join("netsim.ini");
    write_ini(&ini, &format!("pid={DAEMON_A}\ngrpc.port=1111\n"), START - 600);
    let mut tracker = tracker_for(&[root.path()], root.path());
    tick(&mut tracker, &[netsimd(DAEMON_A, START, root.path())], START + 10);
    let d = &tracker.daemons[&(DAEMON_A, START)];
    assert_eq!(d.grpc_port, None);
    assert_eq!(d.ini_path, None);
    assert!(tracker.stale_inis.contains_key(&ini));
}

#[test]
fn test_netsim_tracker_reused_pid_of_live_daemon_is_stale() {
    // As above, but a process with the reused PID is alive: it is the
    // tracked daemon, not the INI's writer, so the INI is still stale.
    let root = tempfile::tempdir().unwrap();
    let ini = root.path().join("netsim.ini");
    write_ini(&ini, &format!("pid={DAEMON_A}\ngrpc.port=1111\n"), START - 600);
    let mut tracker = tracker_for(&[root.path()], root.path());
    tracker.pid_alive = |pid| pid == DAEMON_A;
    tick(&mut tracker, &[netsimd(DAEMON_A, START, root.path())], START + 10);
    assert_eq!(tracker.daemons[&(DAEMON_A, START)].ini_path, None);
    assert!(tracker.stale_inis.contains_key(&ini));
}

#[test]
fn test_netsim_tracker_counts_restart_once() {
    let root = tempfile::tempdir().unwrap();
    let ini = root.path().join("netsim.ini");
    write_ini(&ini, &format!("pid={DAEMON_A}\ngrpc.port=43929\n"), START + 1);
    let mut tracker = tracker_for(&[root.path()], root.path());
    tick(&mut tracker, &[netsimd(DAEMON_A, START, root.path())], START + 10);

    // netsimd B replaces A and rewrites the INI; later ticks see no restart.
    let b_start = START + 60;
    write_ini(&ini, &format!("pid={DAEMON_B}\ngrpc.port=43930\n"), b_start + 1);
    let procs_b = [netsimd(DAEMON_B, b_start, root.path())];
    tick(&mut tracker, &procs_b, b_start + 5);
    tick(&mut tracker, &procs_b, b_start + 10);

    assert_eq!(tracker.restart_count, 1);
    assert_eq!(count_events(&tracker, "RESTARTED("), 1);
    let restarted =
        format!("RESTARTED(ini={}, old_pid={DAEMON_A}, new_pid={DAEMON_B})", ini.display());
    assert!(tracker.timeline.iter().any(|(_, e)| *e == restarted), "{:?}", tracker.timeline);
    assert_eq!(tracker.daemons[&(DAEMON_B, b_start)].grpc_port, Some(43930));
    assert!(tracker.daemons[&(DAEMON_A, START)].exited);
    assert!(tracker.stale_inis.is_empty());
}

#[cfg(unix)]
#[test]
fn test_netsim_tracker_lock_holder_beats_conflicting_pid() {
    // The INI's lock holder (B) wins over the daemon its `pid=` names (A).
    let root = tempfile::tempdir().unwrap();
    let ini = root.path().join("netsim.ini");
    write_ini(&ini, &format!("pid={DAEMON_A}\ngrpc.port=43929\n"), START + 1);
    let lock = root.path().join("netsim.ini.lock");
    std::fs::write(&lock, "").unwrap();
    let lock_key = get_file_lock_key(&lock).unwrap();

    let mut tracker = tracker_for(&[root.path()], root.path());
    tracker.custom_proc_locks =
        Some(format!("1: FLOCK  ADVISORY  WRITE {DAEMON_B} {lock_key} 0 EOF\n"));
    let procs = [netsimd(DAEMON_A, START, root.path()), netsimd(DAEMON_B, START, root.path())];
    tick(&mut tracker, &procs, START + 10);

    let (a, b) = (&tracker.daemons[&(DAEMON_A, START)], &tracker.daemons[&(DAEMON_B, START)]);
    assert_eq!(b.ini_path.as_deref(), Some(ini.as_path()));
    assert_eq!(b.grpc_port, Some(43929));
    assert_eq!(a.ini_path, None);
    assert_eq!(a.grpc_port, None);
    assert!(tracker.stale_inis.is_empty());
}

#[cfg(unix)]
#[test]
fn test_netsim_tracker_ini_with_untracked_holder_ignores_pid() {
    // An INI with lock holders pairs only through them: an untracked
    // holder (B) keeps the daemon its `pid=` names (A) from taking it,
    // and a held INI is not stale.
    let root = tempfile::tempdir().unwrap();
    let ini = root.path().join("netsim.ini");
    write_ini(&ini, &format!("pid={DAEMON_A}\ngrpc.port=43929\n"), START + 1);
    let lock = root.path().join("netsim.ini.lock");
    std::fs::write(&lock, "").unwrap();
    let lock_key = get_file_lock_key(&lock).unwrap();

    let mut tracker = tracker_for(&[root.path()], root.path());
    tracker.custom_proc_locks =
        Some(format!("1: FLOCK  ADVISORY  WRITE {DAEMON_B} {lock_key} 0 EOF\n"));
    tick(&mut tracker, &[netsimd(DAEMON_A, START, root.path())], START + 10);

    let a = &tracker.daemons[&(DAEMON_A, START)];
    assert_eq!(a.ini_path, None);
    assert_eq!(a.grpc_port, None);
    assert!(tracker.stale_inis.is_empty());
}

#[test]
fn test_netsim_tracker_instance_fallback_pairs_by_ini_name() {
    // Without lock or pid evidence, `--instance 2` pairs with
    // netsim_2.ini; the default-instance INI has no daemon and is stale.
    let root = tempfile::tempdir().unwrap();
    let (ini1, ini2) = (root.path().join("netsim.ini"), root.path().join("netsim_2.ini"));
    write_ini(&ini1, "grpc.port=1111\n", START + 1);
    write_ini(&ini2, "grpc.port=43929\n", START + 1);
    let mut proc2 = netsimd(DAEMON_A, START, root.path());
    proc2.cmd.extend(["--instance".to_string(), "2".to_string()]);

    let mut tracker = tracker_for(&[root.path()], root.path());
    tick(&mut tracker, &[proc2], START + 10);
    let d = &tracker.daemons[&(DAEMON_A, START)];
    assert_eq!(d.instance_num, Some(2));
    assert_eq!(d.grpc_port, Some(43929));
    assert_eq!(d.ini_path.as_deref(), Some(ini2.as_path()));
    assert!(tracker.stale_inis.contains_key(&ini1));
}

#[test]
fn test_netsim_tracker_pairs_same_instance_daemons_by_start_time() {
    // Two older-netsim daemons (no `pid=`) with different TMPDIRs each
    // pair with the INI written after their own start. B's INI sorts
    // first, so taking INIs in path order would give A the wrong one.
    let root = tempfile::tempdir().unwrap();
    let (dir_a, dir_b) = (root.path().join("a"), root.path().join("b"));
    std::fs::create_dir_all(&dir_a).unwrap();
    std::fs::create_dir_all(&dir_b).unwrap();
    let b_start = START + 100;
    write_ini(&dir_a.join("netsim.ini"), "grpc.port=2222\n", b_start + 1);
    write_ini(&dir_b.join("netsim.ini"), "grpc.port=1111\n", START + 1);

    let mut tracker = tracker_for(&[&dir_a, &dir_b], root.path());
    let procs = [netsimd(DAEMON_A, START, &dir_b), netsimd(DAEMON_B, b_start, &dir_a)];
    tick(&mut tracker, &procs, b_start + 5);
    assert_eq!(tracker.daemons[&(DAEMON_A, START)].grpc_port, Some(1111));
    assert_eq!(tracker.daemons[&(DAEMON_B, b_start)].grpc_port, Some(2222));
    assert!(tracker.stale_inis.is_empty());
}

#[test]
fn test_netsim_tracker_newest_daemon_takes_oldest_ini() {
    // Both pid-less INIs were written after both daemons started, so
    // either daemon could own either one. B, the newer daemon, chooses
    // first and takes the INI written closest to its start.
    let root = tempfile::tempdir().unwrap();
    let (dir_a, dir_b) = (root.path().join("a"), root.path().join("b"));
    std::fs::create_dir_all(&dir_a).unwrap();
    std::fs::create_dir_all(&dir_b).unwrap();
    let b_start = START + 100;
    write_ini(&dir_a.join("netsim.ini"), "grpc.port=1111\n", b_start + 50);
    write_ini(&dir_b.join("netsim.ini"), "grpc.port=2222\n", b_start + 1);

    let mut tracker = tracker_for(&[&dir_a, &dir_b], root.path());
    let procs = [netsimd(DAEMON_A, START, &dir_a), netsimd(DAEMON_B, b_start, &dir_b)];
    tick(&mut tracker, &procs, b_start + 60);
    assert_eq!(tracker.daemons[&(DAEMON_A, START)].grpc_port, Some(1111));
    assert_eq!(tracker.daemons[&(DAEMON_B, b_start)].grpc_port, Some(2222));
    assert!(tracker.stale_inis.is_empty());
}

#[test]
fn test_netsim_tracker_ini_instance_fallback_without_proc_locks() {
    let dir = tempfile::tempdir().unwrap();
    let ini_path = dir.path().join("netsim.ini");
    write_ini(&ini_path, "grpc.port=43929\n", START + 1);

    let procs = vec![NetsimDaemonProc {
        pid: DAEMON_A,
        parent: Some(8000),
        parent_name: Some("qemu-system-x86_64.exe".to_string()),
        start_time: START,
        exe: Some(PathBuf::from(r"C:\Android\Sdk\emulator\netsimd.exe")),
        cmd: vec![r"C:\Android\Sdk\emulator\netsimd.exe".to_string()],
        environ: env_of(&[("TMPDIR", dir.path().to_str().unwrap())]),
        cpu: 1.0,
        ram_mb: 30.0,
        uid: None,
        same_user: true,
    }];

    let mut tracker = tracker_for(&[dir.path()], dir.path());
    tick(&mut tracker, &procs, START + 10);
    let d = &tracker.daemons[&(DAEMON_A, START)];
    assert_eq!(d.spawner, "qemu");
    assert_eq!(d.grpc_port, Some(43929));
    assert_eq!(d.ini_path.as_deref(), Some(ini_path.as_path()));
    assert!(tracker.stale_inis.is_empty());
}

const WORKER_A: u32 = 5_000_300;
const WORKER_B: u32 = 5_000_400;

fn emu_group(serial: &str, pid: u32, avd_id: &str, name: Option<&str>) -> EmuGroupNetsimInfo {
    EmuGroupNetsimInfo {
        serial: serial.to_string(),
        primary_pid: pid,
        avd_id: Some(avd_id.to_string()),
        display_name: name.map(str::to_string),
        ..Default::default()
    }
}

/// One tick with no netsim daemons and the given live emulators, each as
/// (storage key, group).
fn emu_tick(tracker: &mut NetsimTracker, live: &[(&str, &EmuGroupNetsimInfo)], now_mono: f64) {
    emu_tick_with(tracker, &[], live, now_mono);
}

fn emu_tick_with(
    tracker: &mut NetsimTracker,
    procs: &[NetsimDaemonProc],
    live: &[(&str, &EmuGroupNetsimInfo)],
    now_mono: f64,
) {
    emu_tick_adb(tracker, procs, live, &[], now_mono);
}

/// Like emu_tick_with, with adb listing each (serial, state) in `adb`.
fn emu_tick_adb(
    tracker: &mut NetsimTracker,
    procs: &[NetsimDaemonProc],
    live: &[(&str, &EmuGroupNetsimInfo)],
    adb: &[(&str, &str)],
    now_mono: f64,
) {
    let groups: HashMap<String, EmuGroupNetsimInfo> =
        live.iter().map(|(_, g)| (g.serial.clone(), (*g).clone())).collect();
    let summary: Vec<(String, String, u32, f64)> = live
        .iter()
        .map(|(k, g)| (k.to_string(), g.serial.clone(), g.primary_pid, now_mono))
        .collect();
    let adb: HashMap<String, String> =
        adb.iter().map(|(s, st)| (s.to_string(), st.to_string())).collect();
    let now_wall = START as f64 + now_mono;
    tracker.update(procs, &groups, &summary, &adb, now_wall, now_mono);
}

#[test]
fn test_netsim_tracker_exited_emulator_disconnects() {
    // The emulator was connected from t=10 and has left the host scan by t=40.
    let dir = tempfile::tempdir().unwrap();
    let mut tracker = tracker_for(&[dir.path()], dir.path());
    tracker.emulator_records.insert(
        "pixel_emulator-5554".to_string(),
        EmulatorNetsimRecord {
            storage_key: "pixel_emulator-5554".to_string(),
            serial: "emulator-5554".to_string(),
            worker_pid: WORKER_A,
            state: "CONNECTED".to_string(),
            first_transport_ts: Some(START as f64 + 10.0),
            connected_start_mono: Some(10.0),
            adb_online_since_mono: Some(10.0),
            ..Default::default()
        },
    );

    // The first missed tick may be a scan hiccup, so the record is held.
    emu_tick(&mut tracker, &[], 40.0);
    let rec = &tracker.emulator_records["pixel_emulator-5554"];
    assert_eq!((rec.state.as_str(), rec.disconnect_count), ("CONNECTED", 0));

    // The second settles it as of the first miss, so the hold adds no
    // connected time.
    emu_tick(&mut tracker, &[], 45.0);
    let rec = &tracker.emulator_records["pixel_emulator-5554"];
    assert_eq!(rec.state, "DISCONNECTED");
    assert_eq!(rec.disconnect_count, 1);
    assert_eq!(rec.connected_total_s, 30.0);
    assert_eq!(rec.connected_start_mono, None);
    assert_eq!(rec.adb_online_since_mono, None);

    // Later ticks neither count the disconnect again nor add connected time,
    // and the CSV keeps reporting how the emulator ended.
    emu_tick(&mut tracker, &[], 50.0);
    let rec = &tracker.emulator_records["pixel_emulator-5554"];
    assert_eq!((rec.state.as_str(), rec.disconnect_count), ("DISCONNECTED", 1));
    assert_eq!(rec.connected_total_s, 30.0);
    let fields = tracker.get_csv_fields();
    assert_eq!(fields["netsim_state_pixel_emulator-5554"], "DISCONNECTED");
}

#[test]
fn test_netsim_tracker_exited_emulator_that_never_connected_fails() {
    let dir = tempfile::tempdir().unwrap();
    let mut tracker = tracker_for(&[dir.path()], dir.path());
    let group = emu_group("emulator-5554", WORKER_A, "pixel", None);
    emu_tick(&mut tracker, &[("pixel_emulator-5554", &group)], 1.0);
    assert_eq!(tracker.emulator_records["pixel_emulator-5554"].state, "CONNECTING");

    emu_tick(&mut tracker, &[], 2.0);
    assert_eq!(tracker.emulator_records["pixel_emulator-5554"].state, "CONNECTING");
    emu_tick(&mut tracker, &[], 3.0);
    let rec = &tracker.emulator_records["pixel_emulator-5554"];
    assert_eq!(rec.state, "FAILED(worker_exit_no_connect)");
    assert_eq!(rec.disconnect_count, 0);
}

#[test]
fn test_netsim_tracker_csv_covers_emulators_adb_does_not_list() {
    // netsim can connect before adb lists the emulator (emu_tick passes no
    // adb devices), so the CSV has a column for every record.
    let dir = tempfile::tempdir().unwrap();
    let mut tracker = tracker_for(&[dir.path()], dir.path());
    let group = emu_group("emulator-5554", WORKER_A, "pixel", None);
    emu_tick(&mut tracker, &[("pixel_emulator-5554", &group)], 1.0);
    let fields = tracker.get_csv_fields();
    assert_eq!(fields["netsim_state_pixel_emulator-5554"], "CONNECTING");
}

#[cfg(target_os = "linux")]
#[test]
fn test_netsim_tracker_worker_start_comes_from_proc() {
    // The worker's start time is the kernel's, whatever the tick's clock
    // says: here the tick is dated days before this process started.
    // /proc/uptime and clock ticks have 10 ms resolution, and the test
    // binary may have started only milliseconds before `now`.
    const PROC_TIME_RESOLUTION_S: f64 = 0.1;
    let dir = tempfile::tempdir().unwrap();
    let mut tracker = tracker_for(&[dir.path()], dir.path());
    let now = now_wall_secs();
    let group = emu_group("emulator-5554", std::process::id(), "pixel", None);
    emu_tick(&mut tracker, &[("pixel_emulator-5554", &group)], 10.0);
    let start = tracker.emulator_records["pixel_emulator-5554"].worker_start_ts;
    assert!(
        start <= now + PROC_TIME_RESOLUTION_S && start > now - 3600.0,
        "start={start} now={now}"
    );
}

#[test]
fn test_netsim_tracker_unreadable_worker_start_falls_back_to_now() {
    // A worker whose start time cannot be read must not be dated to the
    // Unix epoch, or every latency derived from it would be ~1.8e9 s.
    let dir = tempfile::tempdir().unwrap();
    let mut tracker = tracker_for(&[dir.path()], dir.path());
    let gone = emu_group("emulator-5554", u32::MAX - 7, "pixel", None);
    emu_tick(&mut tracker, &[("pixel_emulator-5554", &gone)], 10.0);
    let start = tracker.emulator_records["pixel_emulator-5554"].worker_start_ts;
    assert_eq!(start, START as f64 + 10.0);
}

#[test]
fn test_netsim_tracker_session_keys_follow_worker() {
    let dir = tempfile::tempdir().unwrap();
    let mut tracker = tracker_for(&[dir.path()], dir.path());
    let pixel = emu_group("emulator-5554", WORKER_A, "pixel", None);

    // The first scan has no AVD name yet, so the record is keyed by serial.
    emu_tick(&mut tracker, &[("emulator-5554", &pixel)], 1.0);
    assert!(tracker.emulator_records.contains_key("emulator-5554"));

    // Once the AVD name is known the same worker's record moves to the
    // AVD-qualified key instead of being left behind as an exited emulator.
    emu_tick(&mut tracker, &[("pixel_emulator-5554", &pixel)], 2.0);
    let keys: Vec<&String> = tracker.emulator_records.keys().collect();
    assert_eq!(keys, ["pixel_emulator-5554"]);
    let rec = &tracker.emulator_records["pixel_emulator-5554"];
    assert_eq!(rec.storage_key, "pixel_emulator-5554");
    assert_eq!(rec.first_seen_mono, 1.0);
    assert_eq!(rec.state, "CONNECTING");

    // A scan that briefly loses the AVD name keeps using that record.
    emu_tick(&mut tracker, &[("emulator-5554", &pixel)], 3.0);
    let keys: Vec<&String> = tracker.emulator_records.keys().collect();
    assert_eq!(keys, ["pixel_emulator-5554"]);
    assert_eq!(tracker.emulator_records["pixel_emulator-5554"].state, "CONNECTING");

    // A different AVD on the reused port is a new worker: it gets its own
    // record and the previous emulator settles as exited.
    let other = emu_group("emulator-5554", WORKER_B, "other", None);
    emu_tick(&mut tracker, &[("other_emulator-5554", &other)], 4.0);
    assert_eq!(
        tracker.emulator_records["pixel_emulator-5554"].state,
        "FAILED(worker_exit_no_connect)"
    );
    let rec = &tracker.emulator_records["other_emulator-5554"];
    assert_eq!((rec.worker_pid, rec.state.as_str()), (WORKER_B, "CONNECTING"));
}

#[test]
fn test_netsim_tracker_never_downgrades_display_name() {
    let dir = tempfile::tempdir().unwrap();
    let mut tracker = tracker_for(&[dir.path()], dir.path());
    let key = "pixel_emulator-5554";
    let device_name = |t: &NetsimTracker| t.emulator_records[key].device_name.clone();

    // Before pid_<pid>.ini is written only the AVD id is known.
    let no_ini = emu_group("emulator-5554", WORKER_A, "pixel", None);
    emu_tick(&mut tracker, &[(key, &no_ini)], 1.0);
    assert_eq!(device_name(&tracker).as_deref(), Some("pixel"));

    let with_ini = emu_group("emulator-5554", WORKER_A, "pixel", Some("Pixel Test"));
    emu_tick(&mut tracker, &[(key, &with_ini)], 2.0);
    assert_eq!(device_name(&tracker).as_deref(), Some("Pixel Test"));

    // The emulator deletes the ini while the worker shuts down.
    emu_tick(&mut tracker, &[(key, &no_ini)], 3.0);
    assert_eq!(device_name(&tracker).as_deref(), Some("Pixel Test"));
}

/// Phone and Glasses on one netsimd, whose log also has a device named
/// after a serial.
const SHARED_DAEMON_LOG: &str = concat!(
    "netsim I 09-25 22:37:06.000 netsimd.rs:106 - Handling new connection for \"\", device Phone\n",
    "netsim I 09-25 22:37:06.000 service.rs:585 - DeviceActor: AddChip 1 (kind BLUETOOTH) to device 1 (Phone)\n",
    "netsim I 09-25 22:37:08.000 netsimd.rs:106 - Handling new connection for \"\", device Glasses\n",
    "netsim I 09-25 22:37:08.000 service.rs:585 - DeviceActor: AddChip 2 (kind BLUETOOTH) to device 2 (Glasses)\n",
    "netsim I 09-25 22:37:09.000 service.rs:585 - DeviceActor: AddChip 3 (kind WIFI) to device 3 (emulator-5556)\n",
);

/// A tracker that tails the log of its one netsimd, which writes `log`.
fn tracker_with_daemon_log(dir: &Path, log: &str) -> (NetsimTracker, [NetsimDaemonProc; 1]) {
    let art_dir = dir.join("android-test").join("netsimd");
    std::fs::create_dir_all(&art_dir).unwrap();
    std::fs::write(art_dir.join("netsim_stderr.log"), log).unwrap();
    let mut tracker = tracker_for(&[dir], dir);
    tracker.enable_log = true;
    (tracker, [netsimd(DAEMON_A, START, dir)])
}

fn chip_ids(tracker: &NetsimTracker, key: &str) -> Vec<String> {
    tracker.emulator_records[key].chip_ids.iter().cloned().collect()
}

#[test]
fn test_netsim_tracker_fallback_skips_exited_emulator_chips() {
    // Once Phone exits, Glasses is the only live emulator: it takes the chip
    // nobody claims, but never Phone's.
    let dir = tempfile::tempdir().unwrap();
    let (mut tracker, procs) = tracker_with_daemon_log(dir.path(), SHARED_DAEMON_LOG);
    let phone = emu_group("emulator-5554", WORKER_A, "phone", Some("Phone"));
    let glasses = emu_group("emulator-5556", WORKER_B, "glasses", Some("Glasses"));

    let both = [("phone_emulator-5554", &phone), ("glasses_emulator-5556", &glasses)];
    emu_tick_with(&mut tracker, &procs, &both, 10.0);
    assert_eq!(chip_ids(&tracker, "phone_emulator-5554"), ["1"]);
    assert_eq!(chip_ids(&tracker, "glasses_emulator-5556"), ["2"]);
    let glasses_first_ts = tracker.emulator_records["glasses_emulator-5556"].first_transport_ts;
    assert!(glasses_first_ts.is_some());

    emu_tick_with(&mut tracker, &procs, &[("glasses_emulator-5556", &glasses)], 20.0);
    // Phone missed one tick and may still be running, so Glasses is not
    // the only live emulator yet.
    let phone_rec = &tracker.emulator_records["phone_emulator-5554"];
    assert_eq!((phone_rec.state.as_str(), phone_rec.disconnect_count), ("CONNECTED", 0));
    assert_eq!(chip_ids(&tracker, "glasses_emulator-5556"), ["2"]);

    emu_tick_with(&mut tracker, &procs, &[("glasses_emulator-5556", &glasses)], 25.0);
    let phone_rec = &tracker.emulator_records["phone_emulator-5554"];
    assert_eq!((phone_rec.state.as_str(), phone_rec.disconnect_count), ("DISCONNECTED", 1));
    let glasses_rec = &tracker.emulator_records["glasses_emulator-5556"];
    assert_eq!(glasses_rec.state, "CONNECTED");
    assert_eq!(chip_ids(&tracker, "glasses_emulator-5556"), ["2", "3"]);
    assert_eq!(glasses_rec.first_transport_ts, glasses_first_ts);
}

#[test]
fn test_netsim_tracker_fallback_skips_exited_emulator_connections() {
    // An emulator whose name is not known yet matches no device while Phone
    // runs. As the only live emulator it takes the unclaimed connections and
    // chips, and their device name, but none of Phone's.
    let dir = tempfile::tempdir().unwrap();
    let (mut tracker, procs) = tracker_with_daemon_log(dir.path(), SHARED_DAEMON_LOG);
    let phone = emu_group("emulator-5554", WORKER_A, "phone", Some("Phone"));
    let unnamed = EmuGroupNetsimInfo {
        serial: "emulator-5556".to_string(),
        primary_pid: WORKER_B,
        ..Default::default()
    };

    let both = [("phone_emulator-5554", &phone), ("emulator-5556", &unnamed)];
    emu_tick_with(&mut tracker, &procs, &both, 10.0);
    let rec = &tracker.emulator_records["emulator-5556"];
    assert_eq!((rec.state.as_str(), rec.first_transport_ts), ("CONNECTING", None));

    // Phone is held for its first missed tick, so nothing moves yet.
    emu_tick_with(&mut tracker, &procs, &[("emulator-5556", &unnamed)], 20.0);
    let rec = &tracker.emulator_records["emulator-5556"];
    assert_eq!((rec.device_name.as_deref(), rec.first_transport_ts), (None, None));

    emu_tick_with(&mut tracker, &procs, &[("emulator-5556", &unnamed)], 25.0);
    let phone_first_ts = tracker.emulator_records["phone_emulator-5554"].first_transport_ts;
    let rec = &tracker.emulator_records["emulator-5556"];
    assert_eq!(rec.device_name.as_deref(), Some("Glasses"));
    assert_eq!(chip_ids(&tracker, "emulator-5556"), ["2", "3"]);
    assert!(rec.first_transport_ts.is_some_and(|t| phone_first_ts.is_some_and(|p| t > p)));
}

#[test]
fn test_netsim_tracker_host_scan_miss_while_adb_lists_keeps_state() {
    // adb still lists the emulator, so a host-scan miss is a scan hiccup,
    // however long it lasts.
    let dir = tempfile::tempdir().unwrap();
    let (mut tracker, procs) = tracker_with_daemon_log(dir.path(), SHARED_DAEMON_LOG);
    let phone = emu_group("emulator-5554", WORKER_A, "phone", Some("Phone"));
    let key = "phone_emulator-5554";
    emu_tick_with(&mut tracker, &procs, &[(key, &phone)], 10.0);
    assert_eq!(tracker.emulator_records[key].state, "CONNECTED");

    let adb = HashMap::from([("emulator-5554".to_string(), "device".to_string())]);
    for now_mono in [20.0, 30.0, 40.0] {
        tracker.update(&procs, &HashMap::new(), &[], &adb, START as f64 + now_mono, now_mono);
        let rec = &tracker.emulator_records[key];
        assert_eq!((rec.state.as_str(), rec.disconnect_count), ("CONNECTED", 0));
    }
}

#[test]
fn test_netsim_tracker_one_tick_miss_is_not_a_disconnect() {
    // The emulator misses both the host scan and adb for one tick, then
    // comes back: it stays connected, with no disconnect and no lost time.
    let dir = tempfile::tempdir().unwrap();
    let (mut tracker, procs) = tracker_with_daemon_log(dir.path(), SHARED_DAEMON_LOG);
    let phone = emu_group("emulator-5554", WORKER_A, "phone", Some("Phone"));
    let key = "phone_emulator-5554";
    emu_tick_with(&mut tracker, &procs, &[(key, &phone)], 10.0);

    emu_tick_with(&mut tracker, &procs, &[], 20.0);
    let rec = &tracker.emulator_records[key];
    assert_eq!((rec.state.as_str(), rec.disconnect_count), ("CONNECTED", 0));
    assert_eq!(tracker.get_csv_fields()["netsim_state_phone_emulator-5554"], "CONNECTED");

    emu_tick_with(&mut tracker, &procs, &[(key, &phone)], 30.0);
    let rec = &tracker.emulator_records[key];
    assert_eq!(
        (rec.state.as_str(), rec.disconnect_count, rec.connected_start_mono),
        ("CONNECTED", 0, Some(10.0))
    );

    // Seeing it again ended the miss run, so a new miss is held again.
    emu_tick_with(&mut tracker, &procs, &[], 40.0);
    let rec = &tracker.emulator_records[key];
    assert_eq!((rec.state.as_str(), rec.disconnect_count), ("CONNECTED", 0));
}

#[test]
fn test_netsim_tracker_miss_run_restarts_when_adb_lists() {
    // Only consecutive ticks missing from both the host scan and adb
    // count, and the exit is dated to the first of them.
    let dir = tempfile::tempdir().unwrap();
    let (mut tracker, procs) = tracker_with_daemon_log(dir.path(), SHARED_DAEMON_LOG);
    let phone = emu_group("emulator-5554", WORKER_A, "phone", Some("Phone"));
    let key = "phone_emulator-5554";
    let adb = [("emulator-5554", "device")];
    emu_tick_with(&mut tracker, &procs, &[(key, &phone)], 10.0);

    emu_tick_with(&mut tracker, &procs, &[], 20.0);
    emu_tick_adb(&mut tracker, &procs, &[], &adb, 30.0);
    emu_tick_adb(&mut tracker, &procs, &[], &adb, 40.0);
    emu_tick_with(&mut tracker, &procs, &[], 50.0);
    let rec = &tracker.emulator_records[key];
    assert_eq!((rec.state.as_str(), rec.disconnect_count), ("CONNECTED", 0));
    // While held, the summary counts connected time only up to the miss.
    let summary = tracker.format_summary_sections(55.0).join("\n");
    assert!(summary.contains("connected_time=40.0s"), "{summary}");

    emu_tick_with(&mut tracker, &procs, &[], 60.0);
    let rec = &tracker.emulator_records[key];
    assert_eq!((rec.state.as_str(), rec.disconnect_count), ("DISCONNECTED", 1));
    assert_eq!(rec.connected_total_s, 40.0);
}

#[test]
fn test_netsim_tracker_stale_adb_offline_entry_does_not_hold() {
    // adb can keep listing a killed emulator as 'offline'.
    let dir = tempfile::tempdir().unwrap();
    let (mut tracker, procs) = tracker_with_daemon_log(dir.path(), SHARED_DAEMON_LOG);
    let phone = emu_group("emulator-5554", WORKER_A, "phone", Some("Phone"));
    let key = "phone_emulator-5554";
    let adb = [("emulator-5554", "offline")];
    emu_tick_with(&mut tracker, &procs, &[(key, &phone)], 10.0);

    emu_tick_adb(&mut tracker, &procs, &[], &adb, 20.0);
    assert_eq!(tracker.emulator_records[key].state, "CONNECTED");
    emu_tick_adb(&mut tracker, &procs, &[], &adb, 30.0);
    let rec = &tracker.emulator_records[key];
    assert_eq!((rec.state.as_str(), rec.disconnect_count), ("DISCONNECTED", 1));
    assert_eq!(rec.connected_total_s, 10.0);
}

#[test]
fn test_netsim_tracker_settled_emulator_stays_settled() {
    // A daemon that later reports a startup timeout does not turn an
    // emulator that already disconnected into a launcher failure.
    let dir = tempfile::tempdir().unwrap();
    let (mut tracker, procs) = tracker_with_daemon_log(dir.path(), SHARED_DAEMON_LOG);
    let phone = emu_group("emulator-5554", WORKER_A, "phone", Some("Phone"));
    let key = "phone_emulator-5554";
    emu_tick_with(&mut tracker, &procs, &[(key, &phone)], 10.0);
    emu_tick_with(&mut tracker, &procs, &[], 20.0);
    emu_tick_with(&mut tracker, &procs, &[], 30.0);
    let daemon_key = tracker.emulator_records[key].daemon_key.unwrap();
    assert_eq!(tracker.emulator_records[key].state, "DISCONNECTED");

    tracker.daemons.get_mut(&daemon_key).unwrap().exit_reason = Some("startup_timeout".to_string());
    emu_tick_with(&mut tracker, &procs, &[], 40.0);
    let rec = &tracker.emulator_records[key];
    assert_eq!((rec.state.as_str(), rec.disconnect_count), ("DISCONNECTED", 1));
}

#[test]
fn test_netsim_tracker_replaced_emulator_settles_at_once() {
    // Another AVD takes the serial of a held emulator, so the held record
    // settles at once, still dated to its first missed tick.
    let dir = tempfile::tempdir().unwrap();
    let mut tracker = tracker_for(&[dir.path()], dir.path());
    tracker.emulator_records.insert(
        "pixel_emulator-5554".to_string(),
        EmulatorNetsimRecord {
            storage_key: "pixel_emulator-5554".to_string(),
            serial: "emulator-5554".to_string(),
            worker_pid: WORKER_A,
            state: "CONNECTED".to_string(),
            first_transport_ts: Some(START as f64 + 10.0),
            connected_start_mono: Some(10.0),
            ..Default::default()
        },
    );
    emu_tick(&mut tracker, &[], 40.0);
    assert_eq!(tracker.emulator_records["pixel_emulator-5554"].state, "CONNECTED");

    let other = emu_group("emulator-5554", WORKER_B, "other", None);
    emu_tick(&mut tracker, &[("other_emulator-5554", &other)], 45.0);
    let old = &tracker.emulator_records["pixel_emulator-5554"];
    assert_eq!((old.state.as_str(), old.disconnect_count), ("DISCONNECTED", 1));
    assert_eq!(old.connected_total_s, 30.0);
    let new = &tracker.emulator_records["other_emulator-5554"];
    assert_eq!((new.worker_pid, new.state.as_str()), (WORKER_B, "CONNECTING"));
}

#[test]
fn test_poll_sockets_fast_skips_settled_and_non_connecting_records() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(dir.path().join("netsim.ini"), format!("grpc.port=43929\npid={DAEMON_A}\n"))
        .unwrap();
    let mut tracker = tracker_for(&[dir.path()], dir.path());
    tracker.host_os = HostOs::MacOs;
    tracker.socket_cli_runner = |_, _| Some(LSOF_OUT.to_string());
    let procs = [netsimd(DAEMON_A, START, dir.path())];
    emu_tick_with(&mut tracker, &procs, &[], 1.0);
    for (key, state, no_netsim) in [
        ("failed_emulator-5554", "FAILED(worker_exit_no_connect)", false),
        ("disc_emulator-5554", "DISCONNECTED", false),
        ("disabled_emulator-5554", "CONNECTING", true),
        ("connected_emulator-5554", "CONNECTED", false),
        ("live_emulator-5554", "CONNECTING", false),
    ] {
        tracker.emulator_records.insert(
            key.to_string(),
            EmulatorNetsimRecord {
                storage_key: key.to_string(),
                serial: "emulator-5554".to_string(),
                worker_pid: 4001,
                worker_start_ts: 100.0,
                state: state.to_string(),
                no_netsim,
                ..Default::default()
            },
        );
    }

    tracker.poll_sockets_fast(&emu_groups_with(&[4001]), 105.0);
    for skipped in [
        "failed_emulator-5554",
        "disc_emulator-5554",
        "disabled_emulator-5554",
        "connected_emulator-5554",
    ] {
        let rec = &tracker.emulator_records[skipped];
        assert_eq!((rec.first_transport_ts, rec.netsim_connect_s), (None, None), "{skipped}");
    }
    let live = &tracker.emulator_records["live_emulator-5554"];
    assert_eq!((live.first_transport_ts, live.netsim_connect_s), (Some(105.0), Some(5.0)));
}

#[test]
fn test_fast_poll_stops_once_connecting_emulator_has_transport() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(dir.path().join("netsim.ini"), format!("grpc.port=43929\npid={DAEMON_A}\n"))
        .unwrap();
    let mut tracker = tracker_for(&[dir.path()], dir.path());
    tracker.host_os = HostOs::MacOs;
    tracker.socket_cli_runner = |_, _| Some(LSOF_OUT.to_string());
    emu_tick_with(&mut tracker, &[netsimd(DAEMON_A, START, dir.path())], &[], 1.0);
    tracker.emulator_records.insert(
        "live_emulator-5554".to_string(),
        EmulatorNetsimRecord {
            storage_key: "live_emulator-5554".to_string(),
            serial: "emulator-5554".to_string(),
            worker_pid: 4001,
            worker_start_ts: 100.0,
            state: "CONNECTING".to_string(),
            ..Default::default()
        },
    );
    assert!(tracker.has_any_connecting());
    tracker.poll_sockets_fast(&emu_groups_with(&[4001]), 105.0);
    assert_eq!(tracker.emulator_records["live_emulator-5554"].first_transport_ts, Some(105.0));
    // Nothing is left for the sub-poll to measure until the next full tick.
    assert!(!tracker.has_any_connecting());
}

#[test]
fn test_netsim_tracker_worker_exit_with_first_transport_settles_to_failed() {
    let dir = tempfile::tempdir().unwrap();
    let mut tracker = tracker_for(&[dir.path()], dir.path());
    // Unjoined path: worker opened a socket (`first_transport_ts` set)
    // while still CONNECTING, then exited before reaching CONNECTED.
    tracker.emulator_records.insert(
        "pixel_emulator-5554".to_string(),
        EmulatorNetsimRecord {
            storage_key: "pixel_emulator-5554".to_string(),
            serial: "emulator-5554".to_string(),
            worker_pid: WORKER_A,
            worker_start_ts: START as f64 + 1.0,
            first_seen_mono: 1.0,
            state: "CONNECTING".to_string(),
            first_transport_ts: Some(START as f64 + 1.5),
            netsim_connect_s: Some(0.5),
            ..Default::default()
        },
    );
    emu_tick(&mut tracker, &[], 10.0);
    assert_eq!(tracker.emulator_records["pixel_emulator-5554"].state, "CONNECTING");
    emu_tick(&mut tracker, &[], 20.0);
    assert_eq!(
        tracker.emulator_records["pixel_emulator-5554"].state,
        "FAILED(worker_exit_no_connect)"
    );

    // Joined path: UT-5 (live emulator with first_transport_ts set and no
    // active sockets or chips transitions to STALLED after 50s), then
    // settles to FAILED(worker_exit_no_connect) when `emu_groups` no longer
    // contains the worker.
    let conn_only_log = "netsim I 09-25 22:37:06.000 netsimd.rs:106 - Handling new connection for \"\", device Phone\n";
    let dir2 = tempfile::tempdir().unwrap();
    let (mut tracker2, procs2) = tracker_with_daemon_log(dir2.path(), conn_only_log);
    let phone = emu_group("emulator-5554", WORKER_A, "phone", Some("Phone"));
    let key = "phone_emulator-5554";
    let summary = vec![(key.to_string(), "emulator-5554".to_string(), WORKER_A, 1.0)];
    let mut groups = HashMap::new();
    groups.insert("emulator-5554".to_string(), phone);
    tracker2.update(&procs2, &groups, &summary, &HashMap::new(), START as f64 + 10.0, 10.0);
    assert!(tracker2.emulator_records[key].first_transport_ts.is_some());
    assert_eq!(tracker2.emulator_records[key].state, "CONNECTING");

    // UT-5: Still live at t=52.0 (>= 50s since first_seen_mono=1.0) -> STALLED.
    tracker2.update(&procs2, &groups, &summary, &HashMap::new(), START as f64 + 52.0, 52.0);
    assert_eq!(tracker2.emulator_records[key].state, "STALLED");

    // Worker exits from `emu_groups` while still listed in `summary` -> FAILED.
    tracker2.update(&procs2, &HashMap::new(), &summary, &HashMap::new(), START as f64 + 55.0, 55.0);
    let rec = &tracker2.emulator_records[key];
    assert!(rec.first_transport_ts.is_some());
    assert_eq!(rec.state, "FAILED(worker_exit_no_connect)");

    // Subsequent ticks do not overwrite the settled FAILED state back to CONNECTING.
    tracker2.update(&procs2, &HashMap::new(), &summary, &HashMap::new(), START as f64 + 60.0, 60.0);
    assert_eq!(tracker2.emulator_records[key].state, "FAILED(worker_exit_no_connect)");
}

#[test]
fn test_netsim_tracker_cli_socket_failure_retains_cache_until_clear() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(dir.path().join("netsim.ini"), format!("grpc.port=43929\npid={DAEMON_A}\n"))
        .unwrap();
    let mut tracker = tracker_for(&[dir.path()], dir.path());
    tracker.host_os = HostOs::MacOs;
    tracker.socket_cli_runner = |_, _| Some(LSOF_OUT.to_string());
    let procs = [netsimd(DAEMON_A, START, dir.path())];
    let mut phone = emu_group("emulator-5554", 4001, "phone", Some("Phone"));
    phone.pids = vec![4001];
    let key = "phone_emulator-5554";

    emu_tick_with(&mut tracker, &procs, &[(key, &phone)], 10.0);
    let rec = &tracker.emulator_records[key];
    assert_eq!((rec.state.as_str(), rec.disconnect_count), ("CONNECTED", 0));

    // Transient CLI timeout/failure (None) keeps the cached connection for
    // live PID 4001 so the emulator does not flip to DISCONNECTED.
    tracker.socket_cli_runner = |_, _| None;
    emu_tick_with(&mut tracker, &procs, &[(key, &phone)], 20.0);
    let rec = &tracker.emulator_records[key];
    assert_eq!((rec.state.as_str(), rec.disconnect_count), ("CONNECTED", 0));

    // A successful empty CLI scan (Some("")) clears the cache and settles
    // the socket-only connection to DISCONNECTED.
    tracker.socket_cli_runner = |_, _| Some(String::new());
    emu_tick_with(&mut tracker, &procs, &[(key, &phone)], 30.0);
    let rec = &tracker.emulator_records[key];
    assert_eq!((rec.state.as_str(), rec.disconnect_count), ("DISCONNECTED", 1));

    // UT-7: Reconnect PID 4001 at t=40, then replace with PID 4002 at t=50
    // while lsof times out (None) -> stale cached socket for exited PID
    // 4001 is pruned rather than retained.
    tracker.socket_cli_runner = |_, _| Some(LSOF_OUT.to_string());
    emu_tick_with(&mut tracker, &procs, &[(key, &phone)], 40.0);
    assert_eq!(tracker.emulator_records[key].state, "CONNECTED");
    let mut phone_b = emu_group("emulator-5554", 4002, "phone", Some("Phone"));
    phone_b.pids = vec![4002];
    tracker.socket_cli_runner = |_, _| None;
    emu_tick_with(&mut tracker, &procs, &[(key, &phone_b)], 50.0);
    assert_eq!(tracker.emulator_records[key].state, "CONNECTING");
}

#[test]
fn test_netsim_tracker_same_avd_restart_resets_state_and_merges_bare_serial() {
    let phone_log = concat!(
        "netsim I 09-25 22:37:06.000 netsimd.rs:106 - Handling new connection for \"\", device Phone\n",
        "netsim I 09-25 22:37:06.000 service.rs:585 - DeviceActor: AddChip 1 (kind BLUETOOTH) to device 1 (Phone)\n",
    );
    let dir = tempfile::tempdir().unwrap();
    let (mut tracker, procs) = tracker_with_daemon_log(dir.path(), phone_log);
    let phone_a = emu_group("emulator-5554", WORKER_A, "phone", Some("Phone"));
    let key = "phone_emulator-5554";

    // Run 1 (WORKER_A) connects at t=10.
    emu_tick_with(&mut tracker, &procs, &[(key, &phone_a)], 10.0);
    assert_eq!(tracker.emulator_records[key].state, "CONNECTED");
    assert_eq!(chip_ids(&tracker, key), ["1"]);
    let run1_log_ts = tracker.emulator_records[key].first_transport_ts.unwrap() as u64;

    // Run 2 (WORKER_B) of the same AVD restarts on the same port at t=60
    // (>=50s after Run 1): first seen under bare serial "emulator-5554"
    // before AVD id resolves, stamps `first_transport_ts`, then promoted to
    // "phone_emulator-5554" at t=61.
    let mut phone_b_unnamed = emu_group("emulator-5554", WORKER_B, "phone", None);
    phone_b_unnamed.avd_id = None;
    phone_b_unnamed.primary_start_time = run1_log_ts + 60;
    emu_tick_with(&mut tracker, &procs, &[("emulator-5554", &phone_b_unnamed)], 60.0);
    assert!(tracker.emulator_records.contains_key("emulator-5554"));
    tracker.emulator_records.get_mut("emulator-5554").unwrap().first_transport_ts =
        Some(run1_log_ts as f64 + 61.0);

    let mut phone_b = emu_group("emulator-5554", WORKER_B, "phone", Some("Phone"));
    phone_b.primary_start_time = run1_log_ts + 60;
    emu_tick_with(&mut tracker, &procs, &[(key, &phone_b)], 61.0);
    assert!(!tracker.emulator_records.contains_key("emulator-5554"));
    let rec = &tracker.emulator_records[key];
    assert_eq!(rec.worker_pid, WORKER_B);
    assert_eq!(rec.disconnect_count, 1);
    assert_eq!(rec.connected_total_s, 50.0);
    // Pre-restart log entries from Run 1 are before WORKER_B's start time
    // and are ignored, while Tick-1's transport timestamp on the bare
    // record is preserved.
    assert_eq!(rec.state, "CONNECTING");
    assert!(rec.chip_attach_s.is_empty());
    assert!(rec.chip_ids.is_empty());
    assert_eq!(rec.first_transport_ts, Some(run1_log_ts as f64 + 61.0));
    assert_eq!(rec.netsim_connect_s, Some(1.0));

    // Direct same-key restart (WORKER_B -> 5_000_403) at t=70 without bare-serial
    // stage also resets per-run state and `first_seen_mono` so it starts in
    // CONNECTING (not STALLED) while preserving cumulative history.
    let mut phone_c = emu_group("emulator-5554", 5_000_403, "phone", Some("Phone"));
    phone_c.primary_start_time = run1_log_ts + 70;
    emu_tick_with(&mut tracker, &procs, &[(key, &phone_c)], 70.0);
    let rec = &tracker.emulator_records[key];
    assert_eq!(rec.worker_pid, 5_000_403);
    assert_eq!(rec.disconnect_count, 1);
    assert_eq!(rec.connected_total_s, 50.0);
    assert_eq!(rec.state, "CONNECTING");
    assert_eq!(rec.first_transport_ts, None);
}

#[test]
fn test_find_joined_daemon_priority_order() {
    let dir = tempfile::tempdir().unwrap();
    let mut tracker = tracker_for(&[dir.path()], dir.path());
    let procs = [netsimd(DAEMON_A, START, dir.path()), netsimd(DAEMON_B, START, dir.path())];
    tracker.discover_and_poll_daemons(&procs, &HashMap::new(), START as f64, 1.0);
    tracker.daemons.get_mut(&(DAEMON_A, START)).unwrap().grpc_port = Some(43929);
    tracker.daemons.get_mut(&(DAEMON_B, START)).unwrap().grpc_port = Some(43930);
    let active = [(DAEMON_A, START), (DAEMON_B, START)];

    // 1. Explicit gRPC endpoint port wins over socket connections.
    let mut g = emu_group("emulator-5554", WORKER_A, "phone", Some("Phone"));
    g.grpc_endpoint = Some("localhost:43930".to_string());
    let conns = HashMap::from([(
        43929,
        vec![LoopbackGrpcConn {
            serial: "emulator-5554".to_string(),
            pid: WORKER_A,
            peer_port: 50000,
        }],
    )]);
    assert_eq!(
        tracker.find_joined_daemon("emulator-5554", Some(&g), &active, &conns),
        Some((DAEMON_B, START))
    );

    // 2. Without endpoint match, established loopback socket wins.
    g.grpc_endpoint = None;
    assert_eq!(
        tracker.find_joined_daemon("emulator-5554", Some(&g), &active, &conns),
        Some((DAEMON_A, START))
    );

    // 3. With multiple active daemons and neither endpoint nor socket match, returns None.
    assert_eq!(
        tracker.find_joined_daemon("emulator-5554", Some(&g), &active, &HashMap::new()),
        None
    );

    // 4. With a single active daemon, falls back to that daemon.
    assert_eq!(
        tracker.find_joined_daemon(
            "emulator-5554",
            Some(&g),
            &[(DAEMON_A, START)],
            &HashMap::new(),
        ),
        Some((DAEMON_A, START))
    );
}

#[test]
fn test_discover_and_poll_daemons_records_duplicate_artifact_dir() {
    let dir = tempfile::tempdir().unwrap();
    let mut tracker = tracker_for(&[dir.path()], dir.path());
    let procs = [netsimd(DAEMON_A, START, dir.path()), netsimd(DAEMON_B, START, dir.path())];
    let seen = tracker.discover_and_poll_daemons(&procs, &HashMap::new(), START as f64, 1.0);
    assert_eq!(seen.len(), 2);
    assert_eq!(count_events(&tracker, "DUPLICATE_ARTIFACT_DIR("), 1);
}

#[test]
fn test_settled_emulator_does_not_leak_wifi_or_duplicate_pcaps() {
    // When Phone and Glasses share a daemon and Phone associates with Wi-Fi
    // before exiting, Glasses must not inherit Phone's Wi-Fi association.
    let wifi_shared_log = concat!(
        "netsim I 09-25 22:37:06.000 service.rs:585 - DeviceActor: AddChip 1 (kind WIFI) to device 1 (Phone)\n",
        "netsim I 09-25 22:37:07.000 ap_actor.rs:212 - ApActor: Associated 02:15:B2:00:00:00\n",
        "netsim I 09-25 22:37:08.000 service.rs:585 - DeviceActor: AddChip 2 (kind BLUETOOTH) to device 2 (Glasses)\n",
    );
    let dir = tempfile::tempdir().unwrap();
    let (mut tracker, procs) = tracker_with_daemon_log(dir.path(), wifi_shared_log);
    let phone = emu_group("emulator-5554", WORKER_A, "phone", Some("Phone"));
    let glasses = emu_group("emulator-5556", WORKER_B, "glasses", Some("Glasses"));
    let both = [("phone_emulator-5554", &phone), ("glasses_emulator-5556", &glasses)];
    emu_tick_with(&mut tracker, &procs, &both, 10.0);
    assert_eq!(tracker.emulator_records["glasses_emulator-5556"].wifi_associated_s, None);

    emu_tick_with(&mut tracker, &procs, &[("glasses_emulator-5556", &glasses)], 20.0);
    emu_tick_with(&mut tracker, &procs, &[("glasses_emulator-5556", &glasses)], 25.0);
    assert_eq!(tracker.emulator_records["phone_emulator-5554"].state, "DISCONNECTED");
    assert_eq!(tracker.emulator_records["glasses_emulator-5556"].wifi_associated_s, None);

    // When a daemon's sole emulator settles to DISCONNECTED while netsimd
    // stays alive, periodic harvesting must keep its serial attribution
    // instead of re-copying its pcap under shared/.
    let dir2 = tempfile::tempdir().unwrap();
    let art2 = dir2.path().join("android-test").join("netsimd");
    let pcaps2 = art2.join("pcaps");
    std::fs::create_dir_all(&pcaps2).unwrap();
    std::fs::write(pcaps2.join("netsim-1-Phone-BLUETOOTH.pcap"), [0xd4u8; 64]).unwrap();
    std::fs::write(art2.join("netsim_stderr.log"), SHARED_DAEMON_LOG).unwrap();
    let out2 = dir2.path().join("out");
    let mut tracker2 = NetsimTracker::new(Some(""), 500, true, &out2);
    tracker2.host_os = HostOs::Linux;
    tracker2.custom_ini_dirs = vec![dir2.path().to_path_buf()];
    tracker2.custom_proc_locks = Some(String::new());
    let procs2 = [netsimd(DAEMON_A, START, dir2.path())];
    emu_tick_with(&mut tracker2, &procs2, &[("phone_emulator-5554", &phone)], 10.0);
    assert_eq!(tracker2.pcap_mgr.copied_files.len(), 1);

    emu_tick_with(&mut tracker2, &procs2, &[], 20.0);
    emu_tick_with(&mut tracker2, &procs2, &[], 30.0);
    assert_eq!(tracker2.emulator_records["phone_emulator-5554"].state, "DISCONNECTED");
    assert_eq!(tracker2.pcap_mgr.copied_files.len(), 1);
    assert!(!tracker2.pcap_mgr.copied_files.keys().any(|k| k.contains("shared")));
}

#[test]
fn test_settled_record_does_not_hide_live_dashboard_row() {
    let dir = tempfile::tempdir().unwrap();
    let mut tracker = tracker_for(&[dir.path()], dir.path());
    tracker.emulator_records.insert(
        "pixel_emulator-5554".to_string(),
        EmulatorNetsimRecord {
            storage_key: "pixel_emulator-5554".to_string(),
            serial: "emulator-5554".to_string(),
            worker_pid: WORKER_A,
            state: "DISCONNECTED".to_string(),
            disconnect_count: 1,
            ..Default::default()
        },
    );
    let other = emu_group("emulator-5554", WORKER_B, "other", None);
    emu_tick(&mut tracker, &[("other_emulator-5554", &other)], 45.0);
    let snap = tracker.get_dashboard_snapshot(START as f64 + 45.0);
    assert_eq!(
        snap.emulator_rows.get("emulator-5554").map(String::as_str),
        Some("netsim: CONNECTING (no-chips) | disc=0")
    );
}

#[test]
fn test_daemon_restart_preserves_exit_reason_and_ready_latency() {
    let dir = tempfile::tempdir().unwrap();
    let art = dir.path().join("android-test").join("netsimd");
    std::fs::create_dir_all(&art).unwrap();
    let ini_path = dir.path().join("netsim.ini");
    let log_path = art.join("netsim_stderr.log");

    std::fs::write(&ini_path, format!("pid={DAEMON_A}\nport=8554\n")).unwrap();
    std::fs::write(
        &log_path,
        concat!(
            "netsim I 09-25 22:37:06.000 server.rs:100 - Wrote to INI file /tmp/netsim.ini\n",
            "netsim I 09-25 22:37:08.000 server.rs:110 - Shutting down gracefully\n",
        ),
    )
    .unwrap();

    let mut tracker = tracker_for(&[dir.path()], dir.path());
    tracker.enable_log = true;
    let mut phone = emu_group("emulator-5554", WORKER_A, "phone", Some("Phone"));
    phone.primary_start_time = START - 1;

    let procs_a = [netsimd(DAEMON_A, START, dir.path())];
    emu_tick_with(&mut tracker, &procs_a, &[("phone_emulator-5554", &phone)], 10.0);
    assert_eq!(tracker.daemons[&(DAEMON_A, START)].netsim_daemon_ready_s, Some(1.0));
    assert_eq!(tracker.emulator_records["phone_emulator-5554"].netsim_daemon_ready_s, Some(2.0));

    // DAEMON_B spawns at START + 15 before DAEMON_A finishes exiting and
    // before truncating DAEMON_A's log; by t=20 DAEMON_A has exited and
    // DAEMON_B has truncated netsim_stderr.log and written its own INI + ready line.
    let procs_ab =
        [netsimd(DAEMON_A, START, dir.path()), netsimd(DAEMON_B, START + 15, dir.path())];
    emu_tick_with(&mut tracker, &procs_ab, &[("phone_emulator-5554", &phone)], 16.0);

    let procs_b = [netsimd(DAEMON_B, START + 15, dir.path())];
    std::fs::write(&ini_path, format!("pid={DAEMON_B}\nport=8555\n")).unwrap();
    std::fs::write(
        &log_path,
        "netsim I 09-25 22:37:22.000 server.rs:100 - Wrote to INI file /tmp/netsim.ini\n",
    )
    .unwrap();
    emu_tick_with(&mut tracker, &procs_b, &[("phone_emulator-5554", &phone)], 20.0);

    assert_eq!(tracker.daemons[&(DAEMON_A, START)].exit_reason.as_deref(), Some("sigterm_sigint"));
    assert_eq!(tracker.daemons[&(DAEMON_A, START)].netsim_daemon_ready_s, Some(1.0));
    assert_eq!(tracker.daemons[&(DAEMON_B, START + 15)].netsim_daemon_ready_s, Some(2.0));
    assert_eq!(tracker.emulator_records["phone_emulator-5554"].netsim_daemon_ready_s, Some(2.0));
}

#[test]
fn test_daemon_restart_and_settled_records_update_connection_state() {
    let dir = tempfile::tempdir().unwrap();
    let art = dir.path().join("android-test").join("netsimd");
    std::fs::create_dir_all(&art).unwrap();
    let log_path = art.join("netsim_stderr.log");
    std::fs::write(&log_path, SHARED_DAEMON_LOG).unwrap();

    let mut tracker = tracker_for(&[dir.path()], dir.path());
    tracker.enable_log = true;
    let phone = emu_group("emulator-5554", WORKER_A, "phone", Some("Phone"));
    let key = "phone_emulator-5554";

    let procs_a = [netsimd(DAEMON_A, START, dir.path())];
    emu_tick_with(&mut tracker, &procs_a, &[(key, &phone)], 10.0);
    assert_eq!(tracker.emulator_records[key].state, "CONNECTED");
    assert_eq!(tracker.get_csv_fields()["netsim_devices"], "1");

    // DAEMON_A exits and DAEMON_B starts with an empty log and no sockets:
    // Phone must drop DAEMON_A's chips, transition to DISCONNECTED, and not
    // count as an active joined device on DAEMON_B.
    std::fs::write(&log_path, "").unwrap();
    let procs_b = [netsimd(DAEMON_B, START + 15, dir.path())];
    emu_tick_with(&mut tracker, &procs_b, &[(key, &phone)], 20.0);
    let rec = &tracker.emulator_records[key];
    assert_eq!((rec.state.as_str(), rec.disconnect_count), ("DISCONNECTED", 1));
    assert_eq!(rec.connected_total_s, 10.0);
    assert!(rec.chip_attach_s.is_empty());
    assert_eq!(tracker.get_csv_fields()["netsim_devices"], "0");
}

#[test]
fn test_wifi_association_recorded_after_same_avd_worker_restart() {
    let dir = tempfile::tempdir().unwrap();
    let art = dir.path().join("android-test").join("netsimd");
    std::fs::create_dir_all(&art).unwrap();
    let log_path = art.join("netsim_stderr.log");
    std::fs::write(
        &log_path,
        concat!(
            "netsim I 09-25 22:37:06.000 service.rs:585 - DeviceActor: AddChip 1 (kind WIFI) to device 1 (Phone)\n",
            "netsim I 09-25 22:37:07.000 ap_actor.rs:212 - ApActor: Associated 02:15:B2:00:00:00\n",
        ),
    )
    .unwrap();

    let mut tracker = tracker_for(&[dir.path()], dir.path());
    tracker.enable_log = true;
    let mut phone_a = emu_group("emulator-5554", WORKER_A, "phone", Some("Phone"));
    phone_a.primary_start_time = START;
    let key = "phone_emulator-5554";
    let procs = [netsimd(DAEMON_A, START, dir.path())];

    emu_tick_with(&mut tracker, &procs, &[(key, &phone_a)], 10.0);
    assert_eq!(tracker.emulator_records[key].wifi_associated_s, Some(2.0));

    // Run 2 (WORKER_B) of the same AVD restarts at START + 20 and logs a new
    // Wi-Fi association at START + 23 (22:37:28.000).
    std::fs::write(
        &log_path,
        concat!(
            "netsim I 09-25 22:37:06.000 service.rs:585 - DeviceActor: AddChip 1 (kind WIFI) to device 1 (Phone)\n",
            "netsim I 09-25 22:37:07.000 ap_actor.rs:212 - ApActor: Associated 02:15:B2:00:00:00\n",
            "netsim I 09-25 22:37:26.000 service.rs:585 - DeviceActor: AddChip 2 (kind WIFI) to device 2 (Phone)\n",
            "netsim I 09-25 22:37:28.000 ap_actor.rs:212 - ApActor: Associated 02:15:B2:00:00:01\n",
        ),
    )
    .unwrap();
    let mut phone_b = emu_group("emulator-5554", WORKER_B, "phone", Some("Phone"));
    phone_b.primary_start_time = START + 20;
    emu_tick_with(&mut tracker, &procs, &[(key, &phone_b)], 25.0);
    assert_eq!(tracker.emulator_records[key].wifi_associated_s, Some(3.0));
}

#[test]
fn test_deleted_stale_ini_is_pruned_from_dashboard() {
    let dir = tempfile::tempdir().unwrap();
    let ini_path = dir.path().join("netsim.ini");
    write_ini(&ini_path, &format!("pid={DEAD_PID}\nport=8554\n"), START - 60);
    let mut tracker = tracker_for(&[dir.path()], dir.path());

    tick(&mut tracker, &[], START + 10);
    assert_eq!(
        tracker.get_dashboard_snapshot(START as f64 + 10.0).daemon_status_row.as_deref(),
        Some("STALE_INI (netsim.ini)")
    );

    std::fs::remove_file(&ini_path).unwrap();
    tick(&mut tracker, &[], START + 20);
    assert!(tracker.stale_inis.is_empty());
    assert_eq!(
        tracker.get_dashboard_snapshot(START as f64 + 20.0).daemon_status_row.as_deref(),
        Some("N/A")
    );
}

#[test]
fn test_client_mode_and_exiting_daemon_log_isolation() {
    let dir = tempfile::tempdir().unwrap();
    let art = dir.path().join("android-test").join("netsimd");
    std::fs::create_dir_all(&art).unwrap();
    let log_path = art.join("netsim_stderr.log");
    std::fs::write(
        &log_path,
        concat!(
            "netsim I 09-25 22:37:05.612 netsimd.rs:451 - Netsim Version: 1.0.25, OS: linux, Arch: x86_64\n",
            "Args {\n    no_cli_ui: true,\n    pcap: true,\n}\n",
            "netsim I 09-25 22:37:05.650 netsimd.rs:700 - gRPC port: 43929\n",
            "netsim I 09-25 22:37:05.683 netsimd.rs:744 - Wrote to INI file /tmp/netsim.ini\n",
        ),
    )
    .unwrap();

    let mut tracker = tracker_for(&[dir.path()], dir.path());
    tracker.enable_log = true;
    let procs_a = [netsimd(DAEMON_A, START, dir.path())];
    tick(&mut tracker, &procs_a, START + 5);
    assert!(!tracker.daemons[&(DAEMON_A, START)].client_mode_recorded);
    assert_eq!(tracker.daemons[&(DAEMON_A, START)].grpc_port, Some(43929));

    // Client-mode DAEMON_B spawns at START + 15 and appends "Lock held by another process"
    // to the shared log without truncating it.
    let mut f = std::fs::OpenOptions::new().append(true).open(&log_path).unwrap();
    use std::io::Write;
    writeln!(f, "netsim W 09-25 22:37:20.500 netsimd.rs:500 - Lock held by another process")
        .unwrap();
    let procs_ab =
        [netsimd(DAEMON_A, START, dir.path()), netsimd(DAEMON_B, START + 15, dir.path())];
    tick(&mut tracker, &procs_ab, START + 16);

    // Primary server DAEMON_A keeps its metadata and never records CLIENT_MODE_SPAWN;
    // client-mode DAEMON_B records CLIENT_MODE_SPAWN without inheriting DAEMON_A's port/flags.
    let da = &tracker.daemons[&(DAEMON_A, START)];
    let db = &tracker.daemons[&(DAEMON_B, START + 15)];
    assert!(!da.client_mode_recorded);
    assert!(db.client_mode_recorded);
    assert_eq!(db.grpc_port, None);
    assert_eq!(db.ini_path, None);
    assert!(!db.frontend_disabled);
    assert!(!db.pcap_flag);
}

#[test]
fn test_qemu_next_supervisor_worker_transitions_preserve_session() {
    let dir = tempfile::tempdir().unwrap();
    let (mut tracker, procs) = tracker_with_daemon_log(dir.path(), "");
    let log_path = dir.path().join("android-test").join("netsimd").join("netsim_stderr.log");
    let launcher_pid = 5_000_350u32;
    let key = "phone_emulator-5554";

    // Startup: Tick 1 sees only supervisor launcher (5_000_350) under bare serial "emulator-5554";
    // Tick 2 sees supervisor (5_000_350) + worker (WORKER_A) under "phone_emulator-5554".
    let mut phone_launching = emu_group("emulator-5554", launcher_pid, "phone", None);
    phone_launching.avd_id = None;
    phone_launching.pids = vec![launcher_pid];
    phone_launching.primary_start_time = START - 1;
    emu_tick_with(&mut tracker, &procs, &[("emulator-5554", &phone_launching)], 5.0);
    assert!(tracker.emulator_records.contains_key("emulator-5554"));

    std::fs::write(
        &log_path,
        "netsim I 09-25 22:37:06.000 service.rs:585 - DeviceActor: AddChip 1 (kind BLUETOOTH) to device 1 (Phone)\n",
    )
    .unwrap();
    let mut phone_running = emu_group("emulator-5554", WORKER_A, "phone", Some("Phone"));
    phone_running.pids = vec![launcher_pid, WORKER_A];
    phone_running.primary_start_time = START;
    emu_tick_with(&mut tracker, &procs, &[(key, &phone_running)], 10.0);
    assert!(!tracker.emulator_records.contains_key("emulator-5554"));
    assert_eq!(tracker.emulator_records[key].state, "CONNECTED");
    assert_eq!(chip_ids(&tracker, key), ["1"]);
    assert_eq!(tracker.emulator_records[key].chip_attach_s["BT"], (1.0, false));

    // Shutdown: Worker WORKER_A exits first while supervisor (5_000_350) is still alive for a tick.
    let mut phone_shutting_down = emu_group("emulator-5554", launcher_pid, "phone", Some("Phone"));
    phone_shutting_down.pids = vec![launcher_pid];
    phone_shutting_down.primary_start_time = START - 1;
    emu_tick_with(&mut tracker, &procs, &[(key, &phone_shutting_down)], 20.0);

    // Supervisor finishes exiting over the next two ticks.
    emu_tick_with(&mut tracker, &procs, &[], 22.0);
    emu_tick_with(&mut tracker, &procs, &[], 24.0);

    let rec = &tracker.emulator_records[key];
    assert_eq!((rec.state.as_str(), rec.disconnect_count), ("DISCONNECTED", 1));
    assert_eq!(rec.chip_attach_s["BT"], (1.0, false));
}

#[test]
fn test_qemu_next_member_start_times_preserve_session_across_launcher_and_fishtank_shutdown() {
    // Case 1: Supervisor launcher started 5s BEFORE VM worker (START - 5 vs START),
    // lingers during shutdown under both AVD key and bare-serial fallback.
    let dir = tempfile::tempdir().unwrap();
    let (mut tracker, procs) = tracker_with_daemon_log(dir.path(), "");
    let log_path = dir.path().join("android-test").join("netsimd").join("netsim_stderr.log");
    let launcher_pid = 5_000_500u32;
    let fishtank_pid = 5_000_501u32;
    let key = "phone_emulator-5554";

    std::fs::write(
        &log_path,
        "netsim I 09-25 22:37:06.000 service.rs:585 - DeviceActor: AddChip 1 (kind BLUETOOTH) to device 1 (Phone)\n",
    )
    .unwrap();
    let mut phone_running = emu_group("emulator-5554", WORKER_A, "phone", Some("Phone"));
    phone_running.pids = vec![launcher_pid, WORKER_A, fishtank_pid];
    phone_running.primary_start_time = START;
    phone_running.member_start_times =
        BTreeMap::from([(launcher_pid, START - 5), (WORKER_A, START), (fishtank_pid, START + 3)]);
    emu_tick_with(&mut tracker, &procs, &[(key, &phone_running)], 10.0);
    assert_eq!(tracker.emulator_records[key].state, "CONNECTED");
    assert_eq!(tracker.emulator_records[key].chip_attach_s["BT"], (1.0, false));

    // Tick 2 (shutdown): VM worker and fishtank exited; launcher (START - 5) lingers,
    // first under AVD key and then under bare serial after pid_<worker>.ini is deleted.
    let mut launcher_lingering = emu_group("emulator-5554", launcher_pid, "phone", Some("Phone"));
    launcher_lingering.pids = vec![launcher_pid];
    launcher_lingering.primary_start_time = START - 5;
    launcher_lingering.member_start_times = BTreeMap::from([(launcher_pid, START - 5)]);
    emu_tick_with(&mut tracker, &procs, &[(key, &launcher_lingering)], 18.0);

    let mut launcher_bare = emu_group("emulator-5554", launcher_pid, "phone", None);
    launcher_bare.avd_id = None;
    launcher_bare.pids = vec![launcher_pid];
    launcher_bare.primary_start_time = START - 5;
    launcher_bare.member_start_times = BTreeMap::from([(launcher_pid, START - 5)]);
    emu_tick_with(&mut tracker, &procs, &[("emulator-5554", &launcher_bare)], 19.0);
    assert!(
        !tracker.emulator_records.contains_key("emulator-5554"),
        "bare-serial shutdown launcher must map to existing AVD session"
    );

    emu_tick_with(&mut tracker, &procs, &[], 21.0);
    emu_tick_with(&mut tracker, &procs, &[], 23.0);
    let rec = &tracker.emulator_records[key];
    assert_eq!((rec.state.as_str(), rec.disconnect_count), ("DISCONNECTED", 1));
    assert_eq!(rec.chip_attach_s["BT"], (1.0, false));

    // Case 2: GUI companion (fishtank) started 3s AFTER VM worker (START + 3) and lingers
    // for a tick on shutdown when VM worker exits first; then fishtank's PID is reused later.
    let dir2 = tempfile::tempdir().unwrap();
    let (mut tracker2, procs2) = tracker_with_daemon_log(dir2.path(), "");
    let log_path2 = dir2.path().join("android-test").join("netsimd").join("netsim_stderr.log");
    std::fs::write(
        &log_path2,
        "netsim I 09-25 22:37:06.000 service.rs:585 - DeviceActor: AddChip 2 (kind WIFI) to device 1 (Phone)\n",
    )
    .unwrap();
    emu_tick_with(&mut tracker2, &procs2, &[(key, &phone_running)], 10.0);
    assert_eq!(tracker2.emulator_records[key].chip_attach_s["WIFI"], (1.0, false));

    let mut fishtank_lingering = emu_group("emulator-5554", fishtank_pid, "phone", Some("Phone"));
    fishtank_lingering.pids = vec![fishtank_pid];
    fishtank_lingering.primary_start_time = START + 3;
    fishtank_lingering.member_start_times = BTreeMap::from([(fishtank_pid, START + 3)]);
    emu_tick_with(&mut tracker2, &procs2, &[(key, &fishtank_lingering)], 18.0);
    emu_tick_with(&mut tracker2, &procs2, &[], 20.0);
    emu_tick_with(&mut tracker2, &procs2, &[], 22.0);
    let rec2 = &tracker2.emulator_records[key];
    assert_eq!((rec2.state.as_str(), rec2.disconnect_count), ("DISCONNECTED", 1));
    assert_eq!(rec2.chip_attach_s["WIFI"], (1.0, false));

    // Reusing fishtank_pid in a brand-new emulator session at START + 30 resets to CONNECTING.
    let mut reused_fishtank = emu_group("emulator-5554", fishtank_pid, "phone", Some("Phone"));
    reused_fishtank.pids = vec![fishtank_pid];
    reused_fishtank.primary_start_time = START + 30;
    reused_fishtank.member_start_times = BTreeMap::from([(fishtank_pid, START + 30)]);
    emu_tick_with(&mut tracker2, &[], &[(key, &reused_fishtank)], 30.0);
    assert_eq!(tracker2.emulator_records[key].state, "CONNECTING");
}

#[cfg(unix)]
#[test]
fn test_stale_ini_reached_through_symlink_alias_is_listed_once() {
    let root = tempfile::tempdir().unwrap();
    let real = root.path().join("real");
    std::fs::create_dir_all(&real).unwrap();
    let alias = root.path().join("alias");
    std::os::unix::fs::symlink(&real, &alias).unwrap();
    write_ini(&real.join("netsim.ini"), &format!("pid={DEAD_PID}\ngrpc.port=8554\n"), START - 60);

    let mut tracker = tracker_for(&[&real], root.path());
    tick(&mut tracker, &[], START + 10);
    // A daemon's TMPDIR can reach the same file through a symlinked dir.
    tracker.custom_ini_dirs = vec![alias.clone()];
    tick(&mut tracker, &[], START + 20);
    assert_eq!(tracker.stale_inis.len(), 1, "{:?}", tracker.stale_inis.keys());
    assert_eq!(count_events(&tracker, "STALE_INI("), 1);
}

#[test]
fn test_session_stats_keep_refining_approximate_chips() {
    // netsim 1.0.24 logs AddChip without a kind; session stats resolve
    // each kind against those timestamps. WIFI matches at once; BT only
    // after its AddChip line appears.
    let dir = tempfile::tempdir().unwrap();
    let log = "netsim I 09-25 22:37:06.000 service.rs:579 - DeviceActor: AddChip Phone (EMULATOR, Phone) to device Phone\n";
    let (mut tracker, procs) = tracker_with_daemon_log(dir.path(), log);
    let art = dir.path().join("android-test").join("netsimd");
    let stats = art.join("netsim_session_stats.json");
    let payload = serde_json::json!({
        "device_stats": [{"device_id": 1, "kind": "EMULATOR"}],
        "radio_stats": [
            {"device_id": 1, "kind": "WIFI", "duration_secs": "99"},
            {"device_id": 1, "kind": "BLUETOOTH_LOW_ENERGY", "duration_secs": "90"}
        ]
    });
    write_ini(&stats, &payload.to_string(), START + 100);
    let mut phone = emu_group("emulator-5554", WORKER_A, "phone", Some("Phone"));
    phone.primary_start_time = START;
    let key = "phone_emulator-5554";

    emu_tick_with(&mut tracker, &procs, &[(key, &phone)], 10.0);
    let chips = &tracker.emulator_records[key].chip_attach_s;
    assert_eq!(chips["WIFI"], (1.0, false));
    assert_eq!(chips["BT"], (10.0, true));

    let mut f =
        std::fs::OpenOptions::new().append(true).open(art.join("netsim_stderr.log")).unwrap();
    use std::io::Write;
    writeln!(f, "netsim I 09-25 22:37:15.000 service.rs:579 - DeviceActor: AddChip Phone (BLUETOOTH, Phone) to device Phone").unwrap();
    emu_tick_with(&mut tracker, &procs, &[(key, &phone)], 12.0);
    let chips = &tracker.emulator_records[key].chip_attach_s;
    assert_eq!(chips["WIFI"], (1.0, false));
    assert_eq!(chips["BT"], (10.0, false));
}

#[test]
fn test_same_avd_restart_after_held_miss_counts_connected_time_to_the_miss() {
    let dir = tempfile::tempdir().unwrap();
    let (mut tracker, procs) = tracker_with_daemon_log(dir.path(), SHARED_DAEMON_LOG);
    let phone_a = emu_group("emulator-5554", WORKER_A, "phone", Some("Phone"));
    let phone_b = emu_group("emulator-5554", WORKER_B, "phone", Some("Phone"));
    let key = "phone_emulator-5554";
    emu_tick_with(&mut tracker, &procs, &[(key, &phone_a)], 10.0);
    assert_eq!(tracker.emulator_records[key].state, "CONNECTED");
    // Run 1 is missing for one tick (held), then run 2 takes the key.
    emu_tick_with(&mut tracker, &procs, &[], 20.0);
    emu_tick_with(&mut tracker, &procs, &[(key, &phone_b)], 30.0);
    let rec = &tracker.emulator_records[key];
    assert_eq!(rec.worker_pid, WORKER_B);
    assert_eq!(rec.disconnect_count, 1);
    assert_eq!(rec.connected_total_s, 10.0);
}

#[test]
fn test_exited_daemon_releases_log_and_throttle_state() {
    let dir = tempfile::tempdir().unwrap();
    let (mut tracker, procs) = tracker_with_daemon_log(dir.path(), SHARED_DAEMON_LOG);
    let phone = emu_group("emulator-5554", WORKER_A, "phone", Some("Phone"));
    emu_tick_with(&mut tracker, &procs, &[("phone_emulator-5554", &phone)], 10.0);
    let d_key = (DAEMON_A, START);
    assert!(tracker.daemons[&d_key].tailer.is_some());
    tracker.pcap_mgr.last_harvest_by_daemon.insert(d_key, 10.0);
    tracker.pcap_mgr.checked_emulators.insert((d_key, "Phone".to_string()));

    emu_tick_with(&mut tracker, &[], &[("phone_emulator-5554", &phone)], 20.0);
    let d = &tracker.daemons[&d_key];
    assert!(d.exited);
    assert!(d.tailer.is_none());
    assert!(!tracker.pcap_mgr.last_harvest_by_daemon.contains_key(&d_key));
    assert!(tracker.pcap_mgr.checked_emulators.is_empty());
}

#[test]
fn test_shared_log_truncation_client_exit_ready_port_and_1031_metadata() {
    let dir = tempfile::tempdir().unwrap();
    let art_dir = dir.path().join("android-test").join("netsimd");
    std::fs::create_dir_all(&art_dir).unwrap();
    let custom_art = dir.path().join("custom_art");
    std::fs::create_dir_all(&custom_art).unwrap();
    let log_path = art_dir.join("netsim_stderr.log");
    let ini_path = dir.path().join("netsim.ini");
    write_ini(&ini_path, &format!("grpc.port=43929\npid={DAEMON_A}\n"), START);
    write_ini(
        &custom_art.join("netsim_session_stats.json"),
        r#"{"version":"1.0.30","device_stats":[],"radio_stats":[]}"#,
        START + 1,
    );

    std::fs::write(
        &log_path,
        format!(
            "netsim I 09-25 22:37:05.100 netsimd.rs:100 - Artifacts: \"{}\"\n\
             netsim I 09-25 22:37:05.500 netsimd.rs:744 - Wrote to INI file {}\n",
            custom_art.display(),
            ini_path.display()
        ),
    )
    .unwrap();

    let mut tracker = tracker_for(&[dir.path()], dir.path());
    tracker.enable_log = true;
    let proc_a = netsimd(DAEMON_A, START, dir.path());

    tick(&mut tracker, std::slice::from_ref(&proc_a), START + 6);
    let d_a = &tracker.daemons[&(DAEMON_A, START)];
    assert_eq!(d_a.artifact_dir, custom_art);
    assert_eq!(d_a.version.as_deref(), Some("1.0.30"));
    assert!(
        tracker.timeline.iter().any(|(_, e)| e == "READY(pid=5000100, port=43929, ready_s=0.50s)"),
        "timeline={:?}",
        tracker.timeline
    );

    // Client-mode daemon spawns at START + 8 before flushing stderr, then writes
    // "Lock held by another process" + "NetsimDaemon main loop exited" and exits
    // while DAEMON_A stays alive.
    let daemon_c = DAEMON_B + 50;
    let proc_c = netsimd(daemon_c, START + 8, dir.path());
    tick(&mut tracker, &[proc_a.clone(), proc_c], START + 9);
    {
        use std::io::Write;
        let mut f = std::fs::OpenOptions::new().append(true).open(&log_path).unwrap();
        writeln!(
            f,
            "netsim W 09-25 22:37:14.100 netsimd.rs:500 - Lock held by another process\n\
             netsim I 09-25 22:37:14.200 netsimd.rs:900 - NetsimDaemon main loop exited"
        )
        .unwrap();
    }
    tick(&mut tracker, std::slice::from_ref(&proc_a), START + 10);
    assert!(
        tracker.timeline.iter().any(|(_, e)| e == &format!("CLIENT_MODE_SPAWN(pid={daemon_c})")),
        "timeline={:?}",
        tracker.timeline
    );

    // DAEMON_A logs graceful shutdown; on the next tick replacement DAEMON_B truncates
    // the shared log and binds port 43930 while DAEMON_A is still shutting down.
    {
        use std::io::Write;
        let mut f = std::fs::OpenOptions::new().append(true).open(&log_path).unwrap();
        writeln!(f, "netsim I 09-25 22:37:15.500 netsimd.rs:800 - Shutting down gracefully")
            .unwrap();
    }
    tick(&mut tracker, std::slice::from_ref(&proc_a), START + 11);

    write_ini(&ini_path, &format!("grpc.port=43930\npid={DAEMON_B}\n"), START + 12);
    let args_block = "    field: false,\n".repeat(25);
    std::fs::write(
        &log_path,
        format!(
            "netsim I 09-25 22:37:17.050 netsimd.rs:549 - Args {{\n{args_block}}}\n\
             netsim I 09-25 22:37:17.100 netsimd.rs:750 - gRPC port: 43930\n\
             netsim I 09-25 22:37:17.200 netsimd.rs:757 - Wrote to INI file {}\n",
            ini_path.display()
        ),
    )
    .unwrap();
    let proc_b = netsimd(DAEMON_B, START + 12, dir.path());
    tick(&mut tracker, &[proc_a, proc_b.clone()], START + 13);
    tick(&mut tracker, &[proc_b], START + 15);

    let d_a = &tracker.daemons[&(DAEMON_A, START)];
    assert_eq!(d_a.grpc_port, Some(43929));
    assert_eq!(d_a.exit_reason.as_deref(), Some("sigterm_sigint"));

    // Second check: "NetsimDaemon main loop exited" in shared log does not mask a
    // SIGKILL crash when the primary daemon leaves its INI behind, and unknown READY
    // port formats as port=N/A.
    let dir2 = tempfile::tempdir().unwrap();
    let art2 = dir2.path().join("android-test").join("netsimd");
    std::fs::create_dir_all(&art2).unwrap();
    let ini2 = dir2.path().join("netsim.ini");
    write_ini(&ini2, &format!("pid={DAEMON_A}\n"), START);
    std::fs::write(
        art2.join("netsim_stderr.log"),
        "netsim I 09-25 22:37:05.500 netsimd.rs:744 - Wrote to INI file /nonexistent/netsim.ini\n\
         netsim I 09-25 22:37:06.000 netsimd.rs:900 - NetsimDaemon main loop exited\n",
    )
    .unwrap();
    let mut tracker2 = tracker_for(&[dir2.path()], dir2.path());
    tracker2.enable_log = true;
    tick(&mut tracker2, &[netsimd(DAEMON_A, START, dir2.path())], START + 5);
    assert!(
        tracker2.timeline.iter().any(|(_, e)| e == "READY(pid=5000100, port=N/A, ready_s=0.50s)"),
        "timeline2={:?}",
        tracker2.timeline
    );
    tick(&mut tracker2, &[], START + 10);
    assert_eq!(
        tracker2.daemons[&(DAEMON_A, START)].exit_reason.as_deref(),
        Some("killed_or_crashed")
    );
}

#[test]
fn test_session_key_pid_reuse_bystander_ready_disabled_and_settled_dashboard() {
    let dir_a = tempfile::tempdir().unwrap();
    let dir_b = tempfile::tempdir().unwrap();
    let art_a = dir_a.path().join("android-test").join("netsimd");
    let art_b = dir_b.path().join("android-test").join("netsimd");
    std::fs::create_dir_all(&art_a).unwrap();
    std::fs::create_dir_all(&art_b).unwrap();
    let ini_a = dir_a.path().join("netsim.ini");
    let ini_b = dir_b.path().join("netsim.ini");
    write_ini(&ini_a, &format!("grpc.port=43929\npid={DAEMON_A}\n"), START);
    std::fs::write(
        art_a.join("netsim_stderr.log"),
        format!(
            "netsim I 09-25 22:37:05.020 netsimd.rs:744 - Wrote to INI file {}\n\
             netsim I 09-25 22:37:06.000 service.rs:585 - DeviceActor: AddChip 1 (kind WIFI) to device 1 (Alpha)\n",
            ini_a.display()
        ),
    )
    .unwrap();

    let mut tracker = tracker_for(&[dir_a.path(), dir_b.path()], dir_a.path());
    tracker.enable_log = true;
    let proc_a = netsimd(DAEMON_A, START, dir_a.path());

    // Alpha connects on emulator-5554 with group_pids=[WORKER_A, WORKER_A + 1], then settles.
    let mut alpha = emu_group("emulator-5554", WORKER_A, "alpha", Some("Alpha"));
    alpha.pids = vec![WORKER_A, WORKER_A + 1];
    alpha.primary_start_time = START;
    emu_tick_with(
        &mut tracker,
        std::slice::from_ref(&proc_a),
        &[("alpha_emulator-5554", &alpha)],
        5.0,
    );
    assert_eq!(tracker.emulator_records["alpha_emulator-5554"].state, "CONNECTED");
    emu_tick_with(&mut tracker, std::slice::from_ref(&proc_a), &[], 10.0);
    emu_tick_with(&mut tracker, std::slice::from_ref(&proc_a), &[], 12.0);
    assert_eq!(tracker.emulator_records["alpha_emulator-5554"].state, "DISCONNECTED");

    // A new emulator boots on emulator-5554 (bare serial first) reusing PID `WORKER_A + 1`:
    // it must NOT hijack settled `alpha_emulator-5554`.
    let mut zeta_bare = emu_group("emulator-5554", WORKER_A + 1, "zeta", None);
    zeta_bare.avd_id = None;
    zeta_bare.primary_start_time = START + 20;
    emu_tick_with(
        &mut tracker,
        std::slice::from_ref(&proc_a),
        &[("emulator-5554", &zeta_bare)],
        20.0,
    );
    assert!(tracker.emulator_records.contains_key("emulator-5554"));
    assert_eq!(tracker.emulator_records["emulator-5554"].state, "CONNECTING");
    assert_eq!(tracker.emulator_records["alpha_emulator-5554"].state, "DISCONNECTED");

    // Same-key restart reusing the exact same worker_pid with a newer start time also resets.
    let mut alpha_reused_pid = emu_group("emulator-5554", WORKER_A, "alpha", Some("Alpha"));
    alpha_reused_pid.primary_start_time = START + 21;
    emu_tick_with(
        &mut tracker,
        std::slice::from_ref(&proc_a),
        &[("alpha_emulator-5554", &alpha_reused_pid)],
        21.0,
    );
    assert_eq!(tracker.emulator_records["alpha_emulator-5554"].state, "CONNECTING");

    // Bystander single-daemon fallback on Tick 1 must not lock in DAEMON_A's ready_s once
    // phone60's own DAEMON_B spawns on port 43930 while DAEMON_A is still alive.
    let mut phone60 = emu_group("emulator-5560", WORKER_B, "phone60", Some("Phone60"));
    phone60.primary_start_time = START + 22;
    emu_tick_with(
        &mut tracker,
        std::slice::from_ref(&proc_a),
        &[("phone60_emulator-5560", &phone60)],
        22.0,
    );
    assert!(tracker.emulator_records["phone60_emulator-5560"].netsim_daemon_ready_s.is_some());
    write_ini(&ini_b, &format!("grpc.port=43930\npid={DAEMON_B}\n"), START + 23);
    phone60.grpc_endpoint = Some("localhost:43930".to_string());
    std::fs::write(
        art_b.join("netsim_stderr.log"),
        format!(
            "netsim I 09-25 22:37:28.420 netsimd.rs:744 - Wrote to INI file {}\n",
            ini_b.display()
        ),
    )
    .unwrap();
    let proc_b = netsimd(DAEMON_B, START + 23, dir_b.path());
    emu_tick_with(
        &mut tracker,
        &[proc_a.clone(), proc_b],
        &[("phone60_emulator-5560", &phone60)],
        24.0,
    );
    let ready_60 = tracker.emulator_records["phone60_emulator-5560"].netsim_daemon_ready_s;
    assert!((ready_60.unwrap_or(0.0) - 1.42).abs() < 0.05, "ready_60={ready_60:?}");

    // `-no-netsim` resolved on Tick 2 clears `daemon_key` so DISABLED never counts as joined.
    let mut disabled_emu = emu_group("emulator-5562", WORKER_B + 50, "no_net", Some("NoNet"));
    disabled_emu.primary_start_time = START + 25;
    emu_tick_with(
        &mut tracker,
        std::slice::from_ref(&proc_a),
        &[("no_net_emulator-5562", &disabled_emu)],
        25.0,
    );
    disabled_emu.no_netsim = true;
    emu_tick_with(
        &mut tracker,
        std::slice::from_ref(&proc_a),
        &[("no_net_emulator-5562", &disabled_emu)],
        27.0,
    );
    assert_eq!(tracker.emulator_records["no_net_emulator-5562"].state, "DISABLED");
    assert_eq!(tracker.emulator_records["no_net_emulator-5562"].daemon_key, None);

    // When all records on a serial are settled, dashboard picks the latest `first_seen_mono`.
    tracker.emulator_records.insert(
        "alpha_emulator-5570".to_string(),
        EmulatorNetsimRecord {
            storage_key: "alpha_emulator-5570".to_string(),
            serial: "emulator-5570".to_string(),
            first_seen_mono: 5.0,
            state: "FAILED(worker_exit_no_connect)".to_string(),
            ..Default::default()
        },
    );
    tracker.emulator_records.insert(
        "zeta_emulator-5570".to_string(),
        EmulatorNetsimRecord {
            storage_key: "zeta_emulator-5570".to_string(),
            serial: "emulator-5570".to_string(),
            first_seen_mono: 50.0,
            state: "DISCONNECTED".to_string(),
            disconnect_count: 1,
            ..Default::default()
        },
    );
    let dash = tracker.get_dashboard_snapshot(START as f64 + 60.0);
    assert!(
        dash.emulator_rows["emulator-5570"].contains("DISCONNECTED"),
        "row={}",
        dash.emulator_rows["emulator-5570"]
    );
}

#[test]
fn test_exited_bystander_ready_reset_and_zombie_ini_writer_treated_as_stale() {
    let dir_a = tempfile::tempdir().unwrap();
    let dir_b = tempfile::tempdir().unwrap();
    let art_a = dir_a.path().join("android-test").join("netsimd");
    let art_b = dir_b.path().join("android-test").join("netsimd");
    std::fs::create_dir_all(&art_a).unwrap();
    std::fs::create_dir_all(&art_b).unwrap();
    let ini_a = dir_a.path().join("netsim.ini");
    let ini_b = dir_b.path().join("netsim.ini");
    write_ini(&ini_a, &format!("grpc.port=43929\npid={DAEMON_A}\n"), START);
    std::fs::write(
        art_a.join("netsim_stderr.log"),
        format!(
            "netsim I 09-25 22:37:05.020 netsimd.rs:744 - Wrote to INI file {}\n",
            ini_a.display()
        ),
    )
    .unwrap();

    let mut tracker = tracker_for(&[dir_a.path(), dir_b.path()], dir_a.path());
    tracker.enable_log = true;
    let proc_a = netsimd(DAEMON_A, START, dir_a.path());

    // Pre-existing bystander DAEMON_A was already ready at START + 0.020; phone60 spawns
    // at START + 22 on Tick 1 while DAEMON_A is shutting down, and by Tick 2 DAEMON_A has
    // exited and phone60's own DAEMON_B is ready at START + 23.420 (ready_s = 1.42s).
    let mut phone60 = emu_group("emulator-5560", WORKER_B, "phone60", Some("Phone60"));
    phone60.primary_start_time = START + 22;
    emu_tick_with(&mut tracker, &[proc_a], &[("phone60_emulator-5560", &phone60)], 22.0);
    assert!(tracker.emulator_records["phone60_emulator-5560"].netsim_daemon_ready_s.is_some());

    write_ini(&ini_b, &format!("grpc.port=43930\npid={DAEMON_B}\n"), START + 23);
    phone60.grpc_endpoint = Some("localhost:43930".to_string());
    std::fs::write(
        art_b.join("netsim_stderr.log"),
        format!(
            "netsim I 09-25 22:37:28.420 netsimd.rs:744 - Wrote to INI file {}\n",
            ini_b.display()
        ),
    )
    .unwrap();
    let proc_b = netsimd(DAEMON_B, START + 23, dir_b.path());
    emu_tick_with(&mut tracker, &[proc_b], &[("phone60_emulator-5560", &phone60)], 24.0);
    let ready_60 = tracker.emulator_records["phone60_emulator-5560"].netsim_daemon_ready_s;
    assert!((ready_60.unwrap_or(0.0) - 1.42).abs() < 0.05, "ready_60={ready_60:?}");

    #[cfg(target_os = "linux")]
    {
        let zdir = tempfile::tempdir().unwrap();
        let mut child = std::process::Command::new("/bin/true").spawn().unwrap();
        let zpid = child.id();
        let mut sys = sysinfo::System::new();
        let spid = sysinfo::Pid::from_u32(zpid);
        let mut saw_zombie = false;
        for _ in 0..500 {
            sys.refresh_processes_specifics(
                sysinfo::ProcessesToUpdate::Some(&[spid]),
                true,
                sysinfo::ProcessRefreshKind::nothing(),
            );
            if sys.process(spid).is_some_and(|p| p.status() == sysinfo::ProcessStatus::Zombie) {
                saw_zombie = true;
                break;
            }
            std::thread::sleep(Duration::from_millis(10));
        }
        let zombie_alive = pid_exists(zpid);
        write_ini(
            &zdir.path().join("netsim.ini"),
            &format!("grpc.port=43999\npid={zpid}\n"),
            START,
        );
        let mut ztracker = tracker_for(&[zdir.path()], zdir.path());
        tick(&mut ztracker, &[], START + 10);
        child.wait().unwrap();
        assert!(saw_zombie, "child never entered Zombie state");
        assert!(!zombie_alive, "zombie PID must not be treated as alive by pid_exists");
        assert_eq!(ztracker.stale_inis.len(), 1);
    }
}

#[cfg(unix)]
#[test]
fn test_foreign_daemon_empty_env_does_not_hijack_own_artifacts_or_join() {
    let dir = tempfile::tempdir().unwrap();
    let out_dir = tempfile::tempdir().unwrap();
    let art_dir = dir.path().join("android-test").join("netsimd");
    std::fs::create_dir_all(&art_dir).unwrap();
    let ini_own = dir.path().join("netsim.ini");
    write_ini(&ini_own, &format!("grpc.port=43929\npid={DAEMON_A}\n"), START);
    std::fs::write(
        art_dir.join("netsim_stderr.log"),
        format!(
            "netsim I 09-25 22:37:05.050 netsimd.rs:744 - Wrote to INI file {}\n\
             netsim I 09-25 22:37:05.051 netsimd.rs:750 - gRPC port: 43929\n\
             netsim I 09-25 22:37:05.200 device.rs:100 - DeviceActor: Create device Phone\n\
             netsim I 09-25 22:37:05.300 device.rs:110 - DeviceActor: AddChip 10 (kind WIFI, name \"wlan0\") to device 1 (Phone)\n",
            ini_own.display()
        ),
    )
    .unwrap();
    std::fs::write(art_dir.join("netsim_artifacts_own.zip"), b"PK\x03\x04ownzip").unwrap();
    std::fs::write(art_dir.join("netsim_session_stats.json"), r#"{"version":"1.0.31"}"#).unwrap();
    std::fs::File::options()
        .write(true)
        .open(art_dir.join("netsim_session_stats.json"))
        .unwrap()
        .set_modified(UNIX_EPOCH + Duration::from_secs(START + 1))
        .unwrap();

    let mut tracker = NetsimTracker::new(Some(""), 256, true, out_dir.path());
    tracker.host_os = HostOs::Linux;
    tracker.custom_ini_dirs = vec![dir.path().to_path_buf()];
    tracker.custom_proc_locks = Some(String::new());
    tracker.pcap_mgr.cli_runner = std::sync::Arc::new(|_, _| {
        Some(
            "ID | Device Name | Chip Kind | State | Timestamp | Records | Size\n\
             1  | Phone       | WIFI      | off   | 0         | 0       | 0\n"
                .to_string(),
        )
    });

    let foreign_empty_1 = NetsimDaemonProc {
        pid: DAEMON_A - 20,
        parent: Some(1),
        start_time: START - 10,
        exe: Some(PathBuf::from("/sdk/emulator/netsimd")),
        cmd: vec!["/sdk/emulator/netsimd".to_string()],
        environ: HashMap::new(),
        same_user: false,
        ..Default::default()
    };
    let foreign_empty_2 = NetsimDaemonProc {
        pid: DAEMON_A - 19,
        parent: Some(1),
        start_time: START - 9,
        exe: Some(PathBuf::from("/sdk/emulator/netsimd")),
        cmd: vec!["/sdk/emulator/netsimd".to_string()],
        environ: HashMap::new(),
        same_user: false,
        ..Default::default()
    };
    let mut foreign_crafted = netsimd(DAEMON_A - 10, START - 5, dir.path());
    foreign_crafted.same_user = false;
    let own_proc = netsimd(DAEMON_A, START, dir.path());

    let mut phone = emu_group("emulator-5554", WORKER_A, "phone", Some("Phone"));
    phone.primary_start_time = START;
    phone.grpc_endpoint = None;

    emu_tick_with(
        &mut tracker,
        &[foreign_empty_1.clone(), foreign_empty_2.clone(), foreign_crafted.clone(), own_proc],
        &[("phone_emulator-5554", &phone)],
        1.0,
    );

    for fk in [(DAEMON_A - 20, START - 10), (DAEMON_A - 19, START - 9), (DAEMON_A - 10, START - 5)]
    {
        let fd = &tracker.daemons[&fk];
        assert!(
            fd.artifact_dir.as_os_str().is_empty(),
            "foreign daemon adopted {:?}",
            fd.artifact_dir
        );
        assert!(fd.log_path.as_os_str().is_empty());
        assert!(fd.tailer.is_none());
        assert_eq!(fd.grpc_port, None);
    }
    assert!(
        !tracker.timeline.iter().any(|(_, e)| e.contains("DUPLICATE_ARTIFACT_DIR")),
        "timeline={:?}",
        tracker.timeline
    );
    assert_eq!(
        tracker.emulator_records["phone_emulator-5554"].daemon_key,
        Some((DAEMON_A, START)),
        "emulator must join its sole same-user daemon despite active foreign daemons"
    );
    assert_eq!(tracker.emulator_records["phone_emulator-5554"].state, "CONNECTED");
    assert!(
        tracker.pcap_mgr.enabled_by_monitor.contains_key(&((DAEMON_A, START), 1)),
        "auto-capture must run against the sole same-user daemon"
    );

    // Own daemon exits while foreign daemons stay alive: exit harvest + stats backfill
    // must still run, and Phone must settle rather than falling back to a foreign daemon.
    let _ = std::fs::remove_file(&ini_own);
    emu_tick_with(
        &mut tracker,
        &[foreign_empty_1, foreign_empty_2, foreign_crafted],
        &[("phone_emulator-5554", &phone)],
        5.0,
    );
    assert_eq!(tracker.daemons[&(DAEMON_A, START)].version.as_deref(), Some("1.0.31"));
    assert!(
        !tracker.pcap_mgr.rescued_zips.is_empty(),
        "own zip harvest must not be blocked by active foreign daemons"
    );
    assert_eq!(tracker.emulator_records["phone_emulator-5554"].state, "DISCONNECTED");
}

#[test]
fn test_qemu_next_shutdown_companions_do_not_join_bystander_or_unsettle() {
    let dir_a = tempfile::tempdir().unwrap();
    let dir_b = tempfile::tempdir().unwrap();
    let art_a = dir_a.path().join("android-test").join("netsimd");
    let art_b = dir_b.path().join("android-test").join("netsimd");
    std::fs::create_dir_all(&art_a).unwrap();
    std::fs::create_dir_all(&art_b).unwrap();
    let ini_a = dir_a.path().join("netsim.ini");
    let ini_b = dir_b.path().join("netsim_2.ini");
    write_ini(&ini_a, &format!("grpc.port=43929\npid={DAEMON_A}\n"), START);
    write_ini(&ini_b, &format!("grpc.port=43930\npid={DAEMON_B}\n"), START);
    std::fs::write(
        art_a.join("netsim_stderr.log"),
        format!(
            "netsim I 09-25 22:37:05.020 netsimd.rs:744 - Wrote to INI file {}\n\
             netsim I 09-25 22:37:06.000 service.rs:585 - DeviceActor: AddChip 1 (kind BLUETOOTH) to device 1 (Phone)\n",
            ini_a.display()
        ),
    )
    .unwrap();
    std::fs::write(
        art_b.join("netsim_2_stderr.log"),
        format!(
            "netsim I 09-25 22:37:05.030 netsimd.rs:744 - Wrote to INI file {}\n\
             netsim I 09-25 22:37:05.400 service.rs:585 - DeviceActor: AddChip 2 (kind WIFI) to device 1 (Glasses)\n\
             netsim I 09-25 22:37:05.500 ap_actor.rs:100 - ApActor: Associated 02:00:00:00:00:01\n",
            ini_b.display()
        ),
    )
    .unwrap();

    let mut tracker = tracker_for(&[dir_a.path(), dir_b.path()], dir_a.path());
    tracker.enable_log = true;
    let proc_a = netsimd(DAEMON_A, START, dir_a.path());
    let mut proc_b = netsimd(DAEMON_B, START, dir_b.path());
    proc_b.cmd.push("--instance".to_string());
    proc_b.cmd.push("2".to_string());

    let launcher_pid = 5_000_600u32;
    let fishtank_pid = 5_000_601u32;
    let phone_key = "phone_emulator-5554";
    let glasses_key = "glasses_emulator-5556";

    let mut phone = emu_group("emulator-5554", WORKER_A, "phone", Some("Phone"));
    phone.pids = vec![launcher_pid, WORKER_A, fishtank_pid];
    phone.primary_start_time = START;
    phone.member_start_times =
        BTreeMap::from([(launcher_pid, START - 2), (WORKER_A, START), (fishtank_pid, START + 1)]);
    phone.grpc_endpoint = Some("localhost:43929".to_string());

    let mut glasses = emu_group("emulator-5556", WORKER_B, "glasses", Some("Glasses"));
    glasses.primary_start_time = START;
    glasses.grpc_endpoint = Some("localhost:43930".to_string());

    emu_tick_with(
        &mut tracker,
        &[proc_a.clone(), proc_b.clone()],
        &[(phone_key, &phone), (glasses_key, &glasses)],
        10.0,
    );
    assert_eq!(tracker.emulator_records[phone_key].state, "CONNECTED");
    assert_eq!(tracker.emulator_records[phone_key].chip_attach_s["BT"], (1.0, false));
    assert_eq!(tracker.emulator_records[glasses_key].wifi_associated_s, Some(0.5));

    // VM worker and DAEMON_A exit simultaneously while fishtank lingers for 1 tick;
    // Phone must settle cleanly without joining bystander DAEMON_B or wiping its BT chip.
    let _ = std::fs::remove_file(&ini_a);
    let mut fishtank_only = emu_group("emulator-5554", fishtank_pid, "phone", Some("Phone"));
    fishtank_only.pids = vec![fishtank_pid];
    fishtank_only.primary_start_time = START + 1;
    fishtank_only.member_start_times = BTreeMap::from([(fishtank_pid, START + 1)]);
    fishtank_only.grpc_endpoint = None;

    emu_tick_with(
        &mut tracker,
        std::slice::from_ref(&proc_b),
        &[(phone_key, &fishtank_only), (glasses_key, &glasses)],
        12.0,
    );
    let phone_rec = &tracker.emulator_records[phone_key];
    assert_eq!(phone_rec.daemon_key, Some((DAEMON_A, START)));
    assert_eq!(phone_rec.state, "DISCONNECTED");
    assert_eq!(phone_rec.disconnect_count, 1);
    assert_eq!(phone_rec.chip_attach_s.get("BT"), Some(&(1.0, false)));
    assert_eq!(tracker.emulator_records[glasses_key].wifi_associated_s, Some(0.5));

    // A settled record whose companion reappears briefly while its daemon is still alive
    // must remain settled rather than flipping back to CONNECTED.
    let dir_c = tempfile::tempdir().unwrap();
    let (mut tracker2, procs2) = tracker_with_daemon_log(
        dir_c.path(),
        "netsim I 09-25 22:37:06.000 service.rs:585 - DeviceActor: AddChip 1 (kind BLUETOOTH) to device 1 (Phone)\n",
    );
    emu_tick_with(&mut tracker2, &procs2, &[(phone_key, &phone)], 10.0);
    emu_tick_with(&mut tracker2, &procs2, &[], 12.0);
    emu_tick_with(&mut tracker2, &procs2, &[], 14.0);
    assert_eq!(tracker2.emulator_records[phone_key].state, "DISCONNECTED");
    assert_eq!(tracker2.emulator_records[phone_key].disconnect_count, 1);

    let mut launcher_only = emu_group("emulator-5554", launcher_pid, "phone", Some("Phone"));
    launcher_only.pids = vec![launcher_pid];
    launcher_only.primary_start_time = START - 2;
    launcher_only.member_start_times = BTreeMap::from([(launcher_pid, START - 2)]);
    emu_tick_with(&mut tracker2, &procs2, &[(phone_key, &launcher_only)], 16.0);
    assert_eq!(tracker2.emulator_records[phone_key].state, "DISCONNECTED");
    assert_eq!(tracker2.emulator_records[phone_key].disconnect_count, 1);
}

#[test]
fn test_logged_stale_ini_does_not_override_paired_ini_or_exit_reason_and_exited_disabled_row_yields()
 {
    let dir = tempfile::tempdir().unwrap();
    let art = dir.path().join("android-test").join("netsimd");
    std::fs::create_dir_all(&art).unwrap();
    let stale_ini = dir.path().join("netsim.ini");
    let own_ini = dir.path().join("netsim_2.ini");
    write_ini(&stale_ini, &format!("grpc.port=41111\npid={DEAD_PID}\n"), START - 30);
    write_ini(&own_ini, &format!("grpc.port=43930\npid={DAEMON_A}\n"), START);

    // Log contains an earlier stale `Wrote to INI file .../netsim.ini` line while
    // `reconcile_ini_files` pairs DAEMON_A (`--instance 2`) with `netsim_2.ini`.
    std::fs::write(
        art.join("netsim_2_stderr.log"),
        format!(
            "netsim I 09-25 22:37:05.010 netsimd.rs:744 - Wrote to INI file {}\n\
             netsim I 09-25 22:37:05.020 netsimd.rs:750 - gRPC port: 43930\n",
            stale_ini.display()
        ),
    )
    .unwrap();

    let mut tracker = tracker_for(&[dir.path()], dir.path());
    tracker.enable_log = true;
    let mut proc_a = netsimd(DAEMON_A, START, dir.path());
    proc_a.cmd.push("--instance".to_string());
    proc_a.cmd.push("2".to_string());

    emu_tick_with(&mut tracker, std::slice::from_ref(&proc_a), &[], 1.0);
    assert_eq!(tracker.daemons[&(DAEMON_A, START)].ini_path.as_deref(), Some(own_ini.as_path()));

    // Subsequent poll must NOT overwrite `own_ini` with `stale_ini`.
    emu_tick_with(&mut tracker, std::slice::from_ref(&proc_a), &[], 2.0);
    assert_eq!(tracker.daemons[&(DAEMON_A, START)].ini_path.as_deref(), Some(own_ini.as_path()));

    // Clean exit deletes `own_ini` (`netsim_2.ini`), leaving `stale_ini` (`netsim.ini`) on disk:
    // exit reason must be `clean_unknown`, not `killed_or_crashed`.
    let _ = std::fs::remove_file(&own_ini);
    emu_tick_with(&mut tracker, &[], &[], 4.0);
    assert_eq!(tracker.daemons[&(DAEMON_A, START)].exit_reason.as_deref(), Some("clean_unknown"));

    // Exited `-no-netsim` (`DISABLED`) record on emulator-5572 must not hide a newer
    // settled (`DISCONNECTED`) session on the same serial in dashboard snapshots.
    let mut disabled_emu = emu_group("emulator-5572", WORKER_A, "nonet", Some("NoNet"));
    disabled_emu.no_netsim = true;
    disabled_emu.primary_start_time = START + 5;
    emu_tick_with(&mut tracker, &[], &[("nonet_emulator-5572", &disabled_emu)], 5.0);
    emu_tick_with(&mut tracker, &[], &[], 7.0);
    emu_tick_with(&mut tracker, &[], &[], 9.0);
    assert_eq!(tracker.emulator_records["nonet_emulator-5572"].state, "DISABLED");

    tracker.emulator_records.insert(
        "later_emulator-5572".to_string(),
        EmulatorNetsimRecord {
            storage_key: "later_emulator-5572".to_string(),
            serial: "emulator-5572".to_string(),
            first_seen_mono: 20.0,
            state: "DISCONNECTED".to_string(),
            disconnect_count: 1,
            ..Default::default()
        },
    );
    let dash = tracker.get_dashboard_snapshot(START as f64 + 25.0);
    assert!(
        dash.emulator_rows["emulator-5572"].contains("DISCONNECTED"),
        "row={}",
        dash.emulator_rows["emulator-5572"]
    );
}

#[test]
fn test_daemon_exit_before_worker_does_not_join_bystander_daemon() {
    let dir_a = tempfile::tempdir().unwrap();
    let dir_b = tempfile::tempdir().unwrap();
    let art_a = dir_a.path().join("android-test").join("netsimd");
    let art_b = dir_b.path().join("android-test").join("netsimd");
    std::fs::create_dir_all(&art_a).unwrap();
    std::fs::create_dir_all(&art_b).unwrap();
    let ini_a = dir_a.path().join("netsim.ini");
    let ini_b = dir_b.path().join("netsim_2.ini");
    write_ini(&ini_a, &format!("grpc.port=43929\npid={DAEMON_A}\n"), START);
    write_ini(&ini_b, &format!("grpc.port=43930\npid={DAEMON_B}\n"), START);
    std::fs::write(
        art_a.join("netsim_stderr.log"),
        format!(
            "netsim I 09-25 22:37:05.020 netsimd.rs:744 - Wrote to INI file {}\n\
             netsim I 09-25 22:37:06.000 service.rs:585 - DeviceActor: AddChip 1 (kind BLUETOOTH) to device 1 (Pixel 8)\n",
            ini_a.display()
        ),
    )
    .unwrap();
    let log_b = art_b.join("netsim_2_stderr.log");
    std::fs::write(
        &log_b,
        format!(
            "netsim I 09-25 22:37:05.030 netsimd.rs:744 - Wrote to INI file {}\n\
             netsim I 09-25 22:37:05.400 service.rs:585 - DeviceActor: AddChip 2 (kind WIFI) to device 1 (Pixel 8)\n",
            ini_b.display()
        ),
    )
    .unwrap();

    let mut tracker = tracker_for(&[dir_a.path(), dir_b.path()], dir_a.path());
    tracker.enable_log = true;
    let proc_a = netsimd(DAEMON_A, START, dir_a.path());
    let mut proc_b = netsimd(DAEMON_B, START, dir_b.path());
    proc_b.cmd.extend(["--instance".to_string(), "2".to_string()]);

    let mut phone = emu_group("emulator-5554", WORKER_A, "pixel8_a", Some("Pixel 8"));
    phone.pids = vec![WORKER_A];
    phone.primary_start_time = START;
    phone.member_start_times = BTreeMap::from([(WORKER_A, START)]);
    phone.grpc_endpoint = Some("localhost:43929".to_string());

    let mut glasses = emu_group("emulator-5556", WORKER_B, "pixel8_b", Some("Pixel 8"));
    glasses.pids = vec![WORKER_B];
    glasses.primary_start_time = START;
    glasses.member_start_times = BTreeMap::from([(WORKER_B, START)]);
    glasses.grpc_endpoint = Some("localhost:43930".to_string());

    let key_a = "pixel8_a_emulator-5554";
    let key_b = "pixel8_b_emulator-5556";
    emu_tick_with(
        &mut tracker,
        &[proc_a, proc_b.clone()],
        &[(key_a, &phone), (key_b, &glasses)],
        10.0,
    );
    assert_eq!(tracker.emulator_records[key_a].chip_attach_s.get("BT"), Some(&(1.0, false)));

    // DAEMON_A exits while WORKER_A is still alive (e.g. saving Quickboot snapshot);
    // Glasses on DAEMON_B associates with Wi-Fi at START + 11s.
    let _ = std::fs::remove_file(&ini_a);
    {
        use std::io::Write;
        writeln!(
            std::fs::OpenOptions::new().append(true).open(&log_b).unwrap(),
            "netsim I 09-25 22:37:16.000 ap_actor.rs:100 - ApActor: Associated 02:00:00:00:00:01"
        )
        .unwrap();
    }
    emu_tick_with(
        &mut tracker,
        std::slice::from_ref(&proc_b),
        &[(key_a, &phone), (key_b, &glasses)],
        12.0,
    );

    let rec_a = &tracker.emulator_records[key_a];
    let rec_b = &tracker.emulator_records[key_b];
    assert_eq!(rec_a.daemon_key, Some((DAEMON_A, START)));
    assert_eq!(rec_a.state, "DISCONNECTED");
    assert_eq!(rec_a.disconnect_count, 1);
    assert_eq!(rec_a.chip_attach_s.get("BT"), Some(&(1.0, false)));
    assert_eq!(rec_b.wifi_associated_s, Some(11.0));
}

#[test]
fn test_prior_bare_serial_session_preserved_and_reused_pid_bare_migration() {
    let dir = tempfile::tempdir().unwrap();
    let (mut tracker, procs) = tracker_with_daemon_log(
        dir.path(),
        "netsim I 09-25 22:37:06.000 service.rs:585 - DeviceActor: AddChip 1 (kind WIFI) to device 1 (emulator-5554)\n",
    );

    // Part 1: Unnamed Session 1 runs on emulator-5554 from t=5..25 (20s connected), then exits.
    let mut unnamed_g = EmuGroupNetsimInfo {
        serial: "emulator-5554".to_string(),
        port: 5554,
        primary_pid: WORKER_A,
        primary_start_time: START,
        pids: vec![WORKER_A],
        member_start_times: BTreeMap::from([(WORKER_A, START)]),
        ..Default::default()
    };
    emu_tick_with(&mut tracker, &procs, &[("emulator-5554", &unnamed_g)], 5.0);
    assert_eq!(tracker.emulator_records["emulator-5554"].state, "CONNECTED");
    emu_tick_with(&mut tracker, &procs, &[], 25.0);
    emu_tick_with(&mut tracker, &procs, &[], 27.0);
    assert_eq!(tracker.emulator_records["emulator-5554"].state, "DISCONNECTED");
    assert_eq!(tracker.emulator_records["emulator-5554"].connected_total_s, 20.0);

    // Session 2 (named Pixel_8) launches at t=40 with 1 bare-serial launcher tick,
    // then resolves Pixel_8 at t=42.
    let launcher_pid = WORKER_B - 1;
    unnamed_g.primary_pid = launcher_pid;
    unnamed_g.primary_start_time = START + 40;
    unnamed_g.pids = vec![launcher_pid];
    unnamed_g.member_start_times = BTreeMap::from([(launcher_pid, START + 40)]);
    emu_tick_with(&mut tracker, &procs, &[("emulator-5554", &unnamed_g)], 40.0);

    let mut g_pixel = emu_group("emulator-5554", WORKER_B, "Pixel_8", Some("Pixel 8"));
    g_pixel.primary_start_time = START + 41;
    g_pixel.pids = vec![launcher_pid, WORKER_B];
    g_pixel.member_start_times =
        BTreeMap::from([(launcher_pid, START + 40), (WORKER_B, START + 41)]);
    emu_tick_with(&mut tracker, &procs, &[("Pixel_8_emulator-5554", &g_pixel)], 42.0);

    let session1 = &tracker.emulator_records["emulator-5554"];
    assert_eq!(session1.state, "DISCONNECTED");
    assert_eq!(session1.disconnect_count, 1);
    assert_eq!(session1.connected_total_s, 20.0);

    let pixel_rec = &tracker.emulator_records["Pixel_8_emulator-5554"];
    assert_eq!(pixel_rec.disconnect_count, 0);
    assert_eq!(pixel_rec.connected_total_s, 0.0);
    assert_eq!(pixel_rec.first_seen_mono, 40.0);

    // Part 2: Reused OS PID across same-AVD restart with 1 bare-serial tick migrates cleanly.
    let dir2 = tempfile::tempdir().unwrap();
    let mut tracker2 = tracker_for(&[dir2.path()], dir2.path());
    let mut phone1 = emu_group("emulator-5554", WORKER_A, "Phone", Some("Phone"));
    phone1.primary_start_time = START;
    phone1.pids = vec![WORKER_A];
    phone1.member_start_times = BTreeMap::from([(WORKER_A, START)]);
    emu_tick(&mut tracker2, &[("Phone_emulator-5554", &phone1)], 1.0);
    emu_tick(&mut tracker2, &[], 5.0);
    emu_tick(&mut tracker2, &[], 7.0);
    assert_eq!(
        tracker2.emulator_records["Phone_emulator-5554"].state,
        "FAILED(worker_exit_no_connect)"
    );

    let mut phone2_bare = EmuGroupNetsimInfo {
        serial: "emulator-5554".to_string(),
        port: 5554,
        primary_pid: WORKER_A,
        primary_start_time: START + 20,
        pids: vec![WORKER_A],
        member_start_times: BTreeMap::from([(WORKER_A, START + 20)]),
        ..Default::default()
    };
    emu_tick(&mut tracker2, &[("emulator-5554", &phone2_bare)], 20.0);
    tracker2.emulator_records.get_mut("emulator-5554").unwrap().first_transport_ts =
        Some(START as f64 + 20.5);

    phone2_bare.avd_id = Some("Phone".to_string());
    phone2_bare.display_name = Some("Phone".to_string());
    emu_tick(&mut tracker2, &[("Phone_emulator-5554", &phone2_bare)], 22.0);
    assert!(!tracker2.emulator_records.contains_key("emulator-5554"));
    assert_eq!(
        tracker2.emulator_records["Phone_emulator-5554"].first_transport_ts,
        Some(START as f64 + 20.5)
    );
}

#[test]
fn test_duplicate_display_names_disambiguate_by_peer_port_or_stay_ambiguous() {
    fn two_emu_lsof(_: &str, _: &[&str]) -> Option<String> {
        Some(
            "p5000300\nf42\nn127.0.0.1:50001->127.0.0.1:43929\n\
             p5000400\nf43\nn127.0.0.1:50002->127.0.0.1:43929\n"
                .to_string(),
        )
    }

    // Part 1: Modern netsimd logs AddChipByGuid peer ports -> each duplicate-named
    // emulator claims only its own socket's chips and clears `ambiguous`.
    let dir1 = tempfile::tempdir().unwrap();
    std::fs::write(dir1.path().join("netsim.ini"), format!("grpc.port=43929\npid={DAEMON_A}\n"))
        .unwrap();
    let modern_log = concat!(
        "netsim I 09-25 22:37:06.000 netsimd.rs:106 - Handling new connection for WIFI on device Pixel 8\n",
        "netsim I 09-25 22:37:06.000 service.rs:550 - DeviceActor: AddChipByGuid 5554 for device 127.0.0.1:50001\n",
        "netsim I 09-25 22:37:06.000 service.rs:585 - DeviceActor: AddChip 1 (kind WIFI) to device 1 (Pixel 8)\n",
        "netsim I 09-25 22:37:08.000 netsimd.rs:106 - Handling new connection for BLUETOOTH on device Pixel 8\n",
        "netsim I 09-25 22:37:08.000 service.rs:550 - DeviceActor: AddChipByGuid 5556 for device 127.0.0.1:50002\n",
        "netsim I 09-25 22:37:08.000 service.rs:585 - DeviceActor: AddChip 2 (kind BLUETOOTH) to device 2 (Pixel 8)\n",
    );
    let (mut tracker1, procs1) = tracker_with_daemon_log(dir1.path(), modern_log);
    tracker1.host_os = HostOs::MacOs;
    tracker1.socket_cli_runner = two_emu_lsof;

    let mut g_a = emu_group("emulator-5554", WORKER_A, "Pixel_8_a", Some("Pixel 8"));
    g_a.primary_start_time = START;
    g_a.pids = vec![WORKER_A];
    g_a.member_start_times = BTreeMap::from([(WORKER_A, START)]);

    let mut g_b = emu_group("emulator-5556", WORKER_B, "Pixel_8_b", Some("Pixel 8"));
    g_b.primary_start_time = START + 2;
    g_b.pids = vec![WORKER_B];
    g_b.member_start_times = BTreeMap::from([(WORKER_B, START + 2)]);

    emu_tick_with(
        &mut tracker1,
        &procs1,
        &[("Pixel_8_a_emulator-5554", &g_a), ("Pixel_8_b_emulator-5556", &g_b)],
        10.0,
    );

    let rec_a = &tracker1.emulator_records["Pixel_8_a_emulator-5554"];
    let rec_b = &tracker1.emulator_records["Pixel_8_b_emulator-5556"];
    assert_eq!(rec_a.chip_ids, BTreeSet::from(["1".to_string()]));
    assert!(rec_a.chip_attach_s.contains_key("WIFI"));
    assert!(!rec_a.chip_attach_s.contains_key("BT"));
    assert!(!rec_a.ambiguous);

    assert_eq!(rec_b.chip_ids, BTreeSet::from(["2".to_string()]));
    assert!(rec_b.chip_attach_s.contains_key("BT"));
    assert!(!rec_b.chip_attach_s.contains_key("WIFI"));
    assert!(!rec_b.ambiguous);

    // Part 2: Legacy netsimd without AddChipByGuid -> chips match only by shared
    // device_name without peer_port, so `ambiguous` must remain `true`.
    let dir2 = tempfile::tempdir().unwrap();
    std::fs::write(dir2.path().join("netsim.ini"), format!("grpc.port=43929\npid={DAEMON_A}\n"))
        .unwrap();
    let legacy_log = "netsim I 09-25 22:37:08.000 service.rs:585 - DeviceActor: AddChip 1 (kind WIFI) to device 1 (Pixel 8)\n";
    let (mut tracker2, procs2) = tracker_with_daemon_log(dir2.path(), legacy_log);
    tracker2.host_os = HostOs::MacOs;
    tracker2.socket_cli_runner = two_emu_lsof;

    emu_tick_with(
        &mut tracker2,
        &procs2,
        &[("Pixel_8_a_emulator-5554", &g_a), ("Pixel_8_b_emulator-5556", &g_b)],
        10.0,
    );
    assert!(tracker2.emulator_records["Pixel_8_a_emulator-5554"].ambiguous);
    assert!(tracker2.emulator_records["Pixel_8_b_emulator-5556"].ambiguous);
}

#[test]
fn test_precise_start_time_rejects_recycled_pid_and_zero_ports_ignored() {
    let dir = tempfile::tempdir().unwrap();
    let ini_path = dir.path().join("netsim.ini");
    std::fs::write(&ini_path, format!("grpc.port=0\npid={DAEMON_A}\n")).unwrap();
    let obs = IniObservation::read(ini_path, &HashMap::new());
    assert_eq!(obs.port, None, "grpc.port=0 in netsim.ini must be rejected");

    let mut g = emu_group("emulator-5554", WORKER_A, "Phone", Some("Phone"));
    g.grpc_endpoint = Some("localhost:0".to_string());
    assert_eq!(NetsimTracker::extract_target_port(Some(&g)), None);

    #[cfg(target_os = "linux")]
    {
        let stale_create = 1_000_000.0;
        let got = get_proc_precise_start_time(std::process::id(), stale_create);
        assert!(
            (got - stale_create).abs() < 1e-6,
            "recycled PID /proc/<pid>/stat outside 2s window must fall back to create_time, got {got}"
        );
    }

    #[cfg(unix)]
    {
        let real_art = dir.path().join("real_art");
        std::fs::create_dir_all(&real_art).unwrap();
        let android_tmp = dir.path().join("atmp");
        std::fs::create_dir_all(android_tmp.join("android-test")).unwrap();
        std::os::unix::fs::symlink(&real_art, android_tmp.join("android-test").join("netsimd"))
            .unwrap();
        let mut proc_sym = netsimd(DAEMON_A, START, dir.path());
        proc_sym.environ.insert("ANDROID_TMP".to_string(), android_tmp.to_string_lossy().into());

        let mut tracker = tracker_for(&[dir.path()], dir.path());
        tracker.discover_and_poll_daemons(&[proc_sym], &HashMap::new(), START as f64, 1.0);
        assert!(
            tracker.daemons[&(DAEMON_A, START)].artifact_dir.as_os_str().is_empty(),
            "leaf-symlinked artifact_dir must be cleared on daemon registration"
        );
    }
}

#[test]
fn test_settled_bare_preserved_on_named_shutdown_and_worker_reset_clears_names() {
    let dir = tempfile::tempdir().unwrap();
    let (mut tracker, procs) = tracker_with_daemon_log(
        dir.path(),
        concat!(
            "netsim I 09-25 22:37:06.000 service.rs:585 - DeviceActor: AddChip 1 (kind WIFI) to device 1 (Wear_Bare)\n",
            "netsim I 09-25 22:37:46.000 service.rs:585 - DeviceActor: AddChip 2 (kind BLUETOOTH) to device 2 (Phone)\n",
        ),
    );

    // Part 1: Unnamed bare-serial Session 1 runs t=5..25 (20s connected) and settles.
    let unnamed = EmuGroupNetsimInfo {
        serial: "emulator-5554".to_string(),
        port: 5554,
        primary_pid: WORKER_A,
        primary_start_time: START,
        pids: vec![WORKER_A],
        member_start_times: BTreeMap::from([(WORKER_A, START)]),
        ..Default::default()
    };
    emu_tick_with(&mut tracker, &procs, &[("emulator-5554", &unnamed)], 5.0);
    emu_tick_with(&mut tracker, &procs, &[], 25.0);
    emu_tick_with(&mut tracker, &procs, &[], 27.0);
    assert_eq!(tracker.emulator_records["emulator-5554"].state, "DISCONNECTED");
    assert_eq!(tracker.emulator_records["emulator-5554"].connected_total_s, 20.0);

    // Named QEMU-next Session 2 ("Phone_emulator-5554") runs, then shuts down with its
    // launcher lingering under bare-serial fallback "emulator-5554".
    let launcher_pid = WORKER_B - 1;
    let mut phone = emu_group("emulator-5554", WORKER_B, "Phone", Some("Phone"));
    phone.primary_start_time = START + 40;
    phone.pids = vec![launcher_pid, WORKER_B];
    phone.member_start_times = BTreeMap::from([(launcher_pid, START + 39), (WORKER_B, START + 40)]);
    emu_tick_with(&mut tracker, &procs, &[("Phone_emulator-5554", &phone)], 42.0);
    assert_eq!(tracker.emulator_records["Phone_emulator-5554"].state, "CONNECTED");

    let launcher_bare = EmuGroupNetsimInfo {
        serial: "emulator-5554".to_string(),
        port: 5554,
        primary_pid: launcher_pid,
        primary_start_time: START + 39,
        pids: vec![launcher_pid],
        member_start_times: BTreeMap::from([(launcher_pid, START + 39)]),
        ..Default::default()
    };
    emu_tick_with(&mut tracker, &procs, &[("emulator-5554", &launcher_bare)], 50.0);
    emu_tick_with(&mut tracker, &procs, &[], 52.0);
    emu_tick_with(&mut tracker, &procs, &[], 54.0);

    let bare_rec = &tracker.emulator_records["emulator-5554"];
    assert_eq!(bare_rec.state, "DISCONNECTED");
    assert_eq!(bare_rec.connected_total_s, 20.0);
    assert_eq!(bare_rec.device_name.as_deref(), Some("Wear_Bare"));
    assert_eq!(tracker.emulator_records["Phone_emulator-5554"].state, "DISCONNECTED");

    // Part 2: Session 3 starts with 1 bare-serial launcher tick at t=60, then resolves
    // avd_id="Pixel_8" with display_name=None (no pid_<pid>.ini avd.name yet).
    let dir2 = tempfile::tempdir().unwrap();
    let (mut tracker2, procs2) = tracker_with_daemon_log(
        dir2.path(),
        concat!(
            "netsim I 09-25 22:37:06.000 service.rs:585 - DeviceActor: AddChip 1 (kind WIFI) to device 1 (Wear_Bare)\n",
            "netsim I 09-25 22:37:47.000 service.rs:585 - DeviceActor: AddChip 2 (kind BLUETOOTH) to device 2 (Pixel_8)\n",
        ),
    );
    emu_tick_with(&mut tracker2, &procs2, &[("emulator-5554", &unnamed)], 5.0);
    emu_tick_with(&mut tracker2, &procs2, &[], 25.0);
    emu_tick_with(&mut tracker2, &procs2, &[], 27.0);

    let unnamed2 = EmuGroupNetsimInfo {
        serial: "emulator-5554".to_string(),
        port: 5554,
        primary_pid: launcher_pid,
        primary_start_time: START + 40,
        pids: vec![launcher_pid],
        member_start_times: BTreeMap::from([(launcher_pid, START + 40)]),
        ..Default::default()
    };
    let other = emu_group("emulator-5556", WORKER_B + 10, "Other", Some("Other"));
    emu_tick_with(
        &mut tracker2,
        &procs2,
        &[("emulator-5554", &unnamed2), ("Other_emulator-5556", &other)],
        40.0,
    );
    assert_eq!(tracker2.emulator_records["emulator-5554"].avd_id, None);
    assert_eq!(tracker2.emulator_records["emulator-5554"].device_name, None);

    let mut pixel = emu_group("emulator-5554", WORKER_B, "Pixel_8", None);
    pixel.primary_start_time = START + 41;
    pixel.pids = vec![launcher_pid, WORKER_B];
    pixel.member_start_times = BTreeMap::from([(launcher_pid, START + 40), (WORKER_B, START + 41)]);
    emu_tick_with(
        &mut tracker2,
        &procs2,
        &[("Pixel_8_emulator-5554", &pixel), ("Other_emulator-5556", &other)],
        42.0,
    );

    assert_eq!(
        tracker2.emulator_records["emulator-5554"].device_name.as_deref(),
        Some("Wear_Bare")
    );
    let pixel_rec = &tracker2.emulator_records["Pixel_8_emulator-5554"];
    assert_eq!(pixel_rec.device_name.as_deref(), Some("Pixel_8"));
    assert_eq!(pixel_rec.state, "CONNECTED");
}

#[test]
fn test_held_disabled_unattached_bystander_and_joined_worker_exit_parity() {
    // 1. A `-no-netsim` (`DISABLED`) emulator missing 1 host-scan tick (held)
    // must not inflate `held_count` or break `single_live` fallback for the
    // sole netsim-enabled emulator.
    let dir1 = tempfile::tempdir().unwrap();
    let (mut tracker1, procs1) = tracker_with_daemon_log(
        dir1.path(),
        "netsim I 09-25 22:37:06.000 service.rs:585 - DeviceActor: AddChip wlan0 (WIFI, 0) to device CustomDevice\n",
    );
    let phone = emu_group("emulator-5554", WORKER_A, "Phone", Some("Phone"));
    let mut disabled = emu_group("emulator-5556", WORKER_B, "NoNetsim", Some("NoNetsim"));
    disabled.no_netsim = true;

    emu_tick_with(
        &mut tracker1,
        &procs1,
        &[("Phone_emulator-5554", &phone), ("NoNetsim_emulator-5556", &disabled)],
        5.0,
    );
    assert_eq!(tracker1.emulator_records["NoNetsim_emulator-5556"].state, "DISABLED");
    assert_eq!(tracker1.emulator_records["Phone_emulator-5554"].state, "CONNECTED");
    assert_eq!(tracker1.emulator_records["Phone_emulator-5554"].unknown_chip_count, 1);

    // `disabled` misses 1 tick (held); `phone` must stay CONNECTED with 0 disconnects.
    emu_tick_with(&mut tracker1, &procs1, &[("Phone_emulator-5554", &phone)], 10.0);
    let phone_rec = &tracker1.emulator_records["Phone_emulator-5554"];
    assert_eq!(phone_rec.state, "CONNECTED");
    assert_eq!(phone_rec.disconnect_count, 0);
    assert_eq!(phone_rec.unknown_chip_count, 1);

    // 2. An unattached emulator that provisionally parked on a bystander daemon
    // (`--instance 1`) can fall back to its own `--instance 2` daemon once the
    // bystander exits, unless its `grpc_endpoint` conflicts with the daemon's port.
    let root2 = tempfile::tempdir().unwrap();
    let (dir_a, dir_b) = (root2.path().join("a"), root2.path().join("b"));
    let art_b = dir_b.join("android-test").join("netsimd");
    std::fs::create_dir_all(&dir_a).unwrap();
    std::fs::create_dir_all(&art_b).unwrap();
    std::fs::write(
        art_b.join("netsim_2_stderr.log"),
        "netsim I 09-25 22:37:08.000 service.rs:585 - DeviceActor: AddChip 1 (kind WIFI) to device 1 (Phone60)\n",
    )
    .unwrap();
    write_ini(
        &dir_b.join("netsim_2.ini"),
        &format!("pid={DAEMON_B}\ngrpc.port=43929\n"),
        START + 2,
    );

    let mut tracker2 = tracker_for(&[&dir_a, &dir_b], root2.path());
    tracker2.enable_log = true;
    let daemon_a = netsimd(DAEMON_A, START, &dir_a);
    let mut daemon_b = netsimd(DAEMON_B, START + 2, &dir_b);
    daemon_b.cmd =
        vec!["/sdk/emulator/netsimd".to_string(), "--instance".to_string(), "2".to_string()];

    let phone60 = emu_group("emulator-5560", WORKER_A, "Phone60", Some("Phone60"));
    let mut conflict_emu = emu_group("emulator-5562", WORKER_B, "Conflict", Some("Conflict"));
    conflict_emu.grpc_endpoint = Some("localhost:49999".to_string());

    emu_tick_with(
        &mut tracker2,
        &[daemon_a],
        &[("Phone60_emulator-5560", &phone60), ("Conflict_emulator-5562", &conflict_emu)],
        1.0,
    );
    assert_eq!(
        tracker2.emulator_records["Phone60_emulator-5560"].daemon_key,
        Some((DAEMON_A, START))
    );
    assert_eq!(tracker2.emulator_records["Phone60_emulator-5560"].state, "CONNECTING");

    emu_tick_with(
        &mut tracker2,
        &[daemon_b],
        &[("Phone60_emulator-5560", &phone60), ("Conflict_emulator-5562", &conflict_emu)],
        3.0,
    );
    assert_eq!(
        tracker2.emulator_records["Phone60_emulator-5560"].daemon_key,
        Some((DAEMON_B, START + 2))
    );
    assert_eq!(tracker2.emulator_records["Phone60_emulator-5560"].state, "CONNECTED");
    assert_ne!(
        tracker2.emulator_records["Conflict_emulator-5562"].daemon_key,
        Some((DAEMON_B, START + 2))
    );
    assert_eq!(tracker2.emulator_records["Conflict_emulator-5562"].state, "CONNECTING");

    // 3. A joined QEMU-next emulator whose VM worker exits before connecting while
    // its launcher lingers in `emu_groups` settles immediately to
    // `FAILED(worker_exit_no_connect)` and drops `netsim_devices` to `0`.
    let dir3 = tempfile::tempdir().unwrap();
    let (mut tracker3, procs3) = tracker_with_daemon_log(dir3.path(), "");
    let launcher_pid = WORKER_A - 1;
    let mut qnext = emu_group("emulator-5554", WORKER_A, "Wear", Some("Wear"));
    qnext.primary_start_time = START + 2;
    qnext.pids = vec![launcher_pid, WORKER_A];
    qnext.member_start_times = BTreeMap::from([(launcher_pid, START + 1), (WORKER_A, START + 2)]);

    emu_tick_with(&mut tracker3, &procs3, &[("Wear_emulator-5554", &qnext)], 2.0);
    assert_eq!(tracker3.emulator_records["Wear_emulator-5554"].state, "CONNECTING");
    assert_eq!(tracker3.get_csv_fields()["netsim_devices"], "1");

    let mut lingering = qnext.clone();
    lingering.primary_pid = launcher_pid;
    lingering.primary_start_time = START + 1;
    lingering.pids = vec![launcher_pid];
    lingering.member_start_times = BTreeMap::from([(launcher_pid, START + 1)]);

    emu_tick_with(&mut tracker3, &procs3, &[("Wear_emulator-5554", &lingering)], 4.0);
    assert_eq!(
        tracker3.emulator_records["Wear_emulator-5554"].state,
        "FAILED(worker_exit_no_connect)"
    );
    assert_eq!(tracker3.get_csv_fields()["netsim_devices"], "0");
}

#[cfg(unix)]
#[test]
fn test_post_reg_symlink_and_preexisting_foreign_artifact_dir_rejected() {
    let root = tempfile::tempdir().unwrap();
    let secret_dir = root.path().join("secret");
    std::fs::create_dir_all(&secret_dir).unwrap();
    std::fs::write(
        secret_dir.join("netsim_stderr.log"),
        "netsim I 09-25 22:37:05.100 netsimd.rs:750 - gRPC port: 44444\n",
    )
    .unwrap();
    write_ini(
        &secret_dir.join("netsim_session_stats.json"),
        r#"{"version":"9.9.9-secret","device_stats":[{"device_id":1}],"radio_stats":[{"device_id":1,"kind":"WIFI","duration_secs":"2"}]}"#,
        START + 2,
    );

    let sym_art = root.path().join("sym_art");
    std::os::unix::fs::symlink(&secret_dir, &sym_art).unwrap();
    let mut direct_tailer = NetsimLogTailer::new(sym_art.join("netsim_stderr.log"));
    assert!(
        !direct_tailer.poll(START as f64 + 5.0),
        "tailer must reject symlinked parent directory"
    );
    assert_eq!(direct_tailer.grpc_port_logged, None);

    // Daemon registered before its artifact_dir exists must clear artifact_dir
    // if that path is subsequently created as a symlink.
    let tmp1 = tempfile::tempdir().unwrap();
    let mut tracker1 = tracker_for(&[tmp1.path()], tmp1.path());
    tracker1.enable_log = true;
    let proc1 = netsimd(DAEMON_A, START, tmp1.path());
    let phone1 = emu_group("emulator-5554", WORKER_A, "phone", Some("Phone"));
    emu_tick_with(&mut tracker1, std::slice::from_ref(&proc1), &[("phone_5554", &phone1)], 1.0);
    std::fs::create_dir_all(tmp1.path().join("android-test")).unwrap();
    std::os::unix::fs::symlink(&secret_dir, tmp1.path().join("android-test").join("netsimd"))
        .unwrap();
    emu_tick_with(&mut tracker1, std::slice::from_ref(&proc1), &[("phone_5554", &phone1)], 3.0);
    let d1 = &tracker1.daemons[&(DAEMON_A, START)];
    assert!(d1.artifact_dir.as_os_str().is_empty());
    assert_eq!(d1.grpc_port, None);
    assert_eq!(d1.version, None);
    assert!(tracker1.emulator_records["phone_5554"].chip_attach_s.is_empty());

    // Pre-existing foreign daemon seen before the monitor user's artifact_dir exists
    // must drop that artifact_dir once the monitor user creates it, preserving own
    // daemon exit harvest and emulator disconnect isolation.
    let tmp2 = tempfile::tempdir().unwrap();
    let out2 = tempfile::tempdir().unwrap();
    let mut tracker2 = NetsimTracker::new(Some(""), 256, true, out2.path());
    tracker2.host_os = HostOs::Linux;
    tracker2.custom_ini_dirs = vec![tmp2.path().to_path_buf()];
    tracker2.custom_proc_locks = Some(String::new());

    let mut foreign_pre = netsimd(DAEMON_A - 10, START - 5, tmp2.path());
    foreign_pre.same_user = false;
    emu_tick_with(&mut tracker2, std::slice::from_ref(&foreign_pre), &[], 0.0);

    let own_art = tmp2.path().join("android-test").join("netsimd");
    std::fs::create_dir_all(&own_art).unwrap();
    let ini_own = tmp2.path().join("netsim.ini");
    write_ini(&ini_own, &format!("grpc.port=43929\npid={DAEMON_A}\n"), START);
    std::fs::write(
        own_art.join("netsim_stderr.log"),
        format!(
            "netsim I 09-25 22:37:05.050 netsimd.rs:744 - Wrote to INI file {}\n\
             netsim I 09-25 22:37:05.051 netsimd.rs:750 - gRPC port: 43929\n\
             netsim I 09-25 22:37:05.300 device.rs:110 - DeviceActor: AddChip 10 (kind WIFI, name \"wlan0\") to device 1 (Phone)\n",
            ini_own.display()
        ),
    )
    .unwrap();
    let own_proc = netsimd(DAEMON_A, START, tmp2.path());
    let mut phone2 = emu_group("emulator-5554", WORKER_A, "phone", Some("Phone"));
    phone2.primary_start_time = START;
    phone2.grpc_endpoint = Some("localhost:43929".to_string());

    emu_tick_with(
        &mut tracker2,
        &[foreign_pre.clone(), own_proc],
        &[("phone_emulator-5554", &phone2)],
        2.0,
    );
    std::fs::write(own_art.join("netsim_artifacts_own.zip"), b"PK\x03\x04ownzip").unwrap();
    write_ini(&own_art.join("netsim_session_stats.json"), r#"{"version":"1.0.31"}"#, START + 3);
    let _ = std::fs::remove_file(&ini_own);
    emu_tick_with(
        &mut tracker2,
        std::slice::from_ref(&foreign_pre),
        &[("phone_emulator-5554", &phone2)],
        5.0,
    );
    assert!(tracker2.daemons[&(DAEMON_A - 10, START - 5)].artifact_dir.as_os_str().is_empty());
    assert_eq!(tracker2.daemons[&(DAEMON_A - 10, START - 5)].grpc_port, None);
    assert!(!tracker2.timeline.iter().any(|(_, e)| e.contains("DUPLICATE_ARTIFACT_DIR")));
    assert_eq!(tracker2.pcap_mgr.rescued_zips.len(), 1);
    assert_eq!(tracker2.daemons[&(DAEMON_A, START)].version.as_deref(), Some("1.0.31"));
    assert_eq!(tracker2.emulator_records["phone_emulator-5554"].state, "DISCONNECTED");
}

#[test]
fn test_single_daemon_fallback_rejects_port_conflict_and_clears_unattached_daemon_key() {
    // 1. Newly seen emulator (`rec.daemon_key == None`) and unchanged fallback ticks must
    // reject a single active daemon listening on a different gRPC port.
    let dir1 = tempfile::tempdir().unwrap();
    let art1 = dir1.path().join("android-test").join("netsimd");
    std::fs::create_dir_all(&art1).unwrap();
    write_ini(
        &dir1.path().join("netsim.ini"),
        &format!("pid={DAEMON_A}\ngrpc.port=43929\n"),
        START,
    );
    std::fs::write(
        art1.join("netsim_stderr.log"),
        "netsim I 09-25 22:37:06.000 service.rs:585 - DeviceActor: AddChip 1 (kind WIFI) to device 1 (Pixel 8)\n",
    )
    .unwrap();

    let mut tracker1 = tracker_for(&[dir1.path()], dir1.path());
    tracker1.enable_log = true;
    let proc1 = netsimd(DAEMON_A, START, dir1.path());
    let mut conflict = emu_group("emulator-5562", WORKER_B, "Conflict", Some("Conflict"));
    conflict.primary_start_time = START + 2;
    conflict.grpc_endpoint = Some("localhost:49999".to_string());

    for t in [2.0, 4.0] {
        emu_tick_with(
            &mut tracker1,
            std::slice::from_ref(&proc1),
            &[("Conflict_emulator-5562", &conflict)],
            t,
        );
        let rec = &tracker1.emulator_records["Conflict_emulator-5562"];
        assert_eq!(rec.daemon_key, None);
        assert_eq!(rec.state, "CONNECTING");
        assert!(rec.chip_attach_s.is_empty());
        assert_eq!(tracker1.get_csv_fields()["netsim_devices"], "0");
    }

    // 2. Provisional Tick-1 fallback (`grpc_port == None` initially) detaches cleanly once
    // DAEMON_A resolves a conflicting port on Tick 2, preserving `all_count == 1` Wi-Fi
    // attribution for DAEMON_A's real emulator and avoiding false `FAILED(launcher_abort)`.
    let dir2 = tempfile::tempdir().unwrap();
    let art2 = dir2.path().join("android-test").join("netsimd");
    std::fs::create_dir_all(&art2).unwrap();
    let ini2 = dir2.path().join("netsim.ini");
    let log2 = art2.join("netsim_stderr.log");
    let mut tracker2 = tracker_for(&[dir2.path()], dir2.path());
    tracker2.enable_log = true;
    let proc2 = netsimd(DAEMON_A, START, dir2.path());

    let mut phone = emu_group("emulator-5554", WORKER_A, "Phone", Some("Phone"));
    phone.primary_start_time = START;
    phone.grpc_endpoint = Some("localhost:43929".to_string());

    emu_tick_with(
        &mut tracker2,
        std::slice::from_ref(&proc2),
        &[("Phone_emulator-5554", &phone), ("Conflict_emulator-5562", &conflict)],
        1.0,
    );
    assert_eq!(
        tracker2.emulator_records["Conflict_emulator-5562"].daemon_key,
        Some((DAEMON_A, START))
    );

    write_ini(&ini2, &format!("pid={DAEMON_A}\ngrpc.port=43929\n"), START + 1);
    std::fs::write(
        &log2,
        format!(
            "netsim I 09-25 22:37:05.500 netsimd.rs:744 - Wrote to INI file {}\n\
             netsim I 09-25 22:37:06.000 service.rs:585 - DeviceActor: AddChip 1 (kind WIFI) to device 1 (Phone)\n\
             netsim I 09-25 22:37:07.000 ap_actor.rs:212 - ApActor: Associated 02:15:B2:00:00:00\n\
             netsim W 09-25 22:37:09.000 netsimd.rs:800 - Startup timeout reached\n",
            ini2.display()
        ),
    )
    .unwrap();

    emu_tick_with(
        &mut tracker2,
        std::slice::from_ref(&proc2),
        &[("Phone_emulator-5554", &phone), ("Conflict_emulator-5562", &conflict)],
        3.0,
    );
    assert_eq!(tracker2.emulator_records["Conflict_emulator-5562"].daemon_key, None);
    assert_eq!(tracker2.emulator_records["Conflict_emulator-5562"].netsim_daemon_ready_s, None);
    assert_eq!(tracker2.emulator_records["Phone_emulator-5554"].wifi_associated_s, Some(2.0));

    // 3. Provisional Tick-1 fallback on bystander DAEMON_A (`--instance 1`) also clears
    // `daemon_key` and `netsim_daemon_ready_s` when unrelated DAEMON_B (`--instance 2`,
    // before binding its gRPC port) spawns while DAEMON_A remains alive.
    let dir2b = tempfile::tempdir().unwrap();
    let art2b = dir2b.path().join("android-test").join("netsimd");
    std::fs::create_dir_all(&art2b).unwrap();
    let ini2b = dir2b.path().join("netsim.ini");
    write_ini(&ini2b, &format!("pid={DAEMON_A}\ngrpc.port=43929\n"), START);
    std::fs::write(
        art2b.join("netsim_stderr.log"),
        format!(
            "netsim I 09-25 22:37:05.200 netsimd.rs:744 - Wrote to INI file {}\n",
            ini2b.display()
        ),
    )
    .unwrap();
    let mut tracker2b = tracker_for(&[dir2b.path()], dir2b.path());
    tracker2b.enable_log = true;
    let proc_a2b = netsimd(DAEMON_A, START, dir2b.path());
    let mut pending60 = emu_group("emulator-5560", WORKER_B, "Pending60", Some("Pending60"));
    pending60.primary_start_time = START + 2;
    emu_tick_with(
        &mut tracker2b,
        std::slice::from_ref(&proc_a2b),
        &[("Pending60_emulator-5560", &pending60)],
        2.0,
    );
    assert_eq!(
        tracker2b.emulator_records["Pending60_emulator-5560"].daemon_key,
        Some((DAEMON_A, START))
    );
    let mut proc_b2b = netsimd(DAEMON_B, START + 3, dir2b.path());
    proc_b2b.cmd = vec!["netsimd".to_string(), "--instance".to_string(), "2".to_string()];
    emu_tick_with(
        &mut tracker2b,
        &[proc_a2b, proc_b2b],
        &[("Pending60_emulator-5560", &pending60)],
        3.5,
    );
    assert_eq!(tracker2b.emulator_records["Pending60_emulator-5560"].daemon_key, None);
    assert_eq!(tracker2b.emulator_records["Pending60_emulator-5560"].netsim_daemon_ready_s, None);

    // 4. Port-compatible unattached emulator whose single daemon exits with `startup_timeout`
    // still settles to `FAILED(launcher_abort)`.
    let dir3 = tempfile::tempdir().unwrap();
    let (mut tracker3, procs3) = tracker_with_daemon_log(
        dir3.path(),
        "netsim W 09-25 22:37:09.000 netsimd.rs:800 - Startup timeout reached\n",
    );
    let mut compat = emu_group("emulator-5554", WORKER_A, "Compat", Some("Compat"));
    compat.primary_start_time = START;
    emu_tick_with(&mut tracker3, &procs3, &[("Compat_emulator-5554", &compat)], 1.0);
    emu_tick_with(&mut tracker3, &[], &[("Compat_emulator-5554", &compat)], 5.0);
    assert_eq!(tracker3.emulator_records["Compat_emulator-5554"].state, "FAILED(launcher_abort)");
}

#[test]
fn test_zero_offset_shared_log_tailer_skips_replacement_server_and_polls_client_exit() {
    // 1. Lingering DAEMON_A (`offset == 0` after 0-byte log on Tick 1) must not read
    // replacement server DAEMON_B's in-place truncated log or hijack endpoint matching.
    let dir1 = tempfile::tempdir().unwrap();
    let art1 = dir1.path().join("android-test").join("netsimd");
    std::fs::create_dir_all(&art1).unwrap();
    let log1 = art1.join("netsim_stderr.log");
    std::fs::write(&log1, b"").unwrap();

    let mut tracker1 = tracker_for(&[dir1.path()], dir1.path());
    tracker1.enable_log = true;
    let proc_a1 = netsimd(DAEMON_A, START, dir1.path());
    emu_tick_with(&mut tracker1, std::slice::from_ref(&proc_a1), &[], 1.0);
    assert_eq!(tracker1.daemons[&(DAEMON_A, START)].grpc_port, None);

    let ini1 = dir1.path().join("netsim.ini");
    write_ini(&ini1, &format!("pid={DAEMON_B}\ngrpc.port=43930\n"), START + 2);
    std::fs::write(
        &log1,
        format!(
            "netsim I 09-25 22:37:07.100 netsimd.rs:451 - Netsim Version: 1.0.31, OS: linux\n\
             netsim I 09-25 22:37:07.200 netsimd.rs:750 - gRPC port: 43930\n\
             netsim I 09-25 22:37:07.300 netsimd.rs:744 - Wrote to INI file {}\n",
            ini1.display()
        ),
    )
    .unwrap();
    let proc_b1 = netsimd(DAEMON_B, START + 2, dir1.path());
    let mut phone = emu_group("emulator-5554", WORKER_A, "Phone", Some("Phone"));
    phone.primary_start_time = START + 2;
    phone.grpc_endpoint = Some("localhost:43930".to_string());

    emu_tick_with(&mut tracker1, &[proc_a1, proc_b1], &[("Phone_emulator-5554", &phone)], 3.0);
    assert_eq!(tracker1.daemons[&(DAEMON_A, START)].grpc_port, None);
    assert_eq!(tracker1.daemons[&(DAEMON_A, START)].version, None);
    assert!(!tracker1.daemons[&(DAEMON_A, START)].ready_recorded);
    assert_eq!(
        tracker1.emulator_records["Phone_emulator-5554"].daemon_key,
        Some((DAEMON_B, START + 2))
    );

    // 2. Exited DAEMON_A (`offset == 0`, `inode.is_some()` on Tick 1) must not read
    // replacement server DAEMON_B's in-place truncated log inside `mark_exited_daemons`.
    let dir2 = tempfile::tempdir().unwrap();
    let art2 = dir2.path().join("android-test").join("netsimd");
    std::fs::create_dir_all(&art2).unwrap();
    let log2 = art2.join("netsim_stderr.log");
    std::fs::write(&log2, b"").unwrap();

    let mut tracker2 = tracker_for(&[dir2.path()], dir2.path());
    tracker2.enable_log = true;
    let proc_a2 = netsimd(DAEMON_A, START, dir2.path());
    emu_tick_with(&mut tracker2, std::slice::from_ref(&proc_a2), &[], 1.0);

    let ini2 = dir2.path().join("netsim.ini");
    write_ini(&ini2, &format!("pid={DAEMON_B}\ngrpc.port=43930\n"), START + 2);
    std::fs::write(
        &log2,
        format!(
            "netsim I 09-25 22:37:07.100 netsimd.rs:451 - Netsim Version: 1.0.31, OS: linux\n\
             netsim I 09-25 22:37:07.200 netsimd.rs:750 - gRPC port: 43930\n\
             netsim I 09-25 22:37:07.300 netsimd.rs:744 - Wrote to INI file {}\n",
            ini2.display()
        ),
    )
    .unwrap();
    let proc_b2 = netsimd(DAEMON_B, START + 2, dir2.path());
    emu_tick_with(&mut tracker2, &[proc_b2], &[], 3.0);

    let exited_a = &tracker2.daemons[&(DAEMON_A, START)];
    assert_eq!(exited_a.grpc_port, None);
    assert_eq!(exited_a.version, None);
    assert!(!exited_a.ready_recorded);

    // 3. Conversely, a newer client-mode sharer (`offset == 0`, `inode == None` as on
    // Windows) neither blocks primary server DAEMON_A while active nor skips its own
    // final `CLIENT_MODE_SPAWN` poll upon exiting.
    let dir3 = tempfile::tempdir().unwrap();
    let art3 = dir3.path().join("android-test").join("netsimd");
    std::fs::create_dir_all(&art3).unwrap();
    let log3 = art3.join("netsim_stderr.log");
    std::fs::write(&log3, b"").unwrap();

    let mut tracker3 = tracker_for(&[dir3.path()], dir3.path());
    tracker3.enable_log = true;
    let proc_a3 = netsimd(DAEMON_A, START, dir3.path());
    let daemon_c = DAEMON_B + 50;
    let proc_c3 = netsimd(daemon_c, START + 2, dir3.path());
    emu_tick_with(&mut tracker3, &[proc_a3.clone(), proc_c3], &[], 2.5);
    tracker3.daemons.get_mut(&(daemon_c, START + 2)).unwrap().tailer.as_mut().unwrap().inode = None;

    std::fs::write(
        &log3,
        "netsim I 09-25 22:37:05.500 netsimd.rs:750 - gRPC port: 43929\n\
         netsim W 09-25 22:37:07.500 netsimd.rs:500 - Lock held by another process\n",
    )
    .unwrap();
    emu_tick_with(&mut tracker3, &[proc_a3], &[], 4.0);
    assert_eq!(tracker3.daemons[&(DAEMON_A, START)].grpc_port, Some(43929));
    assert!(
        tracker3.timeline.iter().any(|(_, e)| e == &format!("CLIENT_MODE_SPAWN(pid={daemon_c})")),
        "timeline={:?}",
        tracker3.timeline
    );
}

#[test]
fn test_au11_netsim_concurrent_client_mode_spawn_and_active_client_isolation() {
    // 1. Concurrent (<= 1s) client-mode spawn on shared log: DAEMON_A (server) and DAEMON_B
    // (client-mode logging "Lock held by another process") start within 1s. DAEMON_B must
    // record CLIENT_MODE_SPAWN without stealing DAEMON_A's version/port/READY or starving
    // DAEMON_A's 0-offset tailer.
    let dir = tempfile::tempdir().unwrap();
    let art = dir.path().join("android-test").join("netsimd");
    std::fs::create_dir_all(&art).unwrap();
    let ini_path = dir.path().join("netsim.ini");
    std::fs::write(&ini_path, format!("grpc.port=43929\npid={DAEMON_A}\n")).unwrap();
    let log_path = art.join("netsim_stderr.log");
    std::fs::write(
        &log_path,
        format!(
            "netsim I 09-25 22:37:05.100 netsimd.rs:530 - Netsim Version: 1.0.31\n\
             netsim I 09-25 22:37:05.200 netsimd.rs:750 - gRPC port: 43929\n\
             netsim I 09-25 22:37:05.250 netsimd.rs:760 - Wrote to INI file {}\n\
             netsim I 09-25 22:37:05.400 device.rs:120 - DeviceActor: AddChip 0 (kind WIFI) to device 1 (Pixel 8)\n\
             netsim W 09-25 22:37:05.800 netsimd.rs:500 - Lock held by another process\n",
            ini_path.display()
        ),
    )
    .unwrap();

    let mut tracker = tracker_for(&[dir.path()], dir.path());
    tracker.enable_log = true;
    let proc_srv = netsimd(DAEMON_A, START, dir.path());
    let proc_cli = netsimd(DAEMON_B, START + 1, dir.path());
    let mut phone = emu_group("emulator-5554", WORKER_A, "Phone", Some("Pixel 8"));
    phone.primary_start_time = START;
    phone.grpc_endpoint = None; // QEMU-now / single-daemon fallback

    // Active client-mode process alongside server: Phone must join DAEMON_A via fallback
    // and transition to CONNECTED without DAEMON_B breaking single-daemon fallback.
    emu_tick_with(
        &mut tracker,
        &[proc_srv.clone(), proc_cli.clone()],
        &[("Phone_emulator-5554", &phone)],
        2.0,
    );
    let d_srv = &tracker.daemons[&(DAEMON_A, START)];
    let d_cli = &tracker.daemons[&(DAEMON_B, START + 1)];
    assert!(d_cli.client_mode_recorded, "d_cli={d_cli:?}");
    assert_eq!(d_cli.grpc_port, None);
    assert_eq!(d_cli.ready_ts, None);
    assert!(!d_srv.client_mode_recorded);
    assert!(d_srv.log_available);
    assert_eq!(d_srv.version.as_deref(), Some("1.0.31"));
    assert!(d_srv.ready_ts.is_some());
    assert!(
        tracker.timeline.iter().any(|(_, e)| e == &format!("CLIENT_MODE_SPAWN(pid={DAEMON_B})"))
    );
    assert!(
        !tracker.timeline.iter().any(|(_, e)| e.starts_with(&format!("READY(pid={DAEMON_B},")))
    );
    let rec = &tracker.emulator_records["Phone_emulator-5554"];
    assert_eq!(rec.state, "CONNECTED");
    assert_eq!(rec.daemon_key, Some((DAEMON_A, START)));
    assert_eq!(rec.disconnect_count, 0);

    // Even after 35s with DAEMON_B lingering in client-mode, DAEMON_B is never marked ORPHANED,
    // and Phone remains CONNECTED with 0 disconnects.
    emu_tick_with(
        &mut tracker,
        &[proc_srv.clone(), proc_cli.clone()],
        &[("Phone_emulator-5554", &phone)],
        38.0,
    );
    assert!(!tracker.daemons[&(DAEMON_B, START + 1)].orphaned);
    assert_eq!(tracker.emulator_records["Phone_emulator-5554"].state, "CONNECTED");
    assert_eq!(tracker.emulator_records["Phone_emulator-5554"].disconnect_count, 0);

    // 2. Pid-less INI fallback must pair with the server (DAEMON_A), never with an active
    // client_mode_recorded daemon (DAEMON_B) spawned within INI_MTIME_SLACK_S.
    let dir2 = tempfile::tempdir().unwrap();
    let art2 = dir2.path().join("android-test").join("netsimd");
    std::fs::create_dir_all(&art2).unwrap();
    write_ini(&dir2.path().join("netsim.ini"), "grpc.port=43931\n", START + 1);
    std::fs::write(
        art2.join("netsim_stderr.log"),
        "netsim I 09-25 22:37:05.100 netsimd.rs:530 - Netsim Version: 1.0.31\n\
         netsim W 09-25 22:37:06.100 netsimd.rs:500 - Lock held by another process\n",
    )
    .unwrap();
    let mut tracker2 = tracker_for(&[dir2.path()], dir2.path());
    tracker2.enable_log = true;
    emu_tick_with(
        &mut tracker2,
        &[netsimd(DAEMON_A, START, dir2.path()), netsimd(DAEMON_B, START + 1, dir2.path())],
        &[],
        2.0,
    );
    assert_eq!(tracker2.daemons[&(DAEMON_A, START)].grpc_port, Some(43931));
    assert_eq!(tracker2.daemons[&(DAEMON_B, START + 1)].grpc_port, None);

    // 3. Exited sub-second client-mode sharer (offset == 0 on Tick 1, flushed right before
    // exiting on Tick 2) records CLIENT_MODE_SPAWN without stealing DAEMON_A's port/READY.
    let dir3 = tempfile::tempdir().unwrap();
    let art3 = dir3.path().join("android-test").join("netsimd");
    std::fs::create_dir_all(&art3).unwrap();
    let ini3 = dir3.path().join("netsim.ini");
    std::fs::write(&ini3, format!("grpc.port=43932\npid={DAEMON_A}\n")).unwrap();
    let log3 = art3.join("netsim_stderr.log");
    std::fs::write(
        &log3,
        format!(
            "netsim I 09-25 22:37:05.100 netsimd.rs:530 - Netsim Version: 1.0.31\n\
             netsim I 09-25 22:37:05.200 netsimd.rs:750 - gRPC port: 43932\n\
             netsim I 09-25 22:37:05.250 netsimd.rs:760 - Wrote to INI file {}\n",
            ini3.display()
        ),
    )
    .unwrap();
    let mut tracker3 = tracker_for(&[dir3.path()], dir3.path());
    tracker3.enable_log = true;
    let proc_a3 = netsimd(DAEMON_A, START, dir3.path());
    let daemon_c = DAEMON_B + 50;
    let proc_c3 = netsimd(daemon_c, START + 1, dir3.path());
    emu_tick_with(&mut tracker3, &[proc_a3.clone(), proc_c3], &[], 1.5);
    assert_eq!(tracker3.daemons[&(DAEMON_A, START)].grpc_port, Some(43932));
    assert_eq!(tracker3.daemons[&(daemon_c, START + 1)].grpc_port, None);
    {
        use std::io::Write;
        let mut f = std::fs::OpenOptions::new().append(true).open(&log3).unwrap();
        writeln!(f, "netsim W 09-25 22:37:06.100 netsimd.rs:500 - Lock held by another process")
            .unwrap();
    }
    emu_tick_with(&mut tracker3, &[proc_a3], &[], 2.5);
    let dc = &tracker3.daemons[&(daemon_c, START + 1)];
    assert!(dc.client_mode_recorded);
    assert_eq!(dc.grpc_port, None);
    assert_eq!(dc.ready_ts, None);
    assert!(
        tracker3.timeline.iter().any(|(_, e)| e == &format!("CLIENT_MODE_SPAWN(pid={daemon_c})"))
    );

    // 4. Concurrent client-mode daemon must not block primary server pcap exit harvest,
    // double-harvest in cleanup(), or report as netsim_up/netsim_pid in CSV.
    let dir4 = tempfile::tempdir().unwrap();
    let art4 = dir4.path().join("android-test").join("netsimd");
    let pcap4 = art4.join("pcaps");
    std::fs::create_dir_all(&pcap4).unwrap();
    let ini4 = dir4.path().join("netsim.ini");
    std::fs::write(&ini4, format!("grpc.port=43940\npid={DAEMON_B}\n")).unwrap();
    std::fs::write(
        art4.join("netsim_stderr.log"),
        format!(
            "netsim I 09-25 22:37:05.100 netsimd.rs:530 - Netsim Version: 1.0.31\n\
             netsim I 09-25 22:37:05.200 netsimd.rs:750 - gRPC port: 43940\n\
             netsim I 09-25 22:37:05.250 netsimd.rs:760 - Wrote to INI file {}\n\
             netsim I 09-25 22:37:05.400 device.rs:120 - DeviceActor: AddChip 0 (kind WIFI) to device 1 (Pixel 8)\n\
             netsim W 09-25 22:37:05.600 netsimd.rs:500 - Lock held by another process\n",
            ini4.display()
        ),
    )
    .unwrap();
    std::fs::write(pcap4.join("netsim-0-Pixel_8-WIFI.pcap"), b"pcap-bytes").unwrap();
    let pcap_out4 = dir4.path().join("out_pcaps");
    let mut tracker4 = NetsimTracker::new(Some(pcap_out4.to_str().unwrap()), 50, true, dir4.path());
    tracker4.host_os = HostOs::Linux;
    tracker4.custom_ini_dirs = vec![dir4.path().to_path_buf()];
    tracker4.pcap_mgr.cli_runner = std::sync::Arc::new(|_, _| None);
    let proc_cli4 = netsimd(DAEMON_A, START + 1, dir4.path());
    let proc_srv4 = netsimd(DAEMON_B, START, dir4.path());
    let mut phone4 = emu_group("emulator-5554", WORKER_A, "Phone", Some("Pixel 8"));
    phone4.primary_start_time = START;
    phone4.grpc_endpoint = Some("localhost:43940".to_string());
    emu_tick_with(
        &mut tracker4,
        &[proc_cli4.clone(), proc_srv4],
        &[("Phone_emulator-5554", &phone4)],
        2.0,
    );
    let csv1 = tracker4.get_csv_fields();
    assert_eq!(csv1.get("netsim_up").map(String::as_str), Some("1"));
    assert_eq!(csv1.get("netsim_pid").map(String::as_str), Some(&DAEMON_B.to_string() as &str));

    let _ = std::fs::remove_file(&ini4);
    emu_tick_with(&mut tracker4, &[proc_cli4], &[], 4.0);
    let csv2 = tracker4.get_csv_fields();
    assert_eq!(csv2.get("netsim_up").map(String::as_str), Some("0"));
    assert_eq!(csv2.get("netsim_pid").map(String::as_str), Some(""));
    tracker4.cleanup();
    assert_eq!(tracker4.pcap_mgr.copied_files.len(), 1);
    assert!(
        tracker4.pcap_mgr.copied_files.keys().next().unwrap().contains(&format!("{DAEMON_B}-"))
    );
}

#[test]
fn test_match_inis_to_daemons_enforces_uid_compatibility_across_all_unlocked_passes() {
    let dir = tempfile::tempdir().unwrap();
    let mut tracker = tracker_for(&[dir.path()], dir.path());
    let proc_self = netsimd(DAEMON_A, START, dir.path());
    let mut proc_foreign = netsimd(DAEMON_B, START, dir.path());
    proc_foreign.same_user = false;
    emu_tick_with(&mut tracker, &[proc_self, proc_foreign], &[], 1.0);

    let ini_spoof_pid = dir.path().join("spoof_netsim.ini");
    let ini_own = dir.path().join("own_netsim.ini");
    std::fs::write(&ini_spoof_pid, "grpc.port=49999\n").unwrap();
    std::fs::write(&ini_own, "grpc.port=43929\n").unwrap();

    // `pid=` match: a foreign-owned unlocked INI claiming `pid=DAEMON_A` (`same_user=true`)
    // must be rejected so the legitimate `owned_by_self=Some(true)` INI still claims `DAEMON_A`.
    let obs_spoof = IniObservation {
        path: ini_spoof_pid.clone(),
        data: HashMap::new(),
        holders: Vec::new(),
        pid: Some(DAEMON_A),
        port: Some(49999),
        instance: Some(1),
        mtime: Some(START as f64 + 1.0),
        file_uid: None,
        owned_by_self: Some(false),
    };
    let obs_own = IniObservation {
        path: ini_own.clone(),
        data: HashMap::new(),
        holders: Vec::new(),
        pid: Some(DAEMON_A),
        port: Some(43929),
        instance: Some(1),
        mtime: Some(START as f64 + 1.0),
        file_uid: None,
        owned_by_self: Some(true),
    };
    let live = HashSet::from([(DAEMON_A, START), (DAEMON_B, START)]);
    assert_eq!(
        tracker.match_inis_to_daemons_with(&[obs_spoof, obs_own], &live, Some(1000)),
        vec![None, Some((DAEMON_A, START))]
    );

    // INI the daemon already owns (`d.ini_path`): if a daemon previously logged
    // `/tmp/netsim.ini`, a foreign-owned file planted at that path must be rejected when
    // `monitor_uid != Some(0)`, while root (`0`) remains allowed when `file_uid` is unknown
    // (`None`), and enforces `file_uid == expected_uid` when both UIDs are known (`sudo`
    // `monitor_uid = 0`, `daemon_uid = 1000`, spoof `2000`).
    tracker.daemons.get_mut(&(DAEMON_A, START)).unwrap().ini_path = Some(ini_spoof_pid.clone());
    let obs_logged_foreign = IniObservation {
        path: ini_spoof_pid.clone(),
        data: HashMap::new(),
        holders: Vec::new(),
        pid: None,
        port: Some(49999),
        instance: Some(1),
        mtime: Some(START as f64 + 1.0),
        file_uid: None,
        owned_by_self: Some(false),
    };
    let live_self_only = HashSet::from([(DAEMON_A, START)]);
    assert_eq!(
        tracker.match_inis_to_daemons_with(
            std::slice::from_ref(&obs_logged_foreign),
            &live_self_only,
            Some(1000),
        ),
        vec![None]
    );
    assert_eq!(
        tracker.match_inis_to_daemons_with(
            std::slice::from_ref(&obs_logged_foreign),
            &live_self_only,
            Some(0),
        ),
        vec![Some((DAEMON_A, START))]
    );

    tracker.daemons.get_mut(&(DAEMON_B, START)).unwrap().expected_uid = Some(1000);
    let obs_sudo_spoof = IniObservation {
        path: ini_spoof_pid.clone(),
        data: HashMap::new(),
        holders: Vec::new(),
        pid: Some(DAEMON_B),
        port: Some(49999),
        instance: Some(1),
        mtime: Some(START as f64 + 1.0),
        file_uid: Some(2000),
        owned_by_self: Some(false),
    };
    let obs_sudo_dev = IniObservation {
        path: ini_own,
        data: HashMap::new(),
        holders: Vec::new(),
        pid: Some(DAEMON_B),
        port: Some(43929),
        instance: Some(1),
        mtime: Some(START as f64 + 1.0),
        file_uid: Some(1000),
        owned_by_self: Some(false),
    };
    let live_b = HashSet::from([(DAEMON_B, START)]);
    assert_eq!(
        tracker.match_inis_to_daemons_with(&[obs_sudo_spoof, obs_sudo_dev], &live_b, Some(0)),
        vec![None, Some((DAEMON_B, START))]
    );
}

#[cfg(unix)]
#[test]
fn test_is_disallowed_daemon_art_dir_enforces_same_user_ownership_and_android_parent() {
    use std::os::unix::fs::PermissionsExt;

    let uid = super::owner_uid().expect("owner_uid must resolve on Unix");
    let foreign_uid = uid.wrapping_add(1).max(1);
    let tmp = tempfile::tempdir().unwrap();

    // Not-yet-created `android-$USER/netsimd` under owned base dir is ALLOWED (`false`)
    // so newly spawned daemons keep their tailer until `netsimd` creates the directory.
    let pending_art = tmp.path().join("android-test").join("netsimd");
    assert!(!NetsimTracker::is_disallowed_daemon_art_dir_with(&pending_art, true, Some(uid), None));

    // Pre-created `android-test` parent owned by `uid` (simulating a `foreign_uid` monitor)
    // is DISALLOWED (`true`) even BEFORE `netsimd` subdir exists.
    let android_parent = tmp.path().join("android-test");
    std::fs::create_dir_all(&android_parent).unwrap();
    assert!(NetsimTracker::is_disallowed_daemon_art_dir_with(
        &pending_art,
        true,
        Some(foreign_uid),
        None,
    ));
    assert!(!NetsimTracker::is_disallowed_daemon_art_dir_with(&pending_art, true, Some(uid), None));

    // Existing `netsimd` subdir owned by another user is DISALLOWED for `same_user = true`,
    // allowed for `Some(0)` when `daemon_uid` is `None` or matches (`Some(uid)`), and
    // DISALLOWED under `sudo` (`Some(0)`) when `daemon_uid` mismatches (`Some(foreign_uid)`).
    std::fs::create_dir_all(&pending_art).unwrap();
    assert!(NetsimTracker::is_disallowed_daemon_art_dir_with(
        &pending_art,
        true,
        Some(foreign_uid),
        None,
    ));
    assert!(!NetsimTracker::is_disallowed_daemon_art_dir_with(&pending_art, true, Some(0), None));
    assert!(!NetsimTracker::is_disallowed_daemon_art_dir_with(
        &pending_art,
        false,
        Some(0),
        Some(uid),
    ));
    assert!(NetsimTracker::is_disallowed_daemon_art_dir_with(
        &pending_art,
        false,
        Some(0),
        Some(foreign_uid),
    ));
    assert!(NetsimTracker::is_disallowed_daemon_art_dir_with(&pending_art, true, None, None));

    // Symlinked `android-test` parent or foreign symlink prefix component is DISALLOWED.
    let real_target = tmp.path().join("real_target");
    std::fs::create_dir_all(real_target.join("netsimd")).unwrap();
    let sym_parent = tmp.path().join("android-symlink");
    std::os::unix::fs::symlink(&real_target, &sym_parent).unwrap();
    assert!(NetsimTracker::is_disallowed_daemon_art_dir_with(
        &sym_parent.join("netsimd"),
        true,
        Some(uid),
        None,
    ));
    assert!(NetsimTracker::is_disallowed_daemon_art_dir_with(
        &sym_parent.join("netsimd"),
        true,
        Some(0),
        None,
    ));

    let custom_sym = tmp.path().join("custom_link");
    std::os::unix::fs::symlink(&real_target, &custom_sym).unwrap();
    assert!(NetsimTracker::is_disallowed_daemon_art_dir_with(
        &custom_sym.join("netsimd"),
        true,
        Some(foreign_uid),
        None,
    ));

    // A world-writable artifact dir or `android-*` parent is DISALLOWED even with the
    // sticky bit, whether or not the `netsimd` subdir exists yet.
    let set_mode = |p: &std::path::Path, mode: u32| {
        std::fs::set_permissions(p, std::fs::Permissions::from_mode(mode)).unwrap();
    };
    let disallowed = |p: &std::path::Path| {
        NetsimTracker::is_disallowed_daemon_art_dir_with(p, true, Some(uid), None)
    };
    let not_yet_created = android_parent.join("pending");
    for mode in [0o1777, 0o1757, 0o777] {
        set_mode(&android_parent, 0o755);
        set_mode(&pending_art, mode);
        assert!(disallowed(&pending_art), "artifact dir mode {mode:o}");
        set_mode(&pending_art, 0o755);
        set_mode(&android_parent, mode);
        assert!(disallowed(&pending_art), "parent mode {mode:o}");
        assert!(disallowed(&not_yet_created), "parent mode {mode:o}, dir not created");
    }
    set_mode(&android_parent, 0o755);
    assert!(!disallowed(&pending_art));
    assert!(!disallowed(&not_yet_created));
}

#[cfg(unix)]
#[test]
fn test_poll_daemon_log_ignores_symlinked_or_foreign_logged_ini() {
    use std::os::unix::fs::PermissionsExt;

    let uid = super::owner_uid().expect("owner_uid must resolve on Unix");
    let foreign_uid = uid.wrapping_add(1).max(1);
    let dir = tempfile::tempdir().unwrap();
    let art = dir.path().join("android-test").join("netsimd");
    std::fs::create_dir_all(&art).unwrap();

    // The real pid-less INI in `dir` is still matched to DAEMON_A (as the oldest INI
    // without holders or `pid=`) when the log names a symlinked INI: that path must not
    // become `d.ini_path`.
    let real_ini = dir.path().join("netsim.ini");
    write_ini(&real_ini, "grpc.port=43945\n", START + 1);
    let spoof_target = dir.path().join("spoof_target.conf");
    std::fs::write(&spoof_target, "grpc.port=49999\npid=999999\n").unwrap();
    let sym_logged_ini = dir.path().join("sym_logged.ini");
    std::os::unix::fs::symlink(&spoof_target, &sym_logged_ini).unwrap();

    std::fs::write(
        art.join("netsim_stderr.log"),
        format!(
            "netsim I 09-25 22:37:05.100 netsimd.rs:530 - Netsim Version: 1.0.31\n\
             netsim I 09-25 22:37:05.200 netsimd.rs:760 - Wrote to INI file {}\n",
            sym_logged_ini.display()
        ),
    )
    .unwrap();

    let mut tracker = tracker_for(&[dir.path()], dir.path());
    tracker.enable_log = true;
    let proc_a = netsimd(DAEMON_A, START, dir.path());
    tick(&mut tracker, std::slice::from_ref(&proc_a), START + 5);
    let d_a = &tracker.daemons[&(DAEMON_A, START)];
    assert_eq!(
        d_a.ini_path.as_deref(),
        Some(real_ini.as_path()),
        "symlinked ini_path_logged must not become d.ini_path or block the pid-less INI match"
    );
    assert_eq!(d_a.grpc_port, Some(43945));

    // A foreign daemon (`same_user: false`) whose log names an INI owned by the monitor's
    // user must not adopt it as `d.ini_path`: the file does not belong to the daemon's
    // user (`expected_uid`).
    let dir2 = tempfile::tempdir().unwrap();
    // The control below judges every directory down to the INI.
    std::fs::set_permissions(dir2.path(), std::fs::Permissions::from_mode(0o755)).unwrap();
    let art2 = dir2.path().join("android-foreign").join("netsimd");
    std::fs::create_dir_all(&art2).unwrap();
    let own_ini2 = dir2.path().join("own_netsim.ini");
    std::fs::write(&own_ini2, format!("grpc.port=49998\npid={DAEMON_B}\n")).unwrap();
    std::fs::write(
        art2.join("netsim_stderr.log"),
        format!(
            "netsim I 09-25 22:37:05.200 netsimd.rs:760 - Wrote to INI file {}\n",
            own_ini2.display()
        ),
    )
    .unwrap();
    let mut d_foreign = DaemonRecord {
        daemon_key: (DAEMON_B, START),
        pid: DAEMON_B,
        create_time: START,
        start_wall: START as f64,
        spawner: "STANDALONE".to_string(),
        instance_num: None,
        artifact_dir: art2.clone(),
        log_path: art2.join("netsim_stderr.log"),
        tailer: Some(NetsimLogTailer::new(art2.join("netsim_stderr.log"))),
        netsim_cli_bin: "netsim".to_string(),
        same_user: false,
        expected_uid: Some(foreign_uid),
        version: None,
        ini_path: None,
        grpc_port: None,
        ready_ts: None,
        ready_recorded: false,
        netsim_daemon_ready_s: None,
        frontend_disabled: false,
        pcap_flag: false,
        log_available: true,
        log_unavail_recorded: false,
        client_mode_recorded: false,
        exited: false,
        exit_reason_hint: None,
        exit_reason: None,
        orphaned: false,
        zero_devices_since_mono: None,
        stats_cache: None,
        pcap_live_bytes: 0,
        pcap_live_files: 0,
        active_socket_conns: 0,
        joined_emulator_count: 0,
    };
    let mut events = Vec::new();
    NetsimTracker::poll_daemon_log(
        &mut d_foreign,
        START as f64 + 5.0,
        false,
        (false, false),
        &HashSet::new(),
        &mut events,
    );
    // The line was read, so the missing `ini_path` is the ownership check's doing.
    assert_eq!(d_foreign.tailer.as_ref().unwrap().ini_path_logged.as_deref(), own_ini2.to_str());
    assert_eq!(d_foreign.ini_path, None, "foreign daemon must not adopt own_ini2");

    // Control: the monitor's own daemon adopts the same logged path.
    let mut d_own = DaemonRecord { same_user: true, expected_uid: Some(uid), ..d_foreign };
    NetsimTracker::poll_daemon_log(
        &mut d_own,
        START as f64 + 6.0,
        false,
        (false, false),
        &HashSet::new(),
        &mut events,
    );
    assert_eq!(d_own.ini_path.as_deref(), Some(own_ini2.as_path()));
}

#[test]
fn test_netsim_tracker_windows_host_os_discovers_localappdata_ini_and_temp_artifacts() {
    let dir = tempfile::tempdir().unwrap();
    let local_app = dir.path().join("LocalAppData");
    let win_disc = local_app.join("Temp");
    let win_temp = dir.path().join("WinTemp");
    let win_art = win_temp.join("netsimd");
    std::fs::create_dir_all(&win_disc).unwrap();
    std::fs::create_dir_all(&win_art).unwrap();

    let ini_path = win_disc.join("netsim.ini");
    write_ini(&ini_path, &format!("grpc.port=43929\npid={DAEMON_A}\n"), START);
    std::fs::write(
        win_art.join("netsim_stderr.log"),
        format!(
            "netsim I 09-25 22:37:05.100 netsimd.rs:530 - Netsim Version: 1.0.31\n\
             netsim I 09-25 22:37:05.200 netsimd.rs:750 - gRPC port: 43929\n\
             netsim I 09-25 22:37:05.250 netsimd.rs:760 - Wrote to INI file {}\n\
             netsim I 09-25 22:37:05.400 device.rs:120 - DeviceActor: AddChip 1 (kind WIFI) to device 1 (Phone)\n",
            ini_path.display()
        ),
    )
    .unwrap();

    let proc_win = NetsimDaemonProc {
        pid: DAEMON_A,
        parent: Some(1),
        start_time: START,
        exe: Some(PathBuf::from(r"C:\Android\Sdk\emulator\netsimd.exe")),
        cmd: vec![r"C:\Android\Sdk\emulator\netsimd.exe".to_string()],
        environ: env_of(&[
            ("LOCALAPPDATA", local_app.to_str().unwrap()),
            ("TEMP", win_temp.to_str().unwrap()),
        ]),
        same_user: true,
        ..Default::default()
    };
    assert_eq!(
        get_netsim_discovery_dirs(&proc_win.environ, HostOs::Windows).first(),
        Some(&win_disc)
    );

    let mut tracker = NetsimTracker::new(None, 256, true, dir.path());
    tracker.host_os = HostOs::Windows;
    // Without custom dirs, discovery applies the Windows layout to the daemon's environment.
    assert!(
        tracker.discover_ini_files(std::slice::from_ref(&proc_win)).contains(&ini_path),
        "host_os must reach discover_ini_files"
    );
    tracker.custom_ini_dirs = vec![win_disc.clone()];
    tracker.custom_proc_locks = Some(String::new());
    tracker.socket_cli_runner = |cmd, _| {
        assert_eq!(cmd, "netstat");
        Some(
            "  TCP    127.0.0.1:51001        127.0.0.1:43929        ESTABLISHED     4001\r\n"
                .to_string(),
        )
    };

    let mut phone = emu_group("emulator-5554", 4001, "Phone", Some("Phone"));
    phone.primary_start_time = START;
    phone.pids = vec![4001];
    phone.grpc_endpoint = Some("127.0.0.1:43929".to_string());

    emu_tick_with(&mut tracker, &[proc_win], &[("Phone_emulator-5554", &phone)], 2.0);
    let d = &tracker.daemons[&(DAEMON_A, START)];
    assert_eq!(d.artifact_dir, win_art);
    assert_eq!(d.netsim_cli_bin, "netsim.exe");
    assert_eq!(d.grpc_port, Some(43929));
    assert_eq!(d.version.as_deref(), Some("1.0.31"));
    assert_eq!(tracker.emulator_records["Phone_emulator-5554"].state, "CONNECTED");
}
