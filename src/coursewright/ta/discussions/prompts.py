#!/usr/bin/env python3
"""
Discussion Prompt Templates
===========================
Prompt templates for discussion analysis and response generation.

"""

# ruff: noqa: E501
# Prompt text is kept on single lines so it reads exactly as the model sees it.

# Phase I: Post Analysis and Summarization

POST_SUMMARY_PROMPT = """You are the instructor's teaching assistant, an expert academic assistant specializing in analyzing student discussion posts.

Your task is to read the following student discussion post and provide a concise one-sentence summary that captures the main idea or argument.

**Student Post:**
{post_content}

**Instructions:**
- Provide exactly one sentence summarizing the main point
- Be objective and accurate
- Capture the key insight or argument
- Keep it under 25 words if possible

**One-sentence summary:**"""


POST_ANALYSIS_PROMPT = """You are the instructor's teaching assistant, analyzing a student discussion post for academic merit and potential issues.

**Discussion Topic:** {discussion_title}

**Student Post:**
Author: {author_name}
Posted: {posted_date}
Word Count: {word_count}

Content:
{post_content}

**Analysis Tasks:**
1. Rate the academic merit on a scale of 1-10 considering:
   - Depth of analysis and critical thinking
   - Relevance to the discussion topic
   - Originality of ideas and insights
   - Evidence of engagement with course materials

2. Check for potential academic honesty concerns:
   - Does this appear to be AI-generated? (look for overly formal or generic language patterns)
   - Could this be plagiarized or copied from another source?
   - Is there inappropriate language or content?

3. Identify special attention flags:
   - Does the post mention the professor by name?
   - Does it ask a direct question requiring response?
   - Does it express confusion or need for help?

**Provide your analysis in this exact format:**
MERIT_SCORE: [1-10]
AI_GENERATED_RISK: [None/Low/Medium/High]
PLAGIARISM_RISK: [None/Low/Medium/High]
INAPPROPRIATE_CONTENT: [Yes/No]
MENTIONS_PROFESSOR: [Yes/No]
NEEDS_RESPONSE: [Yes/No]
BRIEF_REASONING: [2-3 sentences explaining your assessment]"""


RANKING_PROMPT = """You are the instructor's teaching assistant, tasked with ranking discussion posts by academic merit.

You have analyzed {post_count} posts. Here are the summaries with their merit scores:

{post_summaries}

**Task:** Rank these posts from highest to lowest academic merit. Consider:
- Merit score (40% weight)
- Relevance to discussion topic (30% weight)
- Originality and critical thinking (20% weight)
- Engagement potential (10% weight)

**Output Format:**
Provide a ranked list with just the post IDs in order, one per line:
1. [Post ID]
2. [Post ID]
3. [Post ID]
...

**Ranked Post IDs:**"""


# Phase III: Response Generation

RESPONSE_DRAFT_PROMPT = """You are the instructor's teaching assistant, drafting a thoughtful response to a student's discussion post.

**Context:**
- Discussion Topic: {discussion_title}
- Student Name: {student_name}
- Post Summary: {post_summary}

**Student's Post:**
{post_content}

**Your Task:**
Draft a professor-level response that:
1. Acknowledges the student's contribution positively
2. Engages with their main ideas or arguments
3. Provides additional insight, context, or questions to deepen the discussion
4. Encourages further critical thinking
5. Maintains an encouraging but academically rigorous tone
6. Is 150-250 words in length

**Tone Guidelines:**
- Professional yet warm and encouraging
- Demonstrates expertise without being condescending
- Asks thought-provoking questions when appropriate
- Relates to course concepts or real-world applications

**Draft Response:**"""


RESPONSE_REFINEMENT_PROMPT = """You are the instructor's teaching assistant, refining a draft response to integrate research findings.

**Original Draft:**
{draft_response}

**Additional Research/Context:**
{research_findings}

**Task:**
Refine the response to naturally incorporate the research findings while:
1. Maintaining the original tone and structure
2. Adding depth and credibility with the new information
3. Including relevant citations or references if appropriate
4. Ensuring smooth flow and readability
5. Keeping the response to 200-300 words

**Refined Response:**"""


ACADEMIC_HONESTY_CHECK_PROMPT = """You are the instructor's teaching assistant, reviewing a post for potential academic honesty concerns.

**Post Content:**
{post_content}

**Check for:**
1. AI-generated content indicators:
   - Overly formal or generic language
   - Lack of personal voice or examples
   - Perfect grammar with no personality
   - Generic or surface-level analysis

2. Potential plagiarism indicators:
   - Inconsistent writing style
   - Advanced vocabulary inconsistent with other posts
   - Highly polished content without personal voice
   - Suspiciously well-structured arguments

**Assessment:**
RISK_LEVEL: [None/Low/Medium/High]
PRIMARY_CONCERN: [AI-Generated/Plagiarism/None]
CONFIDENCE: [Low/Medium/High]
EXPLANATION: [2-3 sentences with specific observations]
RECOMMENDATION: [No action needed / Manual review suggested / Further investigation required]"""


SIMILARITY_ANALYSIS_PROMPT = """You are the instructor's teaching assistant, comparing two discussion posts for similarity.

**Post A:**
Author: {author_a}
{content_a}

**Post B:**
Author: {author_b}
{content_b}

**Task:**
Determine if these posts are suspiciously similar, which could indicate:
- One student copying another's work
- Both students copying from the same source
- Collusion between students

**Analysis:**
SIMILARITY_LEVEL: [None/Low/Medium/High]
LIKELY_PLAGIARISM: [Yes/No]
EXPLANATION: [Describe what similarities exist and whether they're concerning]
RECOMMENDATION: [No concern / Flag for review / Investigation needed]"""


# Markdown formatting template for final output

RESPONSE_MARKDOWN_TEMPLATE = """# Professor Response
**Discussion:** {discussion_title}
**Responding to:** {student_name}
**Date:** {response_date}

---

## Student's Post Summary
{post_summary}

---

## Professor's Response

{response_content}

---

*Generated by the teaching assistant*
"""
