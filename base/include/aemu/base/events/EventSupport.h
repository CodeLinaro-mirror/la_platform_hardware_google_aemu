// Copyright (C) 2020 The Android Open Source Project
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
#include <mutex>
#include <unordered_set>

#define DEBUG_EVT 0

#if DEBUG_EVT >= 1
#define DD_EVT(fmt, ...) printf("EventSupport: %s:%d| " fmt "\n", __func__, __LINE__, ##__VA_ARGS__)
#else
#define DD_EVT(...) (void)0
#endif

namespace android {
namespace base {
/**
 * @brief Abstract base class for objects that want to listen for events.
 *
 * Objects that want to listen for events must inherit from this class and
 * implement the `eventArrived()` method.
 *
 * @tparam T The type of event that this listener can handle.
 */
template <class T>
class EventListener {
   public:
    virtual ~EventListener() = default;

    /**
     * @brief Called when an event of type `T` arrives.
     *
     * Derived classes must implement this method to handle the event.
     *
     * @param event The event that arrived.
     */
    virtual void eventArrived(const T event) = 0;
};

template <>
class EventListener<void> {
   public:
    virtual ~EventListener() = default;

    /**
     * @brief Called when an event arrives.
     *
     * Derived classes must implement this method to handle the event.
     */
    virtual void eventArrived() = 0;
};

/**
 * @brief Provides an event-driven programming mechanism.
 *
 * Objects can register themselves as listeners to events of type `T`, and
 * events can be fired, triggering the registered listeners.
 *
 * @tparam T The type of event that this class can handle.
 */
template <class T>
class EventChangeSupport {
   public:
    EventChangeSupport() = default;
    ~EventChangeSupport() = default;

    /**
     * @brief Adds an event listener to this object.
     *
     * The listener will receive events of type `T` that are fired by this
     * object.
     *
     * @param listener A pointer to the listener object to add.
     */
    virtual void addListener(EventListener<T>* listener) {
        const std::lock_guard<std::mutex> lock(mListenerLock);
        DD_EVT("Adding %p", listener);
        mListeners.insert(listener);
    }

    /**
     * @brief Removes an event listener from this object.
     *
     * The listener will no longer receive events of type `T` that are fired by
     * this object.
     *
     * @param listener A pointer to the listener object to remove.
     */
    void removeListener(EventListener<T>* listener) {
        const std::lock_guard<std::mutex> lock(mListenerLock);
        DD_EVT("Removing %p", listener);
        mListeners.erase(listener);
    }

    /**
     * @brief Fires an event of type `T`.
     *
     * This method triggers all the registered listeners with the given event.
     *
     * @param event The event to fire.
     */
    void fireEvent(const T event) {
        const std::lock_guard<std::mutex> lock(mListenerLock);
        for (auto listener : mListeners) {
            DD_EVT("Informing %p", listener);
            listener->eventArrived(event);
        }
    }

    int size() {
        const std::lock_guard<std::mutex> lock(mListenerLock);
        return mListeners.size();
    }

   private:
    std::mutex mListenerLock;  // A mutex to protect the listener set
    std::unordered_set<EventListener<T>*> mListeners;
};

/**
 * @brief Provides an event-driven programming mechanism.
 *
 * Objects can register themselves as listeners to events of type `T`, and
 * events can be fired, triggering the registered listener.
 */
template <>
class EventChangeSupport<void> {
   public:
    EventChangeSupport() = default;
    ~EventChangeSupport() = default;

    /**
     * @brief Adds an event listener to this object.
     *
     * The listener will receive events that are fired by this
     * object.
     *
     * @param listener A pointer to the listener object to add.
     */
    void addListener(EventListener<void>* listener) {
        const std::lock_guard<std::mutex> lock(mListenerLock);
        mListeners.insert(listener);
    }

    /**
     * @brief Removes an event listener from this object.
     *
     * The listener will no longer receive events  that are fired by
     * this object.
     *
     * @param listener A pointer to the listener object to remove.
     */
    void removeListener(EventListener<void>* listener) {
        const std::lock_guard<std::mutex> lock(mListenerLock);
        mListeners.erase(listener);
    }

    /**
     * @brief Fires an event of type `T`.
     *
     * This method triggers all the registered listeners.
     *
     * @param event A pointer to the event to fire.
     */
    void fireEvent() {
        const std::lock_guard<std::mutex> lock(mListenerLock);
        for (auto listener : mListeners) {
            listener->eventArrived();
        }
    }

    int size() {
        const std::lock_guard<std::mutex> lock(mListenerLock);
        return mListeners.size();
    }

   private:
    std::mutex mListenerLock;
    std::unordered_set<EventListener<void>*> mListeners;
};

/**
 * GenericEventHandler is a class for handling events of type T as an event
 * listener. It uses a policy class named EventPolicy to define the behavior of
 * handling events.
 *
 * @tparam T The type of events to be handled.
 * @tparam EventPolicy The policy class responsible for handling events.
 */
template <class T>
class GenericEventHandler : public EventListener<T> {
    using ChangeSupport = EventChangeSupport<T>;

   public:
    /**
     * Constructs a new GenericEventHandler with the specified listener.
     * The listener is used to subscribe to and receive events of type T.
     *
     * @param listener A pointer to the ChangeSupport instance that will handle
     *        event subscriptions and event notifications.
     */
    GenericEventHandler(ChangeSupport* listener) : mListener(listener) {
        DD_EVT("Created %p", this);
        mListener->addListener(this);
    }

    /**
     * Destroys the GenericEventHandler and unregisters itself as an event
     * listener from the ChangeSupport instance.
     */
    ~GenericEventHandler() {
        DD_EVT("Deleted %p", this);
        unsubscribe();
    }

   protected:
    void unsubscribe() { mListener->removeListener(this); }

   private:
    ChangeSupport* mListener;
};

}  // namespace base
}  // namespace android