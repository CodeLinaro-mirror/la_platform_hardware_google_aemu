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

#include <mutex>
#include <vector>

#include "aemu/base/events/EventSource.h"
#include "aemu/base/events/policies/PointerHandlers.h"

namespace android::base::eventing {

struct EventDispatcher {
    template <typename Ptr, typename T>
    static void dispatch(const Ptr& listener_ptr, const T& event) {
        if constexpr (is_weak_ptr_v<Ptr>) {
            if (auto locked_ptr = listener_ptr.lock()) {
                locked_ptr->eventArrived(event);
            }
        } else {
            listener_ptr->eventArrived(event);
        }
    }
};

/**
 * @brief A dispatcher policy that copies the listener list before dispatching.
 *
 * This policy locks the mutex, creates a copy of the listeners, and then
 * releases the lock before dispatching the event. This is the default, safe
 * behavior that prevents deadlocks if a listener tries to modify the event
 * source.
 */
struct NonBlockingDispatcher {
    template <class T, class StoragePolicy>
    void dispatch(const T& event, typename StoragePolicy::Container& listeners, std::mutex& lock) {
        using Ptr = typename StoragePolicy::Ptr;
        std::vector<Ptr> listeners_copy;
        {
            const std::lock_guard<std::mutex> guard(lock);
            listeners_copy = StoragePolicy::copy(listeners);
        }

        for (const auto& listener_ptr : listeners_copy) {
            EventDispatcher::dispatch(listener_ptr, event);
        }
    }
};

/**
 * @brief A dispatcher policy that holds a lock during the entire dispatch.
 *
 * This policy holds the mutex for the entire duration of the event dispatch.
 * It iterates over the original listener container, which can provide a minor
 * performance benefit by avoiding a copy.
 *
 * @warning This pattern can easily lead to **DEADLOCK** if any listener tries
 * to add or remove another listener from within its `eventArrived()` callback.
 * Use with extreme caution.
 */
struct BlockingDispatcher {
    template <class T, class StoragePolicy>
    void dispatch(const T& event, typename StoragePolicy::Container& listeners, std::mutex& lock) {
        const std::lock_guard<std::mutex> guard(lock);
        for (const auto& listener_ptr : listeners) {
            EventDispatcher::dispatch(listener_ptr, event);
        }
    }
};

}  // namespace android::base::eventing
