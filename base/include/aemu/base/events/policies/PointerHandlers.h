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

#include <algorithm>
#include <functional>
#include <memory>
#include <type_traits>

/**
 * @file PointerHandlers.h
 * @brief Provides a centralized trait for handling pointer-specific operations
 * like hashing, comparison, and finding, enabling storage policies to be truly
 * generic.
 */

namespace android::base::eventing {

// Type trait to check if a type is a std::weak_ptr
template <typename T>
struct is_weak_ptr : std::false_type {};
template <typename T>
struct is_weak_ptr<std::weak_ptr<T>> : std::true_type {};
template <typename T>
inline constexpr bool is_weak_ptr_v = is_weak_ptr<T>::value;

// Type trait to check if a type is a std::shared_ptr
template <typename T>
struct is_shared_ptr : std::false_type {};
template <typename T>
struct is_shared_ptr<std::shared_ptr<T>> : std::true_type {};
template <typename T>
inline constexpr bool is_shared_ptr_v = is_shared_ptr<T>::value;

/**
 * @brief A functor that provides `operator()` to compare two `std::weak_ptr`
 * instances for equality by locking them first.
 */
template <typename T>
struct WeakPtrEqual {
    bool operator()(const std::weak_ptr<T>& a, const std::weak_ptr<T>& b) const {
        return a.lock() == b.lock();
    }
};

/**
 * @brief A functor that provides `operator()` to hash a `std::weak_ptr` by
 * locking it and hashing the underlying raw pointer.
 */
template <typename T>
struct WeakPtrHash {
    size_t operator()(const std::weak_ptr<T>& wp) const {
        if (auto sp = wp.lock()) {
            return std::hash<T*>()(sp.get());
        }
        return 0;  // A consistent hash for all expired pointers.
    }
};

/**
 * @brief A type trait to abstract away pointer-specific operations.
 *
 * @details
 * The C++ standard library does not provide uniform support for all pointer types.
 * For example, `std::weak_ptr` cannot be used in a `std::unordered_set` because
 * it lacks `operator==` and a `std::hash` specialization.
 *
 * This trait centralizes the logic for handling different pointer types. Storage
 * policies can delegate finding, hashing, and comparison operations to this
 * trait, allowing them to remain simple and truly generic. This makes it
 * possible for policies like `UnorderedSetStoragePolicy` to work seamlessly with
 * `std::weak_ptr` and enables easy extension for future pointer types.
 *
 * @tparam PtrType The pointer type to handle (e.g., `T*`, `std::shared_ptr<T>`,
 * `std::weak_ptr<T>`).
 */
template <typename PtrType>
struct PointerHandlers {
    /// @brief The appropriate hashing functor for the pointer type.
    using Hash = std::hash<PtrType>;
    /// @brief The appropriate equality comparison functor for the pointer type.
    using Equal = std::equal_to<PtrType>;

    /**
     * @brief Finds an element in a range using the correct comparison method.
     */
    static auto find(auto begin, auto end, const PtrType& val) {
        return std::find(begin, end, val);
    }

    /**
     * @brief Counts the number of "live" (non-expired) pointers in a container.
     * For non-weak pointers, this is just the container's size.
     */
    template <class Container>
    static size_t count_live(const Container& c) {
        if constexpr (requires { c.size(); }) {
            return c.size();
        } else {
            return c.size;
        }
    }
};

/**
 * @brief A partial specialization of `PointerHandlers` for `std::weak_ptr`.
 *
 * @details
 * This specialization provides the necessary `Hash` and `Equal` functors for
 * using `std::weak_ptr` in hash-based containers. It also provides a custom
 * `find` implementation that uses a safe, lock-based comparison.
 */
template <typename T>
struct PointerHandlers<std::weak_ptr<T>> {
    using Ptr = std::weak_ptr<T>;
    /// @brief A custom hashing functor for `std::weak_ptr`.
    using Hash = WeakPtrHash<T>;
    /// @brief A custom equality comparison functor for `std::weak_ptr`.
    using Equal = WeakPtrEqual<T>;

    /**
     * @brief Finds an element in a range using a safe, lock-based comparison.
     */
    static auto find(auto begin, auto end, const Ptr& val) {
        return std::find_if(begin, end, [&](const Ptr& elem) { return Equal()(elem, val); });
    }

    /**
     * @brief Counts the number of "live" (non-expired) weak pointers in a
     * container.
     */
    template <class Container>
    static size_t count_live(const Container& c) {
        return std::count_if(c.begin(), c.end(), [](const Ptr& p) { return !p.expired(); });
    }
};

// A struct to determine if an event should be passed by value or const reference.
// Pointers and fundamental types are passed by value, everything else by const
// reference.
template <typename T>
struct event_param {
    using type = typename std::conditional<
        std::is_pointer_v<T> || std::is_fundamental_v<T>,
        T,
        const T&>::type;
};

}  // namespace android::base::eventing