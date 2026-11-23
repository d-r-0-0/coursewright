# Batch Feedback Generation User Prompt Template (AI Discussion)

Generate personalized feedback for multiple students in a single batch.

**Discussion Topic:** {discussion_context}

**AI Discussion rubric (how each post is graded, 100 points total):**
- Post 1 — Research ({research_points} pts): the initial top-level post sharing the deep-research prompts used and the full, unedited AI output.
- Post 2 — Restatement ({restatement_points} pts): a reply to the student's OWN Post 1 that restates the AI output in their own words. Should exceed 300 words, read as genuinely human (passes an AI detector), show real synthesis, and call out anything from the AI output that was unclear.
- Post 3 — Reply ({reply_points} pts): a substantive reply to a CLASSMATE's post that exceeds 150 words and pushes, catches, asks a real question, or connects ideas.

Each student row below lists per-role word counts as `R1/R2/R3 words` (Research / Restatement / Reply). A 0 means that post is missing.

{gradebook_data}

**Instructions:**

For each student, write 1-2 sentences of feedback (30-50 words max). Ground the comment in the rubric above: if a role lost points (missing post, under the word minimum, weak synthesis), name that plainly; if the restatement showed real understanding or the reply genuinely engaged a classmate, say what landed.

Sound like a real instructor, not a rubric. Reference something specific about their participation. Vary how each comment opens — no two should start the same way, and none should start with "I really like how you" or generic praise like "Great job" or "Excellent work."

If a student didn't participate, be brief and encourage them to reach out.

**Output Format:**
```
StudentName	Feedback text here...
AnotherStudent	Their feedback text here...
```

Important:
- One line per student
- Tab-separated: Name, then Feedback
- No extra formatting or markdown
- Match the exact order and names from the gradebook above
- Include ALL students (even those with 0 participation)
