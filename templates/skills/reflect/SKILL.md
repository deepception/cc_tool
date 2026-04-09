---
name: reflect
description: Reviews the current conversation to extract learning opportunities, skill improvements, and memory candidates. Presents findings for user approval.
user-invocable: true
---

# Session Reflection

Your task is to review the current conversation and extract actionable improvements.

## Steps

1. **Scan the conversation** for:
   - Tasks completed (what was done)
   - Errors encountered (what went wrong)
   - User corrections (where Claude was wrong or suboptimal)
   - Repeated patterns (actions done more than once)
   - Tool usage patterns (which tools were used most, any inefficiencies)

2. **Categorize findings** into:

   **Learning opportunities**:
   - Mistakes that could be prevented by a rule in CLAUDE.md or a rule file
   - Domain knowledge that should be saved to memory
   - Patterns specific to this project that Claude should remember

   **Skill creation candidates**:
   - Multi-step workflows that were performed manually and could become a skill
   - Recurring tasks that would benefit from automation

   **Skill improvement candidates**:
   - Existing skills that failed or needed manual correction
   - Skills that could be more concise or effective

   **Memory candidates**:
   - Project-specific facts discovered during the session
   - Debugging insights or solutions to tricky problems
   - Architecture patterns or file relationships

3. **Present findings** as a prioritized list with:
   - Category (Learn / New Skill / Improve Skill / Memory)
   - Description of the finding
   - Suggested action (specific rule text, skill outline, or memory entry)

4. **Ask the user** what to implement.

5. **Execute approved actions**:
   - Write new rules to `.claude/rules/`
   - Update existing skills
   - Create new skills in `.claude/skills/`
   - Save knowledge to auto memory
