// Copyright 2026 The Android Open Source Project
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//      http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

package com.android.emulator.perfprobe;

import android.os.SharedMemory;
import android.os.SystemClock;
import java.nio.ByteBuffer;
import org.json.JSONObject;

/**
 * Explicit guest memory and hypervisor EPT / Stage-2 page-table fault scenarios.
 */
public final class MemoryScenarios {
    private MemoryScenarios() {}

    /**
     * Repeatedly creates anonymous shared-memory mappings (ashmem/memfd mmap), touches 1 byte on
     * every 4 KiB page to force kernel demand-paging and hypervisor EPT_VIOLATION / Stage-2 faults,
     * and unmaps them (triggering guest+hypervisor TLB shootdowns).
     */
    public static JSONObject runEptPageFaultChurn(int regionMib, int rounds) throws Exception {
        int clampedMib = Math.max(8, Math.min(regionMib, 128));
        int clampedRounds = Math.max(1, Math.min(rounds, 16));
        int regionBytes = clampedMib * 1024 * 1024;
        int pagesPerRound = regionBytes / 4096;
        long totalPagesFaulted = (long) pagesPerRound * clampedRounds;

        long checksum = 0;
        long startNs = SystemClock.elapsedRealtimeNanos();
        for (int r = 0; r < clampedRounds; r++) {
            try (SharedMemory shm = SharedMemory.create("perf_probe_ept_" + r, regionBytes)) {
                ByteBuffer buf = shm.mapReadWrite();
                try {
                    byte marker = (byte) (r + 1);
                    for (int offset = 0; offset < regionBytes; offset += 4096) {
                        buf.put(offset, marker);
                    }
                    checksum += buf.get(regionBytes - 4096);
                } finally {
                    SharedMemory.unmap(buf);
                }
            }
        }
        long elapsedNs = Math.max(1L, SystemClock.elapsedRealtimeNanos() - startNs);
        double elapsedSec = elapsedNs / 1_000_000_000.0;
        double pagesPerSec = totalPagesFaulted / elapsedSec;
        double usPerPage = (elapsedNs / 1000.0) / totalPagesFaulted;

        JSONObject res = new JSONObject();
        res.put("scenario", "mem_ept_page_fault");
        res.put("status", "ok");
        res.put("total_pages", totalPagesFaulted);
        res.put("total_ms", elapsedNs / 1_000_000.0);
        res.put("pages_per_sec", pagesPerSec);
        res.put("us_per_page", usPerPage);
        res.put("checksum", checksum);
        return res;
    }

    /**
     * Measures sustained guest RAM bulk copy throughput across direct buffers.
     */
    public static JSONObject runStreamCopy(int bufferMib, int iterations) throws Exception {
        int clampedMib = Math.max(8, Math.min(bufferMib, 64));
        int clampedIters = Math.max(4, Math.min(iterations, 128));
        int bytes = clampedMib * 1024 * 1024;

        ByteBuffer src = ByteBuffer.allocateDirect(bytes);
        ByteBuffer dst = ByteBuffer.allocateDirect(bytes);
        for (int i = 0; i < bytes; i += 4096) {
            src.putInt(i, i ^ 0x5A5A5A5A);
        }

        long startNs = SystemClock.elapsedRealtimeNanos();
        long checksum = 0;
        for (int iter = 0; iter < clampedIters; iter++) {
            src.clear();
            dst.clear();
            src.putInt(0, iter);
            dst.put(src);
            checksum += dst.getInt(0);
        }
        long elapsedNs = Math.max(1L, SystemClock.elapsedRealtimeNanos() - startNs);

        double totalGib = ((double) bytes * clampedIters) / (1024.0 * 1024.0 * 1024.0);
        double gibPerSec = totalGib / (elapsedNs / 1_000_000_000.0);

        JSONObject res = new JSONObject();
        res.put("scenario", "mem_stream_copy");
        res.put("status", "ok");
        res.put("total_gib", totalGib);
        res.put("total_ms", elapsedNs / 1_000_000.0);
        res.put("copy_gib_s", gibPerSec);
        res.put("checksum", checksum);
        return res;
    }
}
