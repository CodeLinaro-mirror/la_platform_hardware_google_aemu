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

#include <memory>
#include <mutex>
#include <vector>

#include "aemu/base/events/policies/DispatchPolicies.h"
#include "aemu/base/events/policies/PointerHandlers.h"
/**
 * @file EventSource.h
 * @brief Provides the core, policy-agnostic host classes for the eventing system.
 * This file defines the `EventListener` interface and the `EventSource`,
 * `ConcurrentEventSource`, and `BlockingEventSource` host classes that
 * dispatch events to listeners.
 */

namespace android::base::eventing {

/**
 * @brief An abstract interface for an object that can receive events.
 * @tparam T The type of the event data.
 */
template <class T>
class EventListener {
   public:
    virtual ~EventListener() = default;
    /**
     * @brief Called when an event has been fired from a source.
     * @param event The event data.
     */
    virtual void eventArrived(typename event_param<T>::type event) = 0;
};

// Host classes

/**
 * @brief An event dispatcher that uses a global `std::mutex` for thread safety.
 * @tparam T The event type.
 * @tparam StoragePolicy A non-synchronized storage policy (e.g., `UnorderedSetStoragePolicy`).
 * @details This is a general-purpose event source. It protects its listener list
 * with a mutex, ensuring that adding, removing, and firing events are all
 * thread-safe operations. When firing an event, it creates a copy of the
 * listener list to avoid holding the lock during the callbacks, preventing deadlocks.
 */
template <class T,
          class StoragePolicy,
          class DispatcherPolicy = NonBlockingDispatcher>
class EventSource {
   public:
    /// @brief The event type T.
    using EventType = T;
    /// @brief The pointer type for listeners, defined by the storage policy.
    using Ptr = typename StoragePolicy::Ptr;

   private:
    typename StoragePolicy::Container mListeners;
    std::mutex mListenerLock;
    DispatcherPolicy mDispatcher;

   public:
    template <typename... Args>
    EventSource(Args&&... args) : mDispatcher(std::forward<Args>(args)...) {}

    /**
     * @brief Adds a listener to the source.
     * @param listener The listener to add.
     */
    void addListener(Ptr listener) {
        const std::lock_guard<std::mutex> lock(mListenerLock);
        StoragePolicy::add(mListeners, listener);
    }

    /**
     * @brief Removes a listener from the source.
     * @param listener The listener to remove.
     */
    void removeListener(Ptr listener) {
        const std::lock_guard<std::mutex> lock(mListenerLock);
        StoragePolicy::remove(mListeners, listener);
    }

    /**
     * @brief Fires an event, notifying all registered listeners.
     * @param event The event data to send.
     */
    void fireEvent(const T& event) {
        mDispatcher.template dispatch<T, StoragePolicy>(event, mListeners,
                                                        mListenerLock);
    }

    /**
     * @brief Returns the number of listeners.
     * @return The current number of registered listeners.
     */
    size_t size() {
        const std::lock_guard<std::mutex> lock(mListenerLock);
        return StoragePolicy::size(mListeners);
    }

    /**
     * @brief Removes all listeners from the source.
     */
    void clear() {
        const std::lock_guard<std::mutex> lock(mListenerLock);
        StoragePolicy::clear(mListeners);
    }
};

/**
 * @brief An event dispatcher designed for self-synchronizing storage policies.
 * @tparam T The event type.
 * @tparam SelfSyncStoragePolicy A policy that handles its own locking (e.g.,
 * `AbseilReadWritePolicy`).
 * @details This host class is lock-free and delegates all synchronization logic
 * to the storage policy itself. It is suitable for high-performance scenarios
 * where the locking strategy is integral to the data structure, such as with
 * read-copy-update (RCU) or read-write locks.
 */
template <class T, class SelfSyncStoragePolicy>
class ConcurrentEventSource {
   public:
    /// @brief The event type T.
    using EventType = T;
    /// @brief The pointer type for listeners, defined by the storage policy.
    using Ptr = typename SelfSyncStoragePolicy::Ptr;
    typename SelfSyncStoragePolicy::Container mListeners;

    ConcurrentEventSource() = default;

    /**
     * @brief Adds a listener to the source.
     * @param listener The listener to add.
     */
    void addListener(Ptr listener) { SelfSyncStoragePolicy::add(mListeners, listener); }

    /**
     * @brief Removes a listener from the source.
     * @param listener The listener to remove.
     */
    void removeListener(Ptr listener) { SelfSyncStoragePolicy::remove(mListeners, listener); }

    /**
     * @brief Fires an event, notifying all registered listeners.
     * @param event The event data to send.
     */
    void fireEvent(const T& event) {
        std::vector<Ptr> listeners_copy = SelfSyncStoragePolicy::copy(mListeners);
        for (const auto& listener_ptr : listeners_copy) {
            EventDispatcher::dispatch(listener_ptr, event);
        }
    }

    /**
     * @brief Returns the number of listeners.
     * @return The current number of registered listeners.
     */
    size_t size() { return SelfSyncStoragePolicy::size(mListeners); }

    /**
     * @brief Removes all listeners from the source.
     */
    void clear() { SelfSyncStoragePolicy::clear(mListeners); }
};

/**
 * @brief An event dispatcher that holds a lock for the entire duration of event dispatch.
 * @tparam T The event type.
 * @tparam StoragePolicy A non-synchronized storage policy.
 * @warning This is an alias for EventSource with a BlockingDispatcher policy.
 * This pattern can easily lead to **DEADLOCK**. Use with extreme caution.
 */
template <class T, class StoragePolicy>
using BlockingEventSource = EventSource<T, StoragePolicy, BlockingDispatcher>;

}  // namespace android::base::eventing