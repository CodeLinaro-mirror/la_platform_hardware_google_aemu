#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>
#include <unistd.h>
#include <libgen.h>
#include <limits.h>

#if defined(__APPLE__)
#include <mach-o/dyld.h>
#endif

std::string get_executable_path() {
    char path[PATH_MAX];
#if defined(__APPLE__)
    uint32_t size = sizeof(path);
    if (_NSGetExecutablePath(path, &size) == 0) {
        char real_path[PATH_MAX];
        if (realpath(path, real_path) != NULL) {
            return std::string(real_path);
        }
        return std::string(path);
    }
#else
    ssize_t count = readlink("/proc/self/exe", path, PATH_MAX - 1);
    if (count != -1) {
        path[count] = '\0';
        return std::string(path);
    }
#endif
    return "";
}

std::string get_directory(const std::string &filepath) {
    char tmp[PATH_MAX];
    strncpy(tmp, filepath.c_str(), sizeof(tmp) - 1);
    tmp[sizeof(tmp) - 1] = '\0';
    return std::string(dirname(tmp));
}

int main(int argc, char *argv[]) {
    std::string exe_path = get_executable_path();
    std::string exe_dir = exe_path.empty() ? "." : get_directory(exe_path);

    // Release layout: emu-dev-cli/ (binary) with lib/ containing compiled .pyc modules
    std::string lib_dir = exe_dir + "/lib";
    std::string main_pyc = lib_dir + "/__main__.pyc";
    std::string main_py = lib_dir + "/__main__.py";
    std::string direct_pyc = exe_dir + "/__main__.pyc";
    std::string direct_py = exe_dir + "/__main__.py";

    // Lookups: check Bazel runfiles and relative source paths
    std::string aemu_runfiles_root = exe_path + ".runfiles/aemu/tools/emu-dev-cli";
    std::string aemu_runfiles_main = aemu_runfiles_root + "/src/__main__.py";
    std::string aemu_runfiles_src = aemu_runfiles_root + "/src:" + aemu_runfiles_root;
    std::string aemu_bzlmod_root = exe_path + ".runfiles/aemu+/tools/emu-dev-cli";
    std::string aemu_bzlmod_runfiles_main = aemu_bzlmod_root + "/src/__main__.py";
    std::string aemu_bzlmod_runfiles_src = aemu_bzlmod_root + "/src:" + aemu_bzlmod_root;
    std::string aemu_plus_root = exe_path + ".runfiles/_main/external/aemu+/tools/emu-dev-cli";
    std::string aemu_plus_runfiles_main = aemu_plus_root + "/src/__main__.py";
    std::string aemu_plus_runfiles_src = aemu_plus_root + "/src:" + aemu_plus_root;

    std::string target_script;
    std::string pythonpath_dir = lib_dir;

    if (access(main_py.c_str(), F_OK) == 0) {
        target_script = main_py;
        pythonpath_dir = lib_dir;
    } else if (access(main_pyc.c_str(), F_OK) == 0) {
        target_script = main_pyc;
        pythonpath_dir = lib_dir;
    } else if (access(direct_py.c_str(), F_OK) == 0) {
        target_script = direct_py;
        pythonpath_dir = exe_dir;
    } else if (access(direct_pyc.c_str(), F_OK) == 0) {
        target_script = direct_pyc;
        pythonpath_dir = exe_dir;
    } else if (access(aemu_runfiles_main.c_str(), F_OK) == 0) {
        target_script = aemu_runfiles_main;
        pythonpath_dir = aemu_runfiles_src;
    } else if (access(aemu_bzlmod_runfiles_main.c_str(), F_OK) == 0) {
        target_script = aemu_bzlmod_runfiles_main;
        pythonpath_dir = aemu_bzlmod_runfiles_src;
    } else if (access(aemu_plus_runfiles_main.c_str(), F_OK) == 0) {
        target_script = aemu_plus_runfiles_main;
        pythonpath_dir = aemu_plus_runfiles_src;
    } else {
        // Walk up from exe_dir searching for hardware/google/aemu/tools/emu-dev-cli/src/__main__.py
        std::string curr = exe_dir;
        bool found = false;
        for (int i = 0; i < 7; ++i) {
            std::string cand_root = curr + "/hardware/google/aemu/tools/emu-dev-cli";
            std::string cand = cand_root + "/src/__main__.py";
            std::string cand_src = cand_root + "/src:" + cand_root;
            if (access(cand.c_str(), F_OK) == 0) {
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

    std::string python_bin = "/usr/bin/python3";
    std::string embedded_python = lib_dir + "/python3";
    if (access(embedded_python.c_str(), X_OK) == 0) {
        python_bin = embedded_python;
    }

    // Set PYTHONPATH to pythonpath_dir (e.g. emu-dev-cli/lib/)
    std::string existing_pythonpath = getenv("PYTHONPATH") ? getenv("PYTHONPATH") : "";
    std::string new_pythonpath = pythonpath_dir;
    if (!existing_pythonpath.empty()) {
        new_pythonpath += ":" + existing_pythonpath;
    }
    setenv("PYTHONPATH", new_pythonpath.c_str(), 1);
    if (!exe_path.empty()) {
        setenv("EMU_DEV_CLI_LAUNCHER_EXE", exe_path.c_str(), 1);
    }

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
}
