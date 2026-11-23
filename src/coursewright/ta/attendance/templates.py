# Message prose is one paragraph per line, as in the original templates,
# so Canvas wraps it for the reader; line length does not apply here.
# ruff: noqa: E501
"""Student-outreach message templates and their configuration.

The structure and per-student placeholders (``{course_name}``,
``{student_first_name}``, ``{days_absent}``, ...) are kept from the
original templates.  Everything that identifies an instructor or an
institution is a placeholder too, filled from :class:`OutreachContact`,
which the caller builds from configuration.  Nothing here names a real
person, address, college or policy.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

ATTENDANCE_WARNING_TEMPLATE = """\
Subject: Important: Attendance Warning for {course_name}

Dear {student_first_name},

I am writing to you because I have noticed you have been absent from our class for {days_absent}. I want to check in and make sure everything is okay, but I also need to make you aware of the serious academic implications of these absences.

{activity_line}.

THE 14-DAY RULE

Per the {attendance_policy_name}, students who stop attending class for 14 consecutive calendar days are subject to being administratively withdrawn.

As an attendance-taking institution, we are required to report when a student is no longer active. This can have significant impacts on your:

  - Financial Aid: You may be required to pay back funds or face SAP (Satisfactory Academic Progress) suspension.
  - Final Grade: Consistent absences make it difficult to meet the learning objectives outlined in our syllabus.
  - Enrollment Status: You may be dropped (DR) from the course entirely.

IMMEDIATE NEXT STEPS

I want to see you succeed in this course, but I cannot help you if we aren't in communication.

You must reach out to me via email or visit my office hours by {deadline_date} to discuss your status in this class. If I do not hear from you by this deadline, I will proceed with an administrative withdrawal to comply with college policy.

You can find a list of academic and personal support services at the {institution_name} "Get Help" page: {support_services_url}

Please feel free to reach out to me by message in Canvas, email at {instructor_email}, or sign up for an office hours appointment using this link: {office_hours_url}

I look forward to hearing from you and finding a way to get you back on track.

Best regards,
{instructor_signature}
{institution_name}
"""

MISSING_SUBMISSION_TEMPLATE = """\
I noticed that you did not submit an assignment here. If you are having trouble with the material, or if other issues are making it difficult to complete your work, please know that there are resources available to help you.
You can find a list of academic and personal support services at the {institution_name} "Get Help" page: <{support_services_url}>
Please feel free to reach out to me by message in canvas, email at [{instructor_email}](mailto:{instructor_email}), or sign up for an office hours appointment using this link: <{office_hours_url}>
I am here to support you and want to see you succeed in this course. Please don't hesitate to get in touch.
"""

MISSING_PACKET_TRACER_TEMPLATE = """\
Thanks for your submission. I wanted to let you know that you lost a letter grade on {assignment_name}  because the Packet Tracer (.pka/.pkz) file was not uploaded.


Uploading the packet tracer file is important because it allows us to review all the steps you took during the lab, not just the final results. It's a key part of how I assess your work and give feedback.


Please be sure to include the Packet Tracer file with any future submissions. You can find full details on what to include in the lab rubric, located in the Module section of our Canvas class. Here's the example link for reference:

{lab_example_url}

Let me know if you have any questions or need help locating the file.
"""

MISSING_LAB_DOCUMENT_TEMPLATE = """\
Please review the Lab Submission Rubric available in the Module section of our Canvas class, and make sure your resubmission follows those guidelines.

You can also view an example submission at this link: {lab_example_url}

Here are a few key points from the rubric to keep in mind:

Submit one single Microsoft Word or Adobe PDF file that opens properly in Canvas.

The first page must include:

Your name and class number

A screenshot of the final score from Packet Tracer.

Answers to both reflection questions:

One sentence about what you did in the activity

One sentence about how you'd apply it in a real-world situation


Include:

Screenshots for each major lab step

Any questions answered clearly and numbered

A screenshot showing your desktop (clock and background visible)

Any supporting files (.pkt files, code logs, etc.)

If something didn't work: explain what happened and what you did to troubleshoot (including at least three Google searches and their results)

Once your submission meets these standards, feel free to resubmit for grading.
"""

PENDING_REVIEW_TEMPLATE = "Pending Review {assignment_name} 100%\n"

OUTREACH_TEMPLATES: dict[str, str] = {
    "attendance_warning": ATTENDANCE_WARNING_TEMPLATE,
    "missing_submission": MISSING_SUBMISSION_TEMPLATE,
    "missing_packet_tracer": MISSING_PACKET_TRACER_TEMPLATE,
    "missing_lab_document": MISSING_LAB_DOCUMENT_TEMPLATE,
    "pending_review": PENDING_REVIEW_TEMPLATE,
}


@dataclass(frozen=True)
class OutreachContact:
    """Instructor and institution details, supplied from configuration.

    Every field is a plain string so it can come straight from the
    config file.  Placeholders in the templates map one-to-one onto
    these field names.
    """

    instructor_signature: str
    instructor_email: str
    office_hours_url: str
    institution_name: str
    attendance_policy_name: str
    support_services_url: str
    lab_example_url: str = ""

    def as_placeholders(self) -> dict[str, str]:
        """Return the fields as ``str.format`` keyword arguments."""
        return asdict(self)
