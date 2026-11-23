"""Coursewright: an AI teaching assistant and instructional designer.

The package is layered so that every capability is usable three ways:
as a Python function, as a ``coursewright`` command, and as an agent
skill (see ``skills/``).

    core    -- Canvas client, auth, LLM, config, export
    ta      -- running a live course (discussions, grading, imports)
    design  -- building a course (the five-phase framework)
    ui      -- Typer command line and Textual application

``ta`` and ``design`` depend on ``core`` and never on ``ui`` or on
each other.
"""

__version__ = "0.1.0"
