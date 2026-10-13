// Copyright 2014 The Android Open Source Project
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

#include "aemu/base/Optional.h"
#include "aemu/base/synchronization/ConditionVariable.h"
#include "aemu/base/synchronization/Lock.h"

#include <utility>
#include <stddef.h>

namespace android {
namespace base {

// Base non-templated class used to reduce the amount of template
// specialization.
class MessageChannelBase {
public:
    // Get the current channel size
    size_t size() const;

    // Abort the currently pending operations and don't allow any other ones
    void stop();

    // Check if the channel is stopped.
    bool isStopped() const;

    // Block until the channel has no pending messages.
    void waitForEmpty();

protected:
    // Constructor. |capacity| is the buffer capacity in messages.
    MessageChannelBase(size_t capacity);

    // Destructor.
    ~MessageChannelBase() = default;

    // Call this method in the sender thread before writing a new message.
    // This returns the position of the available slot in the message array
    // where to copy the new fixed-size message. After the copy, call
    // afterWrite().
    // If the channel is stopped, return value is undefined.
    size_t beforeWrite() EXCLUDES(mLock) ACQUIRE(mLock);

    // Same as beforeWrite(), but returns an empty optional if there was
    // no room to write to instead of waiting for it.
    // One still needs to call afterWrite() anyway.
    Optional<size_t> beforeTryWrite() EXCLUDES(mLock) ACQUIRE(mLock);

    // To be called after trying to write a new fixed-size message (which should
    // happen after beforeWrite() or beforeTryWrite()).
    // |success| must be true to indicate that a new item was added to the
    // channel, or false otherwise (i.e. if the channel is stopped, or if
    // beforeTryWrite() returned an empty optional).
    void afterWrite(bool success) REQUIRES(mLock) RELEASE(mLock);

    // Call this method in the receiver thread before reading a new message.
    // This returns the position in the message array where the new message
    // can be read. Caller must process the message, then call afterRead().
    // If the channel is stopped, return value is undefined.
    size_t beforeRead() EXCLUDES(mLock) ACQUIRE(mLock);

    // Same as beforeRead(), but returns an empty optional if there was
    // no data to read instead of waiting for it.
    // One still needs to call afterWrite() anyway.
    Optional<size_t> beforeTryRead() EXCLUDES(mLock) ACQUIRE(mLock);

    // Same as beforeRead(), but returns an empty optional if no data arrived
    // by the |wallTimeUs| absolute time. One still needs to call
    // afterWrite() anyway.
    Optional<size_t> beforeTimedRead(uint64_t wallTimeUs) EXCLUDES(mLock) ACQUIRE(mLock);

    // To be called after reading a fixed-size message from the channel (which
    // must happen after beforeRead() or beforeTryRead()).
    // |success| must be true to indicate that a message was read, or false
    // otherwise (i.e. if the channel is stopped or if beforeTryRead() returned
    // an empty optional).
    void afterRead(bool success) REQUIRES(mLock) RELEASE(mLock);

    // A version of isStopped() that doesn't lock the channel but expects it
    // to be locked by the caller.
    bool isStoppedLocked() const { return mStopped; }

private:
    size_t mPos = 0;
    size_t mCapacity;
    size_t mCount = 0;
    bool mStopped = false;
    mutable Lock mLock;     // Mutable to allow const members to lock it.
    ConditionVariable mCanRead;
    ConditionVariable mCanWrite;
};

// Helper class used to implement an uni-directional IPC channel between
// two threads. The channel can be used to send fixed-size messages of type
// |T|, with an internal buffer size of |CAPACITY| items. All calls are
// blocking.
//
// Usage is pretty straightforward:
//
//   - From the sender thread, call send(msg);
//   - From the receiver thread, call receive(&msg);
//   - If you want to stop the IPC, call stop();
template <typename T, size_t CAPACITY>
class MessageChannel : public MessageChannelBase {
public:
    MessageChannel() : MessageChannelBase(CAPACITY) {}

    bool send(const T& msg) {
        const size_t pos = beforeWrite();
        const bool res = !isStoppedLocked();
        if (res) {
            mItems[pos] = msg;
        }
        afterWrite(res);
        return res;
    }

    bool send(T&& msg) {
        const size_t pos = beforeWrite();
        const bool res = !isStoppedLocked();
        if (res) {
            mItems[pos] = std::move(msg);
        }
        afterWrite(res);
        return res;
    }

    bool trySend(const T& msg) {
        const auto pos = beforeTryWrite();
        if (pos) {
            mItems[*pos] = msg;
        }
        afterWrite(pos);
        return pos;
    }

    bool trySend(T&& msg) {
        const auto pos = beforeTryWrite();
        if (pos) {
            mItems[*pos] = std::move(msg);
        }
        afterWrite(pos);
        return pos;
    }

    bool receive(T* msg) {
        T item;
        const size_t pos = beforeRead();
        const bool res = !isStoppedLocked();
        if (res) {
            takeSlot(pos, &item);
        }
        afterRead(res);
        if (res) {
            *msg = std::move(item);
        }
        return res;
    }

    Optional<T> receive() {
        T item;
        const size_t pos = beforeRead();
        const bool res = !isStoppedLocked();
        if (res) {
            takeSlot(pos, &item);
        }
        afterRead(res);
        if (!res) {
            return {};
        }
        return Optional<T>(std::move(item));
    }

    bool tryReceive(T* msg) {
        T item;
        const auto pos = beforeTryRead();
        if (pos) {
            takeSlot(*pos, &item);
        }
        afterRead(pos);
        if (pos) {
            *msg = std::move(item);
        }
        return pos;
    }

    Optional<T> timedReceive(uint64_t wallTimeUs) {
        T item;
        const auto pos = beforeTimedRead(wallTimeUs);
        const bool res = pos && !isStoppedLocked();
        if (res) {
            takeSlot(*pos, &item);
        }
        afterRead(res);
        if (!res) {
            return {};
        }
        return Optional<T>(std::move(item));
    }

    constexpr size_t capacity() const { return CAPACITY; }

private:
 // Moves the message at |pos| into |*item| and leaves a default-constructed
 // T in the slot. A plain move is not enough: a moved-from T is only
 // guaranteed to be valid, not empty (e.g. libc++'s std::function copies
 // callables held in its small buffer when moved, leaving the source
 // intact), so the channel would keep received messages, and anything they
 // own, alive until the slot is overwritten. |*item| must be
 // default-constructed; callers destroy any leftover state after releasing
 // mLock. Must be called with mLock held.
 void takeSlot(size_t pos, T* item) {
     using std::swap;
     swap(*item, mItems[pos]);
 }

    T mItems[CAPACITY];
};

}  // namespace base
}  // namespace android
