/*
 * Copyright 2019 Google
 * SPDX-License-Identifier: MIT
 */

#pragma once

#include "aemu/base/msvc.h"

#include <optional>
#include <string>

#include <cstdarg>
#include <cstdio>
#include <inttypes.h>
#include <sys/types.h>

namespace android {
namespace base {

// Abstract interface to byte streams of all kind.
// This is mainly used to implement disk serialization.
class Stream {
public:
    // Default constructor.
    Stream() = default;

    // Destructor.
    virtual ~Stream() = default;

    // Read up to |size| bytes and copy them to |buffer|. Return the number
    // of bytes that were actually transferred, or -errno value on error.
    virtual ssize_t read(void* buffer, size_t size) = 0;

    // Write up to |size| bytes from |buffer| into the stream. Return the
    // number of bytes that were actually transferred, or -errno value on
    // error.
    virtual ssize_t write(const void* buffer, size_t size) = 0;

    // Error handling interface.
    virtual bool hasErrors() const = 0;
    virtual const std::optional<std::string>& getErrors() const = 0;
    virtual void addErrorV(const char* fmt, va_list args) const
        __attribute__((format(printf, 2, 0))) = 0;

    void addError(const char* fmt, ...) const
        __attribute__((format(printf, 2, 3))) {
        va_list args;
        va_start(args, fmt);
        addErrorV(fmt, args);
        va_end(args);
    }

    virtual void* getProtobuf() { return nullptr; }

    // Write a single byte |value| into the stream. Ignore errors.
    void putByte(uint8_t value);

    // Write a 16-bit |value| as big-endian into the stream. Ignore errors.
    void putBe16(uint16_t value);

    // Write a 32-bit |value| as big-endian into the stream. Ignore errors.
    void putBe32(uint32_t value);

    // Write a 64-bit |value| as big-endian into the stream. Ignore errors.
    void putBe64(uint64_t value);

    // Read a single byte from the stream. Return 0 on error.
    uint8_t getByte();

    // Read a single big-endian 16-bit value from the stream.
    // Return 0 on error.
    uint16_t getBe16();

    // Read a single big-endian 32-bit value from the stream.
    // Return 0 on error.
    uint32_t getBe32();

    // Read a single big-endian 64-bit value from the stream.
    // Return 0 on error.
    uint64_t getBe64();

    // Write a 32-bit float |value| to the stream.
    void putFloat(float value);

    // Read a single 32-bit float value from the stream.
    float getFloat();

    // Write a 0-terminated C string |str| into the stream. Ignore error.
    void putString(const char* str);
    void putString(const std::string& str);

    // Write a string |str| of |strlen| bytes into the stream.
    // Ignore errors.
    void putString(const char* str, size_t strlen);

    // Read a string from the stream. Return a new string instance,
    // which will be empty on error. Note that this can only be used
    // to read strings that were written with putString().
    std::string getString();

    // Put/gen an integer number into the stream, making it use as little space
    // there as possible.
    // It uses a simple byte-by-byte encoding scheme, putting 7 bits of the
    // number with the 8th bit set when there's more data to read, until the
    // whole number is read.
    // The compression is efficient if the number range is small, but it starts
    // wasting space when values approach 14 bits for int16 (16K), 28 bits for
    // int32 (268M) or 56 bits for int64 (still a lot).
    void putPackedNum(uint64_t num);
    uint64_t getPackedNum();

    // Same thing, but encode negative numbers efficiently as well (single sign
    // bit + packed unsigned representation)
    void putPackedSignedNum(int64_t num);
    int64_t getPackedSignedNum();

    // Static big-endian conversions
    static void toByte(uint8_t*);
    static void toBe16(uint8_t*);
    static void toBe32(uint8_t*);
    static void toBe64(uint8_t*);
    static void fromByte(uint8_t*);
    static void fromBe16(uint8_t*);
    static void fromBe32(uint8_t*);
    static void fromBe64(uint8_t*);
};

// Mix-in class that implements the error logging interface for Stream.
class StreamWithErrorLogger : public Stream {
public:
    bool hasErrors() const override { return mError.has_value(); }
    const std::optional<std::string>& getErrors() const override { return mError; }

    void addErrorV(const char* fmt, va_list args) const override {
        char stackBuf[256];
        va_list argsCopy;
        va_copy(argsCopy, args);
        int len = vsnprintf(stackBuf, sizeof(stackBuf), fmt, argsCopy);
        va_end(argsCopy);

        if (len < 0) {
            return;
        }

        if (!mError.has_value()) {
            mError.emplace();
        } else if (!mError->empty()) {
            mError->push_back('\n');
        }

        if (static_cast<size_t>(len) < sizeof(stackBuf)) {
            mError->append(stackBuf, static_cast<size_t>(len));
        } else {
            size_t oldSize = mError->size();
            mError->resize(oldSize + len + 1);
            vsnprintf(mError->data() + oldSize, len + 1, fmt, args);
            mError->resize(oldSize + len);
        }
    }

   protected:
    // mutable, to support logging within 'const' functions
    mutable std::optional<std::string> mError;
};

}  // namespace base
}  // namespace android
