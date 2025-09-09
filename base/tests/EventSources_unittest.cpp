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
#include "aemu/base/events/EventSources.h"

#include <gtest/gtest.h>

#include <memory>
#include <mutex>
#include <thread>
#include <vector>

#include "aemu/base/events/MultiEventSourceWaiter.h"

using namespace android::base;
using namespace android::base::eventing;

// A simple event type for testing.
struct TestEvent {
    int value;
    bool operator==(const TestEvent& other) const { return value == other.value; }
};

// A second event type for testing ambiguous sources.
struct TestEvent2 {
    std::string value;
};

// A mock listener that records received events in a thread-safe manner.
template <class T>
class MockEventListener : public android::base::eventing::EventListener<T> {
public:
    void eventArrived(const T& event) override {
        std::lock_guard<std::mutex> lock(mMutex);
        received_events.push_back(event);
    }

    std::vector<T> received_events;
    std::mutex mMutex;
};

// A typed test suite to verify the common interface of all raw-pointer-based
// event sources. This reduces code duplication.
template <typename T>
class EventSourceTest : public ::testing::Test {};

using RawPointerSources =
        ::testing::Types<FastEventSource<TestEvent>, HighContentionEventSource<TestEvent>>;

TYPED_TEST_SUITE(EventSourceTest, RawPointerSources);

TYPED_TEST(EventSourceTest, AddAndFireEvent) {
    TypeParam source;
    MockEventListener<TestEvent> listener;
    TestEvent event{42};

    source.addListener(&listener);
    ASSERT_EQ(source.size(), 1);

    source.fireEvent(event);
    ASSERT_EQ(listener.received_events.size(), 1);
    EXPECT_EQ(listener.received_events[0], event);
}

TYPED_TEST(EventSourceTest, RemoveListener) {
    TypeParam source;
    MockEventListener<TestEvent> listener;
    TestEvent event{42};

    source.addListener(&listener);
    source.removeListener(&listener);
    ASSERT_EQ(source.size(), 0);

    source.fireEvent(event);
    EXPECT_TRUE(listener.received_events.empty());
}

TYPED_TEST(EventSourceTest, FireToMultipleListeners) {
    TypeParam source;
    MockEventListener<TestEvent> listener1, listener2;
    TestEvent event{42};

    source.addListener(&listener1);
    source.addListener(&listener2);
    ASSERT_EQ(source.size(), 2);

    source.fireEvent(event);
    ASSERT_EQ(listener1.received_events.size(), 1);
    EXPECT_EQ(listener1.received_events[0], event);
    ASSERT_EQ(listener2.received_events.size(), 1);
    EXPECT_EQ(listener2.received_events[0], event);
}

// A specific test to verify the basic thread safety of HighContentionEventSource.
TEST(HighContentionEventSourceTest, BasicThreadSafety) {
    HighContentionEventSource<TestEvent> source;
    MockEventListener<TestEvent> listener;
    source.addListener(&listener);

    constexpr int kNumThreads = 4;
    constexpr int kEventsPerThread = 100;

    std::vector<std::thread> threads;
    for (int i = 0; i < kNumThreads; ++i) {
        threads.emplace_back([&source, i]() {
            for (int j = 0; j < kEventsPerThread; ++j) {
                source.fireEvent({i * 1000 + j});
            }
        });
    }

    for (auto& t : threads) {
        t.join();
    }

    EXPECT_EQ(listener.received_events.size(), kNumThreads * kEventsPerThread);
}

// Tests for SafeEventSource, focusing on its weak_ptr behavior.
TEST(SafeEventSourceTest, AddAndFireEvent) {
    SafeEventSource<TestEvent> source;
    auto listener = std::make_shared<MockEventListener<TestEvent>>();
    TestEvent event{42};

    source.addListener(listener);
    ASSERT_EQ(source.size(), 1);

    source.fireEvent(event);
    ASSERT_EQ(listener->received_events.size(), 1);
    EXPECT_EQ(listener->received_events[0], event);
}

TEST(SafeEventSourceTest, HandlesDestroyedListenerGracefully) {
    SafeEventSource<TestEvent> source;
    auto listener = std::make_shared<MockEventListener<TestEvent>>();
    TestEvent event{42};

    source.addListener(listener);
    ASSERT_EQ(source.size(), 1);

    // Destroy the listener object.
    listener.reset();

    // The source should correctly report that there are no more live listeners.
    ASSERT_EQ(source.size(), 0);

    // Firing the event should not crash and should clean up the expired pointer
    // internally.
    source.fireEvent(event);
    ASSERT_EQ(source.size(), 0);
}

// Tests for CallbackEventSource, focusing on the WithCallbacks API.
TEST(CallbackEventSourceTest, AddCallbackAndFire) {
    CallbackEventSource<TestEvent> source;
    std::vector<TestEvent> received_events;
    TestEvent event{42};

    auto handle =
            makeScopedCallback(source, [&](const TestEvent& e) { received_events.push_back(e); });

    ASSERT_EQ(source.size(), 1);

    source.fireEvent(event);
    ASSERT_EQ(received_events.size(), 1);
    EXPECT_EQ(received_events[0], event);
}

TEST(CallbackEventSourceTest, HandleUnsubscribesOnDestruction) {
    CallbackEventSource<TestEvent> source;
    std::vector<TestEvent> received_events;
    TestEvent event{42};

    {
        auto handle = makeScopedCallback(
                source, [&](const TestEvent& e) { received_events.push_back(e); });
        ASSERT_EQ(source.size(), 1);
    }  // The RAII handle goes out of scope here, unsubscribing the callback.

    ASSERT_EQ(source.size(), 0);

    source.fireEvent(event);
    EXPECT_TRUE(received_events.empty());
}

// --- Tests for MultiEventSourceWaiter ---

TEST(MultiEventSourceWaiterTest, WaiterTimesOut) {
    FastEventSource<TestEvent> source;
    MultiEventSourceWaiter waiter;
    waiter.listen(&source);

    uint64_t lastEvent = waiter.getEventSequence();
    EXPECT_FALSE(waiter.waitForNextEvent(absl::Milliseconds(1), lastEvent));
}

TEST(MultiEventSourceWaiterTest, WaiterUnblocksOnSingleSource) {
    FastEventSource<TestEvent> source;
    MultiEventSourceWaiter waiter;
    waiter.listen(&source);

    uint64_t lastEvent = waiter.getEventSequence();
    source.fireEvent({123});

    EXPECT_TRUE(waiter.waitForNextEvent(absl::Seconds(1), lastEvent));
    EXPECT_EQ(waiter.getEventSequence(), lastEvent + 1);
}

TEST(MultiEventSourceWaiterTest, WaiterUnblocksOnMultipleSources) {
    FastEventSource<TestEvent> source1;
    SafeEventSource<TestEvent2> source2;
    MultiEventSourceWaiter waiter;
    waiter.listen(&source1);
    waiter.listen(&source2);

    uint64_t lastEvent = waiter.getEventSequence();

    // Fire the first source
    source1.fireEvent({1});
    EXPECT_TRUE(waiter.waitForNextEvent(absl::Seconds(1), lastEvent));
    EXPECT_EQ(waiter.getEventSequence(), lastEvent + 1);

    // Fire the second source
    lastEvent = waiter.getEventSequence();
    source2.fireEvent({"hello"});
    EXPECT_TRUE(waiter.waitForNextEvent(absl::Seconds(1), lastEvent));
    EXPECT_EQ(waiter.getEventSequence(), lastEvent + 1);
}

TEST(MultiEventSourceWaiterTest, EventFiredBeforeWait) {
    FastEventSource<TestEvent> source;
    MultiEventSourceWaiter waiter;
    waiter.listen(&source);

    uint64_t lastEvent = waiter.getEventSequence();
    source.fireEvent({123});

    // Should return immediately since the sequence number has advanced.
    EXPECT_TRUE(waiter.waitForNextEvent(absl::ZeroDuration(), lastEvent));
}

TEST(MultiEventSourceWaiterTest, CorrectlyUsesSequenceNumber) {
    FastEventSource<TestEvent> source;
    MultiEventSourceWaiter waiter;
    waiter.listen(&source);

    uint64_t seq1 = waiter.getEventSequence();
    source.fireEvent({1});

    // Wait should succeed because seq is now > seq1.
    EXPECT_TRUE(waiter.waitForNextEvent(absl::Seconds(1), seq1));

    uint64_t seq2 = waiter.getEventSequence();
    EXPECT_GT(seq2, seq1);

    // Wait should time out because seq is not > seq2.
    EXPECT_FALSE(waiter.waitForNextEvent(absl::Milliseconds(1), seq2));
}

TEST(MultiEventSourceWaiterTest, HandlesMixedSourceTypes) {
    FastEventSource<TestEvent> fastSource;    // Requires raw pointer
    SafeEventSource<TestEvent2> safeSource;  // Requires weak_ptr
    MultiEventSourceWaiter waiter;

    waiter.listen(&fastSource);
    waiter.listen(&safeSource);

    uint64_t lastEvent = waiter.getEventSequence();
    fastSource.fireEvent({1});
    EXPECT_TRUE(waiter.waitForNextEvent(absl::Seconds(1), lastEvent));

    lastEvent = waiter.getEventSequence();
    safeSource.fireEvent({"test"});
    EXPECT_TRUE(waiter.waitForNextEvent(absl::Seconds(1), lastEvent));
}

// A mock class that inherits from EventSource twice to create ambiguity.
class AmbiguousSource : public FastEventSource<TestEvent>, public SafeEventSource<TestEvent2> {};

TEST(MultiEventSourceWaiterTest, HandlesAmbiguousSource) {
    AmbiguousSource source;
    MultiEventSourceWaiter waiter;

    // Must explicitly cast to the desired base class to resolve ambiguity.
    waiter.listen<FastEventSource<TestEvent>>(
            static_cast<FastEventSource<TestEvent>*>(&source));
    waiter.listen<SafeEventSource<TestEvent2>>(
            static_cast<SafeEventSource<TestEvent2>*>(&source));

    uint64_t lastEvent = waiter.getEventSequence();
    source.FastEventSource<TestEvent>::fireEvent({1});
    EXPECT_TRUE(waiter.waitForNextEvent(absl::Seconds(1), lastEvent));

    lastEvent = waiter.getEventSequence();
    source.SafeEventSource<TestEvent2>::fireEvent({"test"});
    EXPECT_TRUE(waiter.waitForNextEvent(absl::Seconds(1), lastEvent));
}

TEST(MultiEventSourceWaiterTest, UnsubscribesOnDestruction) {
    FastEventSource<TestEvent> source;
    {
        MultiEventSourceWaiter waiter;
        waiter.listen(&source);
        ASSERT_EQ(source.size(), 1);
    }  // Waiter is destroyed here, should unsubscribe.

    ASSERT_EQ(source.size(), 0);
    // Firing should not crash.
    source.fireEvent({1});
}