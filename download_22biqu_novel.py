#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_ROOT = SCRIPT_DIR
DEFAULT_TIMEOUT = 20
DEFAULT_DELAY_SECONDS = 1.0
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "Chrome/126 Safari/537.36"
)


@dataclass(frozen=True)
class Chapter:
    title: str
    url: str


class NovelPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.meta: dict[str, str] = {}
        self.section_heading = ""
        self._in_h2 = False
        self._h2_parts: list[str] = []
        self.links_by_section: list[tuple[str, str, str]] = []
        self._capture_link = False
        self._link_href = ""
        self._link_parts: list[str] = []
        self.option_values: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key.lower(): value or "" for key, value in attrs}
        if tag == "meta":
            name = attr.get("property") or attr.get("name")
            content = attr.get("content")
            if name and content is not None:
                self.meta[name] = html.unescape(content).strip()
            return
        if tag == "h2" and "layout-tit" in attr.get("class", ""):
            self._in_h2 = True
            self._h2_parts = []
            return
        if tag == "a":
            href = attr.get("href", "")
            if href:
                self._capture_link = True
                self._link_href = href
                self._link_parts = []
            return
        if tag == "option":
            value = attr.get("value", "")
            if value:
                self.option_values.append(value)

    def handle_endtag(self, tag: str) -> None:
        if tag == "h2" and self._in_h2:
            self.section_heading = normalize_space("".join(self._h2_parts))
            self._in_h2 = False
            return
        if tag == "a" and self._capture_link:
            title = normalize_space("".join(self._link_parts))
            if title:
                self.links_by_section.append((self.section_heading, title, self._link_href))
            self._capture_link = False
            self._link_href = ""
            self._link_parts = []

    def handle_data(self, data: str) -> None:
        if self._in_h2:
            self._h2_parts.append(data)
        if self._capture_link:
            self._link_parts.append(data)

    def handle_entityref(self, name: str) -> None:
        self.handle_data(html.unescape(f"&{name};"))

    def handle_charref(self, name: str) -> None:
        self.handle_data(html.unescape(f"&#{name};"))


class ChapterPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.title = ""
        self.next_href: str | None = None
        self._in_title = False
        self._title_parts: list[str] = []
        self._content_depth = 0
        self._paragraph_parts: list[str] = []
        self.paragraphs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key.lower(): value or "" for key, value in attrs}
        if tag == "h1" and "title" in attr.get("class", ""):
            self._in_title = True
            self._title_parts = []
            return
        if tag == "a" and attr.get("id") == "next_url":
            self.next_href = attr.get("href") or None
            return
        if tag == "div" and attr.get("id") == "content":
            self._content_depth = 1
            return
        if self._content_depth:
            if tag == "br":
                self._paragraph_parts.append("\n")
                return
            self._content_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "h1" and self._in_title:
            self.title = normalize_space("".join(self._title_parts))
            self._in_title = False
            return
        if not self._content_depth:
            return
        if tag == "p":
            self._flush_paragraph()
        self._content_depth -= 1
        if self._content_depth == 0:
            self._flush_paragraph()

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_parts.append(data)
        if self._content_depth:
            self._paragraph_parts.append(data)

    def handle_entityref(self, name: str) -> None:
        self.handle_data(html.unescape(f"&{name};"))

    def handle_charref(self, name: str) -> None:
        self.handle_data(html.unescape(f"&#{name};"))

    def _flush_paragraph(self) -> None:
        text = clean_text("".join(self._paragraph_parts))
        if text:
            self.paragraphs.append(text)
        self._paragraph_parts = []


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def clean_text(value: str) -> str:
    value = html.unescape(value).replace("\r", "")
    lines = [normalize_space(line) for line in value.splitlines()]
    return "\n".join(line for line in lines if line)


def safe_filename(value: str, limit: int = 80) -> str:
    value = re.sub(r'[\\/:*?"<>|\r\n]', "_", value).strip()
    value = re.sub(r"\s+", " ", value)
    return value[:limit].rstrip(" .")


def extract_book_info(page_html: str) -> tuple[str, str]:
    parser = NovelPageParser()
    parser.feed(page_html)
    title = parser.meta.get("og:novel:book_name") or parser.meta.get("og:title") or "novel"
    author = parser.meta.get("og:novel:author") or ""
    return safe_filename(title), author


def extract_chapters(page_html: str, directory_url: str) -> list[Chapter]:
    parser = NovelPageParser()
    parser.feed(page_html)
    chapters: list[Chapter] = []
    seen: set[str] = set()
    in_body = False
    for section, title, href in parser.links_by_section:
        if "正文" in section:
            in_body = True
        if not in_body:
            continue
        if not re.search(r"/biqu\d+/\d+\.html$", href):
            continue
        url = urljoin(directory_url, href)
        if url in seen:
            continue
        seen.add(url)
        chapters.append(Chapter(title, url))
    if chapters:
        return chapters

    for _, title, href in parser.links_by_section:
        if re.search(r"/biqu\d+/\d+\.html$", href):
            url = urljoin(directory_url, href)
            if url not in seen:
                seen.add(url)
                chapters.append(Chapter(title, url))
    return chapters


def extract_directory_page_urls(page_html: str, directory_url: str) -> list[str]:
    parser = NovelPageParser()
    parser.feed(page_html)
    book_match = re.search(r"/biqu\d+/", urlparse(directory_url).path)
    if not book_match:
        return [directory_url]
    book_path = book_match.group(0)
    urls: list[str] = []
    seen: set[str] = set()
    for value in [directory_url, *parser.option_values]:
        absolute = urljoin(directory_url, value)
        path = urlparse(absolute).path
        if not path.startswith(book_path):
            continue
        if not re.match(rf"^{re.escape(book_path)}(?:\d+/)?$", path):
            continue
        if absolute not in seen:
            seen.add(absolute)
            urls.append(absolute)
    return urls or [directory_url]


def extract_chapter_text_and_next(
    page_html: str,
    page_url: str,
    chapter: Chapter,
) -> tuple[str, str, str | None]:
    parser = ChapterPageParser()
    parser.feed(page_html)
    title = parser.title or chapter.title
    text = "\n\n".join(parser.paragraphs)
    next_url = urljoin(page_url, parser.next_href) if parser.next_href else None
    if next_url and not is_same_chapter_page(chapter.url, next_url):
        next_url = None
    return title, text, next_url


def is_same_chapter_page(first_page_url: str, next_url: str) -> bool:
    first = urlparse(first_page_url).path
    candidate = urlparse(next_url).path
    match = re.match(r"^(?P<base>.*/\d+)(?:_\d+)?\.html$", first)
    if not match:
        return False
    base = re.escape(match.group("base"))
    return re.match(rf"^{base}_\d+\.html$", candidate) is not None


def fetch(session: requests.Session, url: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    if not response.encoding or response.encoding.lower() == "iso-8859-1":
        response.encoding = response.apparent_encoding or "utf-8"
    return response.text


def load_progress(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"completed_urls": [], "chapters": {}}
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    data.setdefault("completed_urls", [])
    data.setdefault("chapters", {})
    return data


def save_progress(path: Path, progress: dict[str, Any]) -> None:
    path.write_text(json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8")


def download_chapter(
    session: requests.Session,
    chapter: Chapter,
    delay: float,
    timeout: int,
) -> str:
    page_url: str | None = chapter.url
    parts: list[str] = []
    visited: set[str] = set()
    while page_url:
        if page_url in visited:
            raise RuntimeError(f"检测到章节分页循环：{page_url}")
        visited.add(page_url)
        page_html = fetch(session, page_url, timeout)
        _, text, page_url = extract_chapter_text_and_next(page_html, page_url, chapter)
        if text:
            parts.append(text)
        if page_url and delay > 0:
            time.sleep(delay)
    return "\n\n".join(parts).strip()


def write_novel_text(output_path: Path, chapters: list[Chapter], chapter_texts: dict[str, str]) -> None:
    with output_path.open("w", encoding="utf-8") as file:
        for chapter in chapters:
            text = chapter_texts.get(chapter.url, "").strip()
            if not text:
                continue
            file.write(chapter.title)
            file.write("\n")
            file.write("=" * len(chapter.title))
            file.write("\n\n")
            file.write(text)
            file.write("\n\n\n")


def download_novel(
    directory_url: str,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    delay: float = DEFAULT_DELAY_SECONDS,
    timeout: int = DEFAULT_TIMEOUT,
    limit: int | None = None,
) -> Path:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    directory_html = fetch(session, directory_url, timeout)
    book_title, author = extract_book_info(directory_html)
    directory_page_urls = extract_directory_page_urls(directory_html, directory_url)
    directory_pages = [(directory_url, directory_html)]
    for page_url in directory_page_urls[1:]:
        if delay > 0:
            time.sleep(delay)
        directory_pages.append((page_url, fetch(session, page_url, timeout)))

    chapters: list[Chapter] = []
    seen_chapter_urls: set[str] = set()
    for page_url, page_html in directory_pages:
        for chapter in extract_chapters(page_html, page_url):
            if chapter.url in seen_chapter_urls:
                continue
            seen_chapter_urls.add(chapter.url)
            chapters.append(chapter)
    if not chapters:
        raise RuntimeError("未能从目录页解析到章节链接")
    if limit is not None:
        chapters = chapters[:limit]

    book_dir = output_root / book_title
    book_dir.mkdir(parents=True, exist_ok=True)
    progress_path = book_dir / "progress.json"
    output_path = book_dir / f"{book_title}.txt"
    progress = load_progress(progress_path)
    completed_urls = set(progress["completed_urls"])
    chapter_texts: dict[str, str] = dict(progress["chapters"])

    total = len(chapters)
    print(f"小说：{book_title}" + (f"（{author}）" if author else ""))
    print(f"章节数：{total}")
    print(f"输出目录：{book_dir}")

    for index, chapter in enumerate(chapters, start=1):
        if chapter.url in completed_urls and chapter.url in chapter_texts:
            print(f"[{index}/{total}] 跳过已完成：{chapter.title}")
            continue
        print(f"[{index}/{total}] 下载：{chapter.title}")
        chapter_texts[chapter.url] = download_chapter(session, chapter, delay, timeout)
        completed_urls.add(chapter.url)
        progress["completed_urls"] = sorted(completed_urls)
        progress["chapters"] = chapter_texts
        save_progress(progress_path, progress)
        write_novel_text(output_path, chapters, chapter_texts)
        if delay > 0:
            time.sleep(delay)

    write_novel_text(output_path, chapters, chapter_texts)
    print(f"完成：{output_path}")
    return output_path


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="下载 22biqu 小说目录页到本地 TXT 文件。")
    parser.add_argument("url", help="小说目录页 URL，例如 https://www.22biqu.com/biqu71220/")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help="输出根目录，默认是脚本所在目录。",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY_SECONDS,
        help="每个页面请求之间的等待秒数，默认 1.0。",
    )
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="单次请求超时秒数。")
    parser.add_argument("--limit", type=int, default=None, help="只下载前 N 章，便于测试。")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        download_novel(
            directory_url=args.url,
            output_root=args.output,
            delay=max(args.delay, 0),
            timeout=args.timeout,
            limit=args.limit,
        )
    except KeyboardInterrupt:
        print("\n已中断；下次重跑会根据 progress.json 继续。", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"下载失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
