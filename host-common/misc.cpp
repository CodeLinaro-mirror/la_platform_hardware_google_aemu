// Copyright (C) 2016 The Android Open Source Project
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

#include "misc.h"

#include <cstring>

static int s_apiLevel = -1;
static bool s_isPhone = false;

static bool s_shouldSkipDrawing = false;

void emugl::setAvdInfo(bool phone, int apiLevel) {
    s_isPhone = phone;
    s_apiLevel = apiLevel;
}

bool emugl::shouldSkipDraw() {
    return s_shouldSkipDrawing;
}


void emugl::setShouldSkipDraw(bool skip) {
    s_shouldSkipDrawing = skip;
}

void emugl::getAvdInfo(bool* phone, int* apiLevel) {
    if (phone) *phone = s_isPhone;
    if (apiLevel) *apiLevel = s_apiLevel;
}
