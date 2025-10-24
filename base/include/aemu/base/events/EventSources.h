// Copyright (C) 2020 The Android Open Source Project
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
#include "aemu/base/events/EventSource.h"
#include "aemu/base/events/WithCallbacks.h"
#include "aemu/base/events/policies/AbseilReadWritePolicy.h"
#include "aemu/base/events/policies/HybridStoragePolicy.h"
#include "aemu/base/events/policies/VectorStoragePolicy.h"

/**
 * @brief Provides a set of pre-made, easy-to-use event source aliases for the
 * most common use cases.
 *
 * This header is the recommended entry point for developers wanting to use the
 * eventing system without needing to understand the details of the underlying
 * policy-based design.
 */
namespace android::base::eventing {

/**
 * @brief A high-performance source for single-threaded contexts.
 * @details Uses a `HybridStoragePolicy` that promotes to a `std::vector` for
 * fast, cache-friendly event dispatch. It is not thread-safe.
 *
 * @par Performance
 * This is the fastest source for single-threaded event dispatch (`fireEvent`)
 * due to excellent cache locality. However, its performance for adding
 * listeners degrades as the number of listeners increases.
 *
 * @warning A listener object must not be destroyed while a `fireEvent` call is
 * in progress. The `fireEvent` method works on a temporary copy of the listener
 * list. If a listener is destroyed after this copy is made, the source will
 * attempt to call `eventArrived` on a dangling pointer, leading to undefined
 * behavior. It is the responsibility of the listener's owner to ensure its
 * lifetime outlasts any concurrent event dispatch. For scenarios where this
 * lifetime management is complex, consider using the memory-safe
 * `SafeEventSource` instead.
 * @tparam T The event type.
 */
template <typename T>
using FastEventSource = EventSource<T, HybridStoragePolicy<T>>;

/**
 * @brief A thread-safe source optimized for maximum memory safety.
 * @details Uses `std::weak_ptr` to automatically and safely handle listeners
 * that are destroyed without being unregistered, preventing crashes.
 *
 * @par Performance
 * This safety comes at a significant performance cost. `fireEvent` is much
 * slower than `FastEventSource` due to the overhead of locking weak pointers.
 * Adding listeners is also very slow. Use this source only when memory safety
 * is the primary concern.
 * @tparam T The event type.
 */
template <typename T>
using SafeEventSource = EventSource<T, HybridStoragePolicy<T, 16, std::weak_ptr<EventListener<T>>>>;

/**
 * @brief A high-throughput source for read-heavy, high-contention scenarios.
 * @details Uses an Abseil read-write lock (`AbseilReadWritePolicy`) to allow
 * multiple `fireEvent` calls to run in parallel.
 *
 * @par Performance
 * This is the best choice for highly concurrent, read-heavy or mixed
 * read/write workloads. It scales exceptionally well with multiple threads,
 * showing minimal performance degradation under contention.
 *
 * @warning A listener object must not be destroyed while a `fireEvent` call is
 * in progress. The `fireEvent` method works on a temporary copy of the listener
 * list. If a listener is destroyed after this copy is made, the source will
 * attempt to call `eventArrived` on a dangling pointer, leading to undefined
 * behavior. It is the responsibility of the listener's owner to ensure its
 * lifetime outlasts any concurrent event dispatch. For scenarios where this
 * lifetime management is complex, consider using the memory-safe
 * `SafeEventSource` instead.
 * @tparam T The event type.
 */
template <typename T>
using HighContentionEventSource = ConcurrentEventSource<T, AbseilReadWritePolicy<T>>;

/**
 * @brief A full-featured, maximally safe source with a modern callback API.
 * @details This is the recommended choice for modern C++ application code. It
 * layers the convenient `WithCallbacks` API (for lambdas and RAII handles)
 * on top of the memory-safe `SafeEventSource`.
 *
 * @par Performance
 * Inherits the performance characteristics of `SafeEventSource`, making it
 * relatively slow but extremely safe and easy to use.
 * @tparam T The event type.
 */
template <typename T>
using CallbackEventSource = android::base::eventing::WithCallbacks<SafeEventSource<T>>;

}  // namespace android::base::eventing