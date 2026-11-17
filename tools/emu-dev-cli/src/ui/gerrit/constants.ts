// Copyright (C) 2026 The Android Open Source Project
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

export const CHANGE_STATUS_MERGED = 'MERGED';
export const CHANGE_STATUS_ABANDONED = 'ABANDONED';

export const CODE_REVIEW_REJECT_VOTE = -2;
export const CODE_REVIEW_DISLIKE_VOTE = -1;
export const CODE_REVIEW_LIKE_VOTE = 1;
export const DEFAULT_AGENT_REQUIRED_CR2 = 2;
export const DEFAULT_HUMAN_REQUIRED_CR2 = 1;

export const SHORT_SHA_LENGTH = 8;
export const SHORT_CHANGE_ID_LENGTH = 12;
export const REFS_HEADS_PREFIX = 'refs/heads/';
export const DEFAULT_BRANCH_NAME = 'main';
export const DEFAULT_BASE_LABEL = 'base';
export const DEFAULT_TARGET_BRANCH_LABEL = 'target branch';

export const PATCHSET_BASE_TOKEN = 'base';
export const PATCHSET_ZERO_TOKEN = '0';

export const RELATION_HINT_CONTINUOUS_CHAIN =
  'Click commit to jump · Shift+S single CL · Alt+I stack interdiff · [/] step PS · x batch select';
export const RELATION_HINT_SINGLE_CHAIN =
  'Shift+[/] step chain · Shift+S whole stack · Alt+I stack interdiff · [/] step PS · b toggle interdiff · x batch select';
export const RELATION_HINT_SUBMITTED =
  'Click change to view · 🔍 Filter isolates submitted together changes on dashboard';
export const RELATION_HINT_CHERRYPICKS =
  'Cherry-picks sharing Change-Id across branches · 🍒 Cherry-pick creates copy on another branch';
export const RELATION_HINT_TOPIC =
  'Click change to view · 🔍 Filter isolates topic on dashboard';
export const RELATION_HINT_CYCLE_TABS_SUFFIX =
  ' · Press <kbd>Alt+R</kbd> to cycle tabs';
