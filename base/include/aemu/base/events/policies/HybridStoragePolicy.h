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

#include <variant>
#include <vector>

#include "aemu/base/events/EventSource.h"

#include "aemu/base/events/policies/VectorStoragePolicy.h"

namespace android::base::eventing {

/**
 * @brief A small, stack-allocated array for the Hybrid policy.
 */
template <class T, size_t CAPACITY>
struct StaticArray {
    T data[CAPACITY];
    uint32_t size = 0;

    const T* begin() const { return data; }
    const T* end() const { return data + size; }
};

/**
 * @brief A composable "meta-policy" that provides a Small Buffer Optimization (SBO)
 * and promotes to a heap-allocated container defined by a `PromotionPolicy`.
 *
 * @details
 * This policy is a powerful tool for performance tuning. It begins by storing
 * listeners in a small, stack-allocated array (`StaticContainer`) to avoid the
 * overhead of heap allocations for the common case of few listeners.
 *
 * When the number of listeners exceeds the static capacity (`Size`), the policy
 * automatically "promotes" its storage. It creates a new, heap-allocated
 * container as defined by the `PromotionPolicy` and moves all existing listeners
 * into it. From that point on, all operations (`add`, `remove`, `copy`, etc.)
 * are delegated to the `PromotionPolicy`.
 *
 * This composable design allows you to combine the zero-allocation benefit for
 * small listener sets with the specific performance characteristics of any other
 * storage policy for large listener sets.
 *
 * @tparam T The event data type (e.g., `MyEvent`).
 * @tparam Size The capacity of the on-stack buffer before promoting.
 * @tparam PointerType The type of pointer to store (e.g., `EventListener<T>*`,
 * `std::weak_ptr<EventListener<T>>`).
 * @tparam PromotionPolicy The storage policy to use after exceeding the static
 * capacity. Defaults to `VectorStoragePolicy`.
 *
 * @par Usage Example 1: Default Behavior (Promote to Vector)
 * This is the simplest use case and is backward-compatible. It's efficient for
 * event sources that are "fire-heavy."
 * @code
 * #include "aemu/base/events/policies/HybridStoragePolicy.h"
 *
 * using DefaultHybridSource = android::base::eventing::HybridSource<MyEvent>;
 * @endcode
 *
 * @par Usage Example 2: Promote to an UnorderedSet
 * This creates a source that is allocation-free for up to 16 listeners, then
 * transitions to a hash set for O(1) average add/remove performance.
 * @code
 * #include "aemu/base/events/policies/HybridStoragePolicy.h"
 * #include "aemu/base/events/policies/UnorderedSetStoragePolicy.h"
 *
 * using HybridSetPolicy = android::base::eventing::HybridStoragePolicy<
 *     MyEvent,
 *     16,
 *     android::base::eventing::EventListener<MyEvent>*,
 *     android::base::eventing::UnorderedSetStoragePolicy<MyEvent>
 * >;
 * using HybridSetSource = android::base::eventing::EventSource<MyEvent, HybridSetPolicy>;
 * @endcode
 */
template <class T,
          int Size = 16,
          class PointerType = EventListener<T>*,
          class PromotionPolicy = VectorStoragePolicy<T, PointerType>>
struct HybridStoragePolicy {
    using Ptr = PointerType;
    using StaticContainer = StaticArray<Ptr, Size>;
    using DynamicContainer = typename PromotionPolicy::Container;
    using Container = std::variant<StaticContainer, DynamicContainer>;

    static void add(Container& container, const Ptr& listener) {
        std::visit(
                [&](auto& storage) {
                    using StorageType = std::decay_t<decltype(storage)>;
                    if constexpr (std::is_same_v<StorageType, StaticContainer>) {
                        auto* end = storage.data + storage.size;
                        if (PointerHandlers<Ptr>::find(storage.data, end, listener) == end) {
                            if (storage.size < Size) {
                                storage.data[storage.size++] = listener;
                            } else {
                                // Promote to the dynamic container
                                DynamicContainer new_storage;
                                for (size_t i = 0; i < Size; ++i) {
                                    PromotionPolicy::add(new_storage, storage.data[i]);
                                }
                                PromotionPolicy::add(new_storage, listener);
                                container = std::move(new_storage);
                            }
                        }
                    } else {
                        // Already promoted, delegate to the promotion policy
                        PromotionPolicy::add(storage, listener);
                    }
                },
                container);
    }

    static void remove(Container& container, const Ptr& listener) {
        std::visit(
                [&](auto& storage) {
                    using StorageType = std::decay_t<decltype(storage)>;
                    if constexpr (std::is_same_v<StorageType, StaticContainer>) {
                        auto* end = storage.data + storage.size;
                        auto* it = PointerHandlers<Ptr>::find(storage.data, end, listener);
                        if (it != end) {
                           std::move(it + 1, end, it);
                           storage.size--;
                        }
                    } else {
                        PromotionPolicy::remove(storage, listener);
                    }
                },
                container);
    }

    static std::vector<Ptr> copy(const Container& container) {
        return std::visit(
                [](const auto& storage) -> std::vector<Ptr> {
                    using StorageType = std::decay_t<decltype(storage)>;
                    if constexpr (std::is_same_v<StorageType, StaticContainer>) {
                        return {storage.data, storage.data + storage.size};
                    } else {
                        return PromotionPolicy::copy(storage);
                    }
                },
                container);
    }

    static size_t size(const Container& container) {
        return std::visit(
                [](const auto& storage) -> size_t {
                    using StorageType = std::decay_t<decltype(storage)>;
                    if constexpr (std::is_same_v<StorageType, StaticContainer>) {
                        return PointerHandlers<Ptr>::count_live(storage);
                    } else {
                        return PromotionPolicy::size(storage);
                    }
                },
                container);
    }

    static void clear(Container& container) { container = StaticContainer{}; }
};

template <class T,
          int Size = 16,
          class PointerType = EventListener<T>*,
          class PromotionPolicy = VectorStoragePolicy<T, PointerType>>
using HybridSource = EventSource<T, HybridStoragePolicy<T, Size, PointerType, PromotionPolicy>>;

template <class T>
using WeakPtrHybridSource =
        EventSource<T, HybridStoragePolicy<T, 16, std::weak_ptr<EventListener<T>>>>;

template <class T,
          int Size = 16,
          class PointerType = EventListener<T>*,
          class PromotionPolicy = VectorStoragePolicy<T, PointerType>>
using BlockingHybridSource =
        BlockingEventSource<T, HybridStoragePolicy<T, Size, PointerType, PromotionPolicy>>;
}  // namespace android::base::eventing