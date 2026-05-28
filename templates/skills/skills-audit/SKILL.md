---
name: skills-audit
description: Lists all installed skills (project & global) with line counts, then asks which to review for improvement opportunities.
user-invocable: true
---

# Skills Audit

Your task is to audit all Claude Code skills installed at both project and global level.

## Steps

1. **Scan project skills**: List all files matching `.claude/skills/*/SKILL.md` in the current project directory.
2. **Scan global skills**: List all files matching `~/.claude/skills/*/SKILL.md`.
3. **For each skill found**:
   - Display the skill name (directory name)
   - Display the scope (project or global)
   - Count and display the number of lines in the SKILL.md file
   - Show the first line of the `description` field from the frontmatter
4. **Present a summary table** with columns: Name, Scope, Lines, Description.
5. **Ask the user** which skill(s) they want to review for improvement opportunities.
6. **Coverage pass — for selected skills**, surface every potential improvement you can find; do not pre-filter by category or severity. Useful lenses (and anything else you observe):
   - Conciseness: Can instructions be shortened without losing meaning?
   - Clarity: Are instructions unambiguous and actionable?
   - Overlapping scopes: Does this skill duplicate another skill or CLAUDE.md content?
   - Token efficiency: Could any content move to a hook or memory instead?
7. **Prioritize**: as a separate step, rank the coverage-pass findings by impact (high / medium / low).
8. **Present findings** and ask the user what changes to implement.
