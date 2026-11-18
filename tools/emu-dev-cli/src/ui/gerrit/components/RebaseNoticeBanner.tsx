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

import React from 'react';
import {
  DEFAULT_BASE_LABEL,
  DEFAULT_BRANCH_NAME,
  DEFAULT_TARGET_BRANCH_LABEL,
} from '../constants';
import { renderReactToHtml } from '../runtime';
import { createHostBridge } from '../state/store';
import type {
  GerritBridgeAdapter,
  GerritChange,
  GerritDiffBundle,
  GerritRebaseStatus,
} from '../types';

export interface RebaseNoticeBannerProps {
  change?: GerritChange | null;
  bundle?: GerritDiffBundle | null;
  bridge?: GerritBridgeAdapter;
}

interface BannerViewSpec {
  bannerClass: string;
  icon: string;
  title: string;
  desc: string;
  actions: React.ReactNode;
}

function buildConflictBannerSpec(
  num: number,
  change: GerritChange,
  rebaseStatus: GerritRebaseStatus
): BannerViewSpec {
  const conflictList =
    Array.isArray(rebaseStatus.conflicts) && rebaseStatus.conflicts.length > 0
      ? ` (${rebaseStatus.conflicts.join(', ')})`
      : '';
  const targetBranch = change.branch || DEFAULT_TARGET_BRANCH_LABEL;
  return {
    bannerClass: 'is-conflict',
    icon: '⚠️',
    title: 'Merge Conflict',
    desc: `This change cannot be merged cleanly onto ${targetBranch} due to merge conflicts${conflictList}.`,
    actions: (
      <>
        <button
          type="button"
          id={`btnRebaseBannerResolve-${num}`}
          className="pg-btn-rebase-banner"
          htmlOnClick={`openPgRebaseDialog(${num})`}
        >
          ⚡ Rebase Change
        </button>
        <button
          type="button"
          id={`btnRebaseBannerAgent-${num}`}
          className="pg-btn-rebase-banner-agent"
          htmlOnClick={`triggerHarnessForCl(${num}, 'resolve-conflicts')`}
        >
          🤖 Fix with Agent
        </button>
        <button
          type="button"
          id={`btnRebaseBannerCheck-${num}`}
          className="pg-btn-rebase-banner-opt"
          htmlOnClick={`refreshPgMergeableStatus(${num})`}
          title="Re-query Gerrit for mergeability status"
        >
          🔄 Recheck
        </button>
      </>
    ),
  };
}

function buildParentMergedBannerSpec(
  num: number,
  change: GerritChange,
  rebaseStatus: GerritRebaseStatus
): BannerViewSpec {
  const branchName = change.branch || DEFAULT_BRANCH_NAME;
  const desc = rebaseStatus.parentClNumber
    ? `Parent change ag/${rebaseStatus.parentClNumber} was merged into ${branchName}.`
    : rebaseStatus.reason || `Parent commit was merged into ${branchName}.`;
  return {
    bannerClass: 'is-parent-merged',
    icon: 'ℹ️',
    title: 'Parent Merged',
    desc,
    actions: (
      <>
        <button
          type="button"
          id={`btnRebaseBannerMerged-${num}`}
          className="pg-btn-rebase-banner"
          htmlOnClick={`quickRebaseOnParent(${num}, null)`}
        >
          {`⚡ Rebase on ${branchName}`}
        </button>
        <button
          type="button"
          id={`btnRebaseBannerOpt-${num}`}
          className="pg-btn-rebase-banner-opt"
          htmlOnClick={`openPgRebaseDialog(${num})`}
        >
          ▾ Options…
        </button>
      </>
    ),
  };
}

function buildOutdatedBannerSpec(
  num: number,
  change: GerritChange,
  rebaseStatus: GerritRebaseStatus
): BannerViewSpec {
  const title = rebaseStatus.isIndirectlyOutdated
    ? 'Relation Chain Outdated'
    : 'Not Based on Latest Parent';
  const desc =
    rebaseStatus.reason ||
    (rebaseStatus.parentClNumber
      ? `Parent change ag/${rebaseStatus.parentClNumber} has updated patchset PS ${rebaseStatus.parentCurrentPs}.`
      : 'Change is not based on latest branch commits.');
  const btnText = rebaseStatus.parentClNumber
    ? `⚡ Rebase on ag/${rebaseStatus.parentClNumber} (PS ${rebaseStatus.parentCurrentPs})`
    : rebaseStatus.isIndirectlyOutdated
      ? '⚡ Rebase Stack Chain'
      : `⚡ Rebase on ${change.branch || DEFAULT_BASE_LABEL}`;
  const parentArg = rebaseStatus.parentClNumber
    ? rebaseStatus.parentClNumber
    : 'null';
  const rebaseAction = rebaseStatus.isIndirectlyOutdated
    ? `quickRebaseStackChain(${num})`
    : `quickRebaseOnParent(${num}, ${parentArg})`;

  return {
    bannerClass: 'is-outdated',
    icon: '⚠️',
    title,
    desc,
    actions: (
      <>
        <button
          type="button"
          id={`btnRebaseBannerOutdated-${num}`}
          className="pg-btn-rebase-banner"
          htmlOnClick={rebaseAction}
        >
          {btnText}
        </button>
        <button
          type="button"
          id={`btnRebaseBannerOpt-${num}`}
          className="pg-btn-rebase-banner-opt"
          htmlOnClick={`openPgRebaseDialog(${num})`}
        >
          ▾ Options…
        </button>
      </>
    ),
  };
}

function resolveBannerSpec(
  num: number,
  change: GerritChange,
  rebaseStatus: GerritRebaseStatus
): BannerViewSpec {
  if (rebaseStatus.hasConflict) {
    return buildConflictBannerSpec(num, change, rebaseStatus);
  }
  if (rebaseStatus.isParentMerged) {
    return buildParentMergedBannerSpec(num, change, rebaseStatus);
  }
  if (rebaseStatus.isOutdated) {
    return buildOutdatedBannerSpec(num, change, rebaseStatus);
  }
  return {
    bannerClass: 'is-outdated',
    icon: '⚠️',
    title: 'Outdated Base',
    desc: rebaseStatus.reason || 'Change requires rebase',
    actions: null,
  };
}

export function RebaseNoticeBanner({
  change,
  bundle = null,
  bridge,
}: RebaseNoticeBannerProps): React.ReactElement | null {
  if (!change) {
    return null;
  }
  const activeBridge = bridge ?? createHostBridge();
  const rebaseStatus = activeBridge.getChangeRebaseStatus(change, bundle);
  if (!rebaseStatus || !rebaseStatus.needsRebase) {
    return null;
  }
  const num = Number(change.number);
  const spec = resolveBannerSpec(num, change, rebaseStatus);

  return (
    <div
      id={`pgRebaseNoticeBanner-${num}`}
      className={`pg-rebase-needed-banner ${spec.bannerClass}`}
      role="alert"
    >
      <div className="pg-rebase-banner-content">
        <span className="pg-rebase-banner-icon" aria-hidden="true">
          {spec.icon}
        </span>
        <div className="pg-rebase-banner-text">
          <strong className="pg-rebase-banner-title">{`${spec.title}:`}</strong>
          <span className="pg-rebase-banner-desc">{spec.desc}</span>
        </div>
      </div>
      <div className="pg-rebase-banner-actions">{spec.actions}</div>
    </div>
  );
}

export function renderPgRebaseNoticeBannerHtml(
  change?: GerritChange | null,
  bundle: GerritDiffBundle | null = null,
  bridge?: GerritBridgeAdapter
): string {
  return renderReactToHtml(
    <RebaseNoticeBanner change={change} bundle={bundle} bridge={bridge} />
  );
}
