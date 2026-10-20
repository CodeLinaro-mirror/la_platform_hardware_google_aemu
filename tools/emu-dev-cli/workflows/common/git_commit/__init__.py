"""Shared Git & Gerrit commit verification utilities for emu-dev-cli workflows."""

from .commit_verifier import (
    CHANGE_ID_LINE_PATTERN,
    CHANGE_ID_PATTERN,
    extract_all_change_ids,
    extract_change_id,
    find_commit_by_change_id,
    get_commit_message,
    get_commit_parents,
    get_head_commit_hash,
    is_ancestor_commit,
    messages_match,
    resolve_commit_hash,
    validate_commit_message,
    validate_commit_message_file,
)

__all__ = [
    "CHANGE_ID_LINE_PATTERN",
    "CHANGE_ID_PATTERN",
    "extract_all_change_ids",
    "extract_change_id",
    "find_commit_by_change_id",
    "get_commit_message",
    "get_commit_parents",
    "get_head_commit_hash",
    "is_ancestor_commit",
    "messages_match",
    "resolve_commit_hash",
    "validate_commit_message",
    "validate_commit_message_file",
]
