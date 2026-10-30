# Scratch cleanup and bash-guard deletions

bash-guard asks for approval on any deletion unless the whole Bash call is a single plain `rm` (or `rm -rf`) of a literal absolute path under the session scratchpad or /tmp/<dir>.

- Never chain a deletion with other commands (`rm -rf .../t1 && mkdir -p ...`): run the lone `rm -rf <absolute scratch path>` as its own Bash call, then the rest in a separate call.
- Better still, skip the deletion: write into a fresh directory (e.g. .../t2 instead of reusing .../t1).
- Do not use variables, globs or relative paths in the `rm` target; they also go to approval.
