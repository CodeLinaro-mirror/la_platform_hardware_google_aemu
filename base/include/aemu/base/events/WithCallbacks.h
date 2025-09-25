#pragma once
#include <functional>
#include <memory>
#include <mutex>
#include <unordered_map>
#include <vector>

#include "aemu/base/events/EventSource.h"
#include "aemu/base/events/policies/PointerHandlers.h"

namespace android::base::eventing {

// Helper trait to extract the event type 'T' from any event source
template <typename EventSourceType>
struct event_source_traits;

template <template <typename, typename, typename> class Host,
          typename T,
          typename Policy,
          typename Dispatcher>
struct event_source_traits<Host<T, Policy, Dispatcher>> {
    using event_type = T;
};

template <template <typename, typename> class Host, typename T, typename Policy>
struct event_source_traits<Host<T, Policy>> {
    using event_type = T;
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
 * @brief A mixin that adds a modern, safe, and high-performance callback API
 * to any policy-based event source.
 *
 * @details This class inherits from the provided EventSourceType, gaining its
 * performance characteristics. It adds an ID-based callback system where each
 * callback is managed by its own dedicated internal listener.
 *
 * @tparam EventSourceType The concrete event source class to extend (e.g.,
 * eventing::ThreadSafeEventSource<MyEvent>).
 */
template <typename EventSourceType>
class WithCallbacks : public EventSourceType {
   public:
    using T = typename event_source_traits<EventSourceType>::event_type;
    using EventCallback = std::function<void(const T&)>;
    using CallbackId = size_t;
    using PtrType = typename EventSourceType::Ptr;

    /**
     * @brief A unique pointer that holds a ScopedEventCallback, ensuring the
     * callback is automatically unregistered when the handle goes out of scope.
     * This is the return type of `makeScopedCallback`.
     */
    using ScopedCallbackHandle =
        std::unique_ptr<ScopedEventCallback<WithCallbacks<EventSourceType>, T>>;

    using EventSourceType::EventSourceType;

    /**
     * @brief Adds a callback, creating a dedicated listener for it.
     * @return A unique ID for managing the callback's lifetime.
     */
    virtual CallbackId addCallback(EventCallback callback) {
        auto listener = std::make_shared<InternalListener>(std::move(callback));
        CallbackId id;

        {
            const std::lock_guard<std::mutex> lock(mApiLock);
            id = mNextId++;
            mListenerMap[id] = listener;
        }

        // Add the listener to the underlying high-performance EventSource
        if constexpr (eventing::is_shared_ptr_v<PtrType> || eventing::is_weak_ptr_v<PtrType>) {
            EventSourceType::addListener(listener);
        } else {
            EventSourceType::addListener(listener.get());
        }

        return id;
    }

    /**
     * @brief Removes a callback by its ID.
     */
    void removeCallback(CallbackId id) {
        std::shared_ptr<InternalListener> listener;
        {
            const std::lock_guard<std::mutex> lock(mApiLock);
            auto it = mListenerMap.find(id);
            if (it == mListenerMap.end()) {
                return;
            }
            listener = it->second;
            mListenerMap.erase(it);
        }

        // Remove the listener from the underlying EventSource
        if (listener) {
            if constexpr (eventing::is_shared_ptr_v<PtrType> || eventing::is_weak_ptr_v<PtrType>) {
                EventSourceType::removeListener(listener);
            } else {
                EventSourceType::removeListener(listener.get());
            }
        }
    }

    /**
     * @brief Returns the number of listeners in the underlying event source.
     */
    size_t size() { return EventSourceType::size(); }

    /**
     * @brief Fires an event to all listeners in the underlying event source.
     */
    void fireEvent(typename event_param<T>::type event) {
        EventSourceType::fireEvent(event);
    }

    /**
     * @brief Returns the number of active callbacks.
     */
    size_t callbackCount() const {
        const std::lock_guard<std::mutex> lock(mApiLock);
        return mListenerMap.size();
    }

   private:
    // A dedicated listener that holds a single callback.
    class InternalListener : public eventing::EventListener<T> {
       public:
        explicit InternalListener(EventCallback cb) : mCallback(std::move(cb)) {}
        void eventArrived(typename event_param<T>::type event) override {
            mCallback(event);
        }

       private:
        EventCallback mCallback;
    };

    mutable std::mutex mApiLock;
    CallbackId mNextId = 0;
    std::unordered_map<CallbackId, std::shared_ptr<InternalListener>> mListenerMap;
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

// Helper to deduce the event type from a lambda's signature
template <typename T>
struct function_traits;
template <typename ClassType, typename ReturnType, typename Arg>
struct function_traits<ReturnType (ClassType::*)(Arg) const> {
    using event_type = std::decay_t<Arg>;
};
template <typename ClassType, typename ReturnType>
struct function_traits<ReturnType (ClassType::*)() const> {
    using event_type = void;
};

/**
 * @brief Helper function to create a ScopedEventCallback with automatic type deduction.
 * @param system Reference to the event system
 * @param callback The callback function
 * @return A new ScopedEventCallback instance
 */
template <typename EventSystem, typename F>
auto makeScopedCallback(EventSystem& system, F&& callback) {
    using T = typename function_traits<decltype(&F::operator())>::event_type;
    return std::make_unique<ScopedEventCallback<EventSystem, T>>(system, std::forward<F>(callback));
}

}  // namespace android::base::eventing