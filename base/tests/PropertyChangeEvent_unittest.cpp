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

#include <string>
#include <thread>

#include "aemu/base/events/PropertyChangeSupport.h"

namespace android {
namespace base {
TEST(PropertyChangeEventTest, InitialValue) {
    PropertyChangeEvent<int> event(10);
    EXPECT_EQ(event.last(), 10);
    EXPECT_EQ(event.prev(), 0);  // Default initialized
}

TEST(PropertyChangeEventTest, UpdateValue) {
    PropertyChangeEvent<std::string> event;
    event.update("first");
    EXPECT_EQ(event.last(), "first");
    EXPECT_EQ(event.prev(), "");  // Default initialized

    event.update("second");
    EXPECT_EQ(event.last(), "second");
    EXPECT_EQ(event.prev(), "first");

    event.update("third");
    EXPECT_EQ(event.last(), "third");
    EXPECT_EQ(event.prev(), "second");
}

TEST(PropertyChangeEventTest, UpdateValueSame) {
    PropertyChangeEvent<std::string> event("initial");
    event.update("initial");
    EXPECT_EQ(event.last(), "initial");
    EXPECT_EQ(event.prev(), "initial");
}

TEST(PropertyChangeEventTest, ThreadSafetyExample) {
    PropertyChangeEvent<int> event;

    auto updateThread = [&](int newValue) { event.update(newValue); };

    std::vector<std::thread> threads;
    for (int i = 0; i < 100; ++i) {
        threads.push_back(std::thread(updateThread, i % 2));
    }

    for (auto& thread : threads) {
        thread.join();
    }

    EXPECT_THAT(event.last(), ::testing::AnyOf(0, 1));
}
}  // namespace base
}  // namespace android