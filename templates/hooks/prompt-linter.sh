#!/usr/bin/env bash
# Note when a prompt is unusually long (UserPromptSubmit hook). Informational
# only — the model decides whether any clarification is actually warranted.

input=$(cat)
word_count=$(echo "$input" | wc -w | tr -d ' ')

if [ "$word_count" -gt 150 ]; then
    echo "Note: this is a long prompt (${word_count} words); it may bundle several distinct asks."
fi
