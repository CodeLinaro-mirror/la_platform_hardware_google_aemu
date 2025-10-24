// Copyright (C) 2024 The Android Open Source Project
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
#include <gmock/gmock.h>
#include <gtest/gtest.h>

#include <chrono>
#include <thread>

#include "aemu/base/events/CallbackEventSupport.h"

namespace android {
namespace base {

// Mock event system for testing
class TestEventSystem : public WithCallbacks<EventChangeSupport, int> {
   public:
    void triggerEvent(int value) { fireEvent(value); }
};

class VoidEventSystem : public WithCallbacks<EventChangeSupport, void> {
   public:
    void triggerEvent() { fireEvent(); }
};

// Mock listener for traditional interface testing
class MockEventListener : public EventListener<int> {
   public:
    MOCK_METHOD(void, eventArrived, (const int), (override));
};

class CallbackTests : public ::testing::Test {
   protected:
    TestEventSystem events;
    VoidEventSystem voidEvents;
};

// Basic functionality tests
TEST_F(CallbackTests, BasicCallbackRegistration) {
    bool callbackCalled = false;
    auto id = events.addCallback([&callbackCalled](const int&) { callbackCalled = true; });

    EXPECT_EQ(events.callbackCount(), 1);
    events.triggerEvent(42);
    EXPECT_TRUE(callbackCalled);

    events.removeCallback(id);
    EXPECT_EQ(events.callbackCount(), 0);
}

TEST_F(CallbackTests, MultipleCallbacks) {
    int count = 0;
    auto id1 = events.addCallback([&count](const int&) { count++; });
    auto id2 = events.addCallback([&count](const int&) { count++; });

    EXPECT_EQ(events.callbackCount(), 2);
    events.triggerEvent(42);
    EXPECT_EQ(count, 2);

    events.removeCallback(id1);
    events.removeCallback(id2);
}

TEST_F(CallbackTests, VoidEventCallback) {
    bool called = false;
    auto id = voidEvents.addCallback([&called]() { called = true; });

    voidEvents.triggerEvent();
    EXPECT_TRUE(called);

    voidEvents.removeCallback(id);
}

// ScopedEventCallback tests
TEST_F(CallbackTests, ScopedCallbackLifetime) {
    int count = 0;
    {
        auto scoped =
            makeScopedCallback(events, [&count](const int&) { count++; });

        EXPECT_EQ(events.callbackCount(), 1);
        events.triggerEvent(42);
        EXPECT_EQ(count, 1);
    }
    // Scoped callback should be automatically unregistered
    EXPECT_EQ(events.callbackCount(), 0);
    events.triggerEvent(42);
    EXPECT_EQ(count, 1);  // Count shouldn't increase
}

TEST_F(CallbackTests, ScopedCallbackMove) {
    int count = 0;
    auto scoped =
        makeScopedCallback(events, [&count](const int&) { count++; });

    // Move the callback
    auto moved = std::move(scoped);
    events.triggerEvent(42);
    EXPECT_EQ(count, 1);
    EXPECT_EQ(events.callbackCount(), 1);
}

// Thread safety tests
TEST_F(CallbackTests, ThreadSafety) {
    constexpr int NUM_THREADS = 10;
    constexpr int OPERATIONS_PER_THREAD = 100;

    std::atomic<int> totalCallbacks{0};

    {
        std::vector<std::thread> threads;
        // Launch threads that add and remove callbacks
        for (int i = 0; i < NUM_THREADS; ++i) {
            threads.emplace_back([this, &totalCallbacks]() {
                for (int j = 0; j < OPERATIONS_PER_THREAD; ++j) {
                    auto id =
                        events.addCallback([&totalCallbacks](const int&) { ++totalCallbacks; });
                    events.triggerEvent(j);
                    events.removeCallback(id);
                }
            });
        }

        // Join all threads
        for (auto& thread : threads) {
            thread.join();
        }
    }

    // Verify final state:
    // all calbacks are moved
    EXPECT_EQ(events.callbackCount(), 0);
    // We will get exactly (NUM_THREADS * OPERATIONS_PER_THREAD) if
    // addCallback/triggerEvent/removeCallback don't interleave and greater otherwise
    // i.e. triggerEvent triggers callbacks added (but not yet removed) by other threads.
    EXPECT_GE(totalCallbacks, NUM_THREADS * OPERATIONS_PER_THREAD);
}

// Integration with traditional EventListener interface
TEST_F(CallbackTests, MixedListenerCallbackUsage) {
    MockEventListener mockListener;
    events.addListener(&mockListener);

    int callbackCount = 0;
    auto id = events.addCallback([&callbackCount](const int&) { callbackCount++; });

    EXPECT_CALL(mockListener, eventArrived(42)).Times(1);

    events.triggerEvent(42);
    EXPECT_EQ(callbackCount, 1);

    events.removeCallback(id);
    events.removeListener(&mockListener);
}

// Error handling tests
TEST_F(CallbackTests, RemoveNonexistentCallback) {
    events.removeCallback(999);  // Should not crash
    EXPECT_EQ(events.callbackCount(), 0);
}

TEST_F(CallbackTests, RemoveCallbackTwice) {
    auto id = events.addCallback([](const int&) {});
    events.removeCallback(id);
    events.removeCallback(id);  // Should not crash
    EXPECT_EQ(events.callbackCount(), 0);
}

// Exception safety tests
TEST_F(CallbackTests, CallbackThrowsException) {
    auto id = events.addCallback([](const int&) { throw std::runtime_error("Test exception"); });

    EXPECT_NO_THROW({
        try {
            events.triggerEvent(42);
        } catch (const std::runtime_error&) {
            // Expected exception
        }
    });

    // System should remain in consistent state
    EXPECT_EQ(events.callbackCount(), 1);
    events.removeCallback(id);
    EXPECT_EQ(events.callbackCount(), 0);
}

// Memory leak tests (requires running with sanitizers)
TEST_F(CallbackTests, NoMemoryLeaks) {
    for (int i = 0; i < 1000; ++i) {
        auto scoped = makeScopedCallback(events, [](const int&) {});
        events.triggerEvent(42);
    }
    EXPECT_EQ(events.callbackCount(), 0);
}

}  // namespace base
}  // namespace android