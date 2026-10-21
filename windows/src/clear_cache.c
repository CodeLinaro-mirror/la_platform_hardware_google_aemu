//===-- clear_cache.c - Implement __clear_cache ---------------------------===//
//
// Part of the LLVM Project, under the Apache License v2.0 with LLVM Exceptions.
// See https://llvm.org/LICENSE.txt for license information.
// SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
//
//===----------------------------------------------------------------------===//

#include <stddef.h>
#include <windows.h>

void __clear_cache(void* start, void* end) {
    FlushInstructionCache(GetCurrentProcess(), start,
                          (size_t)((const char*)end - (const char*)start));
}
