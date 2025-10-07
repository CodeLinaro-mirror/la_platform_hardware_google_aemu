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
#include <gtest/gtest.h>

#include <thread>

#include "aemu/base/events/EventWaiter.h"

namespace android {
namespace base {

// Test Event type (can be anything)
struct TestEvent {
    int value;
};

struct TestEvent2 {
    float value;
};

struct TestEvent3 {
    bool value;
};

template <typename T>
struct TestEventPolicy {};

TEST(GenericEventWaiterTest, WaitForEvent) {
    EventChangeSupport<TestEvent> support;
    GenericEventWaiter<TestEvent> waiter(&support);

    TestEvent event = {.value = 123};
    support.fireEvent(event);

    EXPECT_TRUE(waiter.waitForNextEvent(absl::Milliseconds(10), 0));
}

TEST(GenericEventWaiterTest, WaitForEventTimeout) {
    EventChangeSupport<TestEvent> support;
    GenericEventWaiter<TestEvent> waiter(&support);

    EXPECT_FALSE(waiter.waitForNextEvent(absl::Milliseconds(10)));
}

TEST(GenericEventWaiterTest, WaitForEventAfterSequence) {
    EventChangeSupport<TestEvent> support;
    GenericEventWaiter<TestEvent> waiter(&support);

    TestEvent event1 = {.value = 1};
    support.fireEvent(event1);
    EXPECT_TRUE(waiter.waitForNextEvent(absl::Milliseconds(0), 0));

    TestEvent event2 = {.value = 2};
    support.fireEvent(event2);
    EXPECT_TRUE(waiter.waitForNextEvent(absl::Milliseconds(0), 1));
}

TEST(GenericEventWaiterTest, WaitForEventAfterSequenceTimeout) {
    EventChangeSupport<TestEvent> support;
    GenericEventWaiter<TestEvent> waiter(&support);

    TestEvent event = {.value = 123};
    support.fireEvent(event);

    EXPECT_FALSE(waiter.waitForNextEvent(absl::Milliseconds(0), 1));
}

TEST(GenericEventWaiterTest, MultithreadedEventWait) {
    EventChangeSupport<TestEvent> support;
    GenericEventWaiter<TestEvent> waiter(&support);

    std::atomic<bool> thread1Finished(false);
    std::atomic<bool> thread2Finished(false);

    std::thread thread1([&]() {
        EXPECT_TRUE(waiter.waitForNextEvent(absl::Milliseconds(500), 0));  // Expect an event
        thread1Finished = true;
    });

    std::thread thread2([&]() {
        TestEvent event = {.value = 456};
        absl::SleepFor(absl::Milliseconds(100));  // Short delay before firing event
        support.fireEvent(event);
        thread2Finished = true;
    });

    thread1.join();
    thread2.join();

    EXPECT_TRUE(thread1Finished);
    EXPECT_TRUE(thread2Finished);

    EXPECT_EQ(waiter.getEventSequence(), 1);  // Verify the event was processed once
}

TEST(GenericEventWaiterTest, MultithreadedEventSequenceWait) {
    EventChangeSupport<TestEvent> support;
    GenericEventWaiter<TestEvent> waiter(&support);

    std::atomic<bool> thread1Finished(false);
    std::atomic<bool> thread2Finished(false);
    std::atomic<bool> thread3Finished(false);

    std::thread thread1([&]() {
        EXPECT_TRUE(waiter.waitForNextEvent(absl::Milliseconds(100), 0));  // Expect event 1
        thread1Finished = true;
    });

    std::thread thread2([&]() {
        TestEvent event1 = {.value = 1};
        absl::SleepFor(absl::Milliseconds(20));  // Short delay before firing event 1
        support.fireEvent(event1);

        TestEvent event2 = {.value = 2};
        support.fireEvent(event2);

        thread2Finished = true;
    });

    std::thread thread3([&]() {
        EXPECT_TRUE(waiter.waitForNextEvent(absl::Milliseconds(100), 1));  // Expect event 2
        thread3Finished = true;
    });

    thread1.join();
    thread2.join();
    thread3.join();

    EXPECT_TRUE(thread1Finished);
    EXPECT_TRUE(thread2Finished);
    EXPECT_TRUE(thread3Finished);

    EXPECT_EQ(waiter.getEventSequence(), 2);  // Verify the events where processed
}
TEST(GenericMultiEventWaiterTest, WaitForAnyEvent) {
    EventChangeSupport<TestEvent> support1;
    EventChangeSupport<TestEvent2> support2;
    GenericMultiEventWaiter<TestEvent, TestEvent2> waiter(&support1, &support2);

    support1.fireEvent({.value = 123});

    EXPECT_TRUE(waiter.waitForNextEvent(absl::Milliseconds(10), 0));
}

TEST(GenericMultiEventWaiterTest, WaitForAnyEventFromSecond) {
    EventChangeSupport<TestEvent> support1;
    EventChangeSupport<TestEvent2> support2;
    GenericMultiEventWaiter<TestEvent, TestEvent2> waiter(&support1, &support2);

    support2.fireEvent({.value = 1.0f});

    EXPECT_TRUE(waiter.waitForNextEvent(absl::Milliseconds(10), 0));
}

TEST(GenericMultiEventWaiterTest, WaitForAnyEventTimeout) {
    EventChangeSupport<TestEvent> support1;
    EventChangeSupport<TestEvent2> support2;
    GenericMultiEventWaiter<TestEvent, TestEvent2> waiter(&support1, &support2);

    EXPECT_FALSE(waiter.waitForNextEvent(absl::Milliseconds(10)));
}

TEST(GenericMultiEventWaiterTest, GetSequence) {
    EventChangeSupport<TestEvent> support1;
    EventChangeSupport<TestEvent2> support2;
    GenericMultiEventWaiter<TestEvent, TestEvent2> waiter(&support1, &support2);
    support1.fireEvent({.value = 123});
    waiter.waitForNextEvent(absl::Milliseconds(10));

    EXPECT_EQ(waiter.getEventSequence(), 1);
    support2.fireEvent({.value = 1.0f});
    waiter.waitForNextEvent(absl::Milliseconds(10));
    EXPECT_EQ(waiter.getEventSequence(), 2);
}

TEST(GenericMultiEventWaiterTest, MultithreadedEventWait) {
    EventChangeSupport<TestEvent> support1;
    EventChangeSupport<TestEvent2> support2;
    GenericMultiEventWaiter<TestEvent, TestEvent2> waiter(&support1, &support2);

    std::atomic<bool> thread1Finished(false);
    std::atomic<bool> thread2Finished(false);

    std::thread thread1([&]() {
        EXPECT_TRUE(waiter.waitForNextEvent(absl::Milliseconds(500)));
        thread1Finished = true;
    });

    std::thread thread2([&]() {
        TestEvent event = {.value = 456};
        absl::SleepFor(absl::Milliseconds(100));
        support1.fireEvent(event);
        thread2Finished = true;
    });

    thread1.join();
    thread2.join();

    EXPECT_TRUE(thread1Finished);
    EXPECT_TRUE(thread2Finished);
    EXPECT_EQ(waiter.getEventSequence(), 1);
}

TEST(GenericMultiEventWaiterTest, MultithreadedEventSequenceWait) {
    EventChangeSupport<TestEvent> support1;
    EventChangeSupport<TestEvent2> support2;
    GenericMultiEventWaiter<TestEvent, TestEvent2> waiter(&support1, &support2);
    std::atomic<bool> thread1Finished(false);
    std::atomic<bool> thread2Finished(false);
    std::atomic<bool> thread3Finished(false);
    std::thread thread1([&]() {
        EXPECT_TRUE(waiter.waitForNextEvent(absl::Milliseconds(100), 0));
        thread1Finished = true;
    });

    std::thread thread2([&]() {
        TestEvent event1 = {.value = 1};
        absl::SleepFor(absl::Milliseconds(20));
        support1.fireEvent(event1);

        TestEvent2 event2 = {.value = 2};
        support2.fireEvent(event2);

        thread2Finished = true;
    });

    std::thread thread3([&]() {
        EXPECT_TRUE(waiter.waitForNextEvent(absl::Milliseconds(100), 1));
        thread3Finished = true;
    });

    thread1.join();
    thread2.join();
    thread3.join();

    EXPECT_TRUE(thread1Finished);
    EXPECT_TRUE(thread2Finished);
    EXPECT_TRUE(thread3Finished);

    EXPECT_EQ(waiter.getEventSequence(), 2);
}
TEST(GenericMultiEventWaiterTest, MultithreadedManyEventSequenceWait) {
    EventChangeSupport<TestEvent> support1;
    EventChangeSupport<TestEvent2> support2;
    EventChangeSupport<TestEvent3> support3;
    GenericMultiEventWaiter<TestEvent, TestEvent2, TestEvent3> waiter(&support1, &support2,
                                                                      &support3);
    std::atomic<bool> thread1Finished(false);
    std::atomic<bool> thread2Finished(false);
    std::atomic<bool> thread3Finished(false);
    std::thread thread1([&]() {
        EXPECT_TRUE(waiter.waitForNextEvent(absl::Milliseconds(100), 0));
        thread1Finished = true;
    });

    std::thread thread2([&]() {
        TestEvent event1 = {.value = 1};
        absl::SleepFor(absl::Milliseconds(20));
        support1.fireEvent(event1);

        TestEvent2 event2 = {.value = 2};
        support2.fireEvent(event2);

        TestEvent3 event3 = {.value = true};
        support3.fireEvent(event3);

        thread2Finished = true;
    });

    std::thread thread3([&]() {
        EXPECT_TRUE(waiter.waitForNextEvent(absl::Milliseconds(100), 2));
        thread3Finished = true;
    });

    thread1.join();
    thread2.join();
    thread3.join();

    EXPECT_TRUE(thread1Finished);
    EXPECT_TRUE(thread2Finished);
    EXPECT_TRUE(thread3Finished);

    EXPECT_EQ(waiter.getEventSequence(), 3);
}

}  // namespace base
}  // namespace android