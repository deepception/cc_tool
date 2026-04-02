#!/usr/bin/env bash
# Warn when a prompt is long and potentially ambiguous (UserPromptSubmit hook)

input=$(cat)
word_count=$(echo "$input" | wc -w | tr -d ' ')

if [ "$word_count" -gt 50 ]; then
    echo "Note: This prompt is ${word_count} words. Before proceeding, verify that the desired outcome and scope are clear. If ambiguous, ask one clarifying question before starting work."
fi
