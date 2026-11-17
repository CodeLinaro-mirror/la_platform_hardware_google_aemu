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
  RELATION_HINT_CHERRYPICKS,
  RELATION_HINT_CONTINUOUS_CHAIN,
  RELATION_HINT_CYCLE_TABS_SUFFIX,
  RELATION_HINT_SINGLE_CHAIN,
  RELATION_HINT_SUBMITTED,
  RELATION_HINT_TOPIC,
  SHORT_CHANGE_ID_LENGTH,
} from '../constants';
import { escapeHtml, renderReactToHtml } from '../runtime';
import { createHostBridge } from '../state/store';
import type {
  GerritBridgeAdapter,
  GerritChange,
  GerritStack,
  RelatedChangesTabId,
} from '../types';
import { RelationBaseRow } from './RelationBaseRow';
import { RelationCommitRow } from './RelationCommitRow';

export interface RelationChainSidebarProps {
  change?: GerritChange | null;
  bridge?: GerritBridgeAdapter;
}

interface AvailableRelationTab {
  id: RelatedChangesTabId;
  label: string;
}

const TAB_BUTTON_ID_PREFIX: Record<RelatedChangesTabId, string> = {
  chain: 'btnRelTabChain-',
  submitted: 'btnRelTabSubmitted-',
  topic: 'btnRelTabTopic-',
  cherrypicks: 'btnRelTabCherrypicks-',
};

function buildAvailableTabs(params: {
  hasStack: boolean;
  stackLen: number;
  hasSubmittedTogether: boolean;
  submittedLen: number;
  hasTopic: boolean;
  topicLen: number;
  hasCherryPicks: boolean;
  cherryLen: number;
}): AvailableRelationTab[] {
  const tabs: AvailableRelationTab[] = [];
  if (params.hasStack) {
    tabs.push({ id: 'chain', label: `Relation chain (${params.stackLen})` });
  }
  if (params.hasSubmittedTogether) {
    tabs.push({
      id: 'submitted',
      label: `Submitted together (${params.submittedLen})`,
    });
  }
  if (params.hasTopic) {
    tabs.push({ id: 'topic', label: `Same topic (${params.topicLen})` });
  }
  if (params.hasCherryPicks) {
    tabs.push({ id: 'cherrypicks', label: `Cherry-picks (${params.cherryLen})` });
  }
  return tabs;
}

function RelationTabsHeaderLeft({
  curNum,
  activeTab,
  availableTabs,
}: {
  curNum: number;
  activeTab: RelatedChangesTabId;
  availableTabs: AvailableRelationTab[];
}): React.ReactElement | null {
  if (availableTabs.length > 1) {
    return (
      <div className="pg-rel-tabs">
        {availableTabs.map((tab) => (
          <button
            key={tab.id}
            id={`${TAB_BUTTON_ID_PREFIX[tab.id]}${curNum}`}
            type="button"
            className={`pg-rel-tab${activeTab === tab.id ? ' is-active' : ''}`}
            htmlOnClick={`setPgRelatedChangesTab(${curNum}, '${tab.id}')`}
            title={`View ${tab.label}`}
          >
            <span>{tab.label}</span>
          </button>
        ))}
      </div>
    );
  }
  if (availableTabs.length === 1) {
    return <span className="pg-rel-title">{availableTabs[0].label}</span>;
  }
  return null;
}

function RelationHeaderActions({
  curNum,
  activeTab,
  hasStack,
  stack,
  isContinuous,
  bridge,
}: {
  curNum: number;
  activeTab: RelatedChangesTabId;
  hasStack: boolean;
  stack: GerritStack | null;
  isContinuous: boolean;
  bridge: GerritBridgeAdapter;
}): React.ReactElement {
  const showChainControls = hasStack && activeTab === 'chain' && stack !== null;
  const commits = stack?.commits || [];
  const stackId = stack?.stack_id || '';
  const canInterdiff = showChainControls && bridge.canStackInterdiff(commits);
  let isStackInterdiff =
    showChainControls && bridge.isStackInterdiffActive(commits, stackId);
  if (
    canInterdiff &&
    !isStackInterdiff &&
    stack &&
    bridge.getPersistedStackInterdiff(stackId)
  ) {
    bridge.ensureStackInterdiffRestored(stack);
    isStackInterdiff = true;
  }
  const curIdx = commits.findIndex((commit) => Number(commit.number) === curNum);
  const canPrev = curIdx > 0;
  const canNext = curIdx >= 0 && curIdx < commits.length - 1;

  return (
    <div className="pg-relation-head-right">
      {canInterdiff ? (
        <button
          id={`btnRelToggleStackInterdiff-${curNum}`}
          type="button"
          className={`pg-btn-subtle-rel pg-btn-rel-interdiff${
            isStackInterdiff ? ' is-active' : ''
          }`}
          htmlOnClick={`event.stopPropagation(); togglePgStackInterdiff('${escapeHtml(
            stackId
          )}')`}
          title={
            isStackInterdiff
              ? 'Reset all stack commits to Base comparison (Alt+I)'
              : 'Toggle stack interdiff (PS n-1 → PS n) across all multi-patchset commits in stack (Alt+I)'
          }
        >
          <span className="pg-rel-interdiff-icon" aria-hidden="true">
            {isStackInterdiff ? '✕' : '⚡'}
          </span>
          <span>{isStackInterdiff ? 'Reset Stack' : 'Stack Interdiff'}</span>
        </button>
      ) : null}
      {showChainControls ? (
        <button
          id="btnToggleContinuousStack"
          type="button"
          className={`pg-btn-stack-mode${isContinuous ? ' active' : ''}`}
          htmlOnClick={`toggleContinuousStackDiff(event, '${escapeHtml(stackId)}')`}
          title="Toggle Continuous Stack Review mode (Shortcut: Shift+S)"
        >
          <span className="pg-stack-mode-icon" aria-hidden="true">
            {isContinuous ? '📚' : '📄'}
          </span>
          <span>{isContinuous ? 'Whole Stack ON' : 'Whole Stack'}</span>
        </button>
      ) : null}
      {showChainControls ? (
        <div
          className="pg-rel-stepper-group"
          role="group"
          aria-label="Relation chain navigation"
        >
          <button
            id={`btnRelPrevCommit-${curNum}`}
            type="button"
            className="pg-btn-subtle-rel pg-btn-rel-step"
            htmlOnClick="goToPrevStackCommit()"
            disabled={!canPrev}
            title="Previous change in relation chain (Shift+[)"
          >
            ◀
          </button>
          <button
            id={`btnRelNextCommit-${curNum}`}
            type="button"
            className="pg-btn-subtle-rel pg-btn-rel-step"
            htmlOnClick="goToNextStackCommit()"
            disabled={!canNext}
            title="Next change in relation chain (Shift+])"
          >
            ▶
          </button>
        </div>
      ) : null}
      <button
        id={`btnCopyRelatedCls-${curNum}`}
        type="button"
        className="pg-btn-subtle-rel"
        htmlOnClick={`copyPgRelatedCls(${curNum}, '${activeTab}')`}
        title="Copy change numbers to clipboard"
      >
        <span>📋 CLs</span>
      </button>
    </div>
  );
}

function ChainTabContent({
  change,
  curNum,
  stack,
  currentBranch,
  currentProject,
  bridge,
}: {
  change: GerritChange;
  curNum: number;
  stack: GerritStack;
  currentBranch: string;
  currentProject: string;
  bridge: GerritBridgeAdapter;
}): React.ReactElement {
  const topDownCommits = stack.commits.slice().reverse();
  const totalNodes = topDownCommits.length;
  const hasBaseRow = stack.commits.length > 0;
  let outdatedCount = 0;
  const rebaseStatuses = topDownCommits.map((peer) => {
    const status = bridge.getChangeRebaseStatus(peer);
    if (status?.needsRebase) {
      outdatedCount++;
    }
    return status;
  });

  return (
    <>
      {outdatedCount > 0 ? (
        <div
          id={`pgRelChainWarningBanner-${curNum}`}
          className="pg-rel-chain-warning-banner"
          role="alert"
        >
          <span className="pg-rel-banner-icon" aria-hidden="true">
            ⚠️
          </span>
          <span className="pg-rel-banner-text">
            {`${outdatedCount} ${
              outdatedCount === 1 ? 'change requires' : 'changes require'
            } rebase`}
          </span>
          <button
            id={`btnRelRebaseAll-${curNum}`}
            type="button"
            className="pg-btn-subtle-rel pg-btn-rel-rebase-all"
            htmlOnClick={`event.stopPropagation(); quickRebaseStackChain(${curNum})`}
            title="Rebase entire relation stack chain"
          >
            ⚡ Rebase Stack
          </button>
        </div>
      ) : null}
      <div className="pg-relation-list pg-rel-tree-list">
        {topDownCommits.map((peer, idx) => (
          <RelationCommitRow
            key={peer.number}
            peer={peer}
            currentClNum={curNum}
            currentBranch={currentBranch}
            currentProject={currentProject}
            mode="chain"
            bridge={bridge}
            stackId={stack.stack_id}
            isFirst={idx === 0}
            isLast={!hasBaseRow && idx === totalNodes - 1}
            rebaseStatus={rebaseStatuses[idx]}
          />
        ))}
        <RelationBaseRow change={change} stack={stack} bridge={bridge} />
      </div>
    </>
  );
}

function SubmittedTogetherTabContent({
  change,
  curNum,
  submittedPeers,
  currentBranch,
  currentProject,
  bridge,
}: {
  change: GerritChange;
  curNum: number;
  submittedPeers: GerritChange[];
  currentBranch: string;
  currentProject: string;
  bridge: GerritBridgeAdapter;
}): React.ReactElement {
  const totalSub = submittedPeers.length;
  const readyPeers = submittedPeers.filter(
    (peer) =>
      bridge.isClSubmittable(peer) &&
      !bridge.isClConflict(peer) &&
      !peer.work_in_progress
  );
  const allReady = readyPeers.length === totalSub;
  const nonReadyCount = totalSub - readyPeers.length;
  const topicName = String(change.topic || '').trim();

  return (
    <>
      {allReady ? (
        <div className="pg-rel-submitted-banner is-ready" role="status">
          <span className="pg-rel-banner-icon" aria-hidden="true">
            ✓
          </span>
          <span className="pg-rel-banner-text">
            {`All ${totalSub} changes are ready to be submitted together`}
          </span>
        </div>
      ) : (
        <div className="pg-rel-submitted-banner is-warning" role="alert">
          <span className="pg-rel-banner-icon" aria-hidden="true">
            ⚠️
          </span>
          <span className="pg-rel-banner-text">
            {`Not all changes can be submitted together (${nonReadyCount} ${
              nonReadyCount === 1 ? 'change requires' : 'changes require'
            } review, verification, or conflict resolution)`}
          </span>
        </div>
      )}
      <div className="pg-rel-submitted-bar">
        <div className="pg-rel-submitted-info">
          <span
            className="pg-rel-submitted-summary"
            title={`${readyPeers.length} of ${totalSub} changes submittable`}
          >
            {'📦 '}
            <strong>{`${readyPeers.length}/${totalSub}`}</strong>
            {' ready'}
          </span>
          {topicName ? (
            <span className="pg-rel-topic-name" title={`Topic: ${topicName}`}>
              {'🏷️ '}
              <strong>{topicName}</strong>
            </span>
          ) : null}
        </div>
        <div className="pg-rel-topic-actions">
          <button
            id={`btnFilterSubmitted-${curNum}`}
            type="button"
            className="pg-btn-subtle-rel"
            htmlOnClick={`applyPgSubmittedTogetherFilter(${curNum})`}
            title="Filter dashboard by submitted together changes"
          >
            🔍 Filter
          </button>
          <button
            id={`btnCopySubmittedCls-${curNum}`}
            type="button"
            className="pg-btn-subtle-rel"
            htmlOnClick={`copyPgRelatedCls(${curNum}, 'submitted')`}
            title="Copy submitted together change numbers"
          >
            📋 Copy
          </button>
        </div>
      </div>
      <div className="pg-relation-list">
        {submittedPeers.map((peer) => (
          <RelationCommitRow
            key={peer.number}
            peer={peer}
            currentClNum={curNum}
            currentBranch={currentBranch}
            currentProject={currentProject}
            mode="submitted"
            bridge={bridge}
          />
        ))}
      </div>
    </>
  );
}

function CherryPicksTabContent({
  change,
  curNum,
  cherryPeers,
  currentBranch,
  currentProject,
  bridge,
}: {
  change: GerritChange;
  curNum: number;
  cherryPeers: GerritChange[];
  currentBranch: string;
  currentProject: string;
  bridge: GerritBridgeAdapter;
}): React.ReactElement {
  const cid = bridge.getChangeId(change);
  const escapedCid = escapeHtml(cid);
  const cherryGroup = bridge.getCherryPickGroup(change);
  const allCherryItems =
    cherryGroup && cherryGroup.length > 0 ? cherryGroup : cherryPeers;

  return (
    <>
      <div className="pg-rel-cherrypick-bar">
        <span className="pg-rel-cherry-id" title={`Change-Id: ${cid}`}>
          {'🍒 '}
          <code>{`${cid.slice(0, SHORT_CHANGE_ID_LENGTH)}…`}</code>
        </span>
        <div className="pg-rel-topic-actions">
          <button
            id={`btnRelCherryPick-${curNum}`}
            type="button"
            className="pg-btn-subtle-rel pg-btn-rel-cherry"
            htmlOnClick={`event.stopPropagation(); openPgCherryPickDialog(${curNum})`}
            title={`Cherry pick ag/${curNum} to another branch (Shift+C)`}
          >
            🍒 Cherry-pick…
          </button>
          <button
            id={`btnFilterCherry-${curNum}`}
            type="button"
            className="pg-btn-subtle-rel"
            htmlOnClick={`applyPgChangeIdFilter('${escapedCid}')`}
            title={`Filter dashboard by Change-Id '${cid}'`}
          >
            🔍 Filter
          </button>
          <button
            id={`btnCopyCherryId-${curNum}`}
            type="button"
            className="pg-btn-subtle-rel"
            htmlOnClick={`copyPgChangeId('${escapedCid}')`}
            title="Copy Change-Id to clipboard"
          >
            📋 Copy
          </button>
        </div>
      </div>
      <div className="pg-relation-list">
        {allCherryItems.map((peer) => (
          <RelationCommitRow
            key={peer.number}
            peer={peer}
            currentClNum={curNum}
            currentBranch={currentBranch}
            currentProject={currentProject}
            mode="cherrypicks"
            bridge={bridge}
          />
        ))}
      </div>
    </>
  );
}

function TopicTabContent({
  change,
  curNum,
  topicPeers,
  currentBranch,
  currentProject,
  bridge,
}: {
  change: GerritChange;
  curNum: number;
  topicPeers: GerritChange[];
  currentBranch: string;
  currentProject: string;
  bridge: GerritBridgeAdapter;
}): React.ReactElement {
  const topicName = String(change.topic || '').trim();
  const escapedTopic = escapeHtml(topicName);

  return (
    <>
      <div className="pg-rel-topic-bar">
        <span className="pg-rel-topic-name" title={`Topic: ${topicName}`}>
          {'🏷️ '}
          <strong>{topicName}</strong>
        </span>
        <div className="pg-rel-topic-actions">
          <button
            id={`btnFilterTopic-${curNum}`}
            type="button"
            className="pg-btn-subtle-rel"
            htmlOnClick={`applyPgTopicFilter('${escapedTopic}')`}
            title={`Filter dashboard by topic '${topicName}'`}
          >
            🔍 Filter
          </button>
          <button
            id={`btnCopyTopic-${curNum}`}
            type="button"
            className="pg-btn-subtle-rel"
            htmlOnClick={`copyPgTopic('${escapedTopic}')`}
            title="Copy topic name to clipboard"
          >
            📋 Copy
          </button>
        </div>
      </div>
      <div className="pg-relation-list">
        {topicPeers.map((peer) => (
          <RelationCommitRow
            key={peer.number}
            peer={peer}
            currentClNum={curNum}
            currentBranch={currentBranch}
            currentProject={currentProject}
            mode="topic"
            bridge={bridge}
          />
        ))}
      </div>
    </>
  );
}

function resolveRelationHintHtml(
  activeTab: RelatedChangesTabId,
  hasStack: boolean,
  isContinuous: boolean,
  tabCount: number
): string {
  let baseHint = RELATION_HINT_TOPIC;
  if (activeTab === 'chain' && hasStack) {
    baseHint = isContinuous
      ? RELATION_HINT_CONTINUOUS_CHAIN
      : RELATION_HINT_SINGLE_CHAIN;
  } else if (activeTab === 'submitted') {
    baseHint = RELATION_HINT_SUBMITTED;
  } else if (activeTab === 'cherrypicks') {
    baseHint = RELATION_HINT_CHERRYPICKS;
  }
  if (tabCount > 1 && baseHint) {
    return `${baseHint}${RELATION_HINT_CYCLE_TABS_SUFFIX}`;
  }
  return baseHint;
}

export function RelationChainSidebar({
  change,
  bridge,
}: RelationChainSidebarProps): React.ReactElement | null {
  if (!change) {
    return null;
  }
  const activeBridge = bridge ?? createHostBridge();
  const curNum = Number(change.number);
  const stack = activeBridge.findStackForClOrId(
    curNum,
    activeBridge.getSelectedStackId()
  );
  const hasStack = Boolean(
    stack && Array.isArray(stack.commits) && stack.commits.length > 1
  );
  const topicPeers = activeBridge.getTopicPeers(change);
  const hasTopic = topicPeers.length > 0;
  const cherryPeers = activeBridge.getCherryPickPeers(change);
  const hasCherryPicks = cherryPeers.length > 0;
  const submittedPeers = activeBridge.getSubmittedTogetherPeers(change);
  const hasSubmittedTogether = submittedPeers.length > 1;

  if (!hasStack && !hasSubmittedTogether && !hasTopic && !hasCherryPicks) {
    return null;
  }

  const activeTab = activeBridge.getRelatedChangesTab(curNum);
  const isContinuous = activeBridge.isContinuousStackDiff();
  const currentBranch = activeBridge.getBranchDisplay(change.branch);
  const currentProject = activeBridge.getProjectDisplay(change.project);
  const availableTabs = buildAvailableTabs({
    hasStack,
    stackLen: stack?.commits.length || 0,
    hasSubmittedTogether,
    submittedLen: submittedPeers.length,
    hasTopic,
    topicLen: topicPeers.length,
    hasCherryPicks,
    cherryLen: cherryPeers.length,
  });
  const hintHtml = resolveRelationHintHtml(
    activeTab,
    hasStack,
    isContinuous,
    availableTabs.length
  );

  return (
    <div className="pg-relation-box">
      <div className="pg-relation-head">
        <div className="pg-relation-head-left">
          <RelationTabsHeaderLeft
            curNum={curNum}
            activeTab={activeTab}
            availableTabs={availableTabs}
          />
        </div>
        <RelationHeaderActions
          curNum={curNum}
          activeTab={activeTab}
          hasStack={hasStack}
          stack={stack}
          isContinuous={isContinuous}
          bridge={activeBridge}
        />
      </div>
      {activeTab === 'chain' && hasStack && stack ? (
        <ChainTabContent
          change={change}
          curNum={curNum}
          stack={stack}
          currentBranch={currentBranch}
          currentProject={currentProject}
          bridge={activeBridge}
        />
      ) : activeTab === 'submitted' ? (
        <SubmittedTogetherTabContent
          change={change}
          curNum={curNum}
          submittedPeers={submittedPeers}
          currentBranch={currentBranch}
          currentProject={currentProject}
          bridge={activeBridge}
        />
      ) : activeTab === 'cherrypicks' ? (
        <CherryPicksTabContent
          change={change}
          curNum={curNum}
          cherryPeers={cherryPeers}
          currentBranch={currentBranch}
          currentProject={currentProject}
          bridge={activeBridge}
        />
      ) : (
        <TopicTabContent
          change={change}
          curNum={curNum}
          topicPeers={topicPeers}
          currentBranch={currentBranch}
          currentProject={currentProject}
          bridge={activeBridge}
        />
      )}
      <div
        className="pg-relation-hint"
        dangerouslySetInnerHTML={{ __html: hintHtml }}
      />
    </div>
  );
}

export function renderPgRelationChainHtml(
  change?: GerritChange | null,
  bridge?: GerritBridgeAdapter
): string {
  return renderReactToHtml(
    <RelationChainSidebar change={change} bridge={bridge} />
  );
}
