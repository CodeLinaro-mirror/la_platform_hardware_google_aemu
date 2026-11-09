use std::ffi::OsStr;
use std::time::Duration;

/// Runs `program` and returns its output, or `None` if it cannot start or does
/// not finish within `timeout`, in which case it is killed.
pub(crate) fn run_with_timeout(
    program: &str,
    args: &[&str],
    timeout: Duration,
) -> Option<std::process::Output> {
    run_with_timeout_and_env(program, args, &[], timeout)
}

pub(crate) fn run_with_timeout_and_env(
    program: &str,
    args: &[&str],
    env: &[(&str, &OsStr)],
    timeout: Duration,
) -> Option<std::process::Output> {
    use std::io::Read;
    let mut cmd = std::process::Command::new(program);
    cmd.args(args)
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped());
    for &(k, v) in env {
        cmd.env(k, v);
    }
    let start = std::time::Instant::now();
    let deadline = start.checked_add(timeout);
    let mut child = cmd.spawn().ok()?;
    let mut stdout = child.stdout.take()?;
    let mut stderr = child.stderr.take()?;
    let (tx, rx) = std::sync::mpsc::channel();
    std::thread::spawn(move || {
        let err_handle = std::thread::spawn(move || {
            let mut buf = Vec::new();
            let _ = stderr.read_to_end(&mut buf);
            buf
        });
        let mut out_buf = Vec::new();
        let _ = stdout.read_to_end(&mut out_buf);
        let err_buf = err_handle.join().unwrap_or_default();
        let _ = tx.send((out_buf, err_buf));
    });
    let Some((stdout, stderr)) = rx.recv_timeout(timeout).ok() else {
        let _ = child.kill();
        let _ = child.wait();
        return None;
    };
    // A child can close stdout/stderr in user space (`atexit(close_stdout)`)
    // just before `_exit(0)`, so wait up to the remaining deadline before
    // falling back to SIGKILL.
    loop {
        match child.try_wait() {
            Ok(Some(status)) => return Some(std::process::Output { status, stdout, stderr }),
            Ok(None) => {
                let now = std::time::Instant::now();
                if deadline.is_some_and(|dl| now >= dl) {
                    let _ = child.kill();
                    let _ = child.wait();
                    return None;
                }
                let step = deadline
                    .map(|dl| dl.saturating_duration_since(now).min(Duration::from_millis(1)))
                    .unwrap_or(Duration::from_millis(1));
                std::thread::sleep(step);
            }
            Err(_) => {
                let _ = child.kill();
                let _ = child.wait();
                return None;
            }
        }
    }
}

/// Like `run_with_timeout`, returning `(stdout, stderr)` as text and ignoring
/// the exit status.
#[cfg(all(test, unix))]
pub(super) fn run_cli_output_with_timeout(
    program: &str,
    args: &[&str],
    timeout: Duration,
) -> Option<(String, String)> {
    run_cli_output_with_timeout_and_env(program, args, &[], timeout)
}

pub(super) fn run_cli_output_with_timeout_and_env(
    program: &str,
    args: &[&str],
    env: &[(&str, &OsStr)],
    timeout: Duration,
) -> Option<(String, String)> {
    run_with_timeout_and_env(program, args, env, timeout).map(|out| {
        (
            String::from_utf8_lossy(&out.stdout).into_owned(),
            String::from_utf8_lossy(&out.stderr).into_owned(),
        )
    })
}

/// Runs `program` and returns its stdout (exit status ignored), or None if it
/// cannot start or does not finish within `timeout`, in which case it is
/// killed.
pub(super) fn run_cli_with_timeout(
    program: &str,
    args: &[&str],
    timeout: Duration,
) -> Option<String> {
    run_cli_output_with_timeout_and_env(program, args, &[], timeout).map(|(stdout, _)| stdout)
}

#[cfg(all(test, unix))]
mod tests {
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
}
