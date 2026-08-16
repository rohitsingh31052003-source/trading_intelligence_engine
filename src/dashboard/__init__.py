"""
Trading Intelligence Engine — Web Dashboard (productization layer).

This package is a THIN presentation / orchestration layer over the
existing trading-intelligence-engine (Sprints 11A-12E). It implements
NO trading, scoring, prediction, decision or geometry logic: every value
it displays is read from the reused engine outputs.

Public entry point::

    from dashboard.app import create_app
    app = create_app()
    # run: python -m uvicorn dashboard.app:app --reload

The dashboard is DESCRIPTIVE ONLY. It does NOT guarantee future
performance, does NOT constitute a trading recommendation, and does NOT
modify the existing decision / scoring logic.
"""
