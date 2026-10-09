"""Lossless text boundaries shared by storage and bounded AI input views."""
import re
from bisect import bisect_right


def semantic_ranges(text, limit=1800):
    paragraphs = [m.end() for m in re.finditer(r'\n+', text)]
    sentences = [m.end() for m in re.finditer(r'[。！？；.!?;](?:[”’"\'])?\s*|\s+', text)]
    cursor = 0
    while cursor < len(text):
        ceiling = min(len(text), cursor + limit)
        end = ceiling
        if ceiling < len(text):
            for boundaries in (paragraphs, sentences):
                index = bisect_right(boundaries, ceiling) - 1
                if index >= 0 and boundaries[index] >= cursor + limit // 2:
                    end = boundaries[index]
                    break
        yield cursor, end
        cursor = end
