# DESIGN: Secure Execution of CrashAdvisor Investigation Scripts

## 1. Context & Security Finding
During presubmit scanning, Warden flagged a **Major** security vulnerability in `emu-dev-cli crash`:
> **Insecure execution of a script from a shared temporary directory.**
> The tool resolves the sandbox directory to a predictable path under `/tmp` and executes `investigation_cmd.sh` from it. A local attacker can pre-create this directory or exploit a race condition to inject malicious commands, leading to arbitrary code execution with the victim's privileges.

## 2. Threat Model & Risk Analysis
- **Attacker Goal**: Pre-create or modify `/tmp/crashadvisor_<user>/<crash_id>/investigation_cmd.sh` to execute arbitrary bash commands with the victim's UID.
- **Vectors**:
  - Symlink redirection pointing `investigation_cmd.sh` to system files or attacker-controlled files.
  - Group or world-writable directory permissions allowing non-owners to modify `investigation_cmd.sh`.
  - Non-owner UIDs owning the sandbox directory or script.

## 3. Proposed Remediation Strategy
Implement strict pre-execution path validation in `hardware/google/aemu/tools/emu-dev-cli/src/commands/crash.py`:

### A. Path Security Validator (`is_path_secure_user_owned`)
```python
def is_path_secure_user_owned(path: str, is_dir: bool = False) -> bool:
    """Verifies that a path exists, is not a symlink, is owned by the current user,
    and is not writable by group or others."""
    try:
        if os.path.islink(path):
            return False
        st = os.stat(path)
        if is_dir and not stat.S_ISDIR(st.st_mode):
            return False
        if not is_dir and not stat.S_ISREG(st.st_mode):
            return False
        if st.st_uid != os.getuid():
            return False
        if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            return False
        return True
    except OSError:
        return False
```

### B. Pre-Execution Enforcement in `crash.py`
Before running `subprocess.run(["bash", script_path])`:
1. Check `is_path_secure_user_owned(sandbox_dir, is_dir=True)`.
2. Check `is_path_secure_user_owned(script_path, is_dir=False)`.
3. If valid, set permissions to `0o700` (`rwx------`) and execute.
4. If invalid, output a security warning to `sys.stderr` and abort execution of the script.

### C. Unit Testing
Add unit tests in `tests/emu_dev_cli_test.py` covering:
- Secure user-owned regular file (`0o700`, `0o755`) -> `True`
- World-writable or group-writable file (`0o777`, `0o775`) -> `False`
- Symlinks -> `False`
- Directory vs File mode checks -> `False` if mismatched
- Non-existent path -> `False`

## 4. Verification Plan
- Run Bazel / Python unit tests: `python3 -m unittest hardware/google/aemu/tools/emu-dev-cli/tests/emu_dev_cli_test.py`
- Perform dry-run and validation check with `emu-dev-cli crash analyze`.
