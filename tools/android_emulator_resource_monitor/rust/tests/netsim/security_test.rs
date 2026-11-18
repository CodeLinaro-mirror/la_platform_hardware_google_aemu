use super::*;

#[test]
#[cfg(unix)]
fn test_copy_regular_file_refuses_symlink_and_copies_plain_file() {
    let tmp = tempfile::tempdir().unwrap();
    let plain = tmp.path().join("plain.bin");
    std::fs::write(&plain, b"abc").unwrap();
    let link = tmp.path().join("link.bin");
    std::os::unix::fs::symlink(&plain, &link).unwrap();
    assert_eq!(copy_regular_file(&link, &tmp.path().join("o1")), None);
    assert!(!tmp.path().join("o1").exists());
    assert_eq!(copy_regular_file(tmp.path(), &tmp.path().join("o2")), None);
    assert_eq!(copy_regular_file(&plain, &tmp.path().join("o3")), Some(3));
    assert_eq!(std::fs::read(tmp.path().join("o3")).unwrap(), b"abc");

    let read = |p: &Path, max| read_owned_regular_file_bounded(p, max, None);
    assert_eq!(read(&plain, 3), Some("abc".to_string()));
    assert_eq!(read(&plain, 2), None);
    assert_eq!(read(&link, 16), None);
    assert_eq!(read(tmp.path(), 16), None);
}

#[test]
#[cfg(unix)]
fn test_resolve_netsim_cli_bin_only_adopts_trusted_sibling() {
    use std::os::unix::fs::PermissionsExt;
    let set_mode = |p: &Path, mode: u32| {
        std::fs::set_permissions(p, std::fs::Permissions::from_mode(mode)).unwrap();
    };
    let write_exe = |p: &Path| {
        std::fs::write(p, b"#!/bin/sh\n").unwrap();
        set_mode(p, 0o755);
    };
    // The daemon's exe dir comes from another process; only a same-user
    // daemon may nominate a sibling, and only one nobody else can replace.
    let tmp = tempfile::tempdir().unwrap();
    // Every directory down to the sibling is judged, so the temp dir's mode must not come
    // from the umask.
    set_mode(tmp.path(), 0o755);
    let sdk = tmp.path().join("sdk");
    std::fs::create_dir(&sdk).unwrap();
    set_mode(&sdk, 0o755);
    let daemon_exe = sdk.join("netsimd");
    let cli = sdk.join("netsim");
    for p in [&daemon_exe, &cli] {
        write_exe(p);
    }
    let cli_str = cli.to_string_lossy().to_string();

    assert_eq!(resolve_netsim_cli_bin(Some(&daemon_exe), true), cli_str);
    // Foreign daemon: never trust its neighbourhood.
    assert_eq!(resolve_netsim_cli_bin(Some(&daemon_exe), false), "netsim");
    assert_eq!(resolve_netsim_cli_bin(None, true), "netsim");
    // Group- or world-writable binary.
    set_mode(&cli, 0o775);
    assert_eq!(resolve_netsim_cli_bin(Some(&daemon_exe), true), "netsim");
    set_mode(&cli, 0o755);
    // World-writable directory (anyone could swap the file).
    set_mode(&sdk, 0o1777);
    assert_eq!(resolve_netsim_cli_bin(Some(&daemon_exe), true), "netsim");
    set_mode(&sdk, 0o755);

    // A directory above the sibling's own counts too: when others, or the user's group, can
    // write to it without the sticky bit, they can swap `sdk` itself.
    let open_dir = tmp.path().join("open");
    let open_sdk = open_dir.join("sdk");
    std::fs::create_dir_all(&open_sdk).unwrap();
    set_mode(&open_sdk, 0o755);
    let open_daemon = open_sdk.join("netsimd");
    let open_cli = open_sdk.join("netsim");
    for p in [&open_daemon, &open_cli] {
        write_exe(p);
    }
    for mode in [0o777, 0o775] {
        set_mode(&open_dir, mode);
        assert_eq!(resolve_netsim_cli_bin(Some(&open_daemon), true), "netsim", "{mode:o}");
    }
    for mode in [0o755, 0o1777] {
        set_mode(&open_dir, mode);
        assert_eq!(
            resolve_netsim_cli_bin(Some(&open_daemon), true),
            open_cli.to_string_lossy().to_string(),
            "{mode:o}"
        );
    }

    // A symlink named netsim is judged by its target, including the directories on the
    // way to it.
    std::fs::remove_file(&cli).unwrap();
    std::os::unix::fs::symlink(&open_cli, &cli).unwrap();
    assert_eq!(resolve_netsim_cli_bin(Some(&daemon_exe), true), cli_str);
    set_mode(&open_dir, 0o777);
    assert_eq!(resolve_netsim_cli_bin(Some(&daemon_exe), true), "netsim");
    std::fs::remove_file(&cli).unwrap();
    let real = sdk.join("netsim-real");
    write_exe(&real);
    std::os::unix::fs::symlink(&real, &cli).unwrap();
    assert_eq!(resolve_netsim_cli_bin(Some(&daemon_exe), true), cli_str);
    // A target spelled as a directory (`netsim-real/`) is one the kernel cannot run.
    std::fs::remove_file(&cli).unwrap();
    std::os::unix::fs::symlink("netsim-real/", &cli).unwrap();
    assert_eq!(resolve_netsim_cli_bin(Some(&daemon_exe), true), "netsim");
    // Missing sibling falls back to PATH.
    std::fs::remove_file(&cli).unwrap();
    std::fs::remove_file(&real).unwrap();
    assert_eq!(resolve_netsim_cli_bin(Some(&daemon_exe), true), "netsim");
}

#[test]
fn test_is_same_user() {
    use crate::host::is_same_user;
    if cfg!(windows) {
        assert!(is_same_user(None, None));
    } else {
        assert!(is_same_user(Some(1000), Some(1000)));
        assert!(!is_same_user(Some(1001), Some(1000)));
        assert!(!is_same_user(None, Some(1000)));
        assert!(!is_same_user(Some(1000), None));
    }
}

#[test]
fn test_unix_open_flags_selects_arch_and_os_specific_nofollow() {
    assert_eq!(unix_open_flags_for("linux", "x86_64"), (0o4000, 0o400000));
    assert_eq!(unix_open_flags_for("linux", "riscv64"), (0o4000, 0o400000));
    assert_eq!(unix_open_flags_for("linux", "aarch64"), (0o4000, 0o100000));
    assert_eq!(unix_open_flags_for("linux", "arm"), (0o4000, 0o100000));
    assert_eq!(unix_open_flags_for("linux", "powerpc64"), (0o4000, 0o100000));
    assert_eq!(unix_open_flags_for("linux", "mips64"), (0x80, 0x20000));
    assert_eq!(unix_open_flags_for("linux", "sparc64"), (0x4000, 0x20000));
    assert_eq!(unix_open_flags_for("android", "aarch64"), (0o4000, 0o100000));
    assert_eq!(unix_open_flags_for("macos", "aarch64"), (0x0004, 0x0100));
    assert_eq!(unix_open_flags_for("freebsd", "x86_64"), (0x0004, 0x0100));
    assert_eq!(unix_open_flags_for("netbsd", "x86_64"), (0x0004, 0x0100));
    assert_eq!(unix_open_flags_for("openbsd", "x86_64"), (0x0004, 0x0100));
    assert_eq!(unix_open_flags_for("illumos", "x86_64"), (0, 0));

    if cfg!(any(target_os = "linux", target_os = "macos")) {
        let (nb, nf) = unix_open_flags_for(std::env::consts::OS, std::env::consts::ARCH);
        assert_ne!(nb, 0);
        assert_ne!(nf, 0);
        assert_eq!(nb & nf, 0);
    }
}

#[test]
#[cfg(unix)]
fn test_query_process_self_uid_and_is_trusted_ini_path() {
    use std::os::unix::fs::PermissionsExt;
    let uid = owner_uid().expect("owner_uid must resolve on Unix");
    assert_eq!(query_process_self_uid(), Some(uid));

    let tmp = tempfile::tempdir().unwrap();
    // Every directory down to the INI is judged below, so the temp dir's mode must not
    // come from the umask.
    std::fs::set_permissions(tmp.path(), std::fs::Permissions::from_mode(0o755)).unwrap();
    let dir = tmp.path().join("ini_dir");
    std::fs::create_dir(&dir).unwrap();
    std::fs::set_permissions(&dir, std::fs::Permissions::from_mode(0o755)).unwrap();
    let ini = dir.join("netsim.ini");
    std::fs::write(&ini, b"grpc.port=43929\n").unwrap();

    // User-owned directory + regular INI file (or not yet written) is trusted.
    assert!(is_trusted_ini_path(&ini));
    assert!(is_trusted_ini_path(&dir.join("netsim_2.ini")));
    assert!(is_trusted_ini_path(Path::new("netsim.ini")));

    // Symlink INI is rejected even inside a trusted directory.
    let link_ini = dir.join("netsim_3.ini");
    std::os::unix::fs::symlink(&ini, &link_ini).unwrap();
    assert!(!is_trusted_ini_path(&link_ini));

    // Foreign directory (dm.uid != uid && dm.uid != root_uid) is rejected.
    let other_uid = uid.wrapping_add(1).max(1);
    assert!(!is_trusted_ini_path_with(&ini, other_uid, 0));

    // Simulated root-owned directory: non-sticky 0o777 and non-sticky 0o775 are rejected;
    // sticky 0o1777 accepts only existing regular files owned by `uid` (rejecting foreign-owned
    // INI files AND `NotFound` INI files even when `dm.uid() == uid`, e.g., `sudo` on `/tmp`).
    std::fs::set_permissions(&dir, std::fs::Permissions::from_mode(0o777)).unwrap();
    assert!(!is_trusted_ini_path_with(&dir.join("netsim_9.ini"), other_uid, uid));
    std::fs::set_permissions(&dir, std::fs::Permissions::from_mode(0o775)).unwrap();
    assert!(!is_trusted_ini_path_with(&ini, uid, uid));
    std::fs::set_permissions(&dir, std::fs::Permissions::from_mode(0o1777)).unwrap();
    assert!(!is_trusted_ini_path_with(&ini, other_uid, uid));
    assert!(is_trusted_ini_path_with(&ini, uid, uid));
    assert!(!is_trusted_ini_path_with(&dir.join("netsim_missing_sticky.ini"), uid, uid));
    assert!(!is_trusted_ini_path_with(
        Path::new("/tmp/netsim_erm_nonexistent_sudo_probe.ini"),
        0,
        0
    ));

    // Real sticky `/tmp` with a regular INI owned by us is trusted.
    let sys_tmp_ini =
        tempfile::Builder::new().prefix("netsim_").suffix(".ini").tempfile_in("/tmp").unwrap();
    assert!(is_trusted_ini_path(sys_tmp_ini.path()));

    // Symlinked `ini_dir` component: rejected when the symlink is owned by a
    // foreign user, accepted when owned by `uid` or `root_uid` (e.g. macOS
    // `/tmp -> private/tmp`) inside a trusted parent directory.
    let real_ini_dir = tmp.path().join("real_ini_dir");
    std::fs::create_dir(&real_ini_dir).unwrap();
    std::fs::set_permissions(&real_ini_dir, std::fs::Permissions::from_mode(0o755)).unwrap();
    let real_ini = real_ini_dir.join("netsim.ini");
    std::fs::write(&real_ini, b"grpc.port=43930\n").unwrap();
    let sym_ini_dir = tmp.path().join("sym_ini_dir");
    std::os::unix::fs::symlink(&real_ini_dir, &sym_ini_dir).unwrap();
    let via_sym = sym_ini_dir.join("netsim.ini");
    assert!(is_trusted_ini_path_with(&via_sym, uid, 0));
    // A monitor running as `other_uid` (`root_uid == 0`) refuses the path: its directories
    // and the symlink belong to `uid`.
    std::fs::set_permissions(&real_ini_dir, std::fs::Permissions::from_mode(0o1777)).unwrap();
    assert!(!is_trusted_ini_path_with(&via_sym, other_uid, 0));
    // macOS-style root-owned symlink (`root_uid == uid`) to sticky root dir is accepted
    // only when the target INI file is owned by the monitor (`uid`), not `other_uid`.
    assert!(!is_trusted_ini_path_with(&via_sym, other_uid, uid));
    assert!(is_trusted_ini_path_with(&via_sym, uid, uid));

    // `is_trusted_owned_real_dir_with_monitor` rejects world-writable owned dirs (`0o1777`)
    // while accepting user-owned `0o775` (`umask 0002` user-private groups) and enforcing UID.
    assert!(!is_trusted_owned_real_dir_with_monitor(&real_ini_dir, Some(uid), Some(uid), 0));
    std::fs::set_permissions(&real_ini_dir, std::fs::Permissions::from_mode(0o775)).unwrap();
    assert!(is_trusted_owned_real_dir_with_monitor(&real_ini_dir, Some(uid), Some(uid), 0));
    assert!(is_trusted_owned_real_dir_with_monitor(&real_ini_dir, None, Some(0), 0));
    assert!(!is_trusted_owned_real_dir_with_monitor(&real_ini_dir, None, Some(uid), 0));
    assert!(!is_trusted_owned_real_dir_with_monitor(&real_ini_dir, Some(other_uid), Some(0), 0));
    assert!(!is_trusted_owned_real_dir_with_monitor(&real_ini_dir, None, None, 0));
    assert!(!is_trusted_owned_real_dir_with_monitor(&sym_ini_dir, Some(uid), Some(uid), 0));
    assert!(!is_trusted_owned_real_dir_with_monitor(&sym_ini_dir, None, Some(0), 0));
    assert_eq!(
        read_owned_regular_file_bounded(&real_ini, 64, Some(uid)),
        Some("grpc.port=43930\n".to_string())
    );
    assert_eq!(read_owned_regular_file_bounded(&real_ini, 64, Some(other_uid)), None);
}

#[test]
fn test_resolve_real_uid_and_cli_bin_for_os() {
    // `resolve_real_uid_with` takes the real uid (first `Uid:` column), never the effective one.
    assert_eq!(
        resolve_real_uid_with(Some("Name:\temulator_mon\nUid:\t1000\t0\t0\t0\n"), || panic!(
            "no fallback"
        )),
        Some(1000)
    );
    assert_eq!(
        resolve_real_uid_with(Some("Name:\temulator_mon\nUid:\t0\t501\t0\t501\n"), || panic!(
            "no fallback"
        )),
        Some(0)
    );
    assert_eq!(
        resolve_real_uid_with(
            Some("Name:\temulator_mon\nDumpable:\t0\nUid:\t472823\t472823\t472823\t472823\n"),
            || Some(0)
        ),
        Some(472823)
    );
    assert_eq!(resolve_real_uid_with(None, || Some(501)), Some(501));

    // `resolve_netsim_cli_bin_for_os` returns `"netsim.exe"` on `HostOs::Windows`
    // and `"netsim"` on `HostOs::Linux` / `HostOs::MacOs` when no trusted sibling exists.
    assert_eq!(
        resolve_netsim_cli_bin_for_os(None, true, super::super::HostOs::Windows),
        "netsim.exe"
    );
    assert_eq!(
        resolve_netsim_cli_bin_for_os(
            Some(Path::new(r"C:\Android\Sdk\emulator\netsimd.exe")),
            false,
            super::super::HostOs::Windows
        ),
        "netsim.exe"
    );
    assert_eq!(resolve_netsim_cli_bin_for_os(None, true, super::super::HostOs::Linux), "netsim");
    assert_eq!(resolve_netsim_cli_bin_for_os(None, true, super::super::HostOs::MacOs), "netsim");
}

#[test]
#[cfg(unix)]
fn test_has_trusted_ancestors_rejects_unowned_intermediate_real_dir_and_logged_ini() {
    use std::os::unix::fs::PermissionsExt;

    let uid = owner_uid().expect("owner_uid must resolve on Unix");
    let attacker_uid = uid.wrapping_add(1).max(1);

    // `/tmp/attacker_sub/owned_dir`, where `owned_dir` is a real directory owned by `uid`,
    // neither component is a symlink and `/tmp` is sticky root, MUST be rejected: the
    // intermediate directory `attacker_sub` belongs to `attacker_uid`, who can swap
    // `owned_dir` with a `rename()`.
    let lex = Path::new("/tmp/attacker_sub/owned_dir");
    let meta_for = |p: &Path| -> std::io::Result<DirNodeMeta> {
        match p.to_str().unwrap_or("") {
            "/" => Ok(DirNodeMeta { is_dir: true, is_symlink: false, uid: 0, mode: 0o755 }),
            "/tmp" => Ok(DirNodeMeta { is_dir: true, is_symlink: false, uid: 0, mode: 0o1777 }),
            "/tmp/attacker_sub" => {
                Ok(DirNodeMeta { is_dir: true, is_symlink: false, uid: attacker_uid, mode: 0o755 })
            }
            "/tmp/attacker_sub/owned_dir" => {
                Ok(DirNodeMeta { is_dir: true, is_symlink: false, uid, mode: 0o700 })
            }
            _ => Err(std::io::Error::from(std::io::ErrorKind::NotFound)),
        }
    };
    let not_a_link = |p: &Path| -> std::io::Result<std::path::PathBuf> {
        Err(std::io::Error::other(format!("not a symlink: {}", p.display())))
    };
    assert!(
        resolve_trusted_dir_with(lex, Some(uid), 0, meta_for, not_a_link).is_none(),
        "intermediate real directory owned by third-party UID must be rejected"
    );

    // Owned symlink `/tmp/owned_sym -> /tmp/attacker_sub/owned_dir`: the link and its leaf
    // target `owned_dir` are owned by `uid` and `/tmp` is sticky root, but resolving the
    // target passes through `attacker_sub`, so it MUST be rejected.
    let lex_sym = Path::new("/tmp/owned_sym");
    let lstat_sym = |p: &Path| -> std::io::Result<DirNodeMeta> {
        if p == lex_sym {
            return Ok(DirNodeMeta { is_dir: false, is_symlink: true, uid, mode: 0o120777 });
        }
        meta_for(p)
    };
    let link_sym = |p: &Path| -> std::io::Result<std::path::PathBuf> {
        if p == lex_sym { Ok(lex.to_path_buf()) } else { not_a_link(p) }
    };
    assert!(
        resolve_trusted_dir_with(lex_sym, Some(uid), 0, lstat_sym, link_sym).is_none(),
        "owned symlink resolving through third-party intermediate real dir must be rejected"
    );

    // Control: when `attacker_sub` is owned by `uid` (or sticky `0`), the chain is accepted.
    let safe_meta = |p: &Path| -> std::io::Result<DirNodeMeta> {
        if p == Path::new("/tmp/attacker_sub") {
            return Ok(DirNodeMeta { is_dir: true, is_symlink: false, uid, mode: 0o755 });
        }
        meta_for(p)
    };
    assert_eq!(
        resolve_trusted_dir_with(lex, Some(uid), 0, safe_meta, not_a_link).as_deref(),
        Some(lex)
    );

    // Live `/tmp` intermediate directory check + `can_adopt_logged_ini_path_with`.
    let sys_sub = tempfile::Builder::new().prefix("erm_sub_").tempdir_in("/tmp").unwrap();
    std::fs::set_permissions(sys_sub.path(), std::fs::Permissions::from_mode(0o755)).unwrap();
    let nested = sys_sub.path().join("owned_dir");
    std::fs::create_dir(&nested).unwrap();
    std::fs::set_permissions(&nested, std::fs::Permissions::from_mode(0o755)).unwrap();
    let ini = nested.join("netsim.ini");
    std::fs::write(&ini, b"grpc.port=43929\npid=5001\n").unwrap();

    assert!(has_trusted_ancestors(&nested, Some(uid), 0));
    assert!(!has_trusted_ancestors(&nested, Some(attacker_uid), 0));
    assert!(can_adopt_logged_ini_path_with(&ini, true, Some(uid), Some(uid)));
    assert!(!can_adopt_logged_ini_path_with(&ini, true, Some(attacker_uid), Some(0)));
    assert!(!can_adopt_logged_ini_path_with(&ini, false, None, Some(uid)));

    let sym_ini = nested.join("sym_netsim.ini");
    std::os::unix::fs::symlink(&ini, &sym_ini).unwrap();
    assert!(!can_adopt_logged_ini_path_with(&sym_ini, true, Some(uid), Some(uid)));
    assert!(!can_adopt_logged_ini_path_with(&sym_ini, false, Some(uid), Some(0)));

    let oversize_ini = nested.join("oversize_netsim.ini");
    std::fs::write(&oversize_ini, vec![b'x'; super::super::MAX_INI_BYTES + 1]).unwrap();
    assert!(!can_adopt_logged_ini_path_with(&oversize_ini, true, Some(uid), Some(uid)));

    // A logged path the OS rejects outright (embedded NUL) is refused like a missing one.
    let nul_path = Path::new("/tmp/netsim\0.ini");
    assert!(!can_adopt_logged_ini_path_with(nul_path, true, Some(uid), Some(uid)));
    assert!(!is_real_dir(nul_path));

    // Non-dumpable (`PR_SET_DUMPABLE=0`) and `seteuid(0)` status files resolve true real UID,
    // never returning `0` when a `stat("/proc/self")` fallback would report root (`0`).
    let status_file = nested.join("proc_self_status");
    std::fs::write(
        &status_file,
        "Name:\temulator_mon\nDumpable:\t0\nUid:\t1000\t0\t0\t0\nGid:\t1000\t0\t0\t0\n",
    )
    .unwrap();
    assert_eq!(resolve_owner_uid_from(&status_file, || Some(0)), Some(1000));
    assert_eq!(resolve_owner_uid_from(&nested.join("missing_status"), || Some(501)), Some(501));
    assert_eq!(current_uid(), Some(uid));
}

#[test]
#[cfg(unix)]
fn test_resolve_trusted_dir_checks_every_directory_the_resolution_enters() {
    use std::collections::HashMap;
    use std::path::PathBuf;

    const UID: u32 = 1000;
    const OTHER: u32 = 2000;
    let dir = |uid, mode| (DirNodeMeta { is_dir: true, is_symlink: false, uid, mode }, None);
    let link = |uid, target: &'static str| {
        (DirNodeMeta { is_dir: false, is_symlink: true, uid, mode: 0o120777 }, Some(target))
    };
    // `/tmp/ww` belongs to someone else, so whatever is under it can be swapped. `leaf` and
    // the link `out` inside it are the user's own.
    let build = |ww_owner: u32| -> HashMap<&'static str, (DirNodeMeta, Option<&'static str>)> {
        HashMap::from([
            ("/", dir(0, 0o755)),
            ("/tmp", dir(0, 0o1777)),
            ("/tmp/safe", dir(UID, 0o700)),
            ("/tmp/safe/sub", dir(UID, 0o700)),
            ("/tmp/ww", dir(ww_owner, 0o755)),
            ("/tmp/ww/leaf", dir(UID, 0o755)),
            ("/tmp/ww/leaf/out", link(UID, "/tmp/safe")),
            ("/tmp/in", link(UID, "/tmp/ww/leaf")),
            ("/tmp/via", link(UID, "ww/leaf/out")),
            ("/tmp/up", link(UID, "safe/sub/../../safe")),
            ("/tmp/loop", link(UID, "loop")),
            ("/tmp/foreign_link", link(OTHER, "safe")),
            ("/tmp/root_link", link(0, "safe")),
            (
                "/tmp/file",
                (DirNodeMeta { is_dir: false, is_symlink: false, uid: UID, mode: 0o644 }, None),
            ),
        ])
    };
    let resolve = |fs: &HashMap<&'static str, (DirNodeMeta, Option<&'static str>)>,
                   path: &str,
                   expected: Option<u32>|
     -> Option<PathBuf> {
        let lookup = |p: &Path| {
            p.to_str()
                .and_then(|s| fs.get(s))
                .ok_or_else(|| std::io::Error::from(std::io::ErrorKind::NotFound))
        };
        resolve_trusted_dir_with(
            Path::new(path),
            expected,
            0,
            |p| lookup(p).map(|(meta, _)| *meta),
            |p| {
                lookup(p)?
                    .1
                    .map(PathBuf::from)
                    .ok_or_else(|| std::io::Error::other("not a symlink"))
            },
        )
    };
    let safe = Some(PathBuf::from("/tmp/safe"));

    let fs = build(OTHER);
    // Both end in the user's own `/tmp/safe`, but get there through `/tmp/ww`.
    assert_eq!(resolve(&fs, "/tmp/in/out", Some(UID)), None);
    assert_eq!(resolve(&fs, "/tmp/via", Some(UID)), None);
    assert_eq!(resolve(&fs, "/tmp/ww/leaf", Some(UID)), None);
    // The same paths are fine once `/tmp/ww` is the user's too.
    let own = build(UID);
    assert_eq!(resolve(&own, "/tmp/in/out", Some(UID)), safe);
    assert_eq!(resolve(&own, "/tmp/via", Some(UID)), safe);
    assert_eq!(resolve(&own, "/tmp/in", Some(UID)), Some(PathBuf::from("/tmp/ww/leaf")));

    // `..` inside a link target steps back to a directory accepted earlier.
    assert_eq!(resolve(&fs, "/tmp/up", Some(UID)), safe);
    assert_eq!(resolve(&fs, "/tmp/safe/sub/../../safe", Some(UID)), None);
    assert_eq!(resolve(&fs, "tmp/safe", Some(UID)), None);
    // A link is followed only if the expected user or root owns it.
    assert_eq!(resolve(&fs, "/tmp/foreign_link", Some(UID)), None);
    assert_eq!(resolve(&fs, "/tmp/root_link", Some(UID)), safe);
    assert_eq!(resolve(&fs, "/tmp/root_link", None), safe);
    assert_eq!(resolve(&fs, "/tmp/up", None), None);
    // Loops, files and missing entries end the resolution.
    assert_eq!(resolve(&fs, "/tmp/loop", Some(UID)), None);
    assert_eq!(resolve(&fs, "/tmp/file", Some(UID)), None);
    assert_eq!(resolve(&fs, "/tmp/missing", Some(UID)), None);
    assert_eq!(resolve(&fs, "/", Some(UID)), Some(PathBuf::from("/")));
}

#[test]
#[cfg(unix)]
fn test_trusted_ancestors_follow_symlink_targets_on_the_real_filesystem() {
    use std::os::unix::fs::{PermissionsExt, symlink};

    let uid = owner_uid().expect("owner_uid must resolve on Unix");
    let tmp = tempfile::Builder::new().prefix("erm_res_").tempdir_in("/tmp").unwrap();
    let root = tmp.path();
    let set_mode = |p: &Path, mode: u32| {
        std::fs::set_permissions(p, std::fs::Permissions::from_mode(mode)).unwrap();
    };
    set_mode(root, 0o755);
    let safe = root.join("safe");
    let open_dir = root.join("open");
    let leaf = open_dir.join("leaf");
    std::fs::create_dir(&safe).unwrap();
    std::fs::create_dir_all(&leaf).unwrap();
    std::fs::write(safe.join("netsim.ini"), b"grpc.port=43929\n").unwrap();
    symlink("../../safe", leaf.join("out")).unwrap();
    symlink("open/leaf", root.join("in")).unwrap();
    symlink("open/leaf/out", root.join("via")).unwrap();
    symlink("loop", root.join("loop")).unwrap();
    set_mode(&safe, 0o755);
    set_mode(&leaf, 0o755);

    // `open` is world-writable without the sticky bit: anyone may replace `leaf`.
    set_mode(&open_dir, 0o777);
    for path in [root.join("in").join("out"), root.join("via")] {
        assert!(!has_trusted_ancestors(&path, Some(uid), 0), "{}", path.display());
        assert!(!is_trusted_ini_path_with(&path.join("netsim.ini"), uid, 0));
    }
    assert!(!has_trusted_ancestors(&root.join("loop"), Some(uid), 0));

    // With the sticky bit nobody else can replace `leaf`, and both resolve to `safe`.
    set_mode(&open_dir, 0o1777);
    for path in [root.join("in").join("out"), root.join("via")] {
        assert!(has_trusted_ancestors(&path, Some(uid), 0), "{}", path.display());
        assert!(is_trusted_ini_path_with(&path.join("netsim.ini"), uid, 0));
    }
    assert_eq!(
        trusted_canonical_dir(&root.join("via"), Some(uid), 0),
        Some(std::fs::canonicalize(&safe).unwrap())
    );
}
