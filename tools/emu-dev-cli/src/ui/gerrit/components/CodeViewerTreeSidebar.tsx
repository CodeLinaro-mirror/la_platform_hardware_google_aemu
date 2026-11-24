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
import { escapeJsQuote, renderReactToHtml } from '../runtime';

export const COMMIT_MSG_MAGIC_PATH = '/COMMIT_MSG';
export const MERGE_LIST_MAGIC_PATH = '/MERGE_LIST';

const TREE_INDENT_BASE_PX = 6;
const TREE_INDENT_STEP_PX = 14;
const BTN_CODE_VIEWER_PREFIX = 'btnCodeViewerMode-';

export type CodeViewerDisplayMode = 'single' | 'all';

export interface CodeViewerFileEntry {
  file: string;
  status: string;
  additions: number;
  deletions: number;
  isReviewed?: boolean;
  unresolvedCount?: number;
  commentCount?: number;
  draftCount?: number;
  isConflict?: boolean;
  isBinary?: boolean;
  isImage?: boolean;
}

export interface CodeViewerTreeNode {
  dirPath: string;
  displayName: string;
  children: CodeViewerTreeNode[];
  files: CodeViewerFileEntry[];
  totalFileCount: number;
  reviewedFileCount: number;
  unresolvedCount: number;
  draftCount: number;
  additions: number;
  deletions: number;
}

interface MutableDirNode {
  segment: string;
  dirPath: string;
  childMap: Map<string, MutableDirNode>;
  files: CodeViewerFileEntry[];
}

function createMutableDirNode(segment: string, dirPath: string): MutableDirNode {
  return {
    segment,
    dirPath,
    childMap: new Map<string, MutableDirNode>(),
    files: [],
  };
}

function insertFileIntoMutableTree(
  root: MutableDirNode,
  entry: CodeViewerFileEntry
): void {
  const rawPath = String(entry.file || '');
  const parts = rawPath.split('/').filter(Boolean);
  if (parts.length <= 1) {
    root.files.push(entry);
    return;
  }
  let curr = root;
  let accPath = '';
  for (let i = 0; i < parts.length - 1; i++) {
    const seg = parts[i];
    accPath = accPath ? `${accPath}/${seg}` : seg;
    let next = curr.childMap.get(seg);
    if (!next) {
      next = createMutableDirNode(seg, accPath);
      curr.childMap.set(seg, next);
    }
    curr = next;
  }
  curr.files.push(entry);
}

function compareCaseInsensitive(a: string, b: string): number {
  const al = a.toLowerCase();
  const bl = b.toLowerCase();
  if (al !== bl) {
    return al < bl ? -1 : 1;
  }
  return a < b ? -1 : a > b ? 1 : 0;
}

function compactSingleChildChain(
  node: MutableDirNode
): { target: MutableDirNode; displayName: string } {
  let curr = node;
  let displayName = node.segment;
  while (curr.files.length === 0 && curr.childMap.size === 1) {
    const onlyChild = curr.childMap.values().next().value as MutableDirNode;
    displayName = `${displayName}/${onlyChild.segment}`;
    curr = onlyChild;
  }
  return { target: curr, displayName };
}

function finalizeDirNode(rawNode: MutableDirNode, isRoot: boolean): CodeViewerTreeNode {
  const compacted = isRoot
    ? { target: rawNode, displayName: '' }
    : compactSingleChildChain(rawNode);
  const target = compacted.target;

  const sortedChildRaw = Array.from(target.childMap.values()).sort((a, b) =>
    compareCaseInsensitive(a.segment, b.segment)
  );
  const children = sortedChildRaw.map((child) => finalizeDirNode(child, false));
  const files = [...target.files].sort((a, b) =>
    compareCaseInsensitive(getBasename(a.file), getBasename(b.file))
  );

  let totalFileCount = files.length;
  let reviewedFileCount = files.filter((f) => Boolean(f.isReviewed)).length;
  let unresolvedCount = files.reduce((acc, f) => acc + Number(f.unresolvedCount || 0), 0);
  let draftCount = files.reduce((acc, f) => acc + Number(f.draftCount || 0), 0);
  let additions = files.reduce((acc, f) => acc + Number(f.additions || 0), 0);
  let deletions = files.reduce((acc, f) => acc + Number(f.deletions || 0), 0);

  for (const child of children) {
    totalFileCount += child.totalFileCount;
    reviewedFileCount += child.reviewedFileCount;
    unresolvedCount += child.unresolvedCount;
    draftCount += child.draftCount;
    additions += child.additions;
    deletions += child.deletions;
  }

  return {
    dirPath: target.dirPath,
    displayName: compacted.displayName,
    children,
    files,
    totalFileCount,
    reviewedFileCount,
    unresolvedCount,
    draftCount,
    additions,
    deletions,
  };
}

export function getBasename(filePath: string): string {
  const slashIdx = filePath.lastIndexOf('/');
  return slashIdx >= 0 ? filePath.slice(slashIdx + 1) : filePath;
}

export function buildCodeViewerDirectoryTree(
  files: CodeViewerFileEntry[]
): CodeViewerTreeNode {
  const root = createMutableDirNode('', '');
  for (const entry of files) {
    if (!entry || !entry.file) continue;
    if (
      entry.file === COMMIT_MSG_MAGIC_PATH ||
      entry.file === MERGE_LIST_MAGIC_PATH
    ) {
      continue;
    }
    insertFileIntoMutableTree(root, entry);
  }
  return finalizeDirNode(root, true);
}

export interface CodeViewerToggleButtonProps {
  clNumber: number;
  isActive?: boolean;
  fileCount?: number;
  compact?: boolean;
}

export function CodeViewerToggleButton({
  clNumber,
  isActive = false,
  fileCount = 0,
  compact = false,
}: CodeViewerToggleButtonProps): React.ReactElement | null {
  if (!clNumber) {
    return null;
  }
  const countSuffix = fileCount > 0 && !compact ? ` (${fileCount})` : '';
  const label = isActive
    ? 'Exit Code Viewer'
    : compact
      ? 'Code Viewer'
      : `Code Viewer${countSuffix}`;
  return (
    <button
      id={compact ? undefined : `${BTN_CODE_VIEWER_PREFIX}${clNumber}`}
      type="button"
      className={`btn pg-btn-header-action pg-btn-code-viewer ${
        isActive ? 'is-active' : ''
      } ${compact ? 'is-compact' : ''}`}
      htmlOnClick={`event.stopPropagation(); togglePgCodeViewerMode(${clNumber})`}
      title="Toggle Commit Code Viewer directory tree sidebar (Shortcut: \\)"
      aria-pressed={isActive ? 'true' : 'false'}
    >
      <span className="pg-btn-icon" aria-hidden="true">
        {isActive ? '←' : '📂'}
      </span>{' '}
      <span>{label}</span>
    </button>
  );
}

function renderStatusBadge(status: string): React.ReactElement {
  const norm = (status || 'M').toUpperCase();
  return (
    <span
      className={`pg-file-status-badge status-${norm} cv-status-badge`}
      title={`File status: ${norm}`}
    >
      {norm}
    </span>
  );
}

function renderHighlightedBasename(
  name: string,
  filterQuery: string
): React.ReactNode {
  const trimmed = (filterQuery || '').trim();
  if (!trimmed) {
    return name;
  }
  const lowerName = name.toLowerCase();
  const lowerQuery = trimmed.toLowerCase();
  const idx = lowerName.indexOf(lowerQuery);
  if (idx < 0) {
    return name;
  }
  const before = name.slice(0, idx);
  const match = name.slice(idx, idx + trimmed.length);
  const after = name.slice(idx + trimmed.length);
  return (
    <>
      {before}
      <mark className="cv-tree-match">{match}</mark>
      {after}
    </>
  );
}

function renderFileAnnotations(entry: CodeViewerFileEntry): React.ReactNode {
  const unres = Number(entry.unresolvedCount || 0);
  const drafts = Number(entry.draftCount || 0);
  const totalComments = Number(entry.commentCount || 0);
  return (
    <>
      {entry.isConflict ? (
        <span className="cv-file-pill is-conflict" title="Merge conflict">
          !
        </span>
      ) : null}
      {drafts > 0 ? (
        <span
          className="cv-file-pill is-draft"
          title={`${drafts} draft comment(s)`}
        >
          📝{drafts}
        </span>
      ) : null}
      {unres > 0 ? (
        <span
          className="cv-file-pill is-unresolved"
          title={`${unres} unresolved comment(s)`}
        >
          ⚠{unres}
        </span>
      ) : totalComments > 0 ? (
        <span
          className="cv-file-pill is-comment"
          title={`${totalComments} comment(s)`}
        >
          💬{totalComments}
        </span>
      ) : null}
    </>
  );
}

function renderDeltaSummary(additions: number, deletions: number): React.ReactNode {
  const adds = Math.max(0, Number(additions || 0));
  const dels = Math.max(0, Number(deletions || 0));
  if (adds === 0 && dels === 0) {
    return null;
  }
  return (
    <span className="pg-file-delta cv-file-delta">
      {adds > 0 ? <span className="pg-delta-add">+{adds}</span> : null}
      {dels > 0 ? <span className="pg-delta-del">-{dels}</span> : null}
    </span>
  );
}

interface FileLeafRowProps {
  clNumber: number;
  patchSet: number;
  entry: CodeViewerFileEntry;
  depth: number;
  selectedFile: string;
  filterQuery: string;
}

function FileLeafRow({
  clNumber,
  patchSet,
  entry,
  depth,
  selectedFile,
  filterQuery,
}: FileLeafRowProps): React.ReactElement {
  const isSelected = entry.file === selectedFile;
  const isReviewed = Boolean(entry.isReviewed);
  const indentPx = TREE_INDENT_BASE_PX + depth * TREE_INDENT_STEP_PX;
  const jsFile = escapeJsQuote(entry.file);
  const baseName = getBasename(entry.file);

  return (
    <div
      role="treeitem"
      tabIndex={0}
      aria-selected={isSelected ? 'true' : 'false'}
      className={`cv-tree-row cv-tree-file-row ${
        isSelected ? 'is-selected' : ''
      } ${isReviewed ? 'is-reviewed' : ''}`}
      data-cv-cl={clNumber}
      data-cv-file={entry.file}
      htmlStyle={`padding-left:${indentPx}px;`}
      htmlOnClick={`selectCodeViewerFile(${clNumber}, ${patchSet}, '${jsFile}', event)`}
      htmlOnKeyDown={`onCodeViewerFileKeyDown(event, ${clNumber}, ${patchSet}, '${jsFile}')`}
      title={entry.file}
    >
      <span className="pg-row-cursor cv-row-cursor" aria-hidden="true">
        ▸
      </span>
      {renderStatusBadge(entry.status)}
      <span className="pg-tree-file-name cv-tree-file-name">
        {renderHighlightedBasename(baseName, filterQuery)}
      </span>
      <span className="cv-tree-file-meta">
        {renderFileAnnotations(entry)}
        {renderDeltaSummary(entry.additions, entry.deletions)}
        <button
          type="button"
          className={`cv-reviewed-toggle ${isReviewed ? 'is-checked' : ''}`}
          htmlOnClick={`toggleCodeViewerFileReviewed(event, ${clNumber}, ${patchSet}, '${jsFile}')`}
          title={
            isReviewed
              ? `Reviewed (${entry.file}) · Click to mark unreviewed`
              : `Mark ${entry.file} as reviewed`
          }
          aria-label={`Toggle reviewed for ${entry.file}`}
        >
          ✓
        </button>
      </span>
    </div>
  );
}

interface DirBranchProps {
  clNumber: number;
  patchSet: number;
  node: CodeViewerTreeNode;
  depth: number;
  selectedFile: string;
  collapsedDirs: Set<string>;
  filterQuery: string;
}

function DirBranch({
  clNumber,
  patchSet,
  node,
  depth,
  selectedFile,
  collapsedDirs,
  filterQuery,
}: DirBranchProps): React.ReactElement {
  const isCollapsed = !filterQuery && collapsedDirs.has(node.dirPath);
  const indentPx = TREE_INDENT_BASE_PX + depth * TREE_INDENT_STEP_PX;
  const jsDir = escapeJsQuote(node.dirPath);
  const allReviewed =
    node.totalFileCount > 0 && node.reviewedFileCount === node.totalFileCount;

  return (
    <div className="cv-tree-group" role="group">
      <div
        role="treeitem"
        tabIndex={0}
        aria-expanded={isCollapsed ? 'false' : 'true'}
        className={`cv-tree-row cv-tree-dir-row ${
          isCollapsed ? 'is-collapsed' : ''
        } ${allReviewed ? 'is-all-reviewed' : ''}`}
        data-cv-dir={node.dirPath}
        htmlStyle={`padding-left:${indentPx}px;`}
        htmlOnClick={`toggleCodeViewerDir(${clNumber}, '${jsDir}')`}
        htmlOnKeyDown={`onCodeViewerDirKeyDown(event, ${clNumber}, '${jsDir}')`}
        title={`${node.dirPath} (${node.totalFileCount} files)`}
      >
        <span className="pg-dir-chevron cv-dir-chevron" aria-hidden="true">
          {isCollapsed ? '▸' : '▾'}
        </span>
        <span className="pg-dir-icon cv-dir-icon" aria-hidden="true">
          📁
        </span>
        <span className="pg-dir-name cv-dir-name">{node.displayName}/</span>
        <span className="cv-tree-file-meta">
          {node.unresolvedCount > 0 ? (
            <span
              className="cv-file-pill is-unresolved"
              title={`${node.unresolvedCount} unresolved comment(s) in folder`}
            >
              ⚠{node.unresolvedCount}
            </span>
          ) : null}
          <span className="pg-dir-count-pill cv-dir-count">
            {node.totalFileCount}
          </span>
        </span>
      </div>
      {!isCollapsed ? (
        <div className="cv-tree-children">
          {node.children.map((child) => (
            <DirBranch
              key={child.dirPath}
              clNumber={clNumber}
              patchSet={patchSet}
              node={child}
              depth={depth + 1}
              selectedFile={selectedFile}
              collapsedDirs={collapsedDirs}
              filterQuery={filterQuery}
            />
          ))}
          {node.files.map((fileEntry) => (
            <FileLeafRow
              key={fileEntry.file}
              clNumber={clNumber}
              patchSet={patchSet}
              entry={fileEntry}
              depth={depth + 1}
              selectedFile={selectedFile}
              filterQuery={filterQuery}
            />
          ))}
        </div>
      ) : null}
    </div>
  );
}

interface CodeViewerTopbarProps {
  displayMode: CodeViewerDisplayMode;
}

function CodeViewerTopbar({
  displayMode,
}: CodeViewerTopbarProps): React.ReactElement {
  return (
    <div className="queue-viewer-toolbar pg-dashboard-bar cv-sidebar-topbar">
      <button
        id="btnExitCodeViewerSidebar"
        type="button"
        className="pg-btn-subtle cv-back-dashboard-btn"
        htmlOnClick="togglePgCodeViewerMode(null, false)"
        title="Return to Dashboard commit queue sidebar (Shortcut: \\ or Esc)"
      >
        <span aria-hidden="true">←</span> Dashboard
      </button>

      <div
        className="group-toggle cv-mode-toggle"
        role="group"
        aria-label="Code viewer layout mode"
        title="Choose whether clicking a file isolates its diff or scrolls within all files"
      >
        <button
          type="button"
          className={displayMode === 'single' ? 'active' : ''}
          htmlOnClick="setCodeViewerDisplayMode('single')"
          title="Show selected file diff cleanly in main viewer (preserves scroll per file)"
        >
          Focused
        </button>
        <button
          type="button"
          className={displayMode === 'all' ? 'active' : ''}
          htmlOnClick="setCodeViewerDisplayMode('all')"
          title="Show all file diffs stacked and scroll to clicked file"
        >
          All Files
        </button>
      </div>

      <div className="cv-topbar-spacer" />

      <button
        type="button"
        className="btn-sidebar-toggle"
        htmlOnClick="toggleSidebarCollapsed(true)"
        title="Collapse file tree panel ([)"
        aria-label="Collapse file tree panel"
      >
        <svg
          width="14"
          height="14"
          viewBox="0 0 16 16"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <rect x="2" y="2.5" width="12" height="11" rx="2" />
          <line x1="6.5" y1="2.5" x2="6.5" y2="13.5" />
          <polyline points="5 6.5 3.5 8 5 9.5" />
        </svg>
      </button>
    </div>
  );
}

interface CodeViewerCommitHeaderProps {
  clNumber: number;
  patchSet: number;
  subject: string;
  totalCount: number;
  canNavigateCommits: boolean;
}

function CodeViewerCommitHeader({
  clNumber,
  patchSet,
  subject,
  totalCount,
  canNavigateCommits,
}: CodeViewerCommitHeaderProps): React.ReactElement {
  return (
    <>
      <div className="pg-section-header cv-section-header">
        <span className="pg-section-title-wrap">
          <span className="pg-section-name">Changed Files</span>
          <span className="pg-section-count">({totalCount})</span>
        </span>
        <div className="cv-cl-badge-group" title={subject}>
          {canNavigateCommits ? (
            <button
              type="button"
              className="cv-cl-step-btn"
              htmlOnClick="navigateVisibleCl('prev')"
              title="Previous commit in queue (k)"
              aria-label="Previous commit"
            >
              ‹
            </button>
          ) : null}
          <span className="cv-cl-badge">
            ag/{clNumber} <span className="cv-cl-ps">· PS{patchSet}</span>
          </span>
          {canNavigateCommits ? (
            <button
              type="button"
              className="cv-cl-step-btn"
              htmlOnClick="navigateVisibleCl('next')"
              title="Next commit in queue (j)"
              aria-label="Next commit"
            >
              ›
            </button>
          ) : null}
        </div>
      </div>

      <div className="cv-commit-meta-bar" title={subject}>
        <span className="cv-commit-subj-text">
          {subject || `Change ag/${clNumber}`}
        </span>
      </div>
    </>
  );
}

interface CodeViewerControlsBarProps {
  clNumber: number;
  filterQuery: string;
  trimmedQuery: string;
  revCount: number;
  totalCount: number;
}

function CodeViewerControlsBar({
  clNumber,
  filterQuery,
  trimmedQuery,
  revCount,
  totalCount,
}: CodeViewerControlsBarProps): React.ReactElement {
  const isAllRev = totalCount > 0 && revCount === totalCount;
  return (
    <div className="pg-file-controls-bar cv-controls-bar">
      <div className="cv-filter-wrap">
        <span className="cv-filter-icon" aria-hidden="true">
          🔍
        </span>
        <input
          id="cvTreeFilterInput"
          type="text"
          className="pg-file-filter-input cv-filter-input"
          placeholder="Filter files…"
          defaultValue={filterQuery}
          htmlOnInput="onCodeViewerFilterInput(this.value)"
          htmlOnKeyDown="onCodeViewerFilterKeyDown(event)"
          aria-label="Filter changed files in commit"
        />
        {trimmedQuery ? (
          <button
            type="button"
            className="cv-filter-clear-btn"
            htmlOnClick="clearCodeViewerFilter()"
            title="Clear file filter (Esc)"
            aria-label="Clear file filter"
          >
            ✕
          </button>
        ) : null}
      </div>
      <div className="cv-tree-actions">
        <span
          id={`cvReviewedSummary-${clNumber}`}
          className={`pg-reviewed-summary-badge cv-reviewed-pill ${
            isAllRev ? 'all-reviewed is-complete' : ''
          }`}
          title={`${revCount} of ${totalCount} changed files marked reviewed`}
        >
          {revCount}/{totalCount} ✓
        </span>
        <button
          type="button"
          className="pg-btn-subtle cv-icon-btn"
          htmlOnClick={`expandAllCodeViewerDirs(${clNumber})`}
          title="Expand all directories"
          aria-label="Expand all directories"
        >
          ⊞
        </button>
        <button
          type="button"
          className="pg-btn-subtle cv-icon-btn"
          htmlOnClick={`collapseAllCodeViewerDirs(${clNumber})`}
          title="Collapse all directories"
          aria-label="Collapse all directories"
        >
          ⊟
        </button>
      </div>
    </div>
  );
}

interface CodeViewerOverviewRowProps {
  clNumber: number;
  patchSet: number;
  isSelected: boolean;
  unresolvedCount: number;
}

function CodeViewerOverviewRow({
  clNumber,
  patchSet,
  isSelected,
  unresolvedCount,
}: CodeViewerOverviewRowProps): React.ReactElement {
  return (
    <div
      role="treeitem"
      tabIndex={0}
      aria-selected={isSelected ? 'true' : 'false'}
      className={`cv-tree-row cv-tree-overview-row ${
        isSelected ? 'is-selected' : ''
      }`}
      data-cv-cl={clNumber}
      data-cv-file={COMMIT_MSG_MAGIC_PATH}
      htmlOnClick={`selectCodeViewerFile(${clNumber}, ${patchSet}, '${COMMIT_MSG_MAGIC_PATH}', event)`}
      htmlOnKeyDown={`onCodeViewerFileKeyDown(event, ${clNumber}, ${patchSet}, '${COMMIT_MSG_MAGIC_PATH}')`}
      title="View Commit Message, Reviewers, Submit Requirements & Checks"
    >
      <span className="pg-row-cursor cv-row-cursor" aria-hidden="true">
        ▸
      </span>
      <span
        className="pg-file-status-badge status-M cv-overview-badge"
        aria-hidden="true"
      >
        📄
      </span>
      <span className="cv-overview-label">Commit Overview &amp; Message</span>
      <span className="cv-tree-file-meta">
        {unresolvedCount > 0 ? (
          <span
            className="cv-file-pill is-unresolved"
            title={`${unresolvedCount} unresolved comment(s) on commit message`}
          >
            ⚠{unresolvedCount}
          </span>
        ) : null}
      </span>
    </div>
  );
}

interface CodeViewerTreeViewportProps {
  clNumber: number;
  patchSet: number;
  rootTree: CodeViewerTreeNode;
  filteredCount: number;
  selectedFile: string;
  collapsedSet: Set<string>;
  filterQuery: string;
  trimmedQuery: string;
  commitMsgUnresolvedCount: number;
  isLoadingDiff: boolean;
}

function renderTreeViewportBody(
  props: CodeViewerTreeViewportProps
): React.ReactNode {
  if (props.isLoadingDiff) {
    return (
      <div className="pg-empty-section-row cv-tree-empty">
        Loading changed files for ag/{props.clNumber}…
      </div>
    );
  }
  if (props.filteredCount === 0) {
    const msg = props.trimmedQuery
      ? `No files matching "${props.trimmedQuery}"`
      : 'No changed files in this patchset.';
    return <div className="pg-empty-section-row cv-tree-empty">{msg}</div>;
  }
  return (
    <>
      {props.rootTree.children.map((child) => (
        <DirBranch
          key={child.dirPath}
          clNumber={props.clNumber}
          patchSet={props.patchSet}
          node={child}
          depth={0}
          selectedFile={props.selectedFile}
          collapsedDirs={props.collapsedSet}
          filterQuery={props.trimmedQuery}
        />
      ))}
      {props.rootTree.files.map((fileEntry) => (
        <FileLeafRow
          key={fileEntry.file}
          clNumber={props.clNumber}
          patchSet={props.patchSet}
          entry={fileEntry}
          depth={0}
          selectedFile={props.selectedFile}
          filterQuery={props.filterQuery}
        />
      ))}
    </>
  );
}

function CodeViewerTreeViewport(
  props: CodeViewerTreeViewportProps
): React.ReactElement {
  return (
    <div
      id="cvTreeScroll"
      className="cv-tree-scroll"
      role="tree"
      aria-label="Changed files directory tree"
    >
      <CodeViewerOverviewRow
        clNumber={props.clNumber}
        patchSet={props.patchSet}
        isSelected={props.selectedFile === COMMIT_MSG_MAGIC_PATH}
        unresolvedCount={props.commitMsgUnresolvedCount}
      />
      {renderTreeViewportBody(props)}
    </div>
  );
}

export interface CodeViewerSidebarProps {
  clNumber: number;
  patchSet: number;
  subject?: string;
  files: CodeViewerFileEntry[];
  selectedFile?: string;
  collapsedDirs?: string[];
  filterQuery?: string;
  displayMode?: CodeViewerDisplayMode;
  totalFileCount?: number;
  reviewedFileCount?: number;
  commitMsgUnresolvedCount?: number;
  isLoadingDiff?: boolean;
  canNavigateCommits?: boolean;
}

export function CodeViewerSidebar({
  clNumber,
  patchSet,
  subject = '',
  files,
  selectedFile = '',
  collapsedDirs = [],
  filterQuery = '',
  displayMode = 'single',
  totalFileCount,
  reviewedFileCount,
  commitMsgUnresolvedCount = 0,
  isLoadingDiff = false,
  canNavigateCommits = false,
}: CodeViewerSidebarProps): React.ReactElement {
  const collapsedSet = new Set(collapsedDirs);
  const trimmedQuery = (filterQuery || '').trim();
  const lowerQuery = trimmedQuery.toLowerCase();
  const filteredFiles = lowerQuery
    ? files.filter((f) => String(f.file || '').toLowerCase().includes(lowerQuery))
    : files;
  const rootTree = buildCodeViewerDirectoryTree(filteredFiles);
  const totalCount =
    totalFileCount !== undefined ? totalFileCount : files.length;
  const revCount =
    reviewedFileCount !== undefined
      ? reviewedFileCount
      : files.filter((f) => Boolean(f.isReviewed)).length;

  return (
    <div className="cv-sidebar-inner" data-cv-cl={clNumber}>
      <CodeViewerTopbar displayMode={displayMode} />
      <div className="cv-sidebar-canvas">
        <div className="pg-section-box cv-section-box">
          <CodeViewerCommitHeader
            clNumber={clNumber}
            patchSet={patchSet}
            subject={subject}
            totalCount={totalCount}
            canNavigateCommits={canNavigateCommits}
          />
          <CodeViewerControlsBar
            clNumber={clNumber}
            filterQuery={filterQuery}
            trimmedQuery={trimmedQuery}
            revCount={revCount}
            totalCount={totalCount}
          />
          <CodeViewerTreeViewport
            clNumber={clNumber}
            patchSet={patchSet}
            rootTree={rootTree}
            filteredCount={filteredFiles.length}
            selectedFile={selectedFile}
            collapsedSet={collapsedSet}
            filterQuery={filterQuery}
            trimmedQuery={trimmedQuery}
            commitMsgUnresolvedCount={commitMsgUnresolvedCount}
            isLoadingDiff={isLoadingDiff}
          />
        </div>
      </div>
    </div>
  );
}

export function renderPgCodeViewerToggleButtonHtml(
  props: CodeViewerToggleButtonProps
): string {
  return renderReactToHtml(<CodeViewerToggleButton {...props} />);
}

export function renderPgCodeViewerSidebarHtml(
  props: CodeViewerSidebarProps
): string {
  return renderReactToHtml(<CodeViewerSidebar {...props} />);
}
