#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <string>
#include <vector>

#if defined(_WIN32)
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <io.h>
#include <process.h>
#ifndef PATH_MAX
#define PATH_MAX MAX_PATH
#endif
static const char kPathListSep = ';';
#else
#include <unistd.h>
#include <libgen.h>
#include <limits.h>
#include <sys/wait.h>
static const char kPathListSep = ':';
#endif

#if defined(__APPLE__)
#include <mach-o/dyld.h>
#endif

static bool file_exists(const std::string &path) {
    if (path.empty()) return false;
#if defined(_WIN32)
    DWORD attr = GetFileAttributesA(path.c_str());
    return (attr != INVALID_FILE_ATTRIBUTES) && !(attr & FILE_ATTRIBUTE_DIRECTORY);
#else
    return access(path.c_str(), F_OK) == 0;
#endif
}

static bool is_executable_file(const std::string &path) {
    if (path.empty()) return false;
#if defined(_WIN32)
    return file_exists(path);
#else
    return access(path.c_str(), X_OK) == 0;
#endif
}

static void set_env_var(const char *name, const std::string &value) {
#if defined(_WIN32)
    _putenv_s(name, value.c_str());
    SetEnvironmentVariableA(name, value.c_str());
#else
    setenv(name, value.c_str(), 1);
#endif
}

std::string get_executable_path() {
#if defined(_WIN32)
    char path[32768];
    DWORD len = GetModuleFileNameA(NULL, path, sizeof(path));
    if (len > 0 && len < sizeof(path)) {
        return std::string(path, len);
    }
#elif defined(__APPLE__)
    char path[PATH_MAX];
    uint32_t size = sizeof(path);
    if (_NSGetExecutablePath(path, &size) == 0) {
        char real_path[PATH_MAX];
        if (realpath(path, real_path) != NULL) {
            return std::string(real_path);
        }
        return std::string(path);
    }
#else
    char path[PATH_MAX];
    ssize_t count = readlink("/proc/self/exe", path, PATH_MAX - 1);
    if (count != -1) {
        path[count] = '\0';
        return std::string(path);
    }
#endif
    return "";
}

std::string get_directory(const std::string &filepath) {
#if defined(_WIN32)
    size_t pos = filepath.find_last_of("/\\");
    if (pos == std::string::npos) return ".";
    if (pos == 0) return filepath.substr(0, 1);
    if (pos == 2 && filepath[1] == ':') return filepath.substr(0, 3);
    return filepath.substr(0, pos);
#else
    char tmp[PATH_MAX];
    strncpy(tmp, filepath.c_str(), sizeof(tmp) - 1);
    tmp[sizeof(tmp) - 1] = '\0';
    return std::string(dirname(tmp));
#endif
}

static std::string find_executable_in_path(const std::string &name) {
    const char *path_env = getenv("PATH");
    if (!path_env) return "";
    std::string path_str(path_env);
    size_t start = 0;
    while (start < path_str.size()) {
        size_t end = path_str.find(kPathListSep, start);
        if (end == std::string::npos) end = path_str.size();
        std::string dir = path_str.substr(start, end - start);
        if (!dir.empty()) {
#if defined(_WIN32)
            if (dir.find("WindowsApps") == std::string::npos) {
                std::string full_path = dir + "\\" + name;
                if (is_executable_file(full_path)) {
                    return full_path;
                }
                if (name.size() < 4 || name.substr(name.size() - 4) != ".exe") {
                    std::string exe_path = full_path + ".exe";
                    if (is_executable_file(exe_path)) {
                        return exe_path;
                    }
                }
            }
#else
            std::string full_path = dir + "/" + name;
            if (is_executable_file(full_path)) {
                return full_path;
            }
#endif
        }
        start = end + 1;
    }
    return "";
}

#if defined(_WIN32)
static std::string quote_win32_arg(const std::string &arg) {
    if (!arg.empty() && arg.find_first_of(" \t\n\v\"") == std::string::npos) {
        return arg;
    }
    std::string quoted = "\"";
    for (size_t i = 0; i < arg.size(); ++i) {
        size_t num_backslashes = 0;
        while (i < arg.size() && arg[i] == '\\') {
            ++i;
            ++num_backslashes;
        }
        if (i == arg.size()) {
            quoted.append(num_backslashes * 2, '\\');
            break;
        } else if (arg[i] == '"') {
            quoted.append(num_backslashes * 2 + 1, '\\');
            quoted.push_back('"');
        } else {
            quoted.append(num_backslashes, '\\');
            quoted.push_back(arg[i]);
        }
    }
    quoted.push_back('"');
    return quoted;
}
#endif

static bool is_python_at_least_3_10(const std::string &python_path) {
    if (!is_executable_file(python_path)) return false;

#if defined(_WIN32)
    if (python_path.find("WindowsApps") != std::string::npos) {
        return false;
    }
    SECURITY_ATTRIBUTES sa = {};
    sa.nLength = sizeof(sa);
    sa.bInheritHandle = TRUE;
    HANDLE h_nul = CreateFileA(
        "NUL", GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE,
        &sa, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    STARTUPINFOA si = {};
    si.cb = sizeof(si);
    if (h_nul != INVALID_HANDLE_VALUE) {
        si.dwFlags = STARTF_USESTDHANDLES;
        si.hStdInput = GetStdHandle(STD_INPUT_HANDLE);
        si.hStdOutput = h_nul;
        si.hStdError = h_nul;
    }
    PROCESS_INFORMATION pi = {};
    std::string cmd_line = quote_win32_arg(python_path) +
        " -c \"import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)\"";
    std::vector<char> cmd_buf(cmd_line.begin(), cmd_line.end());
    cmd_buf.push_back('\0');
    BOOL ok = CreateProcessA(
        python_path.c_str(), cmd_buf.data(), NULL, NULL,
        h_nul != INVALID_HANDLE_VALUE ? TRUE : FALSE,
        CREATE_NO_WINDOW, NULL, NULL, &si, &pi);
    if (h_nul != INVALID_HANDLE_VALUE) {
        CloseHandle(h_nul);
    }
    if (!ok) {
        return false;
    }
    WaitForSingleObject(pi.hProcess, 10000);
    DWORD exit_code = 1;
    GetExitCodeProcess(pi.hProcess, &exit_code);
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    return exit_code == 0;
#else
    pid_t pid = fork();
    if (pid < 0) {
        return false;
    }
    if (pid == 0) {
        int devnull = open("/dev/null", O_WRONLY);
        if (devnull >= 0) {
            dup2(devnull, STDOUT_FILENO);
            dup2(devnull, STDERR_FILENO);
            close(devnull);
        }
        char *const argv[] = {
            const_cast<char*>(python_path.c_str()),
            const_cast<char*>("-c"),
            const_cast<char*>("import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"),
            nullptr
        };
        execv(python_path.c_str(), argv);
        _exit(127);
    }

    int status = 0;
    if (waitpid(pid, &status, 0) < 0) {
        return false;
    }
    return WIFEXITED(status) && WEXITSTATUS(status) == 0;
#endif
}

static std::string resolve_python_bin(const std::string &lib_dir, const std::string &exe_path) {
#if defined(_WIN32)
    // 1. Embedded python runtime in release lib/ directory or Bazel runfiles
    std::vector<std::string> embedded_candidates = {
        lib_dir + "\\python_runtime\\python.exe",
        lib_dir + "\\python3.exe",
        lib_dir + "\\python.exe",
    };
    if (!exe_path.empty()) {
        embedded_candidates.push_back(
            exe_path + ".runfiles\\rules_python++python+python_3_10_x86_64-pc-windows-msvc\\python.exe");
    }
    const char *local_app_data = getenv("LOCALAPPDATA");
    if (local_app_data && local_app_data[0] != '\0') {
        embedded_candidates.push_back(
            std::string(local_app_data) + "\\Google\\EmuDevCLI\\lib\\python_runtime\\python.exe");
    }
    for (const std::string &cand : embedded_candidates) {
        if (is_python_at_least_3_10(cand)) {
            return cand;
        }
    }

    // 2. Python from user's active PATH
    const char *path_names[] = {"python3.exe", "python.exe", nullptr};
    for (int i = 0; path_names[i] != nullptr; ++i) {
        std::string path_python = find_executable_in_path(path_names[i]);
        if (is_python_at_least_3_10(path_python)) {
            return path_python;
        }
    }

    // 3. Common Windows Python installation directories
    const char *win_candidates[] = {
        "C:\\Program Files\\Python315\\python.exe",
        "C:\\Program Files\\Python314\\python.exe",
        "C:\\Program Files\\Python313\\python.exe",
        "C:\\Program Files\\Python312\\python.exe",
        "C:\\Program Files\\Python311\\python.exe",
        "C:\\Program Files\\Python310\\python.exe",
        nullptr
    };
    for (int i = 0; win_candidates[i] != nullptr; ++i) {
        std::string candidate(win_candidates[i]);
        if (is_python_at_least_3_10(candidate)) {
            return candidate;
        }
    }
    if (local_app_data && local_app_data[0] != '\0') {
        const char *vers[] = {"315", "314", "313", "312", "311", "310", nullptr};
        for (int i = 0; vers[i] != nullptr; ++i) {
            std::string candidate = std::string(local_app_data) +
                "\\Programs\\Python\\Python" + vers[i] + "\\python.exe";
            if (is_python_at_least_3_10(candidate)) {
                return candidate;
            }
        }
    }
    return "";
#else
    (void)exe_path;
    // 1. Embedded python in release lib/ directory
    std::string embedded_python = lib_dir + "/python3";
    if (is_python_at_least_3_10(embedded_python)) {
        return embedded_python;
    }

    // 2. Python3 from user's active PATH
    std::string path_python = find_executable_in_path("python3");
    if (is_python_at_least_3_10(path_python)) {
        return path_python;
    }

    // 3. Versioned python binaries in PATH (e.g. python3.13, python3.12, python3.11, python3.10)
    const char *versioned_names[] = {
        "python3.15", "python3.14", "python3.13", "python3.12", "python3.11", "python3.10", nullptr
    };
    for (int i = 0; versioned_names[i] != nullptr; ++i) {
        std::string candidate = find_executable_in_path(versioned_names[i]);
        if (is_python_at_least_3_10(candidate)) {
            return candidate;
        }
    }

    // 4. Common platform search locations
    const char *candidates[] = {
        "/opt/homebrew/bin/python3",
        "/opt/homebrew/bin/python3.13",
        "/opt/homebrew/bin/python3.12",
        "/opt/homebrew/bin/python3.11",
        "/opt/homebrew/bin/python3.10",
        "/usr/local/bin/python3",
        "/usr/local/bin/python3.13",
        "/usr/local/bin/python3.12",
        "/usr/local/bin/python3.11",
        "/usr/local/bin/python3.10",
        "/usr/bin/python3",
        nullptr
    };
    for (int i = 0; candidates[i] != nullptr; ++i) {
        std::string candidate(candidates[i]);
        if (is_python_at_least_3_10(candidate)) {
            return candidate;
        }
    }

    return "";
#endif
}

int main(int argc, char *argv[]) {
    std::string exe_path = get_executable_path();
    std::string exe_dir = exe_path.empty() ? "." : get_directory(exe_path);
    std::string sep_str(1, kPathListSep);

    // Release layout: emu-dev-cli/ (binary) with lib/ containing compiled .pyc modules
    std::string lib_dir = exe_dir + "/lib";
    std::string main_pyc = lib_dir + "/__main__.pyc";
    std::string main_py = lib_dir + "/__main__.py";
    std::string direct_pyc = exe_dir + "/__main__.pyc";
    std::string direct_py = exe_dir + "/__main__.py";

    // Lookups: check Bazel runfiles and relative source paths
    std::string aemu_runfiles_root = exe_path + ".runfiles/aemu/tools/emu-dev-cli";
    std::string aemu_runfiles_main = aemu_runfiles_root + "/src/__main__.py";
    std::string aemu_runfiles_src = aemu_runfiles_root + "/src" + sep_str + aemu_runfiles_root;
    std::string aemu_bzlmod_root = exe_path + ".runfiles/aemu+/tools/emu-dev-cli";
    std::string aemu_bzlmod_runfiles_main = aemu_bzlmod_root + "/src/__main__.py";
    std::string aemu_bzlmod_runfiles_src = aemu_bzlmod_root + "/src" + sep_str + aemu_bzlmod_root;
    std::string aemu_plus_root = exe_path + ".runfiles/_main/external/aemu+/tools/emu-dev-cli";
    std::string aemu_plus_runfiles_main = aemu_plus_root + "/src/__main__.py";
    std::string aemu_plus_runfiles_src = aemu_plus_root + "/src" + sep_str + aemu_plus_root;

    std::string target_script;
    std::string pythonpath_dir = lib_dir;

    if (file_exists(main_py)) {
        target_script = main_py;
        pythonpath_dir = lib_dir;
    } else if (file_exists(main_pyc)) {
        target_script = main_pyc;
        pythonpath_dir = lib_dir;
    } else if (file_exists(direct_py)) {
        target_script = direct_py;
        pythonpath_dir = exe_dir;
    } else if (file_exists(direct_pyc)) {
        target_script = direct_pyc;
        pythonpath_dir = exe_dir;
    } else if (file_exists(aemu_runfiles_main)) {
        target_script = aemu_runfiles_main;
        pythonpath_dir = aemu_runfiles_src;
    } else if (file_exists(aemu_bzlmod_runfiles_main)) {
        target_script = aemu_bzlmod_runfiles_main;
        pythonpath_dir = aemu_bzlmod_runfiles_src;
    } else if (file_exists(aemu_plus_runfiles_main)) {
        target_script = aemu_plus_runfiles_main;
        pythonpath_dir = aemu_plus_runfiles_src;
    } else {
        // Walk up from exe_dir searching for hardware/google/aemu/tools/emu-dev-cli/src/__main__.py
        std::string curr = exe_dir;
        bool found = false;
        for (int i = 0; i < 7; ++i) {
            std::string cand_root = curr + "/hardware/google/aemu/tools/emu-dev-cli";
            std::string cand = cand_root + "/src/__main__.py";
            std::string cand_src = cand_root + "/src" + sep_str + cand_root;
            if (file_exists(cand)) {
                target_script = cand;
                pythonpath_dir = cand_src;
                found = true;
                break;
            }
            std::string parent = get_directory(curr);
            if (parent == curr || parent.empty()) break;
            curr = parent;
        }
        if (!found) {
            fprintf(stderr, "Error: Could not locate emu-dev-cli release runtime.\n");
            return 1;
        }
    }

    std::string python_bin = resolve_python_bin(lib_dir, exe_path);
    if (python_bin.empty()) {
        fprintf(stderr,
            "Error: emu-dev-cli requires Python 3.10 or newer (matching Bazel hermetic runtime).\n"
            "No compatible Python 3.10+ interpreter was found in PATH or standard locations.\n"
            "Please install or make Python 3.10+ available in your PATH (e.g. via 'googet install python3' on Windows, 'brew install python@3.11' on macOS, or 'apt install python3' on Linux).\n");
        return 1;
    }

    // Prepend python binary directory to PATH so child processes invoking python3/python find 3.10+
    std::string python_dir = get_directory(python_bin);
    if (!python_dir.empty()) {
        const char *current_path = getenv("PATH");
        std::string new_path = python_dir;
        if (current_path && current_path[0] != '\0') {
            new_path += sep_str + std::string(current_path);
        }
        set_env_var("PATH", new_path);
    }

    // Set PYTHONPATH to pythonpath_dir (e.g. emu-dev-cli/lib/)
    std::string existing_pythonpath = getenv("PYTHONPATH") ? getenv("PYTHONPATH") : "";
    std::string new_pythonpath = pythonpath_dir;
    if (!existing_pythonpath.empty()) {
        new_pythonpath += sep_str + existing_pythonpath;
    }
    set_env_var("PYTHONPATH", new_pythonpath);
    if (!exe_path.empty()) {
        set_env_var("EMU_DEV_CLI_LAUNCHER_EXE", exe_path);
    }

#if defined(_WIN32)
    std::string cmd_line = quote_win32_arg(python_bin) + " " + quote_win32_arg(target_script);
    for (int i = 1; i < argc; ++i) {
        cmd_line += " " + quote_win32_arg(argv[i]);
    }
    std::vector<char> cmd_buf(cmd_line.begin(), cmd_line.end());
    cmd_buf.push_back('\0');

    STARTUPINFOA si = {};
    si.cb = sizeof(si);
    PROCESS_INFORMATION pi = {};
    SetConsoleCtrlHandler(NULL, TRUE);
    if (!CreateProcessA(
            python_bin.c_str(), cmd_buf.data(), NULL, NULL, TRUE,
            0, NULL, NULL, &si, &pi)) {
        fprintf(stderr, "Error: CreateProcess failed to launch emu-dev-cli backend (%lu)\n",
                GetLastError());
        return 1;
    }
    WaitForSingleObject(pi.hProcess, INFINITE);
    DWORD exit_code = 1;
    GetExitCodeProcess(pi.hProcess, &exit_code);
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    return static_cast<int>(exit_code);
#else
    std::vector<char*> new_argv;
    new_argv.push_back(const_cast<char*>(python_bin.c_str()));
    new_argv.push_back(const_cast<char*>(target_script.c_str()));
    for (int i = 1; i < argc; ++i) {
        new_argv.push_back(argv[i]);
    }
    new_argv.push_back(NULL);

    execv(python_bin.c_str(), new_argv.data());

    perror("execv failed to launch emu-dev-cli backend");
    return 1;
#endif
}
