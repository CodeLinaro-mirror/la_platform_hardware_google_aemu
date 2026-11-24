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

import 'react';

declare module 'react' {
  // Augment HTMLAttributes with inline handler serialization attributes used when
  // rendering React elements into legacy PolyGerrit HTML slots.
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  interface HTMLAttributes<T> {
    htmlOnClick?: string;
    htmlOnInput?: string;
    htmlOnKeyDown?: string;
    htmlStyle?: string;
  }
}

export type RelatedChangesTabId = 'chain' | 'submitted' | 'topic' | 'cherrypicks';

export interface GerritReviewer {
  name?: string;
  email?: string;
  account_id?: number;
  cr_vote?: number;
  v_vote?: number;
  is_bot?: boolean;
}

export interface GerritParentCommitRef {
  commit?: string;
  sha?: string;
}

export interface GerritDiffFileEntry {
  file?: string;
  path?: string;
  status?: string;
  additions?: number;
  deletions?: number;
}

export interface GerritChange {
  number: number;
  subject?: string;
  status?: string;
  branch?: string;
  project?: string;
  topic?: string;
  change_id?: string;
  url?: string;
  patch_set?: number;
  patch_sets?: number[];
  current_revision?: string;
  parent_revision?: string;
  parent_commit?: string;
  parent_patch_set?: number;
  parents?: Array<GerritParentCommitRef | string>;
  files?: GerritDiffFileEntry[];
  is_merged?: boolean;
  is_abandoned?: boolean;
  is_submittable?: boolean;
  submittable?: boolean;
  work_in_progress?: boolean;
  is_agent_cl?: boolean;
  required_cr2_count?: number;
  human_cr2?: string[];
  human_cr2_count?: number;
  human_cr1?: string[];
  reviewers?: GerritReviewer[];
  mergeable?: boolean;
  contains_git_conflicts?: boolean;
  conflicts?: string[];
  rebaseable?: boolean;
  is_outdated?: boolean;
  rebase_needed?: boolean;
  unresolved_comments?: number;
  attention_waiting_display?: string;
  actions?: {
    rebase?: {
      enabled?: boolean;
    };
  };
}

export interface GerritStack {
  stack_id: string;
  commits: GerritChange[];
  title?: string;
  subject?: string;
  branch?: string;
}

export interface GerritDiffBundle {
  patch_set?: number;
  base_patch_set?: string | number;
  is_pure_rebase?: boolean;
  rebase_needed?: boolean;
  conflicts?: string[];
  files?: GerritDiffFileEntry[];
}

export type StackSubmitSequenceStatus =
  | 'ready'
  | 'blocked_current'
  | 'waiting_ancestor'
  | 'merged';

export interface GerritStackSubmitSequenceItem {
  step: number;
  number: number;
  subject: string;
  branch: string;
  author: string;
  is_reviewed: boolean;
  cr_label: string;
  v_label: string;
  seq_status: StackSubmitSequenceStatus;
  blocker_reason?: string;
  blocking_step?: number;
  blocking_ancestor?: { number?: number } | null;
}

export interface GerritStackSubmitSequence {
  items: GerritStackSubmitSequenceItem[];
  total_commits: number;
  reviewed_commits: number;
  submittable_commits: number;
  seq_ready_count: number;
  all_ready: boolean;
  bottleneck_step?: number;
  bottleneck_commit?: { number?: number } | null;
  bottleneck_reason?: string;
}

export interface GerritCumulativeStackFileOccurrence {
  commit_idx: number;
  step: number;
  number: number;
  subject: string;
  patch_set: number;
  additions: number;
  deletions: number;
  status: string;
}

export interface GerritCumulativeStackFileItem {
  file: string;
  extension: string;
  status: string;
  additions: number;
  deletions: number;
  occurrences: GerritCumulativeStackFileOccurrence[];
}

export interface GerritCumulativeStackFilesSummary {
  items: GerritCumulativeStackFileItem[];
  total_files: number;
  total_additions: number;
  total_deletions: number;
  total_commits: number;
  extensions: Array<{ ext: string; count: number }>;
}

export interface GerritHeaderStatusInfo {
  cls: string;
  label: string;
  tip: string;
  hasConflict?: boolean;
  conflicts?: string[];
  secondaryCls?: string;
  secondaryLabel?: string;
  secondaryTip?: string;
}

export interface GerritChangeReferenceInfo {
  changeId: string;
  changeIdShort?: string;
  commitSha: string;
  commitShaShort?: string;
  gerritUrl: string;
  shortlink: string;
  titleAndUrl: string;
  markdown: string;
  gitilesUrl: string;
  codeSearchUrl: string;
  filePaths: string[];
  filePathsText: string;
  cherryPickOfChange: number | null;
  cherryPickOfPatchSet?: number;
  cherryPickOfRef: string;
  revertOfChange: number | null;
  patchSet: number;
  repoDownloadCmd: string;
  gitCheckoutCmd: string;
  gitCherryPickCmd: string;
  gitFormatPatchCmd?: string;
  gitPullCmd?: string;
}

export interface GerritSrSummary {
  total: number;
  satisfied: number;
  status: string;
  label: string;
  tip: string;
  cls: string;
}

export interface GerritPatchsetSelection {
  ps?: number;
  base?: string | number;
}

export interface GerritRebaseStatus {
  hasConflict: boolean;
  isOutdated: boolean;
  isIndirectlyOutdated: boolean;
  isParentMerged: boolean;
  needsRebase: boolean;
  parentCommit: GerritChange | null;
  parentClNumber: number | null;
  parentSubject: string;
  parentCurrentPs: number;
  reason: string;
  conflicts: string[];
}

export interface GerritBridgeAdapter {
  getFallbackReviewersForCl(peer: GerritChange): GerritReviewer[];
  isClConflict(change: GerritChange, bundle?: GerritDiffBundle | null): boolean;
  isClSubmittable(change: GerritChange): boolean;
  getChangeRebaseStatus(
    change: GerritChange,
    bundle?: GerritDiffBundle | null
  ): GerritRebaseStatus;
  getBranchDisplay(branch?: string): string;
  getProjectDisplay(project?: string): string;
  getChangeId(change: GerritChange): string;
  findStackForClOrId(clNum: number, preferredStackId?: string | null): GerritStack | null;
  getTopicPeers(change: GerritChange): GerritChange[];
  getCherryPickPeers(change: GerritChange): GerritChange[];
  getCherryPickGroup(change: GerritChange): GerritChange[];
  getSubmittedTogetherPeers(change: GerritChange): GerritChange[];
  getRelatedChangesTab(clNum: number): RelatedChangesTabId;
  canStackInterdiff(commits: GerritChange[]): boolean;
  isStackInterdiffActive(commits: GerritChange[], stackId?: string): boolean;
  getPersistedStackInterdiff(stackId?: string): boolean;
  ensureStackInterdiffRestored(stack: GerritStack): void;
  getSelectedPatchset(clNum: number): GerritPatchsetSelection;
  getDiffBundle(clNum: number): GerritDiffBundle | null;
  isContinuousStackDiff(): boolean;
  getSelectedClNumber(): number | null;
  getSelectedStackId(): string | null;
  isBatchModeActive(): boolean;
  isClChecked(clNum: number): boolean;
  isStackCommitReviewed(clNum: number): boolean;
  isStackCommitCollapsed(clNum: number): boolean;
  getStackSubmitSequence(
    stack?: GerritStack | null,
    commits?: GerritChange[]
  ): GerritStackSubmitSequence;
}
