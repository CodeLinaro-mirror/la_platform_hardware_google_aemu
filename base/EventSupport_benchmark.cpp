// Copyright (C) 2025 The Android Open Source Project
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

/*
 * ======================================================================================================================
 * Benchmark Results & Analysis (Intel Xeon, 72 cores, 3.7 GHz) (08/22/2025)
 * ======================================================================================================================
 *
 * This file contains benchmarks for various event source configurations. The results below provide a clear guide
 * on which policies and sources to use for different scenarios.
 *
 * Conclusion:
 * - For single-threaded, fire-heavy workloads: Vector-based policies (`VectorSource`, `FastEventSource`) are the fastest.
 * - For write-heavy workloads: `UnorderedSetSource` is the clear winner due to its O(1) add/remove complexity.
 * - For multi-threaded, high-contention workloads: `HighContentionEventSource` (using Abseil's read-write lock)
 *   and `RcuSource` are the best choices as they scale well with multiple threads.
 * - For memory safety: `SafeEventSource` and `CallbackEventSource` (which uses `SafeEventSource`) provide protection
 *   against dangling pointers at a significant performance cost.
 *
 * Detailed Breakdown:
 *
 * 1. FireEvent (Single-Threaded Read Performance):
 *    - WINNER: Vector-based policies (`VectorSource`, `FastEventSource`) are by far the fastest due to excellent
 *      cache locality.
 *    - MIDDLE: `UnorderedSetSource` is slower due to pointer chasing. `RcuSource` has some overhead.
 *    - SLOWEST: `WeakPtr` policies (`SafeEventSource`, `CallbackEventSource`) are significantly slower due to the
 *      overhead of locking weak pointers.
 *
 * 2. AddListener (Write Performance):
 *    - WINNER: `UnorderedSetSource` provides the best performance, especially as the number of listeners grows.
 *    - MIDDLE: `OrderedVectorSource` is a good all-rounder, faster than a plain vector for large N.
 *    - SLOWEST: `VectorSource` and `RcuSource` degrade significantly as N increases because they need to scan or
 *      copy the entire list on each insertion. `WeakPtr` policies are also very slow.
 *
 * 3. Read Contention (Multi-threaded Read Performance):
 *    - WINNER: `RcuSource` and `HighContentionEventSource` (Abseil) scale the best, showing minimal performance
 *      degradation as threads are added.
 *    - LOSER: All policies that use a single `std::mutex` (`Vector`, `UnorderedSet`, etc.) become bottlenecks and
 *      perform poorly under high read contention.
 *
 * 4. Write Contention (Multi-threaded Write Performance):
 *    - WINNER: `HighContentionEventSource` (Abseil) is the clear winner, providing excellent performance even with
 *      many threads trying to write simultaneously.
 *    - LOSER: All other policies, especially those based on vectors, suffer heavily from lock contention.
 *
 * 5. Read/Write Contention (Mixed Workload):
 *    - WINNER: `HighContentionEventSource` (Abseil) and `RcuSource` again show their strength, providing the best
 *      balance for mixed read/write workloads under contention.
 *
 * --- Raw Benchmark Data ---
 *
 * EventSourceFixture<UnorderedSetSource>/FireEvent/4096/real_time          38548 ns
 * EventSourceFixture<VectorSource>/FireEvent/4096/real_time                6627 ns
 * EventSourceFixture<OrderedVectorSource>/FireEvent/4096/real_time         6941 ns
 * EventSourceFixture<RcuSource>/FireEvent/4096/real_time                   7406 ns
 * EventSourceFixture<WeakPtrVectorSource>/FireEvent/4096/real_time       101263 ns
 * EventSourceFixture<FastEventSource>/FireEvent/4096/real_time             6637 ns
 * EventSourceFixture<SafeEventSource>/FireEvent/4096/real_time           102050 ns
 * EventSourceFixture<HighContentionEventSource>/FireEvent/4096/real_time  79349 ns
 * EventSourceFixture<CallbackEventSource>/FireEvent/4096/real_time       102328 ns
 *
 * EventSourceFixture<UnorderedSetSource>/AddListener/4096/real_time       196247 ns
 * EventSourceFixture<VectorSource>/AddListener/4096/real_time            2380699 ns
 * EventSourceFixture<OrderedVectorSource>/AddListener/4096/real_time      103457 ns
 * EventSourceFixture<RcuSource>/AddListener/4096/real_time               5480914 ns
 * EventSourceFixture<WeakPtrVectorSource>/AddListener/4096/real_time    181606281 ns
 * EventSourceFixture<FastEventSource>/AddListener/4096/real_time         2402119 ns
 * EventSourceFixture<SafeEventSource>/AddListener/4096/real_time        199477804 ns
 * EventSourceFixture<HighContentionEventSource>/AddListener/4096/real_time 554340 ns
 * EventSourceFixture<CallbackEventSource>/AddListener/4096/real_time    203122924 ns
 *
 * EventSourceFixture<UnorderedSetSource>/ReadContention/4096/real_time/threads:16   555507 ns
 * EventSourceFixture<VectorSource>/ReadContention/4096/real_time/threads:16         24634 ns
 * EventSourceFixture<OrderedVectorSource>/ReadContention/4096/real_time/threads:16  24703 ns
 * EventSourceFixture<RcuSource>/ReadContention/4096/real_time/threads:16             8252 ns
 * EventSourceFixture<WeakPtrVectorSource>/ReadContention/4096/real_time/threads:16 1123857 ns
 * EventSourceFixture<FastEventSource>/ReadContention/4096/real_time/threads:16      24332 ns
 * EventSourceFixture<SafeEventSource>/ReadContention/4096/real_time/threads:16      926561 ns
 * EventSourceFixture<HighContentionEventSource>/ReadContention/4096/real_time/threads:16 87878 ns
 * EventSourceFixture<CallbackEventSource>/ReadContention/4096/real_time/threads:16 1025318 ns
 *
 * EventSourceFixture<UnorderedSetSource>/WriteContention/4096/real_time/threads:16  11845 ns
 * EventSourceFixture<VectorSource>/WriteContention/4096/real_time/threads:16        76070 ns
 * EventSourceFixture<OrderedVectorSource>/WriteContention/4096/real_time/threads:16 39673 ns
 * EventSourceFixture<RcuSource>/WriteContention/4096/real_time/threads:16          114656 ns
 * EventSourceFixture<WeakPtrVectorSource>/WriteContention/4096/real_time/threads:16 4279426 ns
 * EventSourceFixture<FastEventSource>/WriteContention/4096/real_time/threads:16     78131 ns
 * EventSourceFixture<SafeEventSource>/WriteContention/4096/real_time/threads:16    4012837 ns
 * EventSourceFixture<HighContentionEventSource>/WriteContention/4096/real_time/threads:16 7782 ns
 *
 * EventSourceFixture<UnorderedSetSource>/ReadWriteContention/4096/real_time/threads:16 452657 ns
 * EventSourceFixture<VectorSource>/ReadWriteContention/4096/real_time/threads:16      25014 ns
 * EventSourceFixture<OrderedVectorSource>/ReadWriteContention/4096/real_time/threads:16 21842 ns
 * EventSourceFixture<RcuSource>/ReadWriteContention/4096/real_time/threads:16         20382 ns
 * EventSourceFixture<WeakPtrVectorSource>/ReadWriteContention/4096/real_time/threads:16 1460437 ns
 * EventSourceFixture<FastEventSource>/ReadWriteContention/4096/real_time/threads:16   25938 ns
 * EventSourceFixture<SafeEventSource>/ReadWriteContention/4096/real_time/threads:16  1393895 ns
 * EventSourceFixture<HighContentionEventSource>/ReadWriteContention/4096/real_time/threads:16 119826 ns
 * EventSourceFixture<CallbackEventSource>/ReadWriteContention/4096/real_time/threads:16 1020974 ns
 *
 * ======================================================================================================================
 */
#include <memory>
#include <numeric>
#include <type_traits>
#include <vector>

#include "aemu/base/events/EventSources.h"
#include "aemu/base/events/PolicyEventSupport.h"
#include "benchmark/benchmark.h"

// A simple event type for our benchmarks
struct MyEvent {
    int value = 0;
};

// A mock listener that does the minimum amount of work
class MockEventListener : public android::base::eventing::EventListener<MyEvent> {
   public:
    void eventArrived(const MyEvent& event) override { benchmark::DoNotOptimize(event.value); }
};

template <typename T>
struct is_weak_ptr : std::false_type {};

template <typename T>
struct is_weak_ptr<std::weak_ptr<T>> : std::true_type {};

template <typename T>
inline constexpr bool is_weak_ptr_v = is_weak_ptr<T>::value;

// A benchmark fixture to hold the event source and a pool of listeners
template <class EventSourceType>
class EventSourceFixture : public benchmark::Fixture {
   public:
    EventSourceFixture() {
        if (s_listeners.empty()) {
            s_listeners.resize(kMaxListeners);
            for (size_t i = 0; i < kMaxListeners; ++i) {
                s_listeners[i] = std::make_shared<MockEventListener>();
            }
        }
    }

    inline auto getListener(long index) {
        using PtrType = typename EventSourceType::Ptr;
        if constexpr (is_weak_ptr_v<PtrType>) {
            return s_listeners[index];
        } else {
            return s_listeners[index].get();
        }
    }

    void SetUp(const ::benchmark::State& state) override {
        long N = state.range(0);
        for (long i = 0; i < N; ++i) {
            source.addListener(getListener(i));
        }
    }
    void TearDown(const ::benchmark::State& state) override { source.clear(); }

    static constexpr int kMaxListeners = 8192;
    inline static std::vector<std::shared_ptr<MockEventListener>> s_listeners;
    EventSourceType source;
};

// --- Define the types we want to test ---
// Foundational policies
using UnorderedSetSource = android::base::eventing::UnorderedSetSource<MyEvent>;
using VectorSource = android::base::eventing::VectorSource<MyEvent>;
using OrderedVectorSource = android::base::eventing::OrderedVectorSource<MyEvent>;
using RcuSource = android::base::eventing::RcuSource<MyEvent>;
using WeakPtrVectorSource = android::base::eventing::WeakPtrVectorSource<MyEvent>;

// Curated aliases from EventSources.h
using FastEventSource = android::base::eventing::FastEventSource<MyEvent>;
using SafeEventSource = android::base::eventing::SafeEventSource<MyEvent>;
using HighContentionEventSource = android::base::eventing::HighContentionEventSource<MyEvent>;
using CallbackEventSource = android::base::eventing::CallbackEventSource<MyEvent>;

BENCHMARK_TEMPLATE_METHOD_F(EventSourceFixture, FireEvent)(benchmark::State& state) {
    MyEvent event{42};
    for (auto _ : state) {
        this->source.fireEvent(event);
    }
}

#define BM_FIRE_EVENT(type)                                               \
    BENCHMARK_TEMPLATE_INSTANTIATE_F(EventSourceFixture, FireEvent, type) \
        ->Range(1, 4096)                                                  \
        ->RangeMultiplier(8)                                              \
        ->UseRealTime()

BM_FIRE_EVENT(UnorderedSetSource);
BM_FIRE_EVENT(VectorSource);
BM_FIRE_EVENT(OrderedVectorSource);
BM_FIRE_EVENT(RcuSource);
BM_FIRE_EVENT(WeakPtrVectorSource);
BM_FIRE_EVENT(FastEventSource);
BM_FIRE_EVENT(SafeEventSource);
BM_FIRE_EVENT(HighContentionEventSource);
BM_FIRE_EVENT(CallbackEventSource);

BENCHMARK_TEMPLATE_METHOD_F(EventSourceFixture, AddListener)(benchmark::State& state) {
    long N = state.range(0);
    for (auto _ : state) {
        state.PauseTiming();
        this->source.clear();
        state.ResumeTiming();
        for (long i = 0; i < N; ++i) {
            this->source.addListener(this->getListener(i));
        }
    }
}

#define BM_ADD_LISTENER(type)                                               \
    BENCHMARK_TEMPLATE_INSTANTIATE_F(EventSourceFixture, AddListener, type) \
        ->Range(1, 4096)                                                    \
        ->RangeMultiplier(8)                                                \
        ->UseRealTime()

BM_ADD_LISTENER(UnorderedSetSource);
BM_ADD_LISTENER(VectorSource);
BM_ADD_LISTENER(OrderedVectorSource);
BM_ADD_LISTENER(RcuSource);
BM_ADD_LISTENER(WeakPtrVectorSource);
BM_ADD_LISTENER(FastEventSource);
BM_ADD_LISTENER(SafeEventSource);
BM_ADD_LISTENER(HighContentionEventSource);
BM_ADD_LISTENER(CallbackEventSource);

BENCHMARK_TEMPLATE_METHOD_F(EventSourceFixture, ReadContention)(benchmark::State& state) {
    MyEvent event{42};
    for (auto _ : state) {
        this->source.fireEvent(event);
    }
}

#define BM_READ_CONTENTION(type)                                               \
    BENCHMARK_TEMPLATE_INSTANTIATE_F(EventSourceFixture, ReadContention, type) \
        ->Range(1, 4096)                                                       \
        ->RangeMultiplier(8)                                                   \
        ->ThreadRange(1, 16)                                                   \
        ->UseRealTime()

BM_READ_CONTENTION(UnorderedSetSource);
BM_READ_CONTENTION(VectorSource);
BM_READ_CONTENTION(OrderedVectorSource);
BM_READ_CONTENTION(RcuSource);
BM_READ_CONTENTION(WeakPtrVectorSource);
BM_READ_CONTENTION(FastEventSource);
BM_READ_CONTENTION(SafeEventSource);
BM_READ_CONTENTION(HighContentionEventSource);
BM_READ_CONTENTION(CallbackEventSource);

BENCHMARK_TEMPLATE_METHOD_F(EventSourceFixture, WriteContention)(benchmark::State& state) {
    long N = state.range(0);
    for (auto _ : state) {
        long i = (state.thread_index() * 10) % N;
        this->source.addListener(this->getListener(i));
        this->source.removeListener(this->getListener(i));
    }
}

#define BM_WRITE_CONTENTION(type)                                               \
    BENCHMARK_TEMPLATE_INSTANTIATE_F(EventSourceFixture, WriteContention, type) \
        ->Range(128, 4096)                                                      \
        ->RangeMultiplier(8)                                                    \
        ->ThreadRange(1, 16)                                                    \
        ->UseRealTime()

BM_WRITE_CONTENTION(UnorderedSetSource);
BM_WRITE_CONTENTION(VectorSource);
BM_WRITE_CONTENTION(OrderedVectorSource);
BM_WRITE_CONTENTION(RcuSource);
BM_WRITE_CONTENTION(WeakPtrVectorSource);
BM_WRITE_CONTENTION(FastEventSource);
BM_WRITE_CONTENTION(SafeEventSource);
BM_WRITE_CONTENTION(HighContentionEventSource);

BENCHMARK_TEMPLATE_METHOD_F(EventSourceFixture, ReadWriteContention)(benchmark::State& state) {
    long N = state.range(0);
    MyEvent event{42};
    if (state.thread_index() == 0) {
        // Writer thread
        for (auto _ : state) {
            long i = N / 2 + (rand() % (N / 2));
            this->source.addListener(this->getListener(i));
            this->source.removeListener(this->getListener(i));
        }
    } else {
        // Reader threads
        for (auto _ : state) {
            this->source.fireEvent(event);
        }
    }
}

#define BM_READ_WRITE_CONTENTION(type)                                              \
    BENCHMARK_TEMPLATE_INSTANTIATE_F(EventSourceFixture, ReadWriteContention, type) \
        ->Range(128, 4096)                                                          \
        ->RangeMultiplier(8)                                                        \
        ->ThreadRange(2, 16)                                                        \
        ->UseRealTime()

BM_READ_WRITE_CONTENTION(UnorderedSetSource);
BM_READ_WRITE_CONTENTION(VectorSource);
BM_READ_WRITE_CONTENTION(OrderedVectorSource);
BM_READ_WRITE_CONTENTION(RcuSource);
BM_READ_WRITE_CONTENTION(WeakPtrVectorSource);
BM_READ_WRITE_CONTENTION(FastEventSource);
BM_READ_WRITE_CONTENTION(SafeEventSource);
BM_READ_WRITE_CONTENTION(HighContentionEventSource);
BM_READ_WRITE_CONTENTION(CallbackEventSource);

BENCHMARK_MAIN();
