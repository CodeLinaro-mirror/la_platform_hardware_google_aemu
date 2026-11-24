use super::*;

#[test]
fn test_run_cli_with_timeout() {
    let limit = Duration::from_secs(5);
    assert_eq!(run_cli_with_timeout("sh", &["-c", "echo hi"], limit).as_deref(), Some("hi\n"));
    // The exit status is ignored.
    assert_eq!(
        run_cli_with_timeout("sh", &["-c", "echo out; exit 3"], limit).as_deref(),
        Some("out\n")
    );
    assert_eq!(run_cli_with_timeout("/nonexistent/netstat", &[], limit), None);
    // A hung tool is killed at the deadline.
    let started = std::time::Instant::now();
    let hung = run_cli_with_timeout("sh", &["-c", "exec sleep 5"], Duration::from_millis(200));
    assert_eq!(hung, None);
    assert!(started.elapsed() < Duration::from_secs(3), "took {:?}", started.elapsed());
}

#[test]
fn test_run_cli_output_with_timeout_captures_stderr_and_kills_slow_process() {
    // Verifies stdout+stderr capture and timeout termination for default_cli_runner's helper.
    let ok_res = run_cli_output_with_timeout(
        "sh",
        &["-c", "printf 'out'; printf 'err' >&2"],
        Duration::from_secs(2),
    );
    assert_eq!(ok_res, Some(("out".to_string(), "err".to_string())));

    let timed_out =
        run_cli_output_with_timeout("sh", &["-c", "sleep 30"], Duration::from_millis(50));
    assert_eq!(timed_out, None);
}

#[test]
fn test_run_with_timeout_preserves_exit_status_after_early_stdio_close() {
    let out = run_with_timeout(
        "sh",
        &["-c", "printf '472823\\n'; exec >&- 2>&-; sleep 0.02; exit 0"],
        Duration::from_secs(2),
    )
    .expect("process exited well within timeout");
    assert!(out.status.success(), "unexpected exit status: {:?}", out.status);
    assert_eq!(out.stdout, b"472823\n");

    // A child that closes stdio early and hangs past the deadline is still killed.
    let started = std::time::Instant::now();
    let hung =
        run_with_timeout("sh", &["-c", "exec >&- 2>&-; sleep 5"], Duration::from_millis(100));
    assert!(hung.is_none());
    assert!(started.elapsed() < Duration::from_secs(3));
}
