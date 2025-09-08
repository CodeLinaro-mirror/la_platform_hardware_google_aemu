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

// This is a convenience header that includes all available event policies.
// For more granular control, include the specific policy headers from
// aemu/base/events/policies/.

#include "aemu/base/events/policies/AbseilReadWritePolicy.h"
#include "aemu/base/events/policies/HybridStoragePolicy.h"
#include "aemu/base/events/policies/OrderedVectorStoragePolicy.h"
#include "aemu/base/events/policies/ReadCopyUpdatePolicy.h"
#include "aemu/base/events/policies/UnorderedSetStoragePolicy.h"
#include "aemu/base/events/policies/VectorStoragePolicy.h"
