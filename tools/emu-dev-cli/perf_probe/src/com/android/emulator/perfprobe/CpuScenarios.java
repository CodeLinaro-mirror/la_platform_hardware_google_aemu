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
import java.util.Arrays;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.locks.LockSupport;
import org.json.JSONObject;

/**
 * Explicit CPU compute scaling (1-thread vs N-vCPU threads) and cross-vCPU IPI wakeup benchmarks.
 */
public final class CpuScenarios {
    private CpuScenarios() {}

    private static long runHashMixChunk(long seed, int steps) {
        long x = seed ^ 0x9E3779B97F4A7C15L;
        for (int i = 0; i < steps; i++) {
            x ^= (x >>> 30);
            x *= 0xBF58476D1CE4E5B9L;
            x ^= (x >>> 27);
            x *= 0x94D049BB133111EBL;
            x ^= (x >>> 31);
        }
        return x;
    }

    private static double runComputeThreads(int numThreads, long durationMs) throws Exception {
        final int stepsPerChunk = 10_000;
        final AtomicLong totalOps = new AtomicLong(0L);
        final CountDownLatch ready = new CountDownLatch(numThreads);
        final CountDownLatch startGate = new CountDownLatch(1);
        final CountDownLatch done = new CountDownLatch(numThreads);
        final long durationNs = durationMs * 1_000_000L;

        for (int t = 0; t < numThreads; t++) {
            final int tid = t;
            Thread worker = new Thread(() -> {
                ready.countDown();
                try {
                    startGate.await();
                } catch (InterruptedException ignored) {
                    Thread.currentThread().interrupt();
                }
                long localOps = 0L;
                long acc = tid + 1L;
                long deadlineNs = SystemClock.elapsedRealtimeNanos() + durationNs;
                while (SystemClock.elapsedRealtimeNanos() < deadlineNs) {
                    acc = runHashMixChunk(acc, stepsPerChunk);
                    localOps += stepsPerChunk;
                }
                if (acc == 42L) {
                    totalOps.addAndGet(1L);
                }
                totalOps.addAndGet(localOps);
                done.countDown();
            }, "PerfProbeCompute-" + t);
            worker.start();
        }

        ready.await();
        long startNs = SystemClock.elapsedRealtimeNanos();
        startGate.countDown();
        done.await();
        long elapsedNs = Math.max(1L, SystemClock.elapsedRealtimeNanos() - startNs);
        return (totalOps.get() / 1_000_000.0) / (elapsedNs / 1_000_000_000.0);
    }

    /**
     * Runs a deterministic 64-bit integer mixing workload on 1 thread vs N vCPU threads
     * to measure single-core guest speed and multi-vCPU SMP scaling efficiency.
     */
    public static JSONObject runComputeScaling(long phaseDurationMs) throws Exception {
        long clampedMs = Math.max(200L, Math.min(phaseDurationMs, 3000L));
        int vcpus = Math.max(1, Runtime.getRuntime().availableProcessors());

        // Untimed JIT warmup so ART compiles runHashMixChunk before 1-thread measurement
        if (runHashMixChunk(1L, 500_000) == 42L) {
            System.nanoTime();
        }

        double singleThreadMops = runComputeThreads(1, clampedMs);
        double multiThreadMops = runComputeThreads(vcpus, clampedMs);
        double scalingRatio = singleThreadMops > 0.0 ? (multiThreadMops / singleThreadMops) : 0.0;

        JSONObject res = new JSONObject();
        res.put("scenario", "cpu_compute_scaling");
        res.put("status", "ok");
        res.put("vcpus", vcpus);
        res.put("cpu_1t_mops_s", singleThreadMops);
        res.put("cpu_nt_mops_s", multiThreadMops);
        res.put("smp_scaling_ratio", scalingRatio);
        return res;
    }

    /**
     * Bounces a token back and forth between two threads using LockSupport.unpark/park,
     * forcing cross-vCPU Linux futex/scheduler wakeups and RESCHEDULE_VECTOR IPI VM-exits.
     * Uses 3 untimed warmup batches + 30 timed sub-batches of 100 exchanges and reports
     * the median batch round-trip latency to filter out nested-KVM host preemption spikes.
     */
    public static JSONObject runIpiPingPong(int exchanges) throws Exception {
        final int batchSize = 100;
        final int warmupBatches = 3;
        final int numBatches = Math.max(10, Math.min(exchanges, 50_000) / batchSize);
        final int totalRounds = (warmupBatches + numBatches) * batchSize;
        final double[] batchRoundtripUs = new double[numBatches];

        final AtomicInteger turn = new AtomicInteger(0);
        final AtomicBoolean finished = new AtomicBoolean(false);
        final Thread[] threads = new Thread[2];
        final CountDownLatch ready = new CountDownLatch(2);
        final CountDownLatch done = new CountDownLatch(2);
        final AtomicLong timedStartNs = new AtomicLong(0L);
        final AtomicLong timedEndNs = new AtomicLong(0L);

        threads[0] = new Thread(() -> {
            ready.countDown();
            for (int b = 0; b < warmupBatches + numBatches; b++) {
                if (b == warmupBatches) {
                    timedStartNs.set(SystemClock.elapsedRealtimeNanos());
                }
                long b0 = SystemClock.elapsedRealtimeNanos();
                for (int i = 0; i < batchSize; i++) {
                    while (turn.get() != 0 && !finished.get()) {
                        LockSupport.parkNanos(250_000L);
                    }
                    turn.set(1);
                    LockSupport.unpark(threads[1]);
                }
                long bElapsedNs = Math.max(1L, SystemClock.elapsedRealtimeNanos() - b0);
                if (b >= warmupBatches) {
                    batchRoundtripUs[b - warmupBatches] = (bElapsedNs / 1000.0) / batchSize;
                }
            }
            timedEndNs.set(SystemClock.elapsedRealtimeNanos());
            finished.set(true);
            LockSupport.unpark(threads[1]);
            done.countDown();
        }, "PerfProbeIpiA");

        threads[1] = new Thread(() -> {
            ready.countDown();
            for (int i = 0; i < totalRounds; i++) {
                while (turn.get() != 1 && !finished.get()) {
                    LockSupport.parkNanos(250_000L);
                }
                turn.set(0);
                LockSupport.unpark(threads[0]);
            }
            finished.set(true);
            LockSupport.unpark(threads[0]);
            done.countDown();
        }, "PerfProbeIpiB");

        threads[0].start();
        threads[1].start();
        ready.await();
        LockSupport.unpark(threads[0]);
        done.await();

        int timedRounds = numBatches * batchSize;
        long elapsedNs = Math.max(1L, timedEndNs.get() - timedStartNs.get());
        Arrays.sort(batchRoundtripUs);
        double medianRoundtripUs = batchRoundtripUs[numBatches / 2];
        double p95RoundtripUs = batchRoundtripUs[Math.min(numBatches - 1, (int) (numBatches * 0.95))];
        double switchesPerSec = medianRoundtripUs > 0.0 ? (2_000_000.0 / medianRoundtripUs) : 0.0;

        JSONObject res = new JSONObject();
        res.put("scenario", "cpu_ipi_ping_pong");
        res.put("status", "ok");
        res.put("rounds", timedRounds);
        res.put("total_ms", elapsedNs / 1_000_000.0);
        res.put("roundtrip_us", medianRoundtripUs);
        res.put("p95_roundtrip_us", p95RoundtripUs);
        res.put("switches_per_sec", switchesPerSec);
        return res;
    }
}
