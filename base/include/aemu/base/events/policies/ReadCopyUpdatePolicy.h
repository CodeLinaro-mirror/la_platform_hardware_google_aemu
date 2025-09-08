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

#include <vector>

#include "aemu/base/events/EventSource.h"
#include "absl/synchronization/mutex.h"

#include "aemu/base/events/policies/PointerHandlers.h"

namespace android::base::eventing {

/**
 * @brief A self-synchronizing policy emulating Read-Copy-Update (RCU) via copy-on-write.
 * @details This policy is designed for high read concurrency. Readers (`fireEvent`)
 * take a fast shared lock on the current data. Writers (`addListener`,
 * `removeListener`) take an exclusive lock, copy the entire listener list,
 * modify the copy, and then swap it into place. This makes writes expensive
 * but minimizes the time writers hold the lock, benefiting readers.
 *
 * @par Performance
 * **Best for:** Very high-frequency, "read-heavy" workloads where write
 * performance is not a primary concern. It scales well for concurrent reads.
 * **Worst for:** "Write-heavy" workloads, as it is the slowest policy for
 * adding listeners.
 * @note Must be used with a lock-free host class like `ConcurrentEventSource`.
 */
template <class T, class PointerType = EventListener<T>*>
struct ReadCopyUpdatePolicy {
    using Ptr = PointerType;
    struct Storage {
        mutable absl::Mutex mutex;
        std::vector<Ptr> listeners ABSL_GUARDED_BY(mutex);
    };
    using Container = Storage;

    static void add(Container& storage, const Ptr& listener) {
        absl::MutexLock lock(&storage.mutex);
        auto new_listeners = storage.listeners;
        if (PointerHandlers<Ptr>::find(new_listeners.begin(), new_listeners.end(), listener) == new_listeners.end()) {
            new_listeners.push_back(listener);
            storage.listeners = std::move(new_listeners);
        }
    }
    static void remove(Container& storage, const Ptr& listener) {
        absl::MutexLock lock(&storage.mutex);
        auto new_listeners = storage.listeners;
        auto it = PointerHandlers<Ptr>::find(new_listeners.begin(), new_listeners.end(), listener);
        if (it != new_listeners.end()) {
            new_listeners.erase(it);
            storage.listeners = std::move(new_listeners);
        }
    }
    static std::vector<Ptr> copy(const Container& storage) {
        absl::ReaderMutexLock lock(&storage.mutex);
        return storage.listeners;
    }
    static size_t size(const Container& storage) {
        absl::ReaderMutexLock lock(&storage.mutex);
        return storage.listeners.size();
    }
    static void clear(Container& storage) {
        absl::MutexLock lock(&storage.mutex);
        storage.listeners.clear();
    }
};

template <class T, class PointerType = EventListener<T>*>
using RcuSource = ConcurrentEventSource<T, ReadCopyUpdatePolicy<T, PointerType>>;

template <class T>
using WeakPtrRcuSource = ConcurrentEventSource<T, ReadCopyUpdatePolicy<T, std::weak_ptr<EventListener<T>>>>;

}  // namespace android::base::eventing