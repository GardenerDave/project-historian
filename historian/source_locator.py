from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ParsedSourceLocator:
    raw: str
    normalized: str
    source_path: str
    selector: str
    is_private: bool


def _require_text(locator: str) -> str:
    if not isinstance(locator, str) or not locator.strip():
        raise ValueError("missing source locator")
    return locator.strip()


def parse_source_locator(locator: str) -> ParsedSourceLocator:
    text = _require_text(locator)
    is_private = text.startswith("private:")
    if is_private:
        text = text[len("private:") :]
    if "#" not in text:
        source_path = text.strip()
        if not source_path:
            raise ValueError("locator must include a source path")
        return ParsedSourceLocator(raw=locator, normalized=source_path, source_path=source_path, selector="", is_private=is_private)
    source_path, selector = text.split("#", 1)
    source_path = source_path.strip()
    selector = selector.strip()
    if not source_path or not selector:
        raise ValueError("locator must include source path and selector")
    if "..." in selector:
        raise ValueError("locator selector is malformed")
    if ".." in selector:
        start, end = selector.split("..", 1)
        start = start.strip()
        end = end.strip()
        if not start or not end:
            raise ValueError("locator range must include both endpoints")
        selector = start if start == end else f"{start}..{end}"
    normalized = f"{source_path}#{selector}"
    return ParsedSourceLocator(raw=locator, normalized=normalized, source_path=source_path, selector=selector, is_private=is_private)


def normalize_source_locator(locator: str) -> str:
    return parse_source_locator(locator).normalized


def source_locators_equivalent(left: str, right: str) -> bool:
    return normalize_source_locator(left) == normalize_source_locator(right)
