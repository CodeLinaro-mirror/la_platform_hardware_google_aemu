// Copyright (C) 2026 The Android Open Source Project
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
#include <gtest/gtest.h>

#include "aemu/base/synchronization/MessageChannel.h"

#include <functional>
#include <memory>

namespace android {
namespace base {
namespace {

// A message type whose "move" is actually a copy (no move operations are
// declared, so moves fall back to the copy operations). This models
// std::function with small-buffer storage in libc++, where moving leaves the
// source still holding a copy of the callable.
struct CopyOnMove {
    CopyOnMove() = default;
    explicit CopyOnMove(std::shared_ptr<int> p) : ptr(std::move(p)) {}
    CopyOnMove(const CopyOnMove&) = default;
    CopyOnMove& operator=(const CopyOnMove&) = default;

    std::shared_ptr<int> ptr;
};

}  // namespace

TEST(MessageChannel, TryReceiveReleasesSlot) {
    MessageChannel<CopyOnMove, 4> channel;
    auto payload = std::make_shared<int>(42);
    std::weak_ptr<int> weak = payload;

    EXPECT_TRUE(channel.trySend(CopyOnMove(std::move(payload))));
    {
        CopyOnMove received;
        EXPECT_TRUE(channel.tryReceive(&received));
        EXPECT_EQ(42, *received.ptr);
    }
    // Once the receiver drops the message, the channel must not retain it.
    EXPECT_TRUE(weak.expired());
}

TEST(MessageChannel, ReceiveReleasesSlot) {
    MessageChannel<CopyOnMove, 4> channel;
    auto payload = std::make_shared<int>(7);
    std::weak_ptr<int> weak = payload;

    EXPECT_TRUE(channel.send(CopyOnMove(std::move(payload))));
    {
        CopyOnMove received;
        EXPECT_TRUE(channel.receive(&received));
    }
    EXPECT_TRUE(weak.expired());
}

TEST(MessageChannel, OptionalReceiveReleasesSlot) {
    MessageChannel<CopyOnMove, 4> channel;
    auto payload = std::make_shared<int>(7);
    std::weak_ptr<int> weak = payload;

    EXPECT_TRUE(channel.send(CopyOnMove(std::move(payload))));
    {
        auto received = channel.receive();
        EXPECT_TRUE(received);
    }
    EXPECT_TRUE(weak.expired());
}

TEST(MessageChannel, TimedReceiveReleasesSlot) {
    MessageChannel<CopyOnMove, 4> channel;
    auto payload = std::make_shared<int>(7);
    std::weak_ptr<int> weak = payload;

    EXPECT_TRUE(channel.send(CopyOnMove(std::move(payload))));
    {
        // Deadline is an absolute wall time; UINT64_MAX never times out, and
        // an item is already available.
        auto received = channel.timedReceive(UINT64_MAX);
        EXPECT_TRUE(received);
    }
    EXPECT_TRUE(weak.expired());
}

// The real-world case: a small std::function closure capturing a shared_ptr.
TEST(MessageChannel, TryReceiveReleasesSmallStdFunction) {
    MessageChannel<std::function<void()>, 4> channel;
    auto payload = std::make_shared<int>(1);
    std::weak_ptr<int> weak = payload;

    EXPECT_TRUE(channel.trySend([p = std::move(payload)] { (void)p; }));
    {
        std::function<void()> received;
        EXPECT_TRUE(channel.tryReceive(&received));
        received();
    }
    EXPECT_TRUE(weak.expired());
}

}  // namespace base
}  // namespace android
