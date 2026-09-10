// Copyright 2019 The Android Open Source Project
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

#include "aemu/base/files/StreamSerializing.h"

namespace android {
namespace base {

bool saveStream(Stream* stream, const MemStream& memStream) {
    memStream.save(stream);
    return true;
}

bool loadStream(Stream* stream, MemStream* memStream) {
    memStream->load(stream);
    return true;
}

bool saveBufferRaw(Stream* stream, char* buffer, uint32_t len) {
    stream->putBe32(len);
    ssize_t written = stream->write(buffer, len);
    if (written != len) {
        fprintf(stderr, "%s: saveBufferRaw failed to write %u bytes from stream, got %zd\n",
                __FUNCTION__, len, written);
        return false;
    }
    return true;
}

bool loadBufferRaw(Stream* stream, char* buffer) {
    const uint32_t len = stream->getBe32();
    const size_t bytesToRead = size_t(len);
    ssize_t ret = stream->read(buffer, bytesToRead);
    if (ret != bytesToRead) {
        fprintf(stderr, "%s: loadBufferRaw failed to read %zu bytes from stream, got %zd\n",
                __FUNCTION__, bytesToRead, ret);
        return false;
    }
    return true;
}

bool saveStringArray(Stream* stream, const char* const* strings, uint32_t count) {
    stream->putBe32(count);
    for (uint32_t i = 0; i < count; ++i) {
        stream->putString(strings[i]);
    }
    return true;
}

std::vector<std::string> loadStringArray(Stream* stream) {
    uint32_t count = stream->getBe32();
    std::vector<std::string> res;
    for (uint32_t i = 0; i < count; ++i) {
        res.push_back(stream->getString());
    }
    return res;
}

}  // namespace base
}  // namespace android
