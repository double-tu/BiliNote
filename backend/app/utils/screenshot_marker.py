import re
from dataclasses import dataclass
from typing import List, Optional, Tuple


_CONTENT_PATTERN = re.compile(r"Content-(?:\[(\d{2}):(\d{2})\]|(\d{2}):(\d{2}))", re.IGNORECASE)
_HEADING_PATTERN = re.compile(r"^(#{2,6})\s+(.+?)\s*$")


@dataclass
class _Section:
    heading_line: int
    end_line: int
    title: str
    anchor_seconds: Optional[int]
    effective_seconds: float = 0.0


def extract_screenshot_timestamps(markdown: str) -> List[Tuple[str, int]]:
    pattern = r"(\*?Screenshot-(?:\[(\d{2}):(\d{2})\]|(\d{2}):(\d{2}))\*?)"
    results: List[Tuple[str, int]] = []
    for match in re.finditer(pattern, markdown):
        mm = match.group(2) or match.group(4)
        ss = match.group(3) or match.group(5)
        total_seconds = int(mm) * 60 + int(ss)
        results.append((match.group(1), total_seconds))
    return results


def _content_timestamp(title: str) -> Optional[int]:
    match = _CONTENT_PATTERN.search(title)
    if not match:
        return None
    mm = match.group(1) or match.group(3)
    ss = match.group(2) or match.group(4)
    return int(mm) * 60 + int(ss)


def _is_content_section(title: str) -> bool:
    plain_title = _CONTENT_PATTERN.sub("", title).replace("*", "").strip().lower()
    return plain_title not in {"目录", "ai 总结", "ai总结"}


def _find_sections(lines: List[str]) -> List[_Section]:
    headings: List[Tuple[int, str]] = []
    for line_idx, line in enumerate(lines):
        match = _HEADING_PATTERN.match(line)
        if match:
            headings.append((line_idx, match.group(2)))

    sections: List[_Section] = []
    inherited_anchor: Optional[int] = None
    for idx, (line_idx, title) in enumerate(headings):
        explicit_anchor = _content_timestamp(title)
        if explicit_anchor is not None:
            inherited_anchor = explicit_anchor
        if not _is_content_section(title):
            continue
        end_line = headings[idx + 1][0] if idx + 1 < len(headings) else len(lines)
        # 父级标题后立即跟子标题时没有可承载截图的正文。跳过该标题，但它的
        # Content 时间仍会由后续有正文的子章节继承。
        if not any(line.strip() for line in lines[line_idx + 1:end_line]):
            continue
        sections.append(
            _Section(
                heading_line=line_idx,
                end_line=end_line,
                title=title,
                anchor_seconds=explicit_anchor if explicit_anchor is not None else inherited_anchor,
            )
        )
    return sections


def _assign_effective_times(sections: List[_Section], duration: float) -> None:
    """为章节生成可用于截图归位的单调时间轴。

    模型经常给多个子章节重复相同的 Content 时间。每个不同时间只采用第一次
    出现的位置作为硬锚点，其余章节在相邻锚点或视频结尾之间按正文顺序插值。
    """
    if not sections:
        return

    distinct_anchors: List[Tuple[int, float]] = []
    seen_times = set()
    for idx, section in enumerate(sections):
        anchor = section.anchor_seconds
        if anchor is None or anchor in seen_times:
            continue
        if distinct_anchors and anchor < distinct_anchors[-1][1]:
            continue
        distinct_anchors.append((idx, float(anchor)))
        seen_times.add(anchor)

    max_anchor = max((value for _, value in distinct_anchors), default=0.0)
    timeline_end = max(float(duration or 0), max_anchor, 1.0)
    control_points = list(distinct_anchors)

    if not control_points or control_points[0][0] > 0:
        control_points.insert(0, (0, 0.0))
    if control_points[-1][0] < len(sections):
        control_points.append((len(sections), timeline_end))

    for point_idx in range(len(control_points) - 1):
        start_idx, start_time = control_points[point_idx]
        end_idx, end_time = control_points[point_idx + 1]
        span = max(1, end_idx - start_idx)
        for section_idx in range(start_idx, min(end_idx, len(sections))):
            ratio = (section_idx - start_idx) / span
            sections[section_idx].effective_seconds = start_time + (end_time - start_time) * ratio

    if control_points[-1][0] < len(sections):
        for section in sections[control_points[-1][0]:]:
            section.effective_seconds = control_points[-1][1]


def insert_screenshot_markers_by_section(
    markdown: str,
    timestamps: List[int],
    duration: float = 0,
) -> str:
    """把兜底截图标记插到时间上最匹配的正文章节末尾。

    已有截图标记时保持模型原位置不变。多个时间戳可以归入同一章节。
    """
    if not markdown or extract_screenshot_timestamps(markdown):
        return markdown

    normalized_timestamps = sorted({max(0, int(ts)) for ts in timestamps or []})
    if not normalized_timestamps:
        return markdown

    lines = markdown.rstrip().splitlines()
    sections = _find_sections(lines)
    if not sections:
        markers = [f"*Screenshot-[{ts // 60:02d}:{ts % 60:02d}]" for ts in normalized_timestamps]
        return "\n".join(lines + ["", *markers])

    _assign_effective_times(sections, duration)
    markers_by_end_line: dict[int, List[str]] = {}
    for timestamp in normalized_timestamps:
        target = sections[0]
        for section in sections:
            if section.effective_seconds <= timestamp:
                target = section
            else:
                break
        markers_by_end_line.setdefault(target.end_line, []).append(
            f"*Screenshot-[{timestamp // 60:02d}:{timestamp % 60:02d}]"
        )

    for end_line in sorted(markers_by_end_line, reverse=True):
        markers = markers_by_end_line[end_line]
        insertion = [""] + markers + [""]
        lines[end_line:end_line] = insertion

    return "\n".join(lines).rstrip()
