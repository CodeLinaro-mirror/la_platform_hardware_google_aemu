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

import { useSyncExternalStore } from 'react';
import {
  CHANGE_STATUS_ABANDONED,
  CHANGE_STATUS_MERGED,
  REFS_HEADS_PREFIX,
} from '../constants';
import type {
  GerritBridgeAdapter,
  GerritChange,
  GerritDiffBundle,
  GerritPatchsetSelection,
  GerritRebaseStatus,
  GerritReviewer,
  GerritStack,
  GerritStackSubmitSequence,
  RelatedChangesTabId,
} from '../types';

export interface GerritUIStoreSnapshot {
  revision: number;
  selectedCl: number | null;
  selectedStackId: string | null;
  continuousStackDiff: boolean;
}

type StoreListener = () => void;

export class GerritUIStore {
  private revision = 0;
  private readonly listeners = new Set<StoreListener>();
  private cachedSnapshot: GerritUIStoreSnapshot = {
    revision: 0,
    selectedCl: null,
    selectedStackId: null,
    continuousStackDiff: false,
  };

  subscribe = (listener: StoreListener): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  getSnapshot = (): GerritUIStoreSnapshot => {
    return this.cachedSnapshot;
  };

  notify(): void {
    this.revision += 1;
    const hostState = getHostStateObject();
    this.cachedSnapshot = {
      revision: this.revision,
      selectedCl: typeof hostState?.selectedCl === 'number' ? hostState.selectedCl : null,
      selectedStackId:
        typeof hostState?.selectedStackId === 'string' ? hostState.selectedStackId : null,
      continuousStackDiff: Boolean(hostState?.continuousStackDiff),
    };
    for (const listener of this.listeners) {
      listener();
    }
  }
}

export const gerritUiStore = new GerritUIStore();

export function useGerritUIStore(): GerritUIStoreSnapshot {
  return useSyncExternalStore(
    gerritUiStore.subscribe,
    gerritUiStore.getSnapshot,
    gerritUiStore.getSnapshot
  );
}

interface HostGlobalScope {
  state?: {
    selectedCl?: number | null;
    selectedClNumber?: number | null;
    selectedStackId?: string | null;
    continuousStackDiff?: boolean;
    checkedClNumbers?: Set<number>;
    reviewedStackCommits?: Set<number>;
    collapsedStackCommits?: Set<number>;
    selectedPatchsetByCl?: Record<number, GerritPatchsetSelection>;
    diffsByCl?: Record<number, GerritDiffBundle>;
  };
  getFallbackReviewersForCl?: (peer: GerritChange) => GerritReviewer[];
  isClConflict?: (change: GerritChange, bundle?: GerritDiffBundle | null) => boolean;
  isClSubmittable?: (change: GerritChange) => boolean;
  getPgChangeRebaseStatus?: (
    change: GerritChange,
    bundle?: GerritDiffBundle | null
  ) => GerritRebaseStatus;
  getPgBranchDisplay?: (branch?: string) => string;
  getPgProjectDisplay?: (project?: string) => string;
  getPgChangeId?: (change: GerritChange) => string;
  findPgStackForClOrId?: (
    clNum: number,
    preferredStackId?: string | null
  ) => GerritStack | null;
  getPgTopicPeers?: (change: GerritChange) => GerritChange[];
  getPgCherryPickPeers?: (change: GerritChange) => GerritChange[];
  getPgCherryPickGroup?: (change: GerritChange) => GerritChange[];
  getPgSubmittedTogetherPeers?: (change: GerritChange) => GerritChange[];
  getPgRelatedChangesTab?: (clNum: number) => RelatedChangesTabId;
  canPgStackInterdiff?: (commits: GerritChange[]) => boolean;
  isPgStackInterdiffActive?: (commits: GerritChange[], stackId?: string) => boolean;
  getPgPersistedStackInterdiff?: (stackId?: string) => boolean;
  ensurePgStackInterdiffRestored?: (stack: GerritStack) => void;
  getPgSelectedPatchset?: (clNum: number) => GerritPatchsetSelection;
  isPgStackCommitReviewed?: (clNum: number) => boolean;
  getPgStackSubmitSequence?: (
    stack?: GerritStack | null,
    commits?: GerritChange[]
  ) => GerritStackSubmitSequence;
}

function getHostGlobal(): HostGlobalScope {
  return globalThis as unknown as HostGlobalScope;
}

function getHostStateObject(): HostGlobalScope['state'] {
  return getHostGlobal().state;
}

export function createEmptyRebaseStatus(): GerritRebaseStatus {
  return {
    hasConflict: false,
    isOutdated: false,
    isIndirectlyOutdated: false,
    isParentMerged: false,
    needsRebase: false,
    parentCommit: null,
    parentClNumber: null,
    parentSubject: '',
    parentCurrentPs: 1,
    reason: '',
    conflicts: [],
  };
}

export function formatBranchDisplay(branch?: string): string {
  if (!branch) {
    return '';
  }
  const trimmed = String(branch).trim();
  if (trimmed.startsWith(REFS_HEADS_PREFIX)) {
    return trimmed.slice(REFS_HEADS_PREFIX.length);
  }
  return trimmed;
}

export function isChangeMerged(change?: GerritChange | null): boolean {
  if (!change) {
    return false;
  }
  const statusUpper = String(change.status || '').toUpperCase();
  return statusUpper === CHANGE_STATUS_MERGED || Boolean(change.is_merged);
}

export function isChangeAbandoned(change?: GerritChange | null): boolean {
  if (!change) {
    return false;
  }
  const statusUpper = String(change.status || '').toUpperCase();
  return statusUpper === CHANGE_STATUS_ABANDONED || Boolean(change.is_abandoned);
}

const DEFAULT_HOST_BRIDGE: GerritBridgeAdapter = {
  getFallbackReviewersForCl(peer: GerritChange): GerritReviewer[] {
    const host = getHostGlobal();
    if (typeof host.getFallbackReviewersForCl === 'function') {
      return host.getFallbackReviewersForCl(peer) || [];
    }
    return Array.isArray(peer?.reviewers) ? peer.reviewers : [];
  },
  isClConflict(change: GerritChange, bundle?: GerritDiffBundle | null): boolean {
    const host = getHostGlobal();
    if (typeof host.isClConflict === 'function') {
      return Boolean(host.isClConflict(change, bundle));
    }
    return Boolean(change?.contains_git_conflicts || change?.mergeable === false);
  },
  isClSubmittable(change: GerritChange): boolean {
    const host = getHostGlobal();
    if (typeof host.isClSubmittable === 'function') {
      return Boolean(host.isClSubmittable(change));
    }
    return Boolean(change?.is_submittable || change?.submittable);
  },
  getChangeRebaseStatus(
    change: GerritChange,
    bundle?: GerritDiffBundle | null
  ): GerritRebaseStatus {
    const host = getHostGlobal();
    if (typeof host.getPgChangeRebaseStatus === 'function') {
      return host.getPgChangeRebaseStatus(change, bundle);
    }
    return createEmptyRebaseStatus();
  },
  getBranchDisplay(branch?: string): string {
    const host = getHostGlobal();
    if (typeof host.getPgBranchDisplay === 'function') {
      return host.getPgBranchDisplay(branch);
    }
    return formatBranchDisplay(branch);
  },
  getProjectDisplay(project?: string): string {
    const host = getHostGlobal();
    if (typeof host.getPgProjectDisplay === 'function') {
      return host.getPgProjectDisplay(project);
    }
    return String(project || '').trim();
  },
  getChangeId(change: GerritChange): string {
    const host = getHostGlobal();
    if (typeof host.getPgChangeId === 'function') {
      return host.getPgChangeId(change);
    }
    return String(change?.change_id || '').trim();
  },
  findStackForClOrId(
    clNum: number,
    preferredStackId?: string | null
  ): GerritStack | null {
    const host = getHostGlobal();
    if (typeof host.findPgStackForClOrId === 'function') {
      return host.findPgStackForClOrId(clNum, preferredStackId);
    }
    return null;
  },
  getTopicPeers(change: GerritChange): GerritChange[] {
    const host = getHostGlobal();
    if (typeof host.getPgTopicPeers === 'function') {
      return host.getPgTopicPeers(change) || [];
    }
    return [];
  },
  getCherryPickPeers(change: GerritChange): GerritChange[] {
    const host = getHostGlobal();
    if (typeof host.getPgCherryPickPeers === 'function') {
      return host.getPgCherryPickPeers(change) || [];
    }
    return [];
  },
  getCherryPickGroup(change: GerritChange): GerritChange[] {
    const host = getHostGlobal();
    if (typeof host.getPgCherryPickGroup === 'function') {
      return host.getPgCherryPickGroup(change) || [];
    }
    return this.getCherryPickPeers(change);
  },
  getSubmittedTogetherPeers(change: GerritChange): GerritChange[] {
    const host = getHostGlobal();
    if (typeof host.getPgSubmittedTogetherPeers === 'function') {
      return host.getPgSubmittedTogetherPeers(change) || [];
    }
    return [];
  },
  getRelatedChangesTab(clNum: number): RelatedChangesTabId {
    const host = getHostGlobal();
    if (typeof host.getPgRelatedChangesTab === 'function') {
      return host.getPgRelatedChangesTab(clNum);
    }
    return 'chain';
  },
  canStackInterdiff(commits: GerritChange[]): boolean {
    const host = getHostGlobal();
    if (typeof host.canPgStackInterdiff === 'function') {
      return Boolean(host.canPgStackInterdiff(commits));
    }
    return false;
  },
  isStackInterdiffActive(commits: GerritChange[], stackId?: string): boolean {
    const host = getHostGlobal();
    if (typeof host.isPgStackInterdiffActive === 'function') {
      return Boolean(host.isPgStackInterdiffActive(commits, stackId));
    }
    return false;
  },
  getPersistedStackInterdiff(stackId?: string): boolean {
    const host = getHostGlobal();
    if (typeof host.getPgPersistedStackInterdiff === 'function') {
      return Boolean(host.getPgPersistedStackInterdiff(stackId));
    }
    return false;
  },
  ensureStackInterdiffRestored(stack: GerritStack): void {
    const host = getHostGlobal();
    if (typeof host.ensurePgStackInterdiffRestored === 'function') {
      host.ensurePgStackInterdiffRestored(stack);
    }
  },
  getSelectedPatchset(clNum: number): GerritPatchsetSelection {
    const st = getHostStateObject();
    if (st?.selectedPatchsetByCl && st.selectedPatchsetByCl[clNum]) {
      return st.selectedPatchsetByCl[clNum];
    }
    const host = getHostGlobal();
    if (typeof host.getPgSelectedPatchset === 'function') {
      return host.getPgSelectedPatchset(clNum) || {};
    }
    return {};
  },
  getDiffBundle(clNum: number): GerritDiffBundle | null {
    const st = getHostStateObject();
    return (st?.diffsByCl && st.diffsByCl[clNum]) || null;
  },
  isContinuousStackDiff(): boolean {
    return Boolean(getHostStateObject()?.continuousStackDiff);
  },
  getSelectedClNumber(): number | null {
    const st = getHostStateObject();
    const raw = st?.selectedClNumber ?? st?.selectedCl;
    return typeof raw === 'number' ? raw : null;
  },
  getSelectedStackId(): string | null {
    return getHostStateObject()?.selectedStackId ?? null;
  },
  isBatchModeActive(): boolean {
    const checked = getHostStateObject()?.checkedClNumbers;
    return Boolean(checked && checked.size > 0);
  },
  isClChecked(clNum: number): boolean {
    const checked = getHostStateObject()?.checkedClNumbers;
    return Boolean(checked && checked.has(clNum));
  },
  isStackCommitReviewed(clNum: number): boolean {
    const host = getHostGlobal();
    if (typeof host.isPgStackCommitReviewed === 'function') {
      return Boolean(host.isPgStackCommitReviewed(clNum));
    }
    const reviewed = getHostStateObject()?.reviewedStackCommits;
    return Boolean(reviewed && reviewed.has(Number(clNum)));
  },
  isStackCommitCollapsed(clNum: number): boolean {
    const collapsed = getHostStateObject()?.collapsedStackCommits;
    return Boolean(collapsed && collapsed.has(Number(clNum)));
  },
  getStackSubmitSequence(
    stack?: GerritStack | null,
    commits?: GerritChange[]
  ): GerritStackSubmitSequence {
    const host = getHostGlobal();
    if (typeof host.getPgStackSubmitSequence === 'function') {
      return host.getPgStackSubmitSequence(stack, commits);
    }
    const list = commits || stack?.commits || [];
    return {
      items: [],
      total_commits: list.length,
      reviewed_commits: 0,
      submittable_commits: 0,
      seq_ready_count: 0,
      all_ready: false,
      bottleneck_step: 1,
      bottleneck_commit: list[0] || null,
      bottleneck_reason: 'Not ready',
    };
  },
};

export function createHostBridge(
  overrides?: Partial<GerritBridgeAdapter>
): GerritBridgeAdapter {
  return overrides ? { ...DEFAULT_HOST_BRIDGE, ...overrides } : DEFAULT_HOST_BRIDGE;
}
