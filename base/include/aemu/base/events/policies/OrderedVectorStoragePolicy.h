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

namespace android::base::eventing {

/**
 * @brief A storage policy using a `std::vector` sorted by pointer address.
 * @details This policy maintains a sorted list of listeners. This allows it to
 * find the correct position for an add/remove in `O(log N)` time. However, the
 * insertion or erasure itself is still an `O(N)` operation. Iteration remains
 * fast and cache-friendly.
 *
 * @par Performance
 * This policy provides a good balance between the fast iteration of a vector
 * and the faster lookups of a set. It is a strong general-purpose choice.
 */
template <class T, class PointerType = EventListener<T>*>
struct OrderedVectorStoragePolicy {
    using Ptr = PointerType;
    using Container = std::vector<Ptr>;

    static void add(Container& container, Ptr listener) {
        auto it = std::lower_bound(container.begin(), container.end(), listener);
        if (it == container.end() || *it != listener) {
            container.insert(it, listener);
        }
    }
    static void remove(Container& container, Ptr listener) {
        auto it = std::lower_bound(container.begin(), container.end(), listener);
        if (it != container.end() && *it == listener) {
            container.erase(it);
        }
    }
    static std::vector<Ptr> copy(const Container& container) { return container; }
    static size_t size(const Container& container) { return container.size(); }
    static void clear(Container& container) { container.clear(); }
};

template <class T, class PointerType = EventListener<T>*>
using OrderedVectorSource = EventSource<T, OrderedVectorStoragePolicy<T, PointerType>>;

template <class T>
using WeakPtrOrderedVectorSource = EventSource<T, OrderedVectorStoragePolicy<T, std::weak_ptr<EventListener<T>>>>;

template <class T, class PointerType = EventListener<T>*>
using BlockingOrderedVectorSource = BlockingEventSource<T, OrderedVectorStoragePolicy<T, PointerType>>;

}  // namespace android::base::eventing