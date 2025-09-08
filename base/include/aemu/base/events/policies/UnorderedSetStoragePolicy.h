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

#include <unordered_set>
#include <vector>

#include "aemu/base/events/EventSource.h"

#include "aemu/base/events/policies/PointerHandlers.h"

namespace android::base::eventing {

/**
 * @brief A storage policy using `std::unordered_set`.
 * @details This policy provides fast, `O(1)` average time complexity for adding,
 * removing, and checking for the existence of listeners. However, iteration
 * performance can be slower than vector-based approaches due to poor cache
 * locality from pointer chasing.
 *
 * @par Performance
 * **Best for:** "Write-heavy" scenarios with frequent listener additions and
 * removals.
 * **Worst for:** "Fire-heavy" scenarios, as its `fireEvent` performance is
 * significantly slower than vector-based policies.
 */
template <class T, class PointerType = EventListener<T>*>
struct UnorderedSetStoragePolicy {
    /// @brief The pointer type for listeners, a raw pointer.
    using Ptr = PointerType;
    using Handlers = PointerHandlers<Ptr>;

    /// @brief The container type used for storage.
    using Container = std::unordered_set<Ptr, typename Handlers::Hash, typename Handlers::Equal>;

    static void add(Container& container, const Ptr& listener) { container.insert(listener); }
    static void remove(Container& container, const Ptr& listener) { container.erase(listener); }
    static std::vector<Ptr> copy(const Container& container) {
        return {container.begin(), container.end()};
    }
    static size_t size(const Container& container) { return container.size(); }
    static void clear(Container& container) { container.clear(); }
};

template <class T, class PointerType = EventListener<T>*>
using UnorderedSetSource = EventSource<T, UnorderedSetStoragePolicy<T, PointerType>>;

template <class T>
using WeakPtrUnorderedSetSource = EventSource<T, UnorderedSetStoragePolicy<T, std::weak_ptr<EventListener<T>>>>;

template <class T, class PointerType = EventListener<T>*>
using BlockingUnorderedSetSource = BlockingEventSource<T, UnorderedSetStoragePolicy<T, PointerType>>;

}  // namespace android::base::eventing