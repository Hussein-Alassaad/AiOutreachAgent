"""
All Claude prompt templates, structured for prompt caching.

Built in Phase 4. Not implemented yet.

The long instruction blocks are identical across every lead, so they are marked
as cacheable and only the per-lead data varies. This is the main cost lever on
an estimated 4,500 leads/month.
"""
