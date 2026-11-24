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

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import React from 'react';
import {
  GerritUIStore,
  RebaseNoticeBanner,
  createEmptyRebaseStatus,
  createHostBridge,
  escapeHtml,
  escapeJsQuote,
  mountReactIsland,
  renderAiAlternativeCardHtml,
  renderAiAnalysisStatusBannerHtml,
  renderBatchSubmitButtonHtml,
  renderClSwarmBadgeHtml,
  renderCommitAemuMapButtonHtml,
  renderCommitAiAnalysisPanelHtml,
  renderCommitSubmitButtonHtml,
  renderCommitSwarmPanelHtml,
  renderExpandSidebarButtonHtml,
  renderGroupCheckboxHtml,
  renderPgChangeHeaderTitleBarHtml,
  renderPgColHeaderRowHtml,
  renderPgContinuousStackBannerHtml,
  renderPgCopyLinksMenuHtml,
  renderPgCrSubmitRequirementRowHtml,
  renderPgCrVoteCellHtml,
  renderPgHeaderActionButtonsHtml,
  renderPgPvSubmitRequirementRowHtml,
  renderPgPvVoteCellHtml,
  renderPgRebaseNoticeBannerHtml,
  renderPgRelBaseRowHtml,
  renderPgRelStatusTag,
  renderPgRelVoteChip,
  renderPgRelationChainHtml,
  renderPgSrActionBtnHtml,
  renderPgSrAtomsHtml,
  renderPgSrAuditCardHtml,
  renderPgSrDetailRowHtml,
  renderPgSrSummaryChipHtml,
  renderPgStackCommitSequenceBarHtml,
  renderPgStackFilesExtChipsHtml,
  renderPgStackFilesTableHtml,
  renderPgStackSequenceCheckpointsHtml,
  renderPgStackSequenceHeroHtml,
  renderPgStackSequenceTableHtml,
  renderPgStatusTagHtml,
  renderPgStickyScrollActionsHtml,
  renderPgSubmitRequirementsSectionHtml,
  renderPgTriggerVotesSectionHtml,
  renderPgVVoteCellHtml,
  renderPgVoteBoxChipHtml,
  renderQueueBatchSelectStripHtml,
  renderQueueFilterBannerHtml,
  renderReactToHtml,
  renderSidebarHydratingPillHtml,
  renderSinglePresubmitTargetRowHtml,
  renderSpecialistChipsHtml,
  renderSwarmFindingsListHtml,
  renderPresubmitStatusPillHtml,
  renderPgChecksTabButtonHtml,
  renderPgChecksControlsHtml,
  renderPgCheckDetailCardHtml,
  renderPgCheckRunLogDrawerHtml,
  renderPgChecksViewComponentHtml,
  renderPgSavedSearchesCardsHtml,
  renderPgReplyDialogComponentHtml,
  buildCodeViewerDirectoryTree,
  renderPgCodeViewerSidebarHtml,
  renderPgCodeViewerToggleButtonHtml,
  type GerritChange,
  type GerritChangeReferenceInfo,
  type GerritCumulativeStackFilesSummary,
  type GerritStack,
  type GerritStackSubmitSequence,
} from '../index';

const UNICODE_SAMPLE = '测试_🚀_é';

describe('Gerrit React Runtime & Escaping', () => {
  it('escapes HTML entities while preserving single quotes for parity with PolyGerrit template', () => {
    const raw = `<div class="x" data-name='${UNICODE_SAMPLE}'>a & b</div>`;
    const escaped = escapeHtml(raw);
    assert.equal(
      escaped,
      `&lt;div class=&quot;x&quot; data-name='${UNICODE_SAMPLE}'&gt;a &amp; b&lt;/div&gt;`
    );
  });

  it('escapes JS quote strings including backslashes, single quotes, and newlines', () => {
    const raw = `line1\\path\n'${UNICODE_SAMPLE}' "quoted"`;
    const escaped = escapeJsQuote(raw);
    assert.ok(escaped.includes(`\\'${UNICODE_SAMPLE}\\'`));
    assert.ok(escaped.includes('&quot;quoted&quot;'));
    assert.ok(escaped.includes('\\n'));
  });

  it('mounts React islands with graceful innerHTML fallback in headless containers', () => {
    const fakeContainer = { innerHTML: '' } as unknown as Element;
    mountReactIsland(
      fakeContainer,
      <RebaseNoticeBanner
        change={{ number: 42, branch: 'main' }}
        bridge={createHostBridge({
          getChangeRebaseStatus: () => ({
            ...createEmptyRebaseStatus(),
            hasConflict: true,
            needsRebase: true,
            conflicts: [`path/${UNICODE_SAMPLE}.cc`],
          }),
        })}
      />
    );
    assert.ok(fakeContainer.innerHTML.includes('id="pgRebaseNoticeBanner-42"'));
    assert.ok(fakeContainer.innerHTML.includes(`path/${UNICODE_SAMPLE}.cc`));
  });

  it('notifies subscribers and increments revision in GerritUIStore', () => {
    const store = new GerritUIStore();
    let notifications = 0;
    const unsubscribe = store.subscribe(() => {
      notifications += 1;
    });
    assert.equal(store.getSnapshot().revision, 0);
    store.notify();
    assert.equal(notifications, 1);
    assert.equal(store.getSnapshot().revision, 1);
    unsubscribe();
    store.notify();
    assert.equal(notifications, 1);
  });
});

describe('RelationStatusTag & RelationVoteChip', () => {
  it('renders MERGED, ABANDONED, CONFLICT, NOT CURRENT, INDIRECT, and WIP tags', () => {
    assert.ok(
      renderPgRelStatusTag({ number: 1, status: 'MERGED' }).includes(
        'class="pg-status-tag merged"'
      )
    );
    assert.ok(
      renderPgRelStatusTag({ number: 2, status: 'ABANDONED' }).includes(
        'class="pg-status-tag abandoned"'
      )
    );
    const conflictBridge = createHostBridge({
      isClConflict: () => true,
    });
    assert.ok(
      renderPgRelStatusTag({ number: 3 }, conflictBridge).includes(
        'class="pg-status-tag conflict"'
      )
    );
    const outdatedBridge = createHostBridge({
      getChangeRebaseStatus: () => ({
        ...createEmptyRebaseStatus(),
        isOutdated: true,
        isIndirectlyOutdated: false,
        needsRebase: true,
        reason: `Outdated parent ${UNICODE_SAMPLE}`,
      }),
    });
    const notCurrentHtml = renderPgRelStatusTag({ number: 4 }, outdatedBridge);
    assert.ok(notCurrentHtml.includes('>NOT CURRENT<'));
    assert.ok(notCurrentHtml.includes(`Outdated parent ${UNICODE_SAMPLE}`));

    const indirectBridge = createHostBridge({
      getChangeRebaseStatus: () => ({
        ...createEmptyRebaseStatus(),
        isOutdated: true,
        isIndirectlyOutdated: true,
        needsRebase: true,
      }),
    });
    assert.ok(
      renderPgRelStatusTag({ number: 5 }, indirectBridge).includes('>INDIRECT<')
    );
    assert.ok(
      renderPgRelStatusTag({ number: 6, work_in_progress: true }).includes(
        'class="pg-status-tag wip"'
      )
    );
    assert.equal(renderPgRelStatusTag({ number: 7, status: 'NEW' }), '');
  });

  it('renders Code-Review vote chips for -2, -1, 1/2 agent CLs, +2, and +1', () => {
    assert.ok(
      renderPgRelVoteChip({
        number: 10,
        reviewers: [{ name: 'Alice', cr_vote: -2 }],
      }).includes('v-rej')
    );
    assert.ok(
      renderPgRelVoteChip({
        number: 11,
        reviewers: [{ name: 'Bob', cr_vote: -1 }],
      }).includes('v-disliked')
    );
    const partialAgentHtml = renderPgRelVoteChip({
      number: 12,
      is_agent_cl: true,
      human_cr2: ['Alice'],
    });
    assert.ok(partialAgentHtml.includes('+2·½'));
    assert.ok(partialAgentHtml.includes('1/2 human +2 approvals'));

    const approvedAgentHtml = renderPgRelVoteChip({
      number: 13,
      is_agent_cl: true,
      human_cr2: ['Alice', 'Bob'],
    });
    assert.ok(approvedAgentHtml.includes('✓+2'));

    const cr1Html = renderPgRelVoteChip({
      number: 14,
      human_cr1: ['Charlie'],
    });
    assert.ok(cr1Html.includes('>1<') === false && cr1Html.includes('>+1<'));
  });
});

describe('RelationBaseRow & RelationChainSidebar', () => {
  it('renders base row with short SHA, branch filter, and Unicode branch support', () => {
    const change: GerritChange = {
      number: 43790430,
      branch: `refs/heads/studio-${UNICODE_SAMPLE}`,
    };
    const stack: GerritStack = {
      stack_id: 'stack-1',
      commits: [
        {
          number: 43790430,
          branch: `refs/heads/studio-${UNICODE_SAMPLE}`,
          parent_revision: '6da27204abcdef123456',
        },
      ],
    };
    const html = renderPgRelBaseRowHtml(change, stack);
    assert.ok(html.includes('pg-rel-base-row'));
    assert.ok(html.includes(`studio-${UNICODE_SAMPLE}`));
    assert.ok(html.includes('<code>6da27204</code>'));
    assert.ok(html.includes('id="btnFilterBranch-43790430"'));
    assert.ok(html.includes('id="btnCopyBranch-43790430"'));
    assert.ok(html.includes('id="btnCopyBaseSha-43790430"'));
  });

  it('renders relation chain tab with stack interdiff, stepper buttons, and rebase warning banner', () => {
    const c1: GerritChange = {
      number: 43790430,
      subject: `fix(adh): Support foldable ${UNICODE_SAMPLE}`,
      branch: 'studio-main',
      patch_set: 3,
      patch_sets: [1, 2, 3],
    };
    const c2: GerritChange = {
      number: 43790431,
      subject: 'test(adh): Add foldable E2E test',
      branch: 'studio-main',
      patch_set: 2,
      patch_sets: [1, 2, 5],
    };
    const stack: GerritStack = {
      stack_id: 'stack-foldable',
      commits: [c1, c2],
    };
    const bridge = createHostBridge({
      findStackForClOrId: () => stack,
      getSubmittedTogetherPeers: () => [c1, c2],
      canStackInterdiff: () => true,
      isStackInterdiffActive: () => false,
      isContinuousStackDiff: () => true,
      getChangeRebaseStatus: (ch) => ({
        ...createEmptyRebaseStatus(),
        hasConflict: ch.number === 43790430,
        needsRebase: true,
        isOutdated: ch.number === 43790431,
        parentClNumber: ch.number === 43790431 ? 43790430 : null,
      }),
    });

    const html = renderPgRelationChainHtml(c1, bridge);
    assert.ok(html.includes('id="btnRelTabChain-43790430"'));
    assert.ok(html.includes('id="btnRelTabSubmitted-43790430"'));
    assert.ok(html.includes('id="btnRelToggleStackInterdiff-43790430"'));
    assert.ok(html.includes('id="btnToggleContinuousStack"'));
    assert.ok(html.includes('Whole Stack ON'));
    assert.ok(html.includes('id="btnRelPrevCommit-43790430"'));
    assert.ok(html.includes('id="btnRelNextCommit-43790430"'));
    assert.ok(html.includes('id="pgRelChainWarningBanner-43790430"'));
    assert.ok(html.includes('2 changes require rebase'));
    assert.ok(html.includes('id="btnRelLatest-43790431"'));
    assert.ok(html.includes('id="btnRelInterdiff-43790430"'));
    assert.ok(html.includes(UNICODE_SAMPLE));
    assert.ok(html.includes('Press <kbd>Alt+R</kbd> to cycle tabs'));
  });

  it('renders submitted together, cherrypicks, and topic tabs cleanly', () => {
    const change: GerritChange = {
      number: 100,
      subject: `feat: ${UNICODE_SAMPLE}`,
      topic: `topic-${UNICODE_SAMPLE}`,
      change_id: 'I1234567890abcdef',
      branch: 'main',
    };
    const peer: GerritChange = {
      number: 101,
      subject: 'feat: peer change',
      topic: `topic-${UNICODE_SAMPLE}`,
      change_id: 'I1234567890abcdef',
      branch: 'release',
      is_submittable: true,
    };

    const submittedHtml = renderPgRelationChainHtml(
      change,
      createHostBridge({
        getSubmittedTogetherPeers: () => [change, peer],
        getRelatedChangesTab: () => 'submitted',
      })
    );
    assert.ok(submittedHtml.includes('pg-rel-submitted-banner is-warning'));
    assert.ok(submittedHtml.includes('id="btnFilterSubmitted-100"'));

    const cherryHtml = renderPgRelationChainHtml(
      change,
      createHostBridge({
        getCherryPickPeers: () => [peer],
        getCherryPickGroup: () => [change, peer],
        getRelatedChangesTab: () => 'cherrypicks',
      })
    );
    assert.ok(cherryHtml.includes('id="btnRelCherryPick-100"'));
    assert.ok(cherryHtml.includes('I1234567890a…'));

    const topicHtml = renderPgRelationChainHtml(
      change,
      createHostBridge({
        getTopicPeers: () => [change, peer],
        getRelatedChangesTab: () => 'topic',
      })
    );
    assert.ok(topicHtml.includes('id="btnFilterTopic-100"'));
    assert.ok(topicHtml.includes(`topic-${UNICODE_SAMPLE}`));
  });
});

describe('RebaseNoticeBanner & StickyScrollActions', () => {
  it('renders conflict, parent-merged, and outdated rebase notice banners', () => {
    const change: GerritChange = { number: 500, branch: 'emu-main-next' };

    const conflictHtml = renderPgRebaseNoticeBannerHtml(
      change,
      null,
      createHostBridge({
        getChangeRebaseStatus: () => ({
          ...createEmptyRebaseStatus(),
          hasConflict: true,
          needsRebase: true,
          conflicts: [`emulator/${UNICODE_SAMPLE}.cc`],
        }),
      })
    );
    assert.ok(conflictHtml.includes('pg-rebase-needed-banner is-conflict'));
    assert.ok(conflictHtml.includes('id="btnRebaseBannerResolve-500"'));
    assert.ok(conflictHtml.includes('id="btnRebaseBannerAgent-500"'));
    assert.ok(conflictHtml.includes('id="btnRebaseBannerCheck-500"'));
    assert.ok(conflictHtml.includes(`emulator/${UNICODE_SAMPLE}.cc`));

    const mergedHtml = renderPgRebaseNoticeBannerHtml(
      change,
      null,
      createHostBridge({
        getChangeRebaseStatus: () => ({
          ...createEmptyRebaseStatus(),
          isParentMerged: true,
          needsRebase: true,
          parentClNumber: 499,
        }),
      })
    );
    assert.ok(mergedHtml.includes('is-parent-merged'));
    assert.ok(mergedHtml.includes('Parent change ag/499 was merged into emu-main-next.'));

    const outdatedHtml = renderPgRebaseNoticeBannerHtml(
      change,
      null,
      createHostBridge({
        getChangeRebaseStatus: () => ({
          ...createEmptyRebaseStatus(),
          isOutdated: true,
          needsRebase: true,
          parentClNumber: 499,
          parentCurrentPs: 4,
        }),
      })
    );
    assert.ok(outdatedHtml.includes('is-outdated'));
    assert.ok(outdatedHtml.includes('⚡ Rebase on ag/499 (PS 4)'));
  });

  it('renders sticky scroll Reply and Top buttons with draft counts', () => {
    const noDraftsHtml = renderPgStickyScrollActionsHtml(777, 0);
    assert.ok(noDraftsHtml.includes('id="btnStickyHeaderReply-777"'));
    assert.ok(noDraftsHtml.includes('id="btnStickyBackToTop-777"'));
    assert.ok(noDraftsHtml.includes('>Reply</button>'));
    assert.ok(!noDraftsHtml.includes('has-drafts'));

    const withDraftsHtml = renderPgStickyScrollActionsHtml(777, 3);
    assert.ok(withDraftsHtml.includes('has-drafts'));
    assert.ok(withDraftsHtml.includes('>Reply (3 drafts)</button>'));
    assert.equal(renderReactToHtml(null), '');
  });
});

describe('ContinuousStackBanner & StackSequenceAndFilesDialogs', () => {
  const c1: GerritChange = {
    number: 43330001,
    subject: `feat: Base commit ${UNICODE_SAMPLE}`,
    branch: 'emu-main-next',
    status: 'MERGED',
  };
  const c2: GerritChange = {
    number: 43330002,
    subject: 'feat: Middle commit',
    branch: 'emu-main-next',
    status: 'NEW',
  };
  const c3: GerritChange = {
    number: 43330003,
    subject: `test: Leaf commit ${UNICODE_SAMPLE}`,
    branch: 'emu-main-next',
    status: 'NEW',
  };
  const mockStack: GerritStack = {
    stack_id: 'stack-rel-chain-123',
    title: `${UNICODE_SAMPLE} Stack Review`,
    commits: [c1, c2, c3],
  };
  const mockSequence: GerritStackSubmitSequence = {
    total_commits: 3,
    reviewed_commits: 1,
    submittable_commits: 1,
    seq_ready_count: 1,
    all_ready: false,
    bottleneck_step: 2,
    bottleneck_commit: c2,
    bottleneck_reason: 'Missing Code-Review +2',
    items: [
      {
        step: 1,
        number: 43330001,
        subject: c1.subject || '',
        branch: 'emu-main-next',
        author: 'Alice',
        is_reviewed: true,
        cr_label: '+2',
        v_label: '+1',
        seq_status: 'merged',
        blocker_reason: '',
        blocking_ancestor: null,
      },
      {
        step: 2,
        number: 43330002,
        subject: c2.subject || '',
        branch: 'emu-main-next',
        author: 'Bob',
        is_reviewed: false,
        cr_label: '0',
        v_label: '+1',
        seq_status: 'blocked_current',
        blocker_reason: 'Missing Code-Review +2',
        blocking_ancestor: null,
      },
      {
        step: 3,
        number: 43330003,
        subject: c3.subject || '',
        branch: 'emu-main-next',
        author: 'Charlie',
        is_reviewed: false,
        cr_label: '+2',
        v_label: '+1',
        seq_status: 'waiting_ancestor',
        blocker_reason: '',
        blocking_ancestor: c2,
        blocking_step: 2,
      },
    ],
  };

  it('renders ContinuousStackBanner and StackCommitSequenceBar with interdiff and progress stats', () => {
    const bridge = createHostBridge({
      getSelectedClNumber: () => 43330002,
      isStackCommitReviewed: (num) => num === 43330001,
      isStackCommitCollapsed: () => false,
      getStackSubmitSequence: () => mockSequence,
      canStackInterdiff: () => true,
      isStackInterdiffActive: () => true,
      getDiffBundle: (num) => ({
        patch_set: 2,
        files: [
          {
            file: `src/${num}_${UNICODE_SAMPLE}.cc`,
            additions: 10,
            deletions: 2,
          },
        ],
      }),
    });

    const bannerHtml = renderPgContinuousStackBannerHtml(
      mockStack,
      mockStack.commits,
      bridge
    );
    assert.ok(bannerHtml.includes('id="pgStackReviewBanner"'));
    assert.ok(bannerHtml.includes('id="pgStackInterdiffBadge"'));
    assert.ok(bannerHtml.includes(`${UNICODE_SAMPLE} Stack Review (3 commits)`));
    assert.ok(bannerHtml.includes('+30'));
    assert.ok(bannerHtml.includes('-6'));
    assert.ok(bannerHtml.includes('1 of 3 commits reviewed (33%)'));
    assert.ok(bannerHtml.includes('⏳ Step 2/3 Next'));
    assert.ok(bannerHtml.includes('id="btnStackBatchApproveAll"'));
    assert.ok(bannerHtml.includes('id="btnToggleStackInterdiff"'));
    assert.ok(bannerHtml.includes('Reset Stack vs Base'));
    assert.ok(bannerHtml.includes('id="btnStackFilesDrawer"'));
    assert.ok(bannerHtml.includes('id="btnStackSubmitSeq"'));

    const seqBarHtml = renderPgStackCommitSequenceBarHtml(
      c2,
      1,
      3,
      mockStack,
      bridge
    );
    assert.ok(seqBarHtml.includes('Commit 2 of 3'));
    assert.ok(seqBarHtml.includes('id="btnToggleStackCommitCollapse-43330002"'));
    assert.ok(seqBarHtml.includes('id="btnToggleStackCommitReviewed-43330002"'));
    assert.ok(seqBarHtml.includes('✕ Step 2 Blocked: Missing Code-Review +2'));
    assert.ok(seqBarHtml.includes('◀ Prev (ag/43330001)'));
    assert.ok(seqBarHtml.includes('Next (ag/43330003) ▶'));
  });

  it('renders StackSequenceDialog hero, checkpoints, and table rows', () => {
    const heroHtml = renderPgStackSequenceHeroHtml(
      mockSequence,
      mockStack.stack_id
    );
    assert.ok(heroHtml.includes('Submission Blocked at Step 2 (ag/43330002)'));
    assert.ok(heroHtml.includes('Missing Code-Review +2'));
    assert.ok(heroHtml.includes('Jump to Blocker'));

    const cpHtml = renderPgStackSequenceCheckpointsHtml(mockSequence);
    assert.ok(cpHtml.includes('1/3 reviewed (33%)'));
    assert.ok(cpHtml.includes('1/3 submittable (1 in sequence)'));

    const tableHtml = renderPgStackSequenceTableHtml(
      mockSequence,
      43330002,
      mockStack.stack_id
    );
    assert.ok(tableHtml.includes('class="pg-stack-seq-table"'));
    assert.ok(tableHtml.includes('Merged'));
    assert.ok(tableHtml.includes('✕ Step 2 Blocked: Missing Code-Review +2'));
    assert.ok(tableHtml.includes('⏳ Waiting on #2 (ag/43330002)'));
    assert.ok(tableHtml.includes(UNICODE_SAMPLE));
  });

  it('renders StackFilesExtensionChips and StackFilesTable with filtering', () => {
    const summary: GerritCumulativeStackFilesSummary = {
      total_files: 1,
      total_additions: 35,
      total_deletions: 0,
      total_commits: 3,
      extensions: [{ ext: '.cc', count: 1 }],
      items: [
        {
          file: `test_${UNICODE_SAMPLE}.cc`,
          extension: '.cc',
          status: 'A',
          additions: 35,
          deletions: 0,
          occurrences: [
            {
              commit_idx: 2,
              step: 3,
              number: 43330003,
              subject: `test: Leaf commit ${UNICODE_SAMPLE}`,
              patch_set: 3,
              additions: 35,
              deletions: 0,
              status: 'A',
            },
          ],
        },
      ],
    };

    const chipsHtml = renderPgStackFilesExtChipsHtml(summary.extensions, '.cc');
    assert.ok(chipsHtml.includes('pg-stack-ext-chip is-active'));
    assert.ok(chipsHtml.includes('.cc (1)'));

    const tableHtml = renderPgStackFilesTableHtml(
      summary.items,
      43330003,
      ''
    );
    assert.ok(tableHtml.includes('class="pg-stack-files-table"'));
    assert.ok(tableHtml.includes(`test_${UNICODE_SAMPLE}.cc`));
    assert.ok(tableHtml.includes('Step 3 (ag/43330003)'));
    assert.ok(tableHtml.includes('Jump to diff ↗'));

    const emptyHtml = renderPgStackFilesTableHtml([], 43330003, 'missing');
    assert.ok(
      emptyHtml.includes(
        'No files match the active search filter &quot;missing&quot;.'
      )
    );
  });
});

describe('ChangeHeader Components', () => {
  const refInfo: GerritChangeReferenceInfo = {
    changeId: 'Iabcdef1234567890',
    commitSha: '1234567890abcdef',
    gerritUrl: 'https://googleplex-android-review.git.corp.google.com/c/43330055',
    shortlink: 'ag/43330055',
    titleAndUrl: `ag/43330055: feat: ${UNICODE_SAMPLE}`,
    markdown: `[ag/43330055: feat: ${UNICODE_SAMPLE}](https://googleplex-android-review.git.corp.google.com/c/43330055)`,
    gitilesUrl: 'https://googleplex-android.googlesource.com/goldfish/+/1234567890abcdef',
    codeSearchUrl: 'https://source.corp.google.com/search?q=Iabcdef1234567890',
    filePaths: [`src/${UNICODE_SAMPLE}.cc`],
    filePathsText: `src/${UNICODE_SAMPLE}.cc`,
    cherryPickOfChange: null,
    cherryPickOfRef: '',
    revertOfChange: null,
    patchSet: 2,
    repoDownloadCmd: 'repo download goldfish 43330055/2',
    gitCheckoutCmd: 'git fetch && git checkout FETCH_HEAD',
    gitCherryPickCmd: 'git fetch && git cherry-pick FETCH_HEAD',
  };

  it('renders HeaderActionButtons for active, WIP, edit-in-progress, merged, and abandoned changes', () => {
    const activeHtml = renderPgHeaderActionButtonsHtml(
      { number: 77889, status: 'NEW', work_in_progress: false },
      true
    );
    assert.ok(activeHtml.includes('id="btnPublishEdit-77889"'));
    assert.ok(activeHtml.includes('id="btnDiscardEdit-77889"'));
    assert.ok(activeHtml.includes('id="btnHeaderMarkWip-77889"'));
    assert.ok(activeHtml.includes('id="btnHeaderEdit-77889"'));
    assert.ok(activeHtml.includes('Abandon'));

    const wipHtml = renderPgHeaderActionButtonsHtml({
      number: 77889,
      status: 'NEW',
      work_in_progress: true,
    });
    assert.ok(wipHtml.includes('id="btnHeaderMarkReady-77889"'));
    assert.ok(!wipHtml.includes('id="btnHeaderMarkWip-77889"'));

    const mergedHtml = renderPgHeaderActionButtonsHtml({
      number: 77889,
      status: 'MERGED',
    });
    assert.ok(mergedHtml.includes('id="btnHeaderRevert-77889"'));
    assert.ok(!mergedHtml.includes('id="btnHeaderEdit-77889"'));

    const abandonedHtml = renderPgHeaderActionButtonsHtml({
      number: 77889,
      status: 'ABANDONED',
    });
    assert.ok(abandonedHtml.includes('Restore'));
  });

  it('renders ExpandSidebarButton, CommitAemuMapButton, SrSummaryChip, CopyLinksMenu, and ChangeHeaderTitleBar', () => {
    const expandHtml = renderExpandSidebarButtonHtml(true, 12);
    assert.ok(expandHtml.includes('id="btnExpandSidebar"'));
    assert.ok(expandHtml.includes('style="display:inline-flex;"'));
    assert.ok(expandHtml.includes('Queue (12)'));

    const mapHtml = renderCommitAemuMapButtonHtml(
      43330055,
      'http://go/aemu-map#ag=43330055'
    );
    assert.ok(mapHtml.includes('id="btnArchitectureMap-43330055"'));
    assert.ok(mapHtml.includes('Architecture Map'));

    const srHtml = renderPgSrSummaryChipHtml(43330055, {
      total: 2,
      satisfied: 2,
      status: 'submittable',
      label: '✓ Submittable',
      tip: 'All submit requirements satisfied',
      cls: 'sr-chip-submittable',
    });
    assert.ok(srHtml.includes('sr-chip-submittable'));
    assert.ok(srHtml.includes('>Submittable<'));

    const copyMenuHtml = renderPgCopyLinksMenuHtml(
      { number: 43330055, patch_set: 2 },
      { patch_set: 2 },
      refInfo,
      true
    );
    assert.ok(copyMenuHtml.includes('id="pgCopyLinksWrap-43330055"'));
    assert.ok(copyMenuHtml.includes('id="btnCopyLinks-43330055"'));
    assert.ok(copyMenuHtml.includes('id="pgCopyLinksDropdown-43330055"'));
    assert.ok(copyMenuHtml.includes('Files (1)'));
    assert.ok(copyMenuHtml.includes(`src/${UNICODE_SAMPLE}.cc`));

    const titleBarHtml = renderPgChangeHeaderTitleBarHtml({
      change: {
        number: 43330055,
        subject: `feat: Header ${UNICODE_SAMPLE}`,
        patch_set: 2,
        attention_waiting_display: '2d',
      },
      bundle: { patch_set: 2, is_pure_rebase: true },
      statusInfo: {
        cls: 'status-conflict',
        label: 'Merge Conflict',
        tip: 'Conflict detected',
        secondaryCls: 'status-wip',
        secondaryLabel: 'WIP',
        secondaryTip: 'Work in progress',
      },
      srSummary: {
        total: 2,
        satisfied: 1,
        status: 'pending',
        label: '✕ 1/2 satisfied',
        tip: '1 pending',
        cls: 'sr-chip-pending',
      },
      refInfo,
      isCopyMenuOpen: false,
      isStarred: true,
      isViewerTurn: true,
      expandSidebarVisible: true,
      visibleCommitCount: 5,
    });
    assert.ok(titleBarHtml.includes('class="pg-header-title"'));
    assert.ok(titleBarHtml.includes('Merge Conflict'));
    assert.ok(titleBarHtml.includes('>WIP<'));
    assert.ok(titleBarHtml.includes('Your Turn'));
    assert.ok(titleBarHtml.includes('Pure Rebase'));
    assert.ok(titleBarHtml.includes('id="btnHeaderStar-43330055"'));
    assert.ok(titleBarHtml.includes(`feat: Header ${UNICODE_SAMPLE}`));
  });
});

describe('DashboardQueueTable Components', () => {
  it('renders CommitSubmitButton, BatchSubmitButton, CrVoteCell, VVoteCell, PvVoteCell, and DashboardStatusTag', () => {
    const submitHtml = renderCommitSubmitButtonHtml(
      { canSubmit: true, title: 'Submit ag/101', label: 'Submit' },
      false
    );
    assert.ok(submitHtml.includes('id="btnSubmitCl"'));
    assert.ok(submitHtml.includes('aria-disabled="false"'));

    const batchSubmitHtml = renderBatchSubmitButtonHtml(
      { canSubmit: false, title: 'Blocked', label: 'Submit 2 CLs' },
      true
    );
    assert.ok(batchSubmitHtml.includes('id="btnBatchSubmitCls"'));
    assert.ok(batchSubmitHtml.includes('disabled'));

    const crApprovedHtml = renderPgCrVoteCellHtml(
      { number: 101, human_cr2: [`Alice_${UNICODE_SAMPLE}`] },
      []
    );
    assert.ok(crApprovedHtml.includes('✓+2'));
    assert.ok(crApprovedHtml.includes(`Alice_${UNICODE_SAMPLE}`));

    const vCellHtml = renderPgVVoteCellHtml({
      score: 1,
      text: '+1',
      cls: 'pg-vote-chip v-approved',
      tooltip: 'Verified +1',
    });
    assert.ok(vCellHtml.includes('pg-v-chip-wrap'));
    assert.ok(vCellHtml.includes('>+1<'));

    const pvCellHtml = renderPgPvVoteCellHtml(101, 'stack-1', {
      status: 'passed',
      tooltip: 'Presubmit passed',
    });
    assert.ok(pvCellHtml.includes('data-pv-cl="101"'));
    assert.ok(pvCellHtml.includes('>✓<'));

    const conflictWipTag = renderPgStatusTagHtml({
      isMerged: false,
      isAbandoned: false,
      hasConflict: true,
      isWip: true,
      isCcOnly: false,
      isSubmittable: false,
    });
    assert.ok(conflictWipTag.includes('st-conflict'));
    assert.ok(conflictWipTag.includes('Merge Conflict'));
    assert.ok(conflictWipTag.includes('st-wip'));
  });

  it('renders ColHeaderRow, GroupCheckbox, QueueBatchSelectStrip, QueueFilterBanner, and SidebarHydratingPill', () => {
    const checkHtml = renderGroupCheckboxHtml([101, 102], 2, 'Select all');
    assert.ok(checkHtml.includes('data-group-cls="101,102"'));
    assert.ok(checkHtml.includes('checked'));

    const colHdrHtml = renderPgColHeaderRowHtml(
      [101, 102],
      1,
      'Group',
      'Reviewers',
      'Waiting'
    );
    assert.ok(colHdrHtml.includes('pg-col-header-row has-selection'));
    assert.ok(colHdrHtml.includes('>Reviewers<'));
    assert.ok(colHdrHtml.includes('>Waiting<'));

    const stripHtml = renderQueueBatchSelectStripHtml([101, 102], 1, 1);
    assert.ok(stripHtml.includes('id="queueBatchStrip"'));
    assert.ok(stripHtml.includes('1 selected'));
    assert.ok(stripHtml.includes('pg-batch-btn-clear'));

    const filterHtml = renderQueueFilterBannerHtml(
      {
        filterActive: true,
        gatedCommitCount: 2,
        ungatedCommitCount: 5,
        hiddenByGatesCount: 3,
      },
      'joshuaduong',
      UNICODE_SAMPLE,
      false
    );
    assert.ok(filterHtml.includes('queue-filter-banner'));
    assert.ok(filterHtml.includes('author:joshuaduong'));
    assert.ok(filterHtml.includes(UNICODE_SAMPLE));
    assert.ok(filterHtml.includes('Show All (5)'));

    const hydratingHtml = renderSidebarHydratingPillHtml(true);
    assert.ok(hydratingHtml.includes('sidebar-hydrating-pill'));
    assert.equal(renderSidebarHydratingPillHtml(false), '');
  });
});

describe('AiSwarmAndSubmitReqs Components', () => {
  it('renders AiAnalysisStatusBanner, AiAlternativeCard, and CommitAiAnalysisPanel', () => {
    const thinkingHtml = renderAiAnalysisStatusBannerHtml(101, 2, 'thinking');
    assert.ok(thinkingHtml.includes('id="aiAnalysisPanel-101"'));
    assert.ok(thinkingHtml.includes('Analyzing ag/101 (PS2)…'));

    const errHtml = renderAiAnalysisStatusBannerHtml(101, 2, 'error', `Timeout ${UNICODE_SAMPLE}`);
    assert.ok(errHtml.includes('Problem &amp; Alternatives Unavailable (ag/101)'));
    assert.ok(errHtml.includes(`Timeout ${UNICODE_SAMPLE}`));

    const altCardHtml = renderAiAlternativeCardHtml(
      101,
      { id: 'alt-1', verdict_tone: 'good', title: `Option A ${UNICODE_SAMPLE}` },
      '<p>How it works</p>',
      'Higher latency',
      '<div class="diff-inline-comment">Attached</div>'
    );
    assert.ok(altCardHtml.includes('id="aiAltCard-101-alt-1"'));
    assert.ok(altCardHtml.includes(`Option A ${UNICODE_SAMPLE}`));
    assert.ok(altCardHtml.includes('<strong>Tradeoff:</strong> Higher latency'));
    assert.ok(altCardHtml.includes('<div class="diff-inline-comment">Attached</div>'));

    const panelHtml = renderCommitAiAnalysisPanelHtml({
      num: 101,
      isCollapsed: false,
      scopeTone: 'focus',
      scopeIcon: '🧭',
      scopeLabel: 'Design Assessment',
      analyzedByLdap: 'joshuaduong',
      shared: true,
      bottomLineHtml: 'Bottom line summary',
      underlyingProblemHtml: 'Underlying problem text',
      chosenSolutionHtml: 'Chosen solution text',
      tradeoffsHtmlList: ['Tradeoff 1'],
      alternativesCount: 1,
      alternativesCardsHtml: altCardHtml,
    });
    assert.ok(panelHtml.includes('🧠 Problem &amp; Alternatives · ag/101'));
    assert.ok(panelHtml.includes('👥 Shared · @joshuaduong'));
    assert.ok(panelHtml.includes('Alternative Solutions (1)'));
  });

  it('renders ClSwarmBadge, SpecialistChips, SwarmFindingsList, and CommitSwarmPanel', () => {
    const runningBadge = renderClSwarmBadgeHtml(
      { state: 'running', completed_specialists: 3, total_specialists: 8 },
      12
    );
    assert.ok(runningBadge.includes('Review Swarm · 3/8 (12s)'));

    const chipsHtml = renderSpecialistChipsHtml(
      [
        { role: 'Unicode Auditor', status: 'done', finding_count: 1, duration_sec: 1.4 },
        { role: 'Security Auditor', status: 'done', finding_count: 0, duration_sec: 0.9 },
      ],
      false
    );
    assert.ok(chipsHtml.includes('★ Unicode (1 · 1.4s)'));
    assert.ok(chipsHtml.includes('✓ Security · 0.9s'));

    const findingsHtml = renderSwarmFindingsListHtml(101, [
      {
        file: 'src/foo.cc',
        line: 42,
        category: 'unicode',
        severity: 'blocking',
        message: `Raw path ${UNICODE_SAMPLE}`,
        suggested_fix: 'Use android::base::file',
      },
    ]);
    assert.ok(findingsHtml.includes('src/foo.cc:42'));
    assert.ok(findingsHtml.includes(`Raw path ${UNICODE_SAMPLE}`));
    assert.ok(findingsHtml.includes('Suggested fix: Use android::base::file'));

    const completedPanel = renderCommitSwarmPanelHtml(101, {
      state: 'completed',
      total_findings: 1,
      wall_clock_sec: 2.5,
      parallel_speedup: 4.2,
      findings: [{ file: 'src/foo.cc', line: 42, message: 'Issue' }],
    });
    assert.ok(completedPanel.includes('1 Verified Finding'));
    assert.ok(completedPanel.includes('4.20x parallel speedup'));
  });

  it('renders SubmitRequirements and SrAuditCard components', () => {
    const voteChip = renderPgVoteBoxChipHtml(
      { name: 'Alice', email: 'alice@google.com' },
      2,
      'Code-Review',
      '#1a73e8',
      'A'
    );
    assert.ok(voteChip.includes('pg-vote-box v-pos2'));
    assert.ok(voteChip.includes('>+2<'));

    const atomsHtml = renderPgSrAtomsHtml({
      passing_atoms: ['label:Code-Review=+2'],
      failing_atoms: ['label:Verified=+1'],
    });
    assert.ok(atomsHtml.includes('pg-sr-atom-pass'));
    assert.ok(atomsHtml.includes('pg-sr-atom-fail'));

    const actionBtn = renderPgSrActionBtnHtml({ number: 101 }, { name: 'Presubmit-Verified' });
    assert.ok(actionBtn.includes('Inspect Checks Tab'));

    const detailRow = renderPgSrDetailRowHtml(
      { number: 101 },
      {
        name: 'Code-Review',
        status: 'SATISFIED',
        description: `Approval ${UNICODE_SAMPLE}`,
        expression: 'label:Code-Review=MAX',
      }
    );
    assert.ok(detailRow.includes('tag-satisfied'));
    assert.ok(detailRow.includes(`Approval ${UNICODE_SAMPLE}`));

    const crRow = renderPgCrSubmitRequirementRowHtml({
      satisfied: false,
      minVote: 0,
      reqCr2: 2,
      doneCr2: 1,
      chipsHtml: voteChip,
    });
    assert.ok(crRow.includes('1 more +2 needed'));

    const pvRow = renderPgPvSubmitRequirementRowHtml({
      clNum: 101,
      satisfied: true,
      running: false,
      tooltip: 'Passed',
      votesHtml: voteChip,
    });
    assert.ok(pvRow.includes('Presubmit-Verified'));

    const triggerHtml = renderPgTriggerVotesSectionHtml({
      prVotesHtml: voteChip,
      asVotesHtml: '<span class="pg-no-votes">No votes</span>',
      extraPills: [{ label: 'API-Review', chipsHtml: voteChip }],
    });
    assert.ok(triggerHtml.includes('Trigger Votes'));
    assert.ok(triggerHtml.includes('API-Review'));

    const sectionHtml = renderPgSubmitRequirementsSectionHtml({
      clNum: 101,
      satisfiedCount: 2,
      totalCount: 2,
      rowsHtml: `${crRow}${pvRow}`,
      triggerVotesHtml: triggerHtml,
    });
    assert.ok(sectionHtml.includes('sr-badge-ok'));
    assert.ok(sectionHtml.includes('>2/2<'));

    const auditCardHtml = renderPgSrAuditCardHtml({
      clNum: 101,
      sr: {
        name: 'Code-Review',
        status: 'SATISFIED',
        description: 'Needs +2',
        expression: 'label:Code-Review=MAX',
        passing_atoms: ['label:Code-Review=+2'],
      },
      highlightedExprHtml: '<span class="pg-expr-key">label:</span>Code-Review=MAX',
      crChipsHtml: voteChip,
    });
    assert.ok(auditCardHtml.includes('pg-sr-audit-card is-satisfied'));
    assert.ok(auditCardHtml.includes('Current votes:'));
  });
});

describe('ChecksAndReplyView Components', () => {
  it('renders PresubmitStatusPill, SinglePresubmitTargetRow, ChecksTabButton, and ChecksControls', () => {
    assert.ok(
      renderPresubmitStatusPillHtml({ name: 't1', status: 'failed', raw_status: 'TIMED_OUT' }).includes('✗ TIMEOUT')
    );
    assert.ok(
      renderPresubmitStatusPillHtml({ name: 't2', status: 'passed', cached: true }).includes('✓ CACHED')
    );

    const rowHtml = renderSinglePresubmitTargetRowHtml(
      {
        name: `//emulator/tests:${UNICODE_SAMPLE}_test`,
        kind: 'test',
        status: 'failed',
        subtitle: 'Assertion failed',
        duration_display: '42s',
        artifact_id: 'inv-123',
        secondary_url: 'https://sponge/123',
        secondary_label: 'Logs',
      },
      'https://gerrit/checks'
    );
    assert.ok(rowHtml.includes(`//emulator/tests:${UNICODE_SAMPLE}_test ↗`));
    assert.ok(rowHtml.includes('inv-123'));
    assert.ok(rowHtml.includes('Logs ↗'));

    const tabBtnHtml = renderPgChecksTabButtonHtml(
      { number: 43330025 },
      'checks',
      { data: { counts: { total: 4, failed: 1, warning: 0, running: 0, queued: 0, passed: 3 } } },
      { status: 'failed' }
    );
    assert.ok(tabBtnHtml.includes('id="pgTabChecks-43330025"'));
    assert.ok(tabBtnHtml.includes('pg-tab active'));
    assert.ok(tabBtnHtml.includes('✕ 1'));

    const controlsHtml = renderPgChecksControlsHtml(
      { number: 43330025, viewer_presubmit_ready: false },
      { loading: false },
      { status: 'failed', workplanId: 'wp-99', workplanUrl: 'https://wp/99', checksUrl: 'https://checks/99' }
    );
    assert.ok(controlsHtml.includes('☑ Stage PR +1'));
    assert.ok(controlsHtml.includes('Workplan wp-99 ↗'));
  });

  it('renders ChecksView, CheckDetailCard, and CheckRunLogDrawer', () => {
    const target = {
      name: `goldfish_${UNICODE_SAMPLE}_unit_test`,
      kind: 'test',
      status: 'failed',
      raw_status: 'FAILED',
      subtitle: `Test ${UNICODE_SAMPLE} crashed`,
      duration_display: '1m 12s',
      artifact_id: 'inv-43330025',
      blocking_level: 'BLOCKING',
    };
    const checksViewHtml = renderPgChecksViewComponentHtml(
      { number: 43330025, patch_set: 3 },
      {
        loading: false,
        data: {
          counts: { total: 1, failed: 1, warning: 0, running: 0, queued: 0, passed: 0, cached: 0 },
          targets: [target],
        },
      },
      { status: 'failed', workplanId: 'L123', workplanUrl: 'https://wp', checksUrl: 'https://checks' },
      'all',
      'all',
      '',
      target.name,
      true,
      '',
      '1/1 finished',
      () => [{ type: 'err', text: `[ERROR] ${UNICODE_SAMPLE} failure` }]
    );
    assert.ok(checksViewHtml.includes('1 of 1 checks failed'));
    assert.ok(checksViewHtml.includes(`goldfish_${UNICODE_SAMPLE}_unit_test`));
    assert.ok(checksViewHtml.includes('id="pgChecksLogDrawer-43330025"'));
    assert.ok(checksViewHtml.includes(`[ERROR] ${UNICODE_SAMPLE} failure`));
  });

  it('renders SavedSearchesList and ReplyDialog with Unicode content', () => {
    const savedHtml = renderPgSavedSearchesCardsHtml([
      {
        id: 'sec-1',
        title: `Goldfish ${UNICODE_SAMPLE}`,
        query: 'project:goldfish status:open',
        showOnDashboard: true,
        matchCount: 3,
      },
    ]);
    assert.ok(savedHtml.includes('id="pgSavedSearchCard-sec-1"'));
    assert.ok(savedHtml.includes(`Goldfish ${UNICODE_SAMPLE}`));
    assert.ok(savedHtml.includes('3 matches'));

    const replyHtml = renderPgReplyDialogComponentHtml(
      { number: 43330099, patch_set: 4 },
      true,
      'joshuaduong',
      'Notifications scoped to OWNER (--notify=OWNER)',
      `LGTM ${UNICODE_SAMPLE}`,
      true,
      '<div>Participants</div>',
      '<button>LGTM +2</button>',
      '<div>Toolbar</div>',
      '<div>Attention</div>',
      '<div>Scores</div>'
    );
    assert.ok(replyHtml.includes('id="replyDialogBackdrop"'));
    assert.ok(replyHtml.includes('class="pg-reply-backdrop open"'));
    assert.ok(replyHtml.includes('Reply to <strong>ag/43330099</strong> · PS4'));
    assert.ok(replyHtml.includes(`LGTM ${UNICODE_SAMPLE}`));
    assert.ok(replyHtml.includes('id="btnSubmitVotes"'));
  });

  it('builds compacted directory tree and renders CodeViewerSidebar with Unicode paths', () => {
    const files = [
      {
        file: `tools/emu-dev-cli/src/ui/${UNICODE_SAMPLE}/sidebar.tsx`,
        status: 'A',
        additions: 120,
        deletions: 0,
        isReviewed: true,
        unresolvedCount: 2,
        draftCount: 1,
      },
      {
        file: `tools/emu-dev-cli/src/ui/${UNICODE_SAMPLE}/viewer.tsx`,
        status: 'M',
        additions: 35,
        deletions: 12,
        isReviewed: false,
      },
      {
        file: 'BUILD.bazel',
        status: 'M',
        additions: 4,
        deletions: 1,
      },
    ];
    const tree = buildCodeViewerDirectoryTree(files);
    assert.equal(tree.totalFileCount, 3);
    assert.equal(tree.reviewedFileCount, 1);
    assert.equal(tree.children.length, 1);
    assert.equal(
      tree.children[0].displayName,
      `tools/emu-dev-cli/src/ui/${UNICODE_SAMPLE}`
    );
    assert.equal(tree.children[0].files.length, 2);
    assert.equal(tree.files.length, 1);
    assert.equal(tree.files[0].file, 'BUILD.bazel');

    const toggleInactive = renderPgCodeViewerToggleButtonHtml({
      clNumber: 43851398,
      isActive: false,
      fileCount: 3,
    });
    assert.ok(toggleInactive.includes('id="btnCodeViewerMode-43851398"'));
    assert.ok(toggleInactive.includes('Code Viewer (3)'));

    const toggleActive = renderPgCodeViewerToggleButtonHtml({
      clNumber: 43851398,
      isActive: true,
      fileCount: 3,
    });
    assert.ok(toggleActive.includes('Exit Code Viewer'));
    assert.ok(toggleActive.includes('is-active'));

    const sidebarHtml = renderPgCodeViewerSidebarHtml({
      clNumber: 43851398,
      patchSet: 2,
      subject: `feat(gerrit-ui): Code viewer ${UNICODE_SAMPLE}`,
      files,
      selectedFile: files[0].file,
      filterQuery: 'sidebar',
      displayMode: 'single',
      canNavigateCommits: true,
    });
    assert.ok(sidebarHtml.includes('id="btnExitCodeViewerSidebar"'));
    assert.ok(sidebarHtml.includes('pg-dashboard-bar'));
    assert.ok(sidebarHtml.includes('pg-section-box'));
    assert.ok(sidebarHtml.includes('pg-section-header'));
    assert.ok(sidebarHtml.includes('pg-file-status-badge status-A'));
    assert.ok(sidebarHtml.includes('pg-row-cursor'));
    assert.ok(sidebarHtml.includes('group-toggle'));
    assert.ok(sidebarHtml.includes('pg-reviewed-summary-badge'));
    assert.ok(sidebarHtml.includes('pg-btn-subtle'));
    assert.ok(sidebarHtml.includes('ag/43851398'));
    assert.ok(sidebarHtml.includes(`tools/emu-dev-cli/src/ui/${UNICODE_SAMPLE}/`));
    assert.ok(sidebarHtml.includes('<mark class="cv-tree-match">sidebar</mark>.tsx'));
    assert.ok(sidebarHtml.includes('1/3 ✓'));
  });
});

