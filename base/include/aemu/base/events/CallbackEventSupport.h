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
#pragma once
#include <memory>
#include <mutex>
#include <unordered_map>

#include "aemu/base/events/EventSupport.h"

namespace android {
namespace base {

/**
 * @brief Mixin template that adds callback support to event-based classes.
 *
 * This template class wraps an existing EventChangeSupport implementation
 * and adds callback functionality while maintaining the original event interface.
 *
 * @tparam Base The base class that implements EventChangeSupport
 * @tparam T The event type
 */
template <template <typename> class Base, typename T>
class WithCallbacks : public Base<T> {
   public:
    using EventCallback = std::function<void(const T&)>;
    using CallbackId = size_t;

    // Inherit constructors from base class
    using Base<T>::Base;

    /**
     * @brief Adds a callback function to handle events.
     *
     * @param callback The function to call when an event occurs.
     * @return CallbackId A unique identifier for the callback registration.
     */
    virtual CallbackId addCallback(EventCallback callback) {
        const std::lock_guard<std::mutex> lock(mCallbackLock);
        CallbackId id = mNextId++;
        mCallbacks[id] = std::move(callback);

        // If this is the first callback, register our internal listener
        if (mCallbacks.size() == 1) {
            if (!mInternalListener) {
                mInternalListener = std::make_unique<InternalListener>(this);
            }
            Base<T>::addListener(mInternalListener.get());
        }

        return id;
    }

    /**
     * @brief Removes a previously registered callback.
     *
     * @param id The identifier returned by addCallback.
     */
    void removeCallback(CallbackId id) {
        const std::lock_guard<std::mutex> lock(mCallbackLock);
        mCallbacks.erase(id);

        // If no callbacks remain, remove our internal listener
        if (mCallbacks.empty() && mInternalListener) {
            Base<T>::removeListener(mInternalListener.get());
        }
    }

    /**
     * @brief Returns the number of registered callbacks.
     */
    size_t callbackCount() const {
        const std::lock_guard<std::mutex> lock(mCallbackLock);
        return mCallbacks.size();
    }

   private:
    /**
     * @brief Internal event listener that bridges between the EventListener
     * interface and callbacks.
     */
    class InternalListener : public EventListener<T> {
       public:
        explicit InternalListener(WithCallbacks* parent) : mParent(parent) {}

        void eventArrived(const T event) override { mParent->notifyCallbacks(event); }

       private:
        WithCallbacks* mParent;
    };

    /**
     * @brief Notifies all registered callbacks about an event.
     */
    void notifyCallbacks(const T& event) {
        const std::lock_guard<std::mutex> lock(mCallbackLock);
        for (const auto& [id, callback] : mCallbacks) {
            callback(event);
        }
    }

    mutable std::mutex mCallbackLock;
    std::unordered_map<CallbackId, EventCallback> mCallbacks;
    CallbackId mNextId = 0;
    std::unique_ptr<InternalListener> mInternalListener;
};

/**
 * @brief Specialization for void events.
 */
template <template <typename> class Base>
class WithCallbacks<Base, void> : public Base<void> {
   public:
    using EventCallback = std::function<void()>;
    using CallbackId = size_t;

    using Base<void>::Base;

    CallbackId addCallback(EventCallback callback) {
        const std::lock_guard<std::mutex> lock(mCallbackLock);
        CallbackId id = mNextId++;
        mCallbacks[id] = std::move(callback);

        if (mCallbacks.size() == 1) {
            if (!mInternalListener) {
                mInternalListener = std::make_unique<InternalListener>(this);
            }
            Base<void>::addListener(mInternalListener.get());
        }

        return id;
    }

    void removeCallback(CallbackId id) {
        const std::lock_guard<std::mutex> lock(mCallbackLock);
        mCallbacks.erase(id);

        if (mCallbacks.empty() && mInternalListener) {
            Base<void>::removeListener(mInternalListener.get());
        }
    }

    size_t callbackCount() const {
        const std::lock_guard<std::mutex> lock(mCallbackLock);
        return mCallbacks.size();
    }

   private:
    class InternalListener : public EventListener<void> {
       public:
        explicit InternalListener(WithCallbacks* parent) : mParent(parent) {}

        void eventArrived() override { mParent->notifyCallbacks(); }

       private:
        WithCallbacks* mParent;
    };

    void notifyCallbacks() {
        const std::lock_guard<std::mutex> lock(mCallbackLock);
        for (const auto& [id, callback] : mCallbacks) {
            callback();
        }
    }

    mutable std::mutex mCallbackLock;
    std::unordered_map<CallbackId, EventCallback> mCallbacks;
    CallbackId mNextId = 0;
    std::unique_ptr<InternalListener> mInternalListener;
};

/**
 * @brief Wrapper class that adds callback support to any EventChangeSupport instance.
 *
 * This class uses composition rather than inheritance, allowing it to work with
 * existing EventChangeSupport implementations without modifying them.
 *
 * @tparam T The event type
 */
template <typename T>
class EventCallbacks {
   public:
    using EventCallback = std::function<void(const T&)>;
    using CallbackId = size_t;

    /**
     * @brief Constructs callback support for an existing EventChangeSupport.
     *
     * @param eventSupport Reference to the existing event support instance
     */
    explicit EventCallbacks(EventChangeSupport<T>& eventSupport) : mEventSupport(eventSupport) {
        mInternalListener = std::make_unique<InternalListener>(this);
        mEventSupport.addListener(mInternalListener.get());
    }

    ~EventCallbacks() { mEventSupport.removeListener(mInternalListener.get()); }

    /**
     * @brief Adds a callback function to handle events.
     *
     * @param callback The function to call when an event occurs.
     * @return CallbackId A unique identifier for the callback registration.
     */
    CallbackId addCallback(EventCallback callback) {
        const std::lock_guard<std::mutex> lock(mCallbackLock);
        CallbackId id = mNextId++;
        mCallbacks[id] = std::move(callback);
        return id;
    }

    /**
     * @brief Removes a previously registered callback.
     *
     * @param id The identifier returned by addCallback.
     */
    void removeCallback(CallbackId id) {
        const std::lock_guard<std::mutex> lock(mCallbackLock);
        mCallbacks.erase(id);
    }

    /**
     * @brief Returns the number of registered callbacks.
     */
    size_t callbackCount() const {
        const std::lock_guard<std::mutex> lock(mCallbackLock);
        return mCallbacks.size();
    }

   private:
    class InternalListener : public EventListener<T> {
       public:
        explicit InternalListener(EventCallbacks* parent) : mParent(parent) {}

        void eventArrived(const T event) override { mParent->notifyCallbacks(event); }

       private:
        EventCallbacks* mParent;
    };

    void notifyCallbacks(const T& event) {
        const std::lock_guard<std::mutex> lock(mCallbackLock);
        for (const auto& [id, callback] : mCallbacks) {
            callback(event);
        }
    }

    EventChangeSupport<T>& mEventSupport;
    mutable std::mutex mCallbackLock;
    std::unordered_map<CallbackId, EventCallback> mCallbacks;
    CallbackId mNextId = 0;
    std::unique_ptr<InternalListener> mInternalListener;
};

/**
 * @brief Specialization for void events.
 */
template <>
class EventCallbacks<void> {
   public:
    using EventCallback = std::function<void()>;
    using CallbackId = size_t;

    explicit EventCallbacks(EventChangeSupport<void>& eventSupport) : mEventSupport(eventSupport) {
        mInternalListener = std::make_unique<InternalListener>(this);
        mEventSupport.addListener(mInternalListener.get());
    }

    ~EventCallbacks() { mEventSupport.removeListener(mInternalListener.get()); }

    CallbackId addCallback(EventCallback callback) {
        const std::lock_guard<std::mutex> lock(mCallbackLock);
        CallbackId id = mNextId++;
        mCallbacks[id] = std::move(callback);
        return id;
    }

    void removeCallback(CallbackId id) {
        const std::lock_guard<std::mutex> lock(mCallbackLock);
        mCallbacks.erase(id);
    }

    size_t callbackCount() const {
        const std::lock_guard<std::mutex> lock(mCallbackLock);
        return mCallbacks.size();
    }

   private:
    class InternalListener : public EventListener<void> {
       public:
        explicit InternalListener(EventCallbacks* parent) : mParent(parent) {}

        void eventArrived() override { mParent->notifyCallbacks(); }

       private:
        EventCallbacks* mParent;
    };

    void notifyCallbacks() {
        const std::lock_guard<std::mutex> lock(mCallbackLock);
        for (const auto& [id, callback] : mCallbacks) {
            callback();
        }
    }

    EventChangeSupport<void>& mEventSupport;
    mutable std::mutex mCallbackLock;
    std::unordered_map<CallbackId, EventCallback> mCallbacks;
    CallbackId mNextId = 0;
    std::unique_ptr<InternalListener> mInternalListener;
};

/**
 * @brief RAII wrapper for automatic callback management.
 *
 * This class automatically unregisters the callback when destroyed.
 * It works with both CallbackEventSupport and WithCallbacks classes.
 *
 * @tparam EventSystem The type of event system (CallbackEventSupport or WithCallbacks)
 * @tparam T The event type
 */
template <typename EventSystem, typename T>
class ScopedEventCallback {
   public:
    using EventCallback = std::function<void(const T&)>;

    /**
     * @brief Constructs a scoped callback and registers it with the event system.
     *
     * @param system Reference to the event system
     * @param callback The callback function to register
     */
    ScopedEventCallback(EventSystem& system, EventCallback callback)
        : mSystem(system), mId(system.addCallback(std::move(callback))) {}

    /**
     * @brief Automatically unregisters the callback on destruction.
     */
    ~ScopedEventCallback() { mSystem.removeCallback(mId); }

    /**
     * @brief Gets the callback ID.
     *
     * @return The unique identifier for this callback
     */
    size_t getId() const { return mId; }

    // Prevent copying to maintain RAII semantics
    ScopedEventCallback(const ScopedEventCallback&) = delete;
    ScopedEventCallback& operator=(const ScopedEventCallback&) = delete;

    // Allow moving
    ScopedEventCallback(ScopedEventCallback&& other) noexcept
        : mSystem(other.mSystem), mId(other.mId) {
        other.mId = std::numeric_limits<size_t>::max();  // Invalidate other's ID
    }

    ScopedEventCallback& operator=(ScopedEventCallback&& other) noexcept {
        if (this != &other) {
            if (mId != std::numeric_limits<size_t>::max()) {
                mSystem.removeCallback(mId);  // Clean up existing callback
            }
            mSystem = other.mSystem;
            mId = other.mId;
            other.mId = std::numeric_limits<size_t>::max();
        }
        return *this;
    }

   private:
    EventSystem& mSystem;
    size_t mId;
};

/**
 * @brief Specialization for void events.
 */
template <typename EventSystem>
class ScopedEventCallback<EventSystem, void> {
   public:
    using EventCallback = std::function<void()>;

    ScopedEventCallback(EventSystem& system, EventCallback callback)
        : mSystem(system), mId(system.addCallback(std::move(callback))) {}

    ~ScopedEventCallback() {
        if (mId != std::numeric_limits<size_t>::max()) {
            mSystem.removeCallback(mId);
        }
    }

    size_t getId() const { return mId; }

    ScopedEventCallback(const ScopedEventCallback&) = delete;
    ScopedEventCallback& operator=(const ScopedEventCallback&) = delete;

    ScopedEventCallback(ScopedEventCallback&& other) noexcept
        : mSystem(other.mSystem), mId(other.mId) {
        other.mId = std::numeric_limits<size_t>::max();
    }

    ScopedEventCallback& operator=(ScopedEventCallback&& other) noexcept {
        if (this != &other) {
            if (mId != std::numeric_limits<size_t>::max()) {
                mSystem.removeCallback(mId);
            }
            mSystem = other.mSystem;
            mId = other.mId;
            other.mId = std::numeric_limits<size_t>::max();
        }
        return *this;
    }

   private:
    EventSystem& mSystem;
    size_t mId;
};

/**
 * @brief Helper function to create a ScopedEventCallback
 *
 * @tparam EventSystem The type of event system
 * @tparam T The event type
 * @param system Reference to the event system
 * @param callback The callback function
 * @return A new ScopedEventCallback instance
 */
template <typename EventSystem, typename T>
auto makeScopedCallback(EventSystem& system,
                        typename ScopedEventCallback<EventSystem, T>::EventCallback callback) {
    return std::make_unique<ScopedEventCallback<EventSystem, T>>(system, std::move(callback));
}
}  // namespace base
}  // namespace android