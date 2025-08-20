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

#include "aemu/base/events/EventSupport.h"

#include <gtest/gtest.h>

using android::base::EventChangeSupport;
using android::base::EventListener;
using android::base::GenericEventHandler;

namespace {

// A simple event type for testing.
struct TestEvent {
    int value;
};

// A listener that records the last event it received.
class TestListener : public EventListener<TestEvent> {
public:
    void eventArrived(const TestEvent event) override {
        lastEvent = event;
        callCount++;
    }

    TestEvent lastEvent = {0};
    int callCount = 0;
};

// A listener for void events.
class VoidListener : public EventListener<void> {
public:
    void eventArrived() override {
        callCount++;
    }

    int callCount = 0;
};

}  // namespace

TEST(EventSupportTest, AddAndRemoveListener) {
    EventChangeSupport<TestEvent> support;
    TestListener listener;

    EXPECT_EQ(0, support.size());
    EXPECT_FALSE(support.isRegistered(&listener));

    support.addListener(&listener);
    EXPECT_EQ(1, support.size());
    EXPECT_TRUE(support.isRegistered(&listener));

    support.removeListener(&listener);
    EXPECT_EQ(0, support.size());
    EXPECT_FALSE(support.isRegistered(&listener));
}

TEST(EventSupportTest, FireEvent) {
    EventChangeSupport<TestEvent> support;
    TestListener listener1;
    TestListener listener2;

    support.addListener(&listener1);
    support.addListener(&listener2);

    TestEvent event = {42};
    support.fireEvent(event);

    EXPECT_EQ(1, listener1.callCount);
    EXPECT_EQ(42, listener1.lastEvent.value);
    EXPECT_EQ(1, listener2.callCount);
    EXPECT_EQ(42, listener2.lastEvent.value);
}

TEST(EventSupportTest, FireEventNoListeners) {
    EventChangeSupport<TestEvent> support;
    TestEvent event = {42};
    // This should not crash.
    support.fireEvent(event);
}

// Tests for void specialization
TEST(EventSupportTest, VoidAddAndRemoveListener) {
    EventChangeSupport<void> support;
    VoidListener listener;

    EXPECT_EQ(0, support.size());

    support.addListener(&listener);
    EXPECT_EQ(1, support.size());

    support.removeListener(&listener);
    EXPECT_EQ(0, support.size());
}

TEST(EventSupportTest, VoidFireEvent) {
    EventChangeSupport<void> support;
    VoidListener listener1;
    VoidListener listener2;

    support.addListener(&listener1);
    support.addListener(&listener2);

    support.fireEvent();

    EXPECT_EQ(1, listener1.callCount);
    EXPECT_EQ(1, listener2.callCount);
}

// Tests for GenericEventHandler
class TestEventHandler : public GenericEventHandler<TestEvent> {
public:
    TestEventHandler(EventChangeSupport<TestEvent>* support)
        : GenericEventHandler<TestEvent>(support) {}

    void eventArrived(const TestEvent event) override {
        lastEvent = event;
        callCount++;
    }

    void publicUnsubscribe() {
        unsubscribe();
    }

    TestEvent lastEvent = {0};
    int callCount = 0;
};


TEST(EventSupportTest, GenericEventHandlerSubscribesOnConstructionAndDestruction) {
    EventChangeSupport<TestEvent> support;
    EXPECT_EQ(0, support.size());
    {
        TestEventHandler handler(&support);
        EXPECT_EQ(1, support.size());
        EXPECT_TRUE(support.isRegistered(&handler));
    }
    // The handler is out of scope, destructor should have been called.
    EXPECT_EQ(0, support.size());
}

TEST(EventSupportTest, GenericEventHandlerUnsubscribes) {
    EventChangeSupport<TestEvent> support;
    TestEventHandler handler(&support);
    EXPECT_EQ(1, support.size());

    handler.publicUnsubscribe();
    EXPECT_EQ(0, support.size());
    EXPECT_FALSE(support.isRegistered(&handler));

    // Firing event should not call the handler
    handler.callCount = 0;
    support.fireEvent({10});
    EXPECT_EQ(0, handler.callCount);
}

TEST(EventSupportTest, GenericEventHandlerReceivesEvent) {
    EventChangeSupport<TestEvent> support;
    TestEventHandler handler(&support);

    support.fireEvent({123});
    EXPECT_EQ(1, handler.callCount);
    EXPECT_EQ(123, handler.lastEvent.value);
}
