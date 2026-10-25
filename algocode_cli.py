#!/usr/bin/env python3
"""Интерактивный клиент algocode.ru. Требуется Python 3.10+."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
import re
import shutil
import shlex
import ssl
import sys
from urllib.error import URLError
from urllib.parse import unquote, urljoin, urlsplit
from urllib.request import Request, urlopen
import webbrowser


BASE_URL = "https://algocode.ru/"
DATE = re.compile(r"\b\d{1,2}\s+[а-яё]+\s+\d{4}(?:\s*г\.?)?", re.I)
HIDDEN = re.compile(r"дистанционный\s+тур|дистт?ур", re.I)
ACTIONS = {"вход", "условия"}
TASK_HEADING = re.compile(
    r"^\s*(?:Задача|Problem)\s*([A-Z]{1,3})\s*[.:)](?:\s|$)",
    re.MULTILINE | re.IGNORECASE,
)


@dataclass
class Node:
    tag: str
    attrs: dict = field(default_factory=dict)
    parent: Node | None = field(default=None, repr=False)
    children: list = field(default_factory=list)

    def text(self):
        return " ".join(
            " ".join(
                child.text() if isinstance(child, Node) else child
                for child in self.children
            ).split()
        )

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.walk()


class Document(HTMLParser):
    VOID = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = Node("document")
        self.current = self.root
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        node = Node(tag, dict(attrs), self.current)
        self.current.children.append(node)
        if tag not in self.VOID:
            self.current = node

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        node = self.current
        while node.parent is not None:
            if node.tag == tag:
                self.current = node.parent
                break
            node = node.parent

    def handle_data(self, data):
        if not any(n.tag in {"script", "style"} for n in ancestors(self.current)):
            self.current.children.append(data)


def ancestors(node):
    while node is not None:
        yield node
        node = node.parent


@dataclass(frozen=True)
class Link:
    title: str
    url: str


@dataclass(frozen=True)
class Contest:
    title: str
    login: str | None
    statements: str | None


def link_url(node, base):
    href = node.attrs.get("href", "").strip()
    url = urljoin(base, href)
    return (
        url
        if href
        and not href.startswith("#")
        and urlsplit(url).scheme in {"http", "https"}
        else None
    )


def parse_links(html, base=BASE_URL, courses_only=False):
    """Главное меню берётся из раздела «Курсы», вложенные — из разделов списков."""
    root = Document(html).root
    result, seen = [], set()
    section = ""
    for node in root.walk():
        if re.fullmatch(r"h[1-6]", node.tag):
            section = node.text().casefold()
        if node.tag != "a":
            continue
        if courses_only and section != "курсы":
            continue
        if not courses_only and section in {
            "полезная информация",
            "полезные ссылки",
            "преподаватели",
        }:
            continue
        url = link_url(node, base)
        title = node.text()
        if url and title and title.casefold() not in ACTIONS and url not in seen:
            result.append(Link(title, url))
            seen.add(url)
    return result


def action_nodes(node):
    return [n for n in node.walk() if n.tag == "a" and n.text().casefold() in ACTIONS]


def parse_contests(html, base):
    root = Document(html).root
    result, seen = [], set()
    for anchor in action_nodes(root):
        if id(anchor) in seen:
            continue
        container = anchor.parent
        fallback = container
        # Ищем карточку занятия, не захватывая соседние контесты.
        for parent in ancestors(anchor.parent):
            actions = action_nodes(parent)
            labels = [n.text().casefold() for n in actions]
            if labels.count("вход") > 1 or labels.count("условия") > 1:
                break
            container = parent
            if DATE.search(parent.text()):
                break
            if parent.tag in {"article", "li", "tr"}:
                fallback = parent
        if container is root:
            container = fallback
        actions = action_nodes(container)
        seen.update(id(n) for n in actions)
        title = container.text()
        title = re.sub(r"\b(?:Вход|Условия|Контест)\b", "", title, flags=re.I)
        title = " ".join(title.split()).strip(" ·|—-") or "Контест"
        if HIDDEN.search(title):
            continue
        urls = {n.text().casefold(): link_url(n, base) for n in actions}
        if any(urls.values()):
            result.append(Contest(title, urls.get("вход"), urls.get("условия")))
    return result


def tls_context():
    context = ssl.create_default_context()
    # Python с python.org может не видеть системные CA macOS без
    # запуска Install Certificates.command. Загружаем системный PEM явно.
    system_ca = Path("/etc/ssl/cert.pem")
    if sys.platform == "darwin" and system_ca.is_file():
        context.load_verify_locations(cafile=str(system_ca))
    return context


def open_url(url):
    return urlopen(
        Request(url, headers={"User-Agent": "algocode-cli/1.0"}),
        timeout=30,
        context=tls_context(),
    )


def fetch_html(url):
    with open_url(url) as response:
        return response.read().decode(
            response.headers.get_content_charset() or "utf-8"
        ), response.url


def download(url, directory):
    directory = directory.expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    with open_url(url) as response:
        if response.headers.get_content_type() in {
            "text/html",
            "application/xhtml+xml",
        }:
            raise ValueError(
                "Вместо файла сайт вернул HTML-страницу. Возможно, требуется авторизация."
            )
        filename = response.headers.get_filename() or unquote(
            urlsplit(response.url).path.rsplit("/", 1)[-1]
        )
        filename = re.sub(
            r'[<>:"/\\|?*\x00-\x1f]', "_", filename or "conditions.pdf"
        ).strip(" .")
        filename = filename[:180] or "conditions.pdf"
        target = directory / filename
        suffix = 0
        while True:
            candidate = (
                target
                if suffix == 0
                else target.with_name(f"{target.stem}-{suffix}{target.suffix}")
            )
            try:
                output = candidate.open("xb")
                break
            except FileExistsError:
                suffix += 1
        try:
            with output:
                shutil.copyfileobj(response, output)
            if not candidate.stat().st_size:
                raise ValueError("Сайт вернул пустой файл.")
        except BaseException:
            candidate.unlink(missing_ok=True)
            raise
    return candidate


def task_ranges(texts):
    """Интервалы [start, end): страницы продолжения остаются у своей задачи."""
    starts = []
    seen = set()
    for page_number, text in enumerate(texts):
        labels = list(dict.fromkeys(m.group(1).lower() for m in TASK_HEADING.finditer(text or "")))
        if len(labels) > 1:
            raise ValueError(
                f"На странице {page_number + 1} начинаются несколько задач. "
                "Автоматическое разбиение по страницам невозможно; исходный PDF сохранён."
            )
        if not labels:
            continue
        label = labels[0]
        # На странице продолжения иногда повторяется заголовок той же задачи.
        if starts and label == starts[-1][0]:
            continue
        if label in seen:
            raise ValueError(
                f"Заголовок задачи {label.upper()} встретился повторно после другой задачи. "
                "Не удалось надёжно определить границы; исходный PDF сохранён."
            )
        starts.append((label, page_number))
        seen.add(label)
    if not starts:
        raise ValueError(
            "Не найдены заголовки вида «Задача A.» или «Problem A.». "
            "Исходный PDF сохранён без разбиения."
        )
    return [
        (label, start, starts[i + 1][1] if i + 1 < len(starts) else len(texts))
        for i, (label, start) in enumerate(starts)
    ]


def split_pdf(source, destination=None):
    try:
        from pypdf import PdfReader, PdfWriter
        from pypdf.errors import PyPdfError
    except ImportError as error:
        install = shlex.join([sys.executable, "-m", "pip", "install", "pypdf>=6,<7"])
        raise ValueError(
            f"Для разбиения PDF установите pypdf: {install}. "
            f"Исходный PDF сохранён: {source}"
        ) from error

    output_dir = None
    try:
        with source.open("rb") as stream:
            reader = PdfReader(stream)
            ranges = task_ranges([page.extract_text() for page in reader.pages])
            output_dir = destination or source.with_suffix("")
            if destination is None:
                suffix = 0
                while True:
                    candidate = output_dir if suffix == 0 else output_dir.with_name(f"{output_dir.name}-{suffix}")
                    try:
                        candidate.mkdir()
                        output_dir = candidate
                        break
                    except FileExistsError:
                        suffix += 1
            else:
                output_dir.mkdir(parents=True, exist_ok=True)
            paths = []
            try:
                for label, start, end in ranges:
                    target = output_dir / f"{label}.pdf"
                    with PdfWriter() as writer:
                        for page_number in range(start, end):
                            writer.add_page(reader.pages[page_number])
                        with target.open("xb") as output:
                            paths.append(target)
                            writer.write(output)
            except BaseException:
                if destination is None:
                    shutil.rmtree(output_dir)
                else:
                    for path in paths:
                        path.unlink(missing_ok=True)
                raise
            return paths
    except PyPdfError as error:
        raise ValueError(f"Не удалось обработать PDF: {error}. Исходный файл сохранён: {source}") from error


def choose(title, items, back="Назад"):
    while True:
        print(f"\n{title}\n")
        for i, item in enumerate(items, 1):
            print(f"  {i}. {item}")
        print(f"  0. {back}")
        answer = input("\nВыберите номер: ").strip()
        if answer.isdecimal() and 0 <= int(answer) <= len(items):
            return int(answer) - 1
        print("Введите номер из списка.")


def default_download_directory():
    directory = Path.cwd()
    name = directory.name
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = " ".join(name.split()).strip(" .·|—-")[:120] or "root"
    base = directory / f"tasks-{name}"
    suffix = 0
    while True:
        candidate = base if suffix == 0 else base.with_name(f"{base.name}-{suffix}")
        try:
            candidate.mkdir()
            return candidate
        except FileExistsError:
            suffix += 1


def contest_menu(contest, directory):
    while True:
        actions = []
        if contest.login:
            actions.append(("Показать ссылку на контест", "link"))
        if contest.statements:
            actions.append(("Скачать условия", "download"))
        index = choose(contest.title, [title for title, _ in actions])
        if index < 0:
            return
        if actions[index][1] == "link":
            print(f"\n{contest.login}")
        else:
            try:
                print("\nСкачиваю условия…")
                target_directory = directory if directory is not None else default_download_directory()
                source = download(contest.statements, target_directory)
                print("Разбиваю PDF по заголовкам задач…")
                paths = split_pdf(source, destination=source.parent) if directory is None else split_pdf(source)
                source.unlink()
                for path in paths:
                    print(f"Сохранено: {path}")
            except (OSError, URLError, ValueError) as error:
                print(f"Не удалось подготовить условия: {error}", file=sys.stderr)


def browse(start_url, directory):
    stack = [("algocode.ru", start_url)]
    while stack:
        title, url = stack[-1]
        print(f"\nЗагружаю {url}…")
        try:
            html, resolved_url = fetch_html(url)
        except (OSError, URLError, ValueError) as error:
            print(f"Не удалось загрузить страницу: {error}", file=sys.stderr)
            if (
                choose(
                    "Действия", ["Повторить"], "Назад" if len(stack) > 1 else "Выход"
                )
                < 0
            ):
                stack.pop()
            continue
        contests = parse_contests(html, resolved_url)
        is_home = urlsplit(resolved_url).path in {"", "/"}
        links = (
            [] if contests else parse_links(html, resolved_url, courses_only=is_home)
        )
        # Все отфильтрованные контесты не должны превращаться в обычные ссылки.
        if not contests and action_nodes(Document(html).root):
            links = []
        external = urlsplit(resolved_url).hostname not in {
            "algocode.ru",
            "www.algocode.ru",
        }
        if external:
            links = []
        entries = [c.title for c in contests] + [l.title for l in links]
        if not entries:
            print("На странице нет доступных контестов или разделов.")
            entries = ["Показать ссылку на страницу", "Открыть страницу в браузере"]
        while True:
            index = choose(title, entries, "Выход" if len(stack) == 1 else "Назад")
            if index < 0:
                stack.pop()
                break
            if contests:
                contest_menu(contests[index], directory)
            elif links:
                link = links[index]
                stack.append((link.title, link.url))
                break
            elif index == 0:
                print(f"\n{resolved_url}")
            else:
                if not webbrowser.open(resolved_url):
                    print(f"Откройте ссылку вручную: {resolved_url}")


def main():
    parser = argparse.ArgumentParser(
        description="Курсы и контесты algocode.ru в терминале"
    )
    parser.add_argument(
        "--download-dir",
        type=Path,
        default=None,
        help="папка для условий (по умолчанию: ./tasks-<имя текущей папки>)",
    )
    parser.add_argument(
        "--url",
        default=BASE_URL,
        help="начальная страница, например https://algocode.ru/b2026/",
    )
    args = parser.parse_args()
    if urlsplit(args.url).scheme not in {"http", "https"}:
        parser.error("--url должен начинаться с http:// или https://")
    try:
        browse(args.url, args.download_dir)
    except (KeyboardInterrupt, EOFError):
        print("\nДо встречи!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
