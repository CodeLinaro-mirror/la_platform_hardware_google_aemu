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

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <memory>
#include <mutex>

#include "absl/base/thread_annotations.h"
#include "absl/synchronization/mutex.h"
#include "absl/time/time.h"
#include "aemu/base/events/EventSupport.h"

namespace android {
namespace base {

using Callback = void (*)(void* opaque);
using RegisterCallback = void (*)(Callback frameAvailable, void* opaque);
using RemoveCallback = void (*)(void* opaque);

// An EventWaiter can be used to block and wait for events that are coming from
// qemu. The QEMU engine that produces events must provide the following C
// interface:
//
// - A RegisterCallback function, which can be used to register a Callback. The
//   Callback should be invoked when a new event is available. Registration is
//   identified by the opaque pointer.
// - A RemoveCallback function, that can be used to unregister a Callback
//   identified by the opaque pointer. No events should be delivered after the
//   return of this function. You will see crashes if you do.
//
// Typical usage would be something like:
//
// int cnt = 0;
// EventWaiter frameEvent(&gpu_register_shared_memory_callback,
// &gpu_unregister_shared_memory_callback);
//
// while(cnt < 10) {
//    if (frameEvent.next(std::chrono::seconds(1)) > 0)
//      handleEvent(cnt++);
// }
//
// If you wish to use the EventWaiter without the registration mechanism
// you can use the default constructor. Call the "newEvent" method to
// notify listeners of a newEvent.
class EventWaiter {
   public:
    EventWaiter() = default;
    EventWaiter(RegisterCallback add, RemoveCallback remove);
    ~EventWaiter();

    // Block and wait at most timeout milliseconds until the next event
    // arrives. returns the number of missed events. Note, that this is
    // the least number of events you missed, you might have missed more.
    //
    // |afterEvent| Wait until we have received event number x+1.
    // |timeout|  Maximimum time to wait in milliseconds.
    uint64_t next(uint64_t afterEvent, std::chrono::milliseconds timeout);

    // Block and wait at most timeout milliseconds until the next event
    // arrives. Returns the number of missed events.
    //
    // This convenience method is not thread safe, and might not accurately
    // report events if multiple threads are using the same waiter.
    //
    // |timeout|  Maximimum time to wait in milliseconds.
    uint64_t next(std::chrono::milliseconds timeout);

    // The number of events received by the waiter. This is a monotonically
    // increasing number.
    uint64_t current() const;

    // Call this when a new event has arrived.
    void newEvent();

   private:
    static void callbackForwarder(void* opaque);

    std::mutex mStreamLock;
    std::condition_variable mCv;
    uint64_t mEventCounter{0};
    std::atomic<uint64_t> mLastEvent{0};
    RemoveCallback mRemove{nullptr};
};

/**
 * @brief A generic event waiter class that allows waiting for events from multiple
 * EventChangeSupport instances of different types.
 *
 * This class provides a mechanism to wait for new events arriving through multiple
 * EventChangeSupport instances of potentially different types. It manages a single
 * internal event sequence counter. Any event arriving on any listener will unblock
 * the wait and increment the counter.
 *
 * @tparam Ts... The types of the events to be handled.
 */
template <typename... Ts>
class GenericMultiEventWaiter {
   private:
    template <typename V>
    class InnerEventListener : public GenericEventHandler<V> {
       public:
        InnerEventListener(GenericMultiEventWaiter* waiter, EventChangeSupport<V>* listener)
            : GenericEventHandler<V>(listener), mWaiter(waiter) {}
        ~InnerEventListener() = default;
        void eventArrived(const V event) override { mWaiter->onEventArrived(); }

       private:
        GenericMultiEventWaiter* mWaiter;
    };

    std::tuple<std::unique_ptr<InnerEventListener<Ts>>...> mInnerListeners;

   public:
    /**
     * @brief Constructs a GenericMultiEventWaiter.
     *
     * @param listeners A tuple of pointers to the EventChangeSupport instances.
     */
    explicit GenericMultiEventWaiter(EventChangeSupport<Ts>*... listeners)
        : mInnerListeners(
              std::make_tuple(std::make_unique<InnerEventListener<Ts>>(this, listeners)...)) {}

    ~GenericMultiEventWaiter() = default;

    /**
     * @brief Waits for a new event with a sequence number greater than the given one.
     *
     * This method blocks until either a new event with a higher sequence number than
     * |lastSequenceNumber| arrives or the specified timeout is reached.
     *
     * @param timeout The maximum duration to wait for a new event.
     * @param lastSequenceNumber The sequence number of the last processed event.  The waiter
     *        will only return true if a new event with a greater sequence number arrives.
     * @return True if a new event with a larger sequence number arrived, false if the timeout
     *         was reached.
     */
    bool waitForNextEvent(absl::Duration timeout, uint64_t lastSequenceNumber) const {
        auto nextEvent = [&]() {
            // Note: that the following holds:
            // mEventSequenceMutex.AssertHeld();
            return mEventSequence > lastSequenceNumber;
        };
        absl::MutexLock lock(&mEventSequenceMutex);
        mEventSequenceMutex.AwaitWithTimeout(absl::Condition(&nextEvent), timeout);
        return mEventSequence > lastSequenceNumber;
    }

    /**
     * @brief Waits for any new event to arrive.
     *
     * This method blocks until a new event arrives or the timeout is reached. It is equivalent
     * to calling `waitForNextEvent` with the current event sequence number.  This means the
     * method will return immediately if an event has already arrived *before* this method is
     * called, even if that event has already been processed by another thread.
     *
     * @note This method is not race-free. If an event arrives between retrieving the current
     * sequence number and calling `waitForNextEvent`, this call will miss that event and may
     * wait for the *next* event.  If precise event ordering is essential or if the event
     * source could fire events very rapidly, it's recommended to use the overload
     * `waitForNextEvent(timeout, lastSequenceNumber)` and manage the sequence numbers
     * explicitly.
     *
     * @param timeout The maximum duration to wait for a new event.
     * @return True if a new event arrived, false if the timeout was reached.
     */
    bool waitForNextEvent(absl::Duration timeout) const {
        return waitForNextEvent(timeout, getEventSequence());
    }

    /**
     * @brief Gets the current event sequence number.
     *
     * @return The current event sequence number.
     */
    uint64_t getEventSequence() const {
        absl::MutexLock lock(&mEventSequenceMutex);
        return mEventSequence;
    }

   private:
    void onEventArrived() {
        absl::MutexLock lock(&mEventSequenceMutex);
        mEventSequence++;
    }

    /// The current event sequence number. Guarded by |mEventSequenceMutex|.
    uint64_t mEventSequence ABSL_GUARDED_BY(mEventSequenceMutex) = 0;
    /// Mutex for protecting access to |mEventSequence|.
    mutable absl::Mutex mEventSequenceMutex;
};

// A waiter for a single event of type T.
//
// This is a type alias that simplifies the creation of a GenericMultiEventWaiter
// specifically designed to listen for events of a single type.
// It effectively treats a single-type event scenario as a special case of
// multiple-type event handling.  Using GenericEventWaiter<T> is equivalent to
// using GenericMultiEventWaiter<T>.
template <typename T>
using GenericEventWaiter = GenericMultiEventWaiter<T>;

}  // namespace base
}  // namespace android
