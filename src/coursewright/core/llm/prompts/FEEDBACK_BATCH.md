# Batch Feedback Generation User Prompt Template

Generate personalized feedback for multiple students in a single batch.

**Discussion Topic:** {discussion_context}

**Rubric Information:**
- Substantial Post: {substantial_points} points (minimum {min_words} words)
- Replies: {reply_points} points each (max {max_replies} counted)
- Total Possible: {total_points} points

{gradebook_data}

**Instructions:**

For each student, write 1-2 sentences of feedback (30-50 words max). Sound like a real instructor, not a rubric. Reference something specific about their participation. Vary how each comment opens — no two should start the same way, and none should start with "I really like how you" or generic praise like "Great job" or "Excellent work."

If a student fell short, say what was missing plainly. If they didn't participate, be brief and encourage them to reach out.

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
