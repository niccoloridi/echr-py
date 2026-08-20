"""Text processing utilities for HUDOC document HTML."""

from .composition import extract_bench_composition
from .conversion import html_to_md, html_to_text
from .dispositive import extract_dispositive_paragraphs
from .judges import (
    is_ad_hoc_judge,
    judge_country,
    judge_region,
    judge_years,
    normalise_judge_name,
)
from .opinions import split_opinions, split_opinions_report
from .segmentation import segment_full, segment_html, segment_main_sections
from .spine import build_spine_from_html, build_spine_from_text, spine_text

__all__ = [
    "html_to_md",
    "html_to_text",
    "extract_dispositive_paragraphs",
    "extract_bench_composition",
    "segment_main_sections",
    "segment_full",
    "segment_html",
    "build_spine_from_html",
    "build_spine_from_text",
    "spine_text",
    "split_opinions",
    "split_opinions_report",
    "normalise_judge_name",
    "judge_country",
    "judge_region",
    "judge_years",
    "is_ad_hoc_judge",
]
