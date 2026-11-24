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

const VOID_HTML_TAGS = new Set([
  'area',
  'base',
  'br',
  'col',
  'embed',
  'hr',
  'img',
  'input',
  'link',
  'meta',
  'param',
  'source',
  'track',
  'wbr',
]);

const SKIPPED_REACT_PROPS = new Set([
  'children',
  'key',
  'ref',
  'dangerouslySetInnerHTML',
]);

const mountedContainers = new WeakSet<Element>();
const HTML_ESCAPE_RE = /[&<>"]/;
const JS_QUOTE_ESCAPE_RE = /[\\'\"\n\r]/;
const UPPERCASE_TEST_RE = /[A-Z]/;
const UPPERCASE_REPLACE_RE = /[A-Z]/g;

export function escapeHtml(value: unknown): string {
  if (value === null || value === undefined) {
    return '';
  }
  if (typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }
  const str = typeof value === 'string' ? value : String(value);
  if (!str || !HTML_ESCAPE_RE.test(str)) {
    return str;
  }
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

export function escapeJsQuote(value: unknown): string {
  if (value === null || value === undefined) {
    return '';
  }
  if (typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }
  const str = typeof value === 'string' ? value : String(value);
  if (!str || !JS_QUOTE_ESCAPE_RE.test(str)) {
    return str;
  }
  return str
    .replace(/\\/g, '\\\\')
    .replace(/'/g, "\\'")
    .replace(/"/g, '&quot;')
    .replace(/\n/g, '\\n')
    .replace(/\r/g, '\\r');
}

const EVENT_ATTR_MAP: Record<string, string> = {
  htmlOnClick: 'onclick',
  htmlOnInput: 'oninput',
  htmlOnKeyDown: 'onkeydown',
  onClick: 'onclick',
  onInput: 'oninput',
  onChange: 'onchange',
  onKeyDown: 'onkeydown',
  onBlur: 'onblur',
  onFocus: 'onfocus',
  onMouseDown: 'onmousedown',
};

function serializeStyleObject(styleObj: Record<string, unknown>): string {
  let out = '';
  for (const k in styleObj) {
    const v = styleObj[k];
    if (v === undefined || v === null || v === '') continue;
    const cssKey = UPPERCASE_TEST_RE.test(k)
      ? k.replace(UPPERCASE_REPLACE_RE, (m) => `-${m.toLowerCase()}`)
      : k;
    out += `${cssKey}:${String(v)};`;
  }
  return out;
}

function serializeSingleAttribute(
  tagName: string,
  key: string,
  rawVal: unknown
): string {
  if (SKIPPED_REACT_PROPS.has(key) || typeof rawVal === 'function') {
    return '';
  }
  if (tagName === 'textarea' && key === 'defaultValue') {
    return '';
  }
  if (rawVal === undefined || rawVal === null) {
    return '';
  }
  if (key in EVENT_ATTR_MAP) {
    return ` ${EVENT_ATTR_MAP[key]}="${String(rawVal)}"`;
  }
  if (key === 'htmlStyle' || key === 'style') {
    const cssText =
      typeof rawVal === 'object'
        ? serializeStyleObject(rawVal as Record<string, unknown>)
        : String(rawVal);
    return cssText ? ` style="${escapeHtml(cssText)}"` : '';
  }
  if (key === 'disabled' || key === 'checked') {
    return rawVal ? ` ${key}` : '';
  }
  if (rawVal === false) {
    return '';
  }
  const attrName =
    key === 'className'
      ? 'class'
      : key === 'tabIndex'
        ? 'tabindex'
        : key === 'colSpan'
          ? 'colspan'
          : key === 'defaultValue'
            ? 'value'
            : key;
  return ` ${attrName}="${escapeHtml(rawVal)}"`;
}

function serializeElementAttributes(
  tagName: string,
  props: Record<string, unknown>
): string {
  let out = '';
  for (const key in props) {
    const serialized = serializeSingleAttribute(tagName, key, props[key]);
    if (serialized) {
      out += serialized;
    }
  }
  return out;
}

function extractDangerousInnerHtml(props: Record<string, unknown>): string | null {
  const raw = props.dangerouslySetInnerHTML as { __html?: unknown } | undefined;
  if (raw && typeof raw.__html === 'string') {
    return raw.__html;
  }
  return null;
}

function renderIntrinsicElement(
  tagName: string,
  props: Record<string, unknown>
): string {
  const attrs = serializeElementAttributes(tagName, props);
  if (VOID_HTML_TAGS.has(tagName)) {
    return `<${tagName}${attrs}/>`;
  }
  const rawHtml = extractDangerousInnerHtml(props);
  let inner: string;
  if (rawHtml !== null) {
    inner = rawHtml;
  } else if (
    tagName === 'textarea' &&
    props.children === undefined &&
    props.defaultValue !== undefined
  ) {
    inner = escapeHtml(props.defaultValue);
  } else {
    inner = renderReactToHtml(props.children as React.ReactNode);
  }
  return `<${tagName}${attrs}>${inner}</${tagName}>`;
}

function renderReactArray(nodes: React.ReactNode[]): string {
  let out = '';
  for (let i = 0; i < nodes.length; i++) {
    out += renderReactToHtml(nodes[i]);
  }
  return out;
}

export function renderReactToHtml(node: React.ReactNode): string {
  if (node === null || node === undefined || typeof node === 'boolean') {
    return '';
  }
  if (typeof node === 'number') {
    return String(node);
  }
  if (typeof node === 'string') {
    return escapeHtml(node);
  }
  if (Array.isArray(node)) {
    return renderReactArray(node);
  }
  if (typeof node === 'object' && 'props' in node) {
    const element = node as React.ReactElement<Record<string, unknown>>;
    if (typeof element.type === 'function') {
      const fnComponent = element.type as (
        props: Record<string, unknown>
      ) => React.ReactNode;
      return renderReactToHtml(fnComponent(element.props));
    }
    if (typeof element.type === 'string') {
      return renderIntrinsicElement(element.type, element.props || {});
    }
    return renderReactToHtml((element.props?.children as React.ReactNode) ?? null);
  }
  return '';
}

export function mountReactIsland(
  container: Element | null | undefined,
  element: React.ReactElement
): void {
  if (!container) {
    return;
  }
  mountedContainers.add(container);
  container.innerHTML = renderReactToHtml(element);
}

export function unmountReactIsland(container: Element | null | undefined): void {
  if (!container) {
    return;
  }
  if (mountedContainers.has(container)) {
    container.innerHTML = '';
    mountedContainers.delete(container);
  }
}
