"""Library-layer primitives: resolution/enrichment worker and helpers.

This package owns the *resumable* side of library-first add (spec sections 4.1a, 4.3):
tracks land in the library immediately, and resolution/enrichment happens
out-of-band through the :class:`~tuneshift.library.worker.ResolutionWorker`.
"""
