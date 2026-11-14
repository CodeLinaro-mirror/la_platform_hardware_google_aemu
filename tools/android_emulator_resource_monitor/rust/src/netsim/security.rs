use std::path::Path;
use std::sync::OnceLock;

#[cfg(any(unix, test))]
fn resolve_real_uid_with(
    proc_status: Option<&str>,
    fallback: impl FnOnce() -> Option<u32>,
) -> Option<u32> {
    proc_status
        .and_then(crate::host::parse_proc_status_uids)
        .map(|(ruid, _)| ruid)
        .or_else(fallback)
}

/// Queries the running monitor process's real UID (`ruid`) via `sysinfo`
/// (`Process::user_id()`, `pbi_ruid` on macOS/BSD), falling back to `id -ru`.
#[cfg(unix)]
fn query_process_self_uid() -> Option<u32> {
    use sysinfo::{Pid, ProcessRefreshKind, ProcessesToUpdate, System, UpdateKind};
    let pid = Pid::from_u32(std::process::id());
    let mut sys = System::new();
    sys.refresh_processes_specifics(
        ProcessesToUpdate::Some(&[pid]),
        true,
        ProcessRefreshKind::nothing().with_user(UpdateKind::OnlyIfNotSet),
    );
    if let Some(uid) = sys.process(pid).and_then(crate::host::process_uid) {
        return Some(uid);
    }
    let out = super::run_with_timeout("id", &["-ru"], std::time::Duration::from_secs(2))?;
    if !out.status.success() {
        return None;
    }
    String::from_utf8_lossy(&out.stdout).trim().parse::<u32>().ok()
}

#[cfg(any(unix, test))]
fn resolve_owner_uid_from(
    status_path: &Path,
    fallback: impl FnOnce() -> Option<u32>,
) -> Option<u32> {
    resolve_real_uid_with(std::fs::read_to_string(status_path).ok().as_deref(), fallback)
}

/// The monitor process's real UID (`ruid`) for daemon/filesystem ownership checks,
/// cached for the process lifetime.
///
/// Resolves `/proc/self/status` `Uid:` column 1 (`ruid`) first, then `sysinfo::Process::user_id()`,
/// then `id -ru` (matching Python `os.getuid()`). Never stats `/proc/self` (whose owner Linux
/// sets to `0` under `PR_SET_DUMPABLE=0` and `euid` otherwise) and never calls `id -u` (`euid`);
/// privilege checks for passwordless `sudo -n -u` live in `guest::effective_uid()`.
pub(super) fn owner_uid() -> Option<u32> {
    static OWNER: OnceLock<Option<u32>> = OnceLock::new();
    *OWNER.get_or_init(|| {
        #[cfg(unix)]
        {
            resolve_owner_uid_from(Path::new("/proc/self/status"), query_process_self_uid)
        }
        #[cfg(not(unix))]
        {
            None
        }
    })
}

/// The monitor's real UID (`ruid`) on Unix, used for `/run/user/<uid>` discovery.
pub(super) fn current_uid() -> Option<u32> {
    owner_uid()
}

/// An existing non-symlink directory (`lstat` on the final component).
pub fn is_real_dir(path: &Path) -> bool {
    !path.as_os_str().is_empty()
        && std::fs::symlink_metadata(path).is_ok_and(|m| m.file_type().is_dir())
}

#[cfg(unix)]
#[derive(Clone, Copy, Debug)]
struct DirNodeMeta {
    is_dir: bool,
    is_symlink: bool,
    uid: u32,
    mode: u32,
}

#[cfg(unix)]
fn dir_node_from_meta(m: &std::fs::Metadata) -> DirNodeMeta {
    use std::os::unix::fs::MetadataExt;
    DirNodeMeta {
        is_dir: m.file_type().is_dir(),
        is_symlink: m.file_type().is_symlink(),
        uid: m.uid(),
        mode: m.mode(),
    }
}

/// Whether nobody but `expected_uid` or root can replace entries of directory `node`.
///
/// * Owned by `expected_uid`: not world-writable unless sticky. Group-writable is accepted
///   for a user (user-private groups under umask 002) but not for root.
/// * Owned by anyone else: only root, and then neither group- nor world-writable unless
///   sticky (`/tmp`).
/// * No expected uid (the process's user is unknown): any owner, not world-writable unless
///   sticky.
#[cfg(unix)]
fn is_trusted_dir_node(node: DirNodeMeta, expected_uid: Option<u32>, root_uid: u32) -> bool {
    if !node.is_dir {
        return false;
    }
    let safe_user_mode = (node.mode & 0o002 == 0) || (node.mode & 0o1000 != 0);
    let safe_root_mode = (node.mode & 0o022 == 0) || (node.mode & 0o1000 != 0);
    match expected_uid {
        Some(uid) if node.uid == uid => {
            if uid == 0 || uid == root_uid {
                safe_root_mode
            } else {
                safe_user_mode
            }
        }
        Some(_) => (node.uid == 0 || node.uid == root_uid) && safe_root_mode,
        None => safe_user_mode,
    }
}

/// Symlinks one path resolution may follow (Linux `MAXSYMLINKS`).
#[cfg(unix)]
const MAX_SYMLINK_HOPS: usize = 40;

/// One step of a path resolution.
#[cfg(unix)]
enum PathStep {
    Root,
    Parent,
    Name(std::ffi::OsString),
}

/// Pushes the steps of `path` so that its first component is popped first.
#[cfg(unix)]
fn push_path_steps(pending: &mut Vec<PathStep>, path: &Path) {
    use std::path::Component;
    for comp in path.components().rev() {
        match comp {
            Component::RootDir => pending.push(PathStep::Root),
            Component::ParentDir => pending.push(PathStep::Parent),
            Component::Normal(name) => pending.push(PathStep::Name(name.to_os_string())),
            Component::CurDir | Component::Prefix(_) => {}
        }
    }
}

/// Resolves absolute `target_dir` one component at a time, as the kernel does, and returns
/// its canonical path only if every directory entered is a trusted node and every symlink
/// followed is owned by `expected_uid` or root.
///
/// Each symlink is read inside a directory that was already accepted, and its target is
/// resolved the same way, so no directory a third party can write to takes part in the
/// resolution. `..` is refused in `target_dir` itself; inside a symlink target it steps back
/// to a directory accepted earlier.
#[cfg(unix)]
fn resolve_trusted_dir_with(
    target_dir: &Path,
    expected_uid: Option<u32>,
    root_uid: u32,
    lstat: impl Fn(&Path) -> std::io::Result<DirNodeMeta>,
    read_link: impl Fn(&Path) -> std::io::Result<std::path::PathBuf>,
) -> Option<std::path::PathBuf> {
    use std::path::{Component, PathBuf};
    if !target_dir.is_absolute()
        || target_dir.components().any(|c| matches!(c, Component::ParentDir))
    {
        return None;
    }
    let mut pending = Vec::new();
    push_path_steps(&mut pending, target_dir);
    let mut cur = PathBuf::new();
    let mut hops = 0;
    while let Some(step) = pending.pop() {
        let next = match step {
            PathStep::Root => PathBuf::from("/"),
            PathStep::Parent => {
                cur.pop();
                continue;
            }
            PathStep::Name(name) => cur.join(name),
        };
        let node = lstat(&next).ok()?;
        if node.is_symlink {
            hops += 1;
            let owner_ok = expected_uid == Some(node.uid) || node.uid == 0 || node.uid == root_uid;
            if hops > MAX_SYMLINK_HOPS || !owner_ok {
                return None;
            }
            push_path_steps(&mut pending, &read_link(&next).ok()?);
        } else if is_trusted_dir_node(node, expected_uid, root_uid) {
            cur = next;
        } else {
            return None;
        }
    }
    Some(cur)
}

/// `resolve_trusted_dir_with()` on the real filesystem; a relative `target_dir` starts at the
/// current directory.
#[cfg(unix)]
fn trusted_canonical_dir(
    target_dir: &Path,
    expected_uid: Option<u32>,
    root_uid: u32,
) -> Option<std::path::PathBuf> {
    if target_dir.as_os_str().is_empty() {
        return None;
    }
    let absolute = if target_dir.is_absolute() {
        target_dir.to_path_buf()
    } else {
        std::env::current_dir().ok()?.join(target_dir)
    };
    resolve_trusted_dir_with(
        &absolute,
        expected_uid,
        root_uid,
        |p| std::fs::symlink_metadata(p).map(|m| dir_node_from_meta(&m)),
        |p| std::fs::read_link(p),
    )
}

/// Whether resolving `target_dir` only passes through directories owned by `expected_uid` or
/// root (`0` / `root_uid`) with safe permissions, and symlinks owned by them (keeping macOS
/// `/tmp -> private/tmp` and `/var -> private/var` while rejecting third-party intermediate
/// directories such as `/tmp/attacker_sub/owned_dir`).
pub(super) fn has_trusted_ancestors(
    target_dir: &Path,
    expected_uid: Option<u32>,
    root_uid: u32,
) -> bool {
    if target_dir.as_os_str().is_empty() {
        return false;
    }
    #[cfg(unix)]
    {
        trusted_canonical_dir(target_dir, expected_uid, root_uid).is_some()
    }
    #[cfg(not(unix))]
    {
        let _ = (expected_uid, root_uid);
        target_dir.is_dir()
    }
}

/// Validates all existing prefix components of `path` when leaf components may not yet
/// exist on disk (`ErrorKind::NotFound` at daemon startup).
pub(super) fn has_trusted_existing_ancestors(
    path: &Path,
    expected_uid: Option<u32>,
    root_uid: u32,
) -> bool {
    if path.as_os_str().is_empty() {
        return false;
    }
    #[cfg(unix)]
    {
        use std::path::{Component, PathBuf};
        let mut cur = PathBuf::new();
        let mut deepest_existing: Option<PathBuf> = None;
        for comp in path.components() {
            if matches!(comp, Component::ParentDir) {
                return false;
            }
            cur.push(comp);
            match std::fs::symlink_metadata(&cur) {
                Ok(_) => deepest_existing = Some(cur.clone()),
                Err(e) if e.kind() == std::io::ErrorKind::NotFound => break,
                Err(_) => return false,
            }
        }
        let target = deepest_existing.as_deref().unwrap_or_else(|| Path::new("."));
        has_trusted_ancestors(target, expected_uid, root_uid)
    }
    #[cfg(not(unix))]
    {
        let _ = (expected_uid, root_uid);
        true
    }
}

/// Verifies `dir` is a non-symlink real directory with non-world-writable permissions,
/// owned by `expected_uid` (failing closed on Unix when `expected_uid` is `None` and
/// `monitor_uid != Some(0)`), and backed by trusted ancestor directories.
pub(super) fn is_trusted_owned_real_dir_with_monitor(
    dir: &Path,
    expected_uid: Option<u32>,
    monitor_uid: Option<u32>,
    root_uid: u32,
) -> bool {
    if dir.as_os_str().is_empty() {
        return false;
    }
    let Ok(m) = std::fs::symlink_metadata(dir) else {
        return false;
    };
    if !m.file_type().is_dir() {
        return false;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if (m.mode() & 0o002) != 0 {
            return false;
        }
        match expected_uid {
            Some(uid) if m.uid() != uid => return false,
            Some(_) => {}
            None if monitor_uid == Some(0) => {}
            None => return false,
        }
        has_trusted_ancestors(dir, expected_uid, root_uid)
    }
    #[cfg(not(unix))]
    {
        let _ = (expected_uid, monitor_uid, root_uid);
        true
    }
}

pub(super) fn is_trusted_owned_real_dir(dir: &Path, expected_uid: Option<u32>) -> bool {
    is_trusted_owned_real_dir_with_monitor(dir, expected_uid, owner_uid(), 0)
}

#[cfg(unix)]
fn is_trusted_ini_path_with(ini_path: &Path, uid: u32, root_uid: u32) -> bool {
    use std::os::unix::fs::MetadataExt;
    let Some(ini_dir) = ini_path.parent().filter(|p| !p.as_os_str().is_empty()) else {
        return true;
    };
    let Some(canon_dir) = trusted_canonical_dir(ini_dir, Some(uid), root_uid) else {
        return false;
    };
    let Ok(dm) = std::fs::symlink_metadata(&canon_dir) else {
        return false;
    };
    if !is_trusted_dir_node(dir_node_from_meta(&dm), Some(uid), root_uid) {
        return false;
    }
    let private_owned_dir = dm.uid() == uid
        && if uid == 0 || uid == root_uid {
            (dm.mode() & 0o022) == 0
        } else {
            (dm.mode() & 0o002) == 0
        };
    let check_ini = |p: &Path| match std::fs::symlink_metadata(p) {
        Ok(im) => im.file_type().is_file() && im.uid() == uid,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => private_owned_dir,
        Err(_) => false,
    };
    if !check_ini(ini_path) {
        return false;
    }
    if canon_dir != ini_dir {
        let Some(fname) = ini_path.file_name() else {
            return false;
        };
        if !check_ini(&canon_dir.join(fname)) {
            return false;
        }
    }
    true
}

/// Whether `ini_path`'s directory is safe to pass as `TMPDIR` to `netsim`:
/// full ancestor chain is trusted and `ini_dir` is either owned by the monitor's user,
/// or a sticky / non-group-world-writable root-owned temp directory (`/tmp`, `/var/tmp`,
/// `/private/tmp`) when `ini_path` (if present) is a regular file owned by the monitor's user.
pub fn is_trusted_ini_path(ini_path: &Path) -> bool {
    let Some(ini_dir) = ini_path.parent().filter(|p| !p.as_os_str().is_empty()) else {
        return true;
    };
    #[cfg(unix)]
    {
        let _ = ini_dir;
        match owner_uid() {
            Some(uid) => is_trusted_ini_path_with(ini_path, uid, 0),
            None => false,
        }
    }
    #[cfg(not(unix))]
    {
        ini_dir.is_dir()
    }
}

/// Whether a `tailer.ini_path_logged` candidate is a non-symlink regular file whose owner
/// and ancestor directories are compatible with the daemon (`same_user` / `expected_uid`).
pub(super) fn can_adopt_logged_ini_path_with(
    path: &Path,
    same_user: bool,
    expected_uid: Option<u32>,
    monitor_uid: Option<u32>,
) -> bool {
    if path.as_os_str().is_empty() {
        return false;
    }
    let Ok(meta) = std::fs::symlink_metadata(path) else {
        return false;
    };
    if !meta.file_type().is_file() || meta.len() > super::MAX_INI_BYTES as u64 {
        return false;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        let eff_uid =
            expected_uid.or_else(|| same_user.then_some(monitor_uid).flatten().filter(|&u| u != 0));
        if same_user && monitor_uid.is_none() && expected_uid.is_none() {
            return false;
        }
        if let Some(uid) = eff_uid {
            return meta.uid() == uid && is_trusted_ini_path_with(path, uid, 0);
        }
        if !same_user && monitor_uid.is_some_and(|mu| mu != 0 && meta.uid() == mu) {
            return false;
        }
        path.parent()
            .filter(|p| !p.as_os_str().is_empty())
            .is_none_or(|d| has_trusted_ancestors(d, None, 0))
    }
    #[cfg(not(unix))]
    {
        let _ = (same_user, expected_uid, monitor_uid);
        true
    }
}

pub(super) fn can_adopt_logged_ini_path(
    path: &Path,
    same_user: bool,
    expected_uid: Option<u32>,
) -> bool {
    can_adopt_logged_ini_path_with(path, same_user, expected_uid, owner_uid())
}

/// Whether `path` is a program the monitor's user or root put in place.
///
/// The file and its directory must be owned by the monitor's user or root and must not be
/// group- or world-writable. Every directory entered on the way to them must pass
/// `trusted_canonical_dir`, with the user's own directories held to root's rule (no group
/// write): unlike a data file, a program swapped in by a member of the user's group would
/// run as the user. A symlink is followed only if the user or root owns it, under the same
/// rules. Windows has no POSIX ownership; there only the regular-file check applies.
pub fn is_trusted_executable(path: &Path) -> bool {
    #[cfg(unix)]
    {
        owner_uid().is_some_and(|uid| is_trusted_executable_for(path, uid))
    }
    #[cfg(not(unix))]
    {
        let dir = path.parent().filter(|d| !d.as_os_str().is_empty()).unwrap_or(Path::new("."));
        std::fs::metadata(path).is_ok_and(|m| m.is_file())
            && std::fs::metadata(dir).is_ok_and(|m| m.is_dir())
    }
}

#[cfg(unix)]
fn is_trusted_executable_for(path: &Path, uid: u32) -> bool {
    use std::os::unix::fs::MetadataExt;
    let owned = |m: &std::fs::Metadata| m.uid() == uid || m.uid() == 0;
    let only_owner_writes = |m: &std::fs::Metadata| owned(m) && m.mode() & 0o022 == 0;
    let mut candidate = path.to_path_buf();
    for _ in 0..=MAX_SYMLINK_HOPS {
        let (Some(parent), Some(name)) = (candidate.parent(), candidate.file_name()) else {
            return false;
        };
        // `Path` ignores a trailing `/` or `/.`; to the kernel such a path names a directory.
        if !candidate.as_os_str().as_encoded_bytes().ends_with(name.as_encoded_bytes()) {
            return false;
        }
        // `root_uid = uid` applies root's rule to the user's own directories as well.
        let Some(dir) = trusted_canonical_dir(parent, Some(uid), uid) else {
            return false;
        };
        let leaf = dir.join(name);
        let (Ok(dm), Ok(m)) = (std::fs::symlink_metadata(&dir), std::fs::symlink_metadata(&leaf))
        else {
            return false;
        };
        if !only_owner_writes(&dm) {
            return false;
        }
        if !m.file_type().is_symlink() {
            return m.file_type().is_file() && only_owner_writes(&m);
        }
        if !owned(&m) {
            return false;
        }
        let Ok(target) = std::fs::read_link(&leaf) else {
            return false;
        };
        candidate = dir.join(target);
    }
    false
}

/// Picks the `netsim` CLI to drive a daemon with on `host_os`.
///
/// A sibling of the daemon's executable is used only when the daemon runs as
/// the monitor's user and the sibling passes `is_trusted_executable`.
/// Otherwise the bare name (`"netsim.exe"` on `HostOs::Windows`, `"netsim"` elsewhere)
/// resolves through the monitor's own PATH.
pub fn resolve_netsim_cli_bin_for_os(
    daemon_exe: Option<&Path>,
    same_user: bool,
    host_os: super::HostOs,
) -> String {
    let default = if host_os == super::HostOs::Windows { "netsim.exe" } else { "netsim" };
    if !same_user {
        return default.to_string();
    }
    daemon_exe
        .and_then(|e| e.parent())
        .and_then(|dir| {
            ["netsim.exe", "netsim"]
                .iter()
                .map(|name| dir.join(name))
                .find(|cand| is_trusted_executable(cand))
        })
        .map(|cand| cand.to_string_lossy().to_string())
        .unwrap_or_else(|| default.to_string())
}

#[cfg(test)]
pub fn resolve_netsim_cli_bin(daemon_exe: Option<&Path>, same_user: bool) -> String {
    resolve_netsim_cli_bin_for_os(daemon_exe, same_user, super::HostOs::current())
}

/// Returns `(O_NONBLOCK, O_NOFOLLOW)` for `OpenOptionsExt::custom_flags` on `(os, arch)`.
///
/// Unknown Unix targets fall back to `(0, 0)`, where `symlink_metadata` before
/// `open` and the `(dev, ino)` check on the open descriptor still reject symlinks.
#[cfg(any(unix, test))]
fn unix_open_flags_for(os: &str, arch: &str) -> (i32, i32) {
    match os {
        "linux" | "android" => match arch {
            "aarch64" | "arm" | "powerpc" | "powerpc64" | "m68k" => (0o4000, 0o100000),
            "mips" | "mips64" | "mips32r6" | "mips64r6" => (0x80, 0x20000),
            "sparc" | "sparc64" => (0x4000, 0x20000),
            _ => (0o4000, 0o400000),
        },
        "macos" | "ios" | "freebsd" | "netbsd" | "openbsd" | "dragonfly" => (0x0004, 0x0100),
        _ => (0, 0),
    }
}

/// Opens `path` read-only without following a symlink at `path` or blocking on
/// a FIFO, verifying that the open descriptor refers to a regular file (and, on
/// Unix, the exact `(dev, ino)` observed before `open` plus `expected_uid` when set).
pub(super) fn open_owned_regular_file(
    path: &Path,
    expected_uid: Option<u32>,
) -> Option<(std::fs::File, std::fs::Metadata)> {
    let lm = std::fs::symlink_metadata(path).ok()?;
    if !lm.file_type().is_file() {
        return None;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if let Some(uid) = expected_uid {
            if lm.uid() != uid {
                return None;
            }
        }
    }
    #[cfg(unix)]
    let f = {
        use std::os::unix::fs::OpenOptionsExt;
        let (o_nonblock, o_nofollow) =
            unix_open_flags_for(std::env::consts::OS, std::env::consts::ARCH);

        std::fs::OpenOptions::new()
            .read(true)
            .custom_flags(o_nonblock | o_nofollow)
            .open(path)
            .ok()?
    };
    #[cfg(not(unix))]
    let f = {
        let _ = expected_uid;
        std::fs::File::open(path).ok()?
    };
    let fm = f.metadata().ok()?;
    if !fm.is_file() {
        return None;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if (fm.dev(), fm.ino()) != (lm.dev(), lm.ino()) {
            return None;
        }
        if let Some(uid) = expected_uid {
            if fm.uid() != uid {
                return None;
            }
        }
    }
    Some((f, fm))
}

/// Reads a UTF-8 regular file of at most `max_bytes` without following a symlink at `path`
/// or blocking on a FIFO or device. With `expected_uid`, the file must also belong to that
/// user (`None`: any owner). Returns `None` for anything else, including invalid UTF-8.
pub(super) fn read_owned_regular_file_bounded(
    path: &Path,
    max_bytes: usize,
    expected_uid: Option<u32>,
) -> Option<String> {
    let (f, fm) = open_owned_regular_file(path, expected_uid)?;
    read_open_file_bounded(f, &fm, max_bytes)
}

/// The UTF-8 text of an open regular file with metadata `fm`, if it holds at most
/// `max_bytes`.
pub(super) fn read_open_file_bounded(
    f: std::fs::File,
    fm: &std::fs::Metadata,
    max_bytes: usize,
) -> Option<String> {
    use std::io::Read;
    let cap = u64::try_from(max_bytes).unwrap_or(u64::MAX);
    if fm.len() > cap {
        return None;
    }
    let init_cap = usize::try_from(fm.len()).unwrap_or(0).min(max_bytes);
    let mut buf = Vec::with_capacity(init_cap);
    f.take(cap.saturating_add(1)).read_to_end(&mut buf).ok()?;
    if buf.len() > max_bytes {
        return None;
    }
    String::from_utf8(buf).ok()
}

/// Copies `src` to `dst` without following a symlink at `src`, so an artifact directory named
/// by another process cannot turn the copy into a read of an arbitrary file. With
/// `expected_uid`, `src` must also belong to that user. Returns the bytes copied, or `None`
/// when `src` is not such a plain file.
pub(super) fn copy_owned_regular_file(
    src: &Path,
    dst: &Path,
    expected_uid: Option<u32>,
) -> Option<u64> {
    let (mut fsrc, _) = open_owned_regular_file(src, expected_uid)?;
    let mut fdst = std::fs::File::create(dst).ok()?;
    std::io::copy(&mut fsrc, &mut fdst).ok()
}

/// Copies `src` to `dst` without following a symlink at `src`.
#[cfg(test)]
pub fn copy_regular_file(src: &Path, dst: &Path) -> Option<u64> {
    copy_owned_regular_file(src, dst, None)
}

#[cfg(test)]
#[path = "../../tests/netsim/security_test.rs"]
mod tests;
