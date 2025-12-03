// Copyright 2025 The Android Open Source Project
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
// http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
#include <iostream>
#include <string>
#include <vector>
#include <memory>
#include <windows.h>
#include <RestartManager.h>

// Link against the Restart Manager library
#pragma comment(lib, "Rstrtmgr.lib")
#pragma comment(linker, "/SUBSYSTEM:CONSOLE /ENTRY:wmainCRTStartup")

void PrintError(const std::string& functionName, DWORD errorCode) {
    std::cerr << "Error in " << functionName << ". Code: " << errorCode << std::endl;
}

int wmain(int argc, wchar_t* argv[]) {
    if (argc != 2) {
        std::wcout << L"Usage: " << argv[0] << L" <directory_path>" << std::endl;
        return 1;
    }

    const wchar_t* targetPath = argv[1];
    std::wcout << L"Searching for processes with open handles in: " << targetPath << std::endl;

    DWORD dwSession;
    WCHAR szSessionKey[CCH_RM_SESSION_KEY + 1] = { 0 };
    DWORD dwError = RmStartSession(&dwSession, 0, szSessionKey);

    if (dwError != ERROR_SUCCESS) {
        PrintError("RmStartSession", dwError);
        return 1;
    }

    // Register the directory path as the resource to be checked.
    dwError = RmRegisterResources(dwSession, 1, &targetPath, 0, NULL, 0, NULL);
    if (dwError != ERROR_SUCCESS) {
        PrintError("RmRegisterResources", dwError);
        RmEndSession(dwSession);
        return 1;
    }

    // Get the list of processes locking the resource.
    DWORD dwReason;
    UINT nProcInfoNeeded = 0;
    UINT nProcInfo = 0;
    std::vector<RM_PROCESS_INFO> rgpi;

    // First call to get the required buffer size.
    dwError = RmGetList(dwSession, &nProcInfoNeeded, &nProcInfo, NULL, &dwReason);
    if (dwError == ERROR_MORE_DATA) {
        rgpi.resize(nProcInfoNeeded);
        nProcInfo = nProcInfoNeeded;
        
        // Second call to get the actual process info.
        dwError = RmGetList(dwSession, &nProcInfoNeeded, &nProcInfo, rgpi.data(), &dwReason);
    }

    if (dwError != ERROR_SUCCESS) {
        if (dwError == ERROR_SEM_TIMEOUT) {
             std::wcout << L"No processes found with open handles." << std::endl;
             RmEndSession(dwSession);
             return 0;
        }
        PrintError("RmGetList", dwError);
        RmEndSession(dwSession);
        return 1;
    }

    if (nProcInfo == 0) {
        std::wcout << L"No processes found with open handles." << std::endl;
        RmEndSession(dwSession);
        return 0;
    }

    std::wcout << L"Found " << nProcInfo << L" process(es) to terminate:" << std::endl;

    for (UINT i = 0; i < nProcInfo; ++i) {
        std::wcout << L"  - PID: " << rgpi[i].Process.dwProcessId
                   << L", Name: " << rgpi[i].strAppName << std::endl;
        
        HANDLE hProcess = OpenProcess(PROCESS_TERMINATE, FALSE, rgpi[i].Process.dwProcessId);
        if (hProcess == NULL) {
            std::cerr << "  - Failed to open process " << rgpi[i].Process.dwProcessId << ". It may have already closed." << std::endl;
            continue;
        }

        if (TerminateProcess(hProcess, 1)) {
            std::wcout << L"  - Successfully terminated." << std::endl;
        } else {
            PrintError("TerminateProcess", GetLastError());
        }
        CloseHandle(hProcess);
    }

    RmEndSession(dwSession);
    return 0;
}