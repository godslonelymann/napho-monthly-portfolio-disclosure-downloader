"""Per-AMC monthly-portfolio parsers.

Each parser answers four questions for its AMC: which files/sheets are
schemes, where's the header row, which column is which, and what section
is this row under. Output is always a list[pipeline.schema.IntermediateRow].
"""
