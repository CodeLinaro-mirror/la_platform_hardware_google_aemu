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
#pragma once

#include <algorithm>
#include <vector>

#include "aemu/base/events/EventSource.h"

#include "aemu/base/events/policies/PointerHandlers.h"

namespace android::base::eventing {

/**
 * @brief A storage policy using `std::vector`.
 * @details This policy's strength is its excellent cache locality, which makes
 * iterating over the listeners during `fireEvent` extremely fast. Its primary
 * weakness is that adding a listener requires an `O(N)` scan to prevent
 * duplicates, making it slow for write-heavy workloads.
 *
 * @par Performance
 * **Best for:** "Fire-heavy" scenarios where listeners are added infrequently
 * but events are dispatched often. It has the fastest single-threaded
 * `fireEvent` performance.
 * **Worst for:** Scenarios with frequent listener additions or removals, as
 * performance degrades quadratically.
 */
template <class T, class PointerType = EventListener<T>*>
struct VectorStoragePolicy {
    using Ptr = PointerType;
    using Container = std::vector<Ptr>;

    static void add(Container& container, const Ptr& listener) {
        if (PointerHandlers<Ptr>::find(container.begin(), container.end(), listener) == container.end()) {
            container.push_back(listener);
        }
    }
    static void remove(Container& container, const Ptr& listener) {
        auto it = PointerHandlers<Ptr>::find(container.begin(), container.end(), listener);
        if (it != container.end()) {
            container.erase(it);
        }
    }
    static std::vector<Ptr> copy(const Container& container) { return container; }
    static size_t size(const Container& container) { return container.size(); }
    static void clear(Container& container) { container.clear(); }
};

template <class T, class PointerType = EventListener<T>*>
using VectorSource = EventSource<T, VectorStoragePolicy<T, PointerType>>;

template <class T>
using WeakPtrVectorSource = EventSource<T, VectorStoragePolicy<T, std::weak_ptr<EventListener<T>>>>;

template <class T, class PointerType = EventListener<T>*>
using BlockingVectorSource = BlockingEventSource<T, VectorStoragePolicy<T, PointerType>>;

}  // namespace android::base::eventing