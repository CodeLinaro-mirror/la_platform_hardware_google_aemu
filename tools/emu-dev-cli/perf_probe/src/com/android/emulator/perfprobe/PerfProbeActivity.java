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

import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.graphics.Canvas;
import android.graphics.Color;
import android.os.Bundle;
import android.os.SystemClock;
import android.util.Log;
import android.view.Choreographer;
import android.view.InputEvent;
import android.view.KeyEvent;
import android.view.MotionEvent;
import android.view.View;
import android.view.WindowManager;
import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import org.json.JSONArray;
import org.json.JSONObject;

/**
 * Deterministic guest performance probe Activity for emu-dev-cli perf.
 *
 * <p>Handles:
 * <ul>
 *   <li>Sub-millisecond KeyEvent and TouchEvent arrival instrumentation + Choreographer frame commit
 *   <li>Color-encoded full-screen frame updates ("Software WALT") for gRPC streamScreenshot
 *   <li>Intent-dispatched Disk, Memory/EPT, and CPU/IPI microbenchmark scenarios
 * </ul>
 */
public final class PerfProbeActivity extends Activity {
    public static final String TAG = "PerfProbeJson";

    private ProbeCanvasView probeView;
    private volatile int currentColor = Color.rgb(0, 0, 0);

    private final List<Double> keyKernelToAppMs =
            Collections.synchronizedList(new ArrayList<>());
    private final List<Double> keyAppToFrameMs =
            Collections.synchronizedList(new ArrayList<>());
    private final List<Double> touchKernelToAppMs =
            Collections.synchronizedList(new ArrayList<>());
    private final List<Double> touchAppToFrameMs =
            Collections.synchronizedList(new ArrayList<>());

    private volatile int keyDownCount = 0;
    private volatile int touchEventCount = 0;
    private volatile int touchSampleCount = 0;
    private Method eventTimeNanosMethod;

    private final class ProbeCanvasView extends View {
        ProbeCanvasView(Context context) {
            super(context);
            setFocusable(true);
            setFocusableInTouchMode(true);
        }

        @Override
        protected void onDraw(Canvas canvas) {
            canvas.drawColor(currentColor);
        }
    }

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        try {
            eventTimeNanosMethod = InputEvent.class.getMethod("getEventTimeNanos");
        } catch (NoSuchMethodException ignored) {
            eventTimeNanosMethod = null;
        }

        probeView = new ProbeCanvasView(this);
        setContentView(probeView);
        probeView.requestFocus();

        emitReady("onCreate");
        handleScenarioIntent(getIntent());
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        handleScenarioIntent(intent);
    }

    private void emitReady(String lifecycle) {
        try {
            JSONObject obj = new JSONObject();
            obj.put("scenario", "ready");
            obj.put("status", "ok");
            obj.put("lifecycle", lifecycle);
            obj.put("uptime_ns", SystemClock.uptimeNanos());
            Log.i(TAG, obj.toString());
        } catch (Exception ignored) {
        }
    }

    private long extractEventTimeNanos(InputEvent event) {
        if (eventTimeNanosMethod != null) {
            try {
                Object val = eventTimeNanosMethod.invoke(event);
                if (val instanceof Long) {
                    return (Long) val;
                }
            } catch (Exception ignored) {
            }
        }
        return event.getEventTime() * 1_000_000L;
    }

    @Override
    public boolean dispatchKeyEvent(KeyEvent event) {
        final long arrivalNs = SystemClock.uptimeNanos();
        final long eventNs = extractEventTimeNanos(event);
        final double kernelToAppMs = Math.max(0.0, (arrivalNs - eventNs) / 1_000_000.0);

        if (event.getAction() == KeyEvent.ACTION_DOWN) {
            final int seq = ++keyDownCount;
            keyKernelToAppMs.add(kernelToAppMs);
            final int r = ((seq * 17) % 200) + 32;
            final int g = 255 - r;
            final int b = 128;
            currentColor = Color.rgb(r, g, b);
            probeView.invalidate();

            Choreographer.getInstance().postFrameCallback(frameTimeNanos -> {
                long commitDoneNs = SystemClock.uptimeNanos();
                double appToFrameMs = Math.max(0.0, (commitDoneNs - arrivalNs) / 1_000_000.0);
                keyAppToFrameMs.add(appToFrameMs);
                try {
                    JSONObject obj = new JSONObject();
                    obj.put("type", "key_event");
                    obj.put("seq", seq);
                    obj.put("key_code", event.getKeyCode());
                    obj.put("event_time_ns", eventNs);
                    obj.put("arrival_time_ns", arrivalNs);
                    obj.put("frame_commit_ns", commitDoneNs);
                    obj.put("kernel_to_app_ms", kernelToAppMs);
                    obj.put("app_to_frame_ms", appToFrameMs);
                    Log.i(TAG, obj.toString());
                } catch (Exception ignored) {
                }
            });
        }
        return true;
    }

    @Override
    public boolean dispatchTouchEvent(MotionEvent event) {
        final long arrivalNs = SystemClock.uptimeNanos();
        final long eventNs = extractEventTimeNanos(event);
        final double kernelToAppMs = Math.max(0.0, (arrivalNs - eventNs) / 1_000_000.0);

        final int action = event.getActionMasked();
        final int samples = 1 + event.getHistorySize();
        touchEventCount++;
        touchSampleCount += samples;
        touchKernelToAppMs.add(kernelToAppMs);

        if (action == MotionEvent.ACTION_DOWN || action == MotionEvent.ACTION_MOVE) {
            final int x = Math.round(event.getRawX());
            final int y = Math.round(event.getRawY());
            final int r = Math.max(16, Math.min(240, x & 0xFF));
            final int g = 255 - r;
            final int b = 96;
            currentColor = Color.rgb(r, g, b);
            probeView.invalidate();

            if (action == MotionEvent.ACTION_DOWN) {
                Choreographer.getInstance().postFrameCallback(frameTimeNanos -> {
                    long commitDoneNs = SystemClock.uptimeNanos();
                    double appToFrameMs = Math.max(0.0, (commitDoneNs - arrivalNs) / 1_000_000.0);
                    touchAppToFrameMs.add(appToFrameMs);
                    try {
                        JSONObject obj = new JSONObject();
                        obj.put("type", "touch_down");
                        obj.put("x", x);
                        obj.put("y", y);
                        obj.put("r", r);
                        obj.put("g", g);
                        obj.put("b", b);
                        obj.put("event_time_ns", eventNs);
                        obj.put("arrival_time_ns", arrivalNs);
                        obj.put("frame_commit_ns", commitDoneNs);
                        obj.put("kernel_to_app_ms", kernelToAppMs);
                        obj.put("app_to_frame_ms", appToFrameMs);
                        Log.i(TAG, obj.toString());
                    } catch (Exception ignored) {
                    }
                });
            }
        }
        return true;
    }

    private void handleScenarioIntent(Intent intent) {
        if (intent == null) {
            return;
        }
        final String scenario = intent.getStringExtra("scenario");
        if (scenario == null || scenario.isEmpty()) {
            return;
        }

        if ("reset_input".equals(scenario)) {
            keyDownCount = 0;
            touchEventCount = 0;
            touchSampleCount = 0;
            keyKernelToAppMs.clear();
            keyAppToFrameMs.clear();
            touchKernelToAppMs.clear();
            touchAppToFrameMs.clear();
            currentColor = Color.rgb(0, 0, 0);
            runOnUiThread(() -> {
                if (probeView != null) {
                    probeView.requestFocus();
                    probeView.invalidate();
                }
            });
            try {
                JSONObject res = new JSONObject();
                res.put("scenario", "reset_input");
                res.put("status", "ok");
                Log.i(TAG, res.toString());
            } catch (Exception ignored) {
            }
            return;
        }

        if ("input_summary".equals(scenario)) {
            try {
                JSONObject res = new JSONObject();
                res.put("scenario", "input_summary");
                res.put("status", "ok");
                res.put("key_down_count", keyDownCount);
                res.put("touch_event_count", touchEventCount);
                res.put("touch_sample_count", touchSampleCount);
                res.put("key_kernel_to_app_ms", toJsonArray(keyKernelToAppMs));
                res.put("key_app_to_frame_ms", toJsonArray(keyAppToFrameMs));
                res.put("touch_kernel_to_app_ms", toJsonArray(touchKernelToAppMs));
                res.put("touch_app_to_frame_ms", toJsonArray(touchAppToFrameMs));
                Log.i(TAG, res.toString());
            } catch (Exception ignored) {
            }
            return;
        }

        final int sizeMib = intent.getIntExtra("size_mib", 64);
        final int iterations = intent.getIntExtra("iterations", 200);
        final int rounds = intent.getIntExtra("rounds", 4);
        final int durationMs = intent.getIntExtra("duration_ms", 600);

        new Thread(() -> {
            try {
                JSONObject result;
                switch (scenario) {
                    case "disk_seq_io":
                    case "disk_seq_128mib":
                        result = DiskScenarios.runSequentialIo(
                                getCacheDir(),
                                "disk_seq_128mib".equals(scenario)
                                        ? intent.getIntExtra("size_mib", 128)
                                        : sizeMib);
                        result.put("scenario", scenario);
                        break;
                    case "disk_rand_4k_fsync":
                        result = DiskScenarios.runRandom4kFsync(getCacheDir(), iterations);
                        break;
                    case "mem_ept_page_fault":
                        result = MemoryScenarios.runEptPageFaultChurn(sizeMib, rounds);
                        break;
                    case "mem_stream_copy":
                        result = MemoryScenarios.runStreamCopy(sizeMib, iterations);
                        break;
                    case "cpu_compute_scaling":
                        result = CpuScenarios.runComputeScaling(durationMs);
                        break;
                    case "cpu_ipi_ping_pong":
                        result = CpuScenarios.runIpiPingPong(iterations);
                        break;
                    default:
                        result = new JSONObject();
                        result.put("scenario", scenario);
                        result.put("status", "error");
                        result.put("error", "Unknown scenario: " + scenario);
                        break;
                }
                Log.i(TAG, result.toString());
            } catch (Throwable t) {
                try {
                    JSONObject err = new JSONObject();
                    err.put("scenario", scenario);
                    err.put("status", "error");
                    err.put("error", String.valueOf(t));
                    Log.e(TAG, err.toString());
                } catch (Exception ignored) {
                }
            }
        }, "PerfProbeScenario-" + scenario).start();
    }

    private static JSONArray toJsonArray(List<Double> values) {
        JSONArray arr = new JSONArray();
        synchronized (values) {
            for (Double v : values) {
                arr.put(v);
            }
        }
        return arr;
    }
}
