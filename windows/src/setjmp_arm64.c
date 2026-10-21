// Copyright 2026 The Android Open Source Project
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

#if defined(__aarch64__) || defined(_M_ARM64)

#include <setjmp.h>

// Direct register save/restore setjmp/longjmp for Windows ARM64 without
// SEH stack unwinding, matching MinGW-w64's __mingw_setjmp/__mingw_longjmp
// required by QEMU TCG generated code.
__attribute__((naked)) int __mingw_setjmp(jmp_buf buf) {
    __asm__ volatile(
        "stp x19, x20, [x0, #0x00]\n"
        "stp x21, x22, [x0, #0x10]\n"
        "stp x23, x24, [x0, #0x20]\n"
        "stp x25, x26, [x0, #0x30]\n"
        "stp x27, x28, [x0, #0x40]\n"
        "stp x29, x30, [x0, #0x50]\n"
        "mov x2, sp\n"
        "str x2,       [x0, #0x60]\n"
        "stp d8,  d9,  [x0, #0x68]\n"
        "stp d10, d11, [x0, #0x78]\n"
        "stp d12, d13, [x0, #0x88]\n"
        "stp d14, d15, [x0, #0x98]\n"
        "mrs x2, fpcr\n"
        "mrs x3, fpsr\n"
        "stp x2,  x3,  [x0, #0xa8]\n"
        "mov w0, #0\n"
        "ret\n");
}

__attribute__((naked, noreturn)) void __mingw_longjmp(jmp_buf buf, int val) {
    __asm__ volatile(
        "ldp x19, x20, [x0, #0x00]\n"
        "ldp x21, x22, [x0, #0x10]\n"
        "ldp x23, x24, [x0, #0x20]\n"
        "ldp x25, x26, [x0, #0x30]\n"
        "ldp x27, x28, [x0, #0x40]\n"
        "ldp x29, x30, [x0, #0x50]\n"
        "ldr x2,       [x0, #0x60]\n"
        "mov sp, x2\n"
        "ldp d8,  d9,  [x0, #0x68]\n"
        "ldp d10, d11, [x0, #0x78]\n"
        "ldp d12, d13, [x0, #0x88]\n"
        "ldp d14, d15, [x0, #0x98]\n"
        "ldp x2,  x3,  [x0, #0xa8]\n"
        "msr fpcr, x2\n"
        "msr fpsr, x3\n"
        "cmp w1, #0\n"
        "csinc w0, w1, wzr, ne\n"
        "ret\n");
}

#endif  // defined(__aarch64__) || defined(_M_ARM64)
