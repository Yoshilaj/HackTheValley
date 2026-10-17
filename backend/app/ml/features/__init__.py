"""Shared feature extractors used by both training (``ml_training/``) and serving.

Modules owned here:
- ``quality``  — recording-quality gate (spec phase-a-assessments.md §5.4 "recording_quality").
- ``vowel``    — UCI-Parkinson's-compatible sustained-vowel features (spec §5.4).
- ``tapping``  — finger-tapping task features (spec §5.3).

``speech`` (story-recall task) lives in a sibling module owned elsewhere.
"""
