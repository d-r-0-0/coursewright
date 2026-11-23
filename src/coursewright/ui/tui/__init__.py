"""The Textual full-screen application.

Each workflow is a Textual ``Screen``; ``CoursewrightApp`` owns the
shared session state and navigates between them.  ``coursewright tui``
launches it.  Screens call library code in ``core`` and ``ta`` and show
remote activity through the transmission log (docs/HOUSE_STYLE.md 3).
"""
