// Copyright 2020 The Android Open Source Project
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

#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "aemu/base/c_header.h"
#include "aemu/base/export.h"
#include "render-utils/renderer_enums.h"

ANDROID_BEGIN_HEADER

// A small structure used to model the EmuGL configuration
// to use.
// |enabled| is true if GPU emulation is enabled, false otherwise.
// |backend| contains the name of the backend to use, if |enabled|
// is true.
// |status| is a string used to report error or the current status
// of EmuGL emulation.
typedef struct {
    char vulkan_backend[64];
    char gles_backend[64];
    char status[256];
} EmuglConfig;

// Check whether or not the host GPU is blacklisted. If so, fall back
// to software rendering.
bool isHostGpuBlacklisted();

typedef struct {
    char* make;
    char* model;
    char* device_id;
    char* revision_id;
    char* version;
    char* renderer;
} emugl_host_gpu_props;

typedef struct {
    int num_gpus;
    emugl_host_gpu_props* props;
} emugl_host_gpu_prop_list;

// Get a description of host GPU properties.
// Need to free after use.
emugl_host_gpu_prop_list emuglConfig_get_host_gpu_props();

// Returns SelectedRenderer value the selected gpu mode.
// Assumes that the -gpu command line option
// has been taken into account already.
SelectedRenderer emuglConfig_get_renderer(const char* gpu_mode);

// Returns the renderer that is active, after config is done.
SelectedRenderer emuglConfig_get_current_renderer();
SelectedRenderer emuglConfig_get_current_gles_renderer();
SelectedRenderer emuglConfig_get_current_vulkan_renderer();

// Returns the '-gpu <mode>' option. If '-gpu <mode>' option is NULL, returns
// the hw.gpu.mode hardware property.
const char* emuglConfig_get_user_gpu_option();

// Returns the full path for vulkan runtime library to be used
const char* emuglConfig_get_vulkan_runtime_full_path();

// Returns the properties of the hardware gpu to be used for emulation
void emuglConfig_get_vulkan_hardware_gpu(char** vendor, int* major, int* minor, int* patch,
                                         uint64_t* deviceMemBytes, uint32_t* driverVersion,
                                         uint64_t* deviceMaxAllocationCount,
                                         bool* supportsExternalMemory, bool* supportsSwapchain,
                                         bool* supportsYcbcrConversion);

// Returns a string representation of the renderer enum. Return value is a
// static constant string, it is NOT heap-allocated.
const char* emuglConfig_renderer_to_string(SelectedRenderer renderer);

void free_emugl_host_gpu_props(emugl_host_gpu_prop_list props);

// Initialize an EmuglConfig instance based on the AVD's hardware properties
// and the command-line -gpu option, if any.
//
// |config| is the instance to initialize.
// |gpu_mode| is the value of the hw.gpu.mode hardware property.
// |no_window| is true if the '-no-window' emulator flag was used.
// |api_level| is the api level of the android image.
//
// Returns true on success, or false if there was an error (e.g. bad
// mode or option value), in which case the |status| field will contain
// a small error message.
AEMU_EXPORT bool emuglConfig_init(EmuglConfig* config,
                                  const char* gpu_mode,
                                  bool no_window,
                                  int api_level);

// Setup GPU emulation according to a given |backend|.
// |bitness| is the host bitness, and can be 0 (autodetect), 32 or 64.
AEMU_EXPORT void emuglConfig_setupEnv(const EmuglConfig* config);

ANDROID_END_HEADER
