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

import android.os.SystemClock;
import android.system.Os;
import java.io.File;
import java.io.FileDescriptor;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.RandomAccessFile;
import java.lang.reflect.Method;
import java.util.Arrays;
import java.util.Random;
import org.json.JSONObject;

/** Explicit guest storage and virtio-blk / QCOW2 I/O scenarios. */
public final class DiskScenarios {
    private static final int LINUX_POSIX_FADV_DONTNEED = 4;

    private DiskScenarios() {}

    private static void evictPageCache(FileDescriptor fd, long lengthBytes) {
        try {
            Method m =
                    Os.class.getMethod(
                            "posix_fadvise",
                            FileDescriptor.class,
                            long.class,
                            long.class,
                            int.class);
            m.invoke(null, fd, 0L, lengthBytes, LINUX_POSIX_FADV_DONTNEED);
        } catch (Throwable ignored) {
            // Best-effort page cache eviction on guests supporting posix_fadvise
        }
    }

    private static double trimmedMean(double[] sorted) {
        if (sorted.length == 0) {
            return 0.0;
        }
        if (sorted.length < 4) {
            return sorted[sorted.length / 2];
        }
        int trim = Math.max(1, sorted.length / 10);
        int start = trim;
        int end = sorted.length - trim;
        if (start >= end) {
            return sorted[sorted.length / 2];
        }
        double sum = 0.0;
        for (int i = start; i < end; i++) {
            sum += sorted[i];
        }
        return sum / (end - start);
    }

    /**
     * Writes and reads a sequential file in 1 MiB blocks with fsync and POSIX_FADV_DONTNEED
     * page-cache eviction, bypassing adbd/vsock to measure deterministic guest filesystem ->
     * virtio-blk -> host QCOW2 sequential DMA throughput.
     */
    public static JSONObject runSequentialIo(File dir, int sizeMib) throws Exception {
        int clampedMib = Math.max(8, Math.min(sizeMib, 512));
        int numWindows = Math.min(8, clampedMib);
        int mibPerWindow = Math.max(1, clampedMib / numWindows);
        int actualTotalMib = numWindows * mibPerWindow;

        File target = new File(dir, "perf_probe_seq.bin");
        byte[] block = new byte[1024 * 1024];
        new Random(42L).nextBytes(block);

        double[] writeWindowMibS = new double[numWindows];
        long writeStartNs = SystemClock.elapsedRealtimeNanos();
        try (FileOutputStream fos = new FileOutputStream(target)) {
            FileDescriptor fd = fos.getFD();
            // Untimed 2 MiB warmup write + sync to fault buffer pages and journal state
            fos.write(block);
            fos.write(block);
            fos.flush();
            fd.sync();
            fos.getChannel().position(0L);

            writeStartNs = SystemClock.elapsedRealtimeNanos();
            int blockIdx = 0;
            for (int w = 0; w < numWindows; w++) {
                long w0 = SystemClock.elapsedRealtimeNanos();
                for (int i = 0; i < mibPerWindow; i++) {
                    block[0] = (byte) (blockIdx++);
                    fos.write(block);
                }
                fos.flush();
                fd.sync();
                long wElapsed = Math.max(1L, SystemClock.elapsedRealtimeNanos() - w0);
                writeWindowMibS[w] = mibPerWindow / (wElapsed / 1_000_000_000.0);
            }
            evictPageCache(fd, (long) actualTotalMib * 1024L * 1024L);
        }
        long writeElapsedNs = Math.max(1L, SystemClock.elapsedRealtimeNanos() - writeStartNs);

        double[] readWindowMibS = new double[numWindows];
        long readStartNs = SystemClock.elapsedRealtimeNanos();
        long checksum = 0;
        try (FileInputStream fis = new FileInputStream(target)) {
            for (int w = 0; w < numWindows; w++) {
                long r0 = SystemClock.elapsedRealtimeNanos();
                int bytesToRead = mibPerWindow * 1024 * 1024;
                int readSoFar = 0;
                while (readSoFar < bytesToRead) {
                    int n = fis.read(block, 0, Math.min(block.length, bytesToRead - readSoFar));
                    if (n <= 0) {
                        break;
                    }
                    readSoFar += n;
                    checksum += (block[0] & 0xFF) + n;
                }
                long rElapsed = Math.max(1L, SystemClock.elapsedRealtimeNanos() - r0);
                readWindowMibS[w] = (readSoFar / (1024.0 * 1024.0)) / (rElapsed / 1_000_000_000.0);
            }
        } finally {
            target.delete();
        }
        long readElapsedNs = Math.max(1L, SystemClock.elapsedRealtimeNanos() - readStartNs);

        Arrays.sort(writeWindowMibS);
        Arrays.sort(readWindowMibS);
        double writeMibPerSec = trimmedMean(writeWindowMibS);
        double readMibPerSec = trimmedMean(readWindowMibS);

        JSONObject res = new JSONObject();
        res.put("scenario", "disk_seq_io");
        res.put("status", "ok");
        res.put("size_mib", actualTotalMib);
        res.put("write_ms", writeElapsedNs / 1_000_000.0);
        res.put("read_ms", readElapsedNs / 1_000_000.0);
        res.put("write_mib_s", writeMibPerSec);
        res.put("read_mib_s", readMibPerSec);
        res.put("checksum", checksum);
        return res;
    }

    /**
     * Performs random 4 KiB pwrite + fsync operations on a pre-allocated file to stress
     * virtio-blk ioeventfd kicks, barrier flushes, and host QCOW2 L2 table updates.
     */
    public static JSONObject runRandom4kFsync(File dir, int iterations) throws Exception {
        int ops = Math.max(20, Math.min(iterations, 2000));
        int warmupOps = Math.min(16, ops / 5);
        int filePages = 2048; // 8 MiB span (2048 * 4 KiB pages)
        File target = new File(dir, "perf_probe_rand4k.bin");
        byte[] page = new byte[4096];
        Random rng = new Random(1337L);
        rng.nextBytes(page);

        double[] opLatenciesMs = new double[ops];
        long totalStartNs;
        long totalElapsedNs;

        try (RandomAccessFile raf = new RandomAccessFile(target, "rw")) {
            raf.setLength((long) filePages * 4096L);
            FileDescriptor fd = raf.getFD();
            Os.fsync(fd);

            // Untimed warmup fsyncs to stabilize QCOW2 L2 cache & filesystem journal
            for (int w = 0; w < warmupOps; w++) {
                long offset = (long) rng.nextInt(filePages) * 4096L;
                Os.pwrite(fd, page, 0, 4096, offset);
                Os.fsync(fd);
            }

            totalStartNs = SystemClock.elapsedRealtimeNanos();
            for (int i = 0; i < ops; i++) {
                long offset = (long) rng.nextInt(filePages) * 4096L;
                page[0] = (byte) (i & 0xFF);
                page[1] = (byte) ((i >> 8) & 0xFF);
                long t0 = SystemClock.elapsedRealtimeNanos();
                Os.pwrite(fd, page, 0, 4096, offset);
                Os.fsync(fd);
                long t1 = SystemClock.elapsedRealtimeNanos();
                opLatenciesMs[i] = (t1 - t0) / 1_000_000.0;
            }
            totalElapsedNs = Math.max(1L, SystemClock.elapsedRealtimeNanos() - totalStartNs);
        } finally {
            target.delete();
        }

        Arrays.sort(opLatenciesMs);
        double p50Ms = opLatenciesMs[ops / 2];
        double p95Ms = opLatenciesMs[Math.min(ops - 1, (int) (ops * 0.95))];
        double trimmedLatencyMs = Math.max(0.001, trimmedMean(opLatenciesMs));
        double iops = 1000.0 / trimmedLatencyMs;
        double rawIops = ops / (totalElapsedNs / 1_000_000_000.0);

        JSONObject res = new JSONObject();
        res.put("scenario", "disk_rand_4k_fsync");
        res.put("status", "ok");
        res.put("ops", ops);
        res.put("total_ms", totalElapsedNs / 1_000_000.0);
        res.put("iops", iops);
        res.put("raw_iops", rawIops);
        res.put("p50_ms", p50Ms);
        res.put("p95_ms", p95Ms);
        return res;
    }
}
