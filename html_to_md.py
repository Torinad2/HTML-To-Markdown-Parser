#!/usr/bin/env python3

# ============================================================
# RU: Установка необходимых сторонних библиотек через pip:
# EN: Install the required third-party libraries using pip:
#
# pip install beautifulsoup4 markdownify
# ============================================================

"""
RU: Массово преобразует HTML-копию сайта HTTrack в Markdown с категориями и картинками.
EN: Batch-converts an HTTrack website mirror into categorized Markdown with images.
"""
from __future__ import annotations

import argparse
import csv
import re
import shutil
import unicodedata
from pathlib import Path
from urllib.parse import unquote, urlsplit

from bs4 import BeautifulSoup, Tag
from markdownify import markdownify as html_to_md

# RU: Исходная папка HTML и изображений; результаты записываются в подпапку main.
# EN: Root of source HTML and images; results are written into the main subfolder.
SOURCE_DIR = Path(r"F:\Yngliism")

OUTPUT_DIR = SOURCE_DIR / "main"

# RU: Таблица транслитерации кириллицы для названий папок и slug.
# EN: Cyrillic transliteration mapping for folder names and slugs.
# RU: Соответствие русских букв латинице для названий каталогов.
# EN: Mapping of Cyrillic letters to Latin for directory names.
TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g",
    "д": "d", "е": "e", "ё": "yo", "ж": "zh",
    "з": "z", "и": "i", "й": "y", "к": "k",
    "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t",
    "у": "u", "ф": "f", "х": "kh", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e",
    "ю": "yu", "я": "ya",
}


def slugify(text: str) -> str:
    """
    RU: Создаёт безопасное латинское имя папки или slug из текста: транслитерирует кириллицу, заменяет разделители на подчёркивания.
    EN: Create a filesystem-friendly Latin folder name or slug: transliterate Cyrillic and replace separators with underscores.
    """

    text = unicodedata.normalize("NFKC", text).lower()
    text = "".join(TRANSLIT.get(char, char) for char in text)
    text = re.sub(r"[^a-z0-9_-]+", "_", text)
    text = re.sub(r"_+", "_", text)
    return text.strip("_-") or "untitled"


def yaml_quote(value: str) -> str:
    """
    RU: Экранирует значение для строкового поля YAML в двойных кавычках.
    EN: Escape a value for a double-quoted YAML string field.
    """

    value = str(value)
    value = value.replace("\\", "\\\\")
    value = value.replace('"', '\\"')
    value = value.replace("\n", " ")
    return f'-79-"{value}"'


def read_html(path: Path) -> BeautifulSoup:
    """
    RU: Читает HTML как байты; BeautifulSoup самостоятельно определяет кодировку.
    EN: Read HTML as bytes and let BeautifulSoup detect its encoding.
    """

    raw = path.read_bytes()

    # BeautifulSoup определяет кодировку HTML.
    soup = BeautifulSoup(raw, "html.parser")
    return soup


def clean_text(element) -> str:
    """
    RU: Извлекает видимый текст HTML-элемента и нормализует пробелы.
    EN: Extract visible text from an HTML element and normalize whitespace.
    """

    if element is None:
        return ""

    return " ".join(
        element.get_text(" ", strip=True).split()
    )


def extract_title(soup: BeautifulSoup, path: Path) -> str:
    """
    RU: Ищет заголовок статьи в H1, затем в Open Graph; при отсутствии использует имя файла.
    EN: Find the article title in H1, then Open Graph; fall back to the filename.
    """

    selectors = [
        "h1.posthead",
        "article h1",
        ".entry-title",
        "h1",
    ]

    for selector in selectors:
        element = soup.select_one(selector)
        title = clean_text(element)
        if title:
            return title

    meta = soup.select_one('meta[property="og:title"]')

    if meta and meta.get("content"):
        return str(meta["content"]).split(" ⋆ ")[0].strip()

    return path.stem.replace("_", " ").replace("-", " ")


def extract_categories(soup: BeautifulSoup) -> list[str]:
    """
    RU: Извлекает категории из ссылок breadcrumbs; «Главная» не создаёт отдельную папку.
    EN: Extract category levels from breadcrumb links; Home does not create a directory.
    """

    breadcrumbs = soup.select_one(
        "#breadcrumbs, "
        ".breadcrumbs, "
        ".breadcrumb, "
        "nav[aria-label='breadcrumb']"
    )

    if breadcrumbs is None:
        return []

    # Берём только ссылки из хлебных крошек.
    # Последний элемент обычно является названием статьи.
    links = breadcrumbs.find_all("a")

    categories = []

    for link in links:
        name = clean_text(link)
        normalized = name.casefold().strip()

        if normalized in {
            "главная",
            "главная страница",
            "home",
            "main",
        }:
            continue

        if name:
            categories.append(slugify(name))

    return categories


def find_article(soup: BeautifulSoup):
    """
    RU: Находит контейнер основного содержимого статьи по набору CSS-селекторов.
    EN: Find the main article content container using CSS selectors.
    """

    selectors = [
        "article .entry-content",
        "article .post-content",
        "article .article-content",
        "article .post-body",
        "article .post-content-inner",
        "article #post-content",
        "article div[id^='post-']",
        "article",
        "main .entry-content",
        "main .post-content",
        "main #content",
        "main",
    ]

    for selector in selectors:
        element = soup.select_one(selector)

        if element is not None:
            return element

    return None


def resolve_image_path(src: str, html_file: Path,) -> Path | None:
    """
    RU: Преобразует URL изображения в существующий локальный файл внутри исходной папки.
    EN: Resolve an image URL to an existing local file inside the source directory.
    """

    if not src:
        return None

    if src.startswith("data:"):
        return None

    parsed = urlsplit(src)
    relative_url = unquote(parsed.path)

    if not relative_url:
        return None

    relative_url = relative_url.replace("\\", "/")

    # Абсолютные URL исходного сайта переводим в локальные пути.
    if parsed.scheme or parsed.netloc:

        if parsed.hostname not in {"derzhavarus.ru","www.derzhavarus.ru",}:
            return None

        relative_url = relative_url.lstrip("/")
        candidate = SOURCE_DIR / relative_url

    elif relative_url.startswith("/"):
        candidate = SOURCE_DIR / relative_url.lstrip("/")

    else:
        # В скачанных HTTrack HTML пути могут
        # быть относительными к самому HTML.
        candidate = html_file.parent / relative_url

    candidate = candidate.resolve()
    source_root = SOURCE_DIR.resolve()

    # Не позволяем ссылкам выйти за пределы SOURCE_DIR.
    if not candidate.is_relative_to(source_root):
        return None

    if candidate.is_file():
        return candidate

    # Запасной вариант: путь относительно корня сайта.
    fallback = (
        SOURCE_DIR / relative_url.lstrip("/")
    ).resolve()

    if (
        fallback.is_relative_to(source_root)
        and fallback.is_file()
    ):
        return fallback

    return None


def unique_image_name(source: Path, destination: Path, used_names: dict[str, Path],) -> str:
    """
    RU: Выбирает имя изображения без конфликтов с другими изображениями этой статьи.
    EN: Choose an image filename without collisions within the article.
    """

    original_name = source.name
    name = original_name
    counter = 2

    while name.lower() in used_names:
        if used_names[name.lower()] == source:

            return name

        name = (f"{source.stem}_{counter}{source.suffix}")
        counter += 1

    used_names[name.lower()] = source
    return name


def process_images(article: Tag, html_file: Path, article_dir: Path, warnings: list[str],) -> int:
    """
    RU: Копирует изображения рядом с index.md и заменяет ссылки на относительные пути.
    EN: Copy images next to index.md and rewrite references to relative paths.
    """

    copied = 0
    used_names = {}

    for image in list(article.find_all("img")):
        src = (
            image.get("data-src")
            or image.get("data-lazy-src")
            or image.get("src")
            or ""
        )

        src = str(src).strip()

        source_image = resolve_image_path(
            src,
            html_file,
        )

        if source_image is None:
            warnings.append(f"Image not found: {src}")
            image.decompose()
            continue

        filename = unique_image_name(
            source_image,
            article_dir,
            used_names,
        )

        destination = article_dir / filename

        if not destination.exists():
            shutil.copy2(source_image, destination)
            copied += 1

        # Заменяем ссылку на локальное имя файла.
        image["src"] = filename

        # Удаляем лишние HTML-атрибуты.
        for attribute in list(image.attrs):
            if attribute not in {"src", "alt", "title"}:
                del image[attribute]

        # Удаляем ссылку-обёртку, но оставляем картинку.
        parent = image.parent
        if isinstance(parent, Tag) and parent.name == "a":
            parent.unwrap()

    return copied


def remove_unwanted_elements(article: Tag) -> None:
    unwanted = [
        "script",
        "style",
        "noscript",
        "iframe",
        "form",
        "nav",
        "aside",
        "footer",
        "header",
        ".postinfo",
        ".post-meta",
        ".entry-meta",
        ".sharedaddy",
        ".social-share",
        ".social-sharing",
        ".advertisement",
        ".ads",
        ".related-posts",
        ".post-navigation",
        "#nav-nextprev",
        "#breadcrumbs",
        ".breadcrumbs",
        ".breadcrumb",
        ".comments",
        "#comments",
    ]

    for selector in unwanted:
        for element in list(article.select(selector)):
            element.decompose()


def remove_html_links(article: Tag) -> None:
    # Оставляем текст ссылок, но убираем HTML <a>.
    for link in list(article.find_all("a")):
        link.unwrap()


def normalize_markdown(markdown: str) -> str:
    markdown = markdown.replace("\r\n", "\n")

    # Удаляем чрезмерные пустые строки.
    markdown = re.sub(
        r"\n{3,}",
        "\n\n",
        markdown,
    )

    return markdown.strip()


def make_frontmatter(slug: str, title: str, category: str, description: str,) -> str:
    return "\n".join([
        "---",
        f"slug: {yaml_quote(slug)}",
        f"title: {yaml_quote(title)}",
        f"description: {yaml_quote(description)}",
        f"category: {yaml_quote(category)}",
        "status: published",
        "language: ru",
        "order: 0",
        "---",
    ])


def convert_file(html_file: Path, overwrite: bool = False,) -> dict:
    
    result = {
        "source": str(html_file),
        "destination": "",
        "status": "",
        "images": 0,
        "warnings": "",
    }

    warnings = []

    try:
        soup = read_html(html_file)

        title = extract_title(soup, html_file)
        categories = extract_categories(soup)

        article = find_article(soup)

        if article is None:
            result["status"] = "NO_ARTICLE"
            return result

        # Создаём путь из категорий.
        category_dir = OUTPUT_DIR

        for category in categories:
            category_dir = category_dir / category

        # Название папки статьи берём из имени HTML.
        article_slug = slugify(html_file.stem)
        article_dir = category_dir / article_slug

        # RU: Имя Markdown-файла совпадает с именем папки статьи с добавлением суффикса _RU.
        # EN: The Markdown filename matches the article folder name with the _RU suffix appended.
        markdown_file = article_dir / f"{article_slug}_RU.md"

        result["destination"] = str(markdown_file)

        if markdown_file.exists() and not overwrite:
            result["status"] = "SKIPPED_EXISTS"
            return result

        # Работаем с копией HTML-элемента,
        # чтобы не затрагивать исходный soup.
        article_copy = BeautifulSoup(
            str(article),
            "html.parser",
        )

        content = article_copy.find()

        if content is None:
            result["status"] = "EMPTY"
            return result

        remove_unwanted_elements(content)

        # Убираем заголовок из содержимого,
        # поскольку добавим его отдельно в Markdown.
        first_h1 = content.find("h1")
        if first_h1 is not None:
            first_h1.decompose()

        article_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        images_count = process_images(
            content,
            html_file,
            article_dir,
            warnings,
        )

        remove_html_links(content)

        markdown_body = html_to_md(
            str(content),
            heading_style="ATX",
            bullets="-",
            strip=[
                "script",
                "style",
            ],
        )

        markdown_body = normalize_markdown(
            markdown_body
        )

        # Удаляем пустой результат.
        if not markdown_body:
            result["status"] = "EMPTY"
            return result

        category_name = (
            " / ".join(categories)
            if categories
            else ""
        )

        # Описание: первые 160 символов текста.
        plain_text = clean_text(content)
        description = plain_text[:160].strip()

        frontmatter = make_frontmatter(
            slug=article_slug,
            title=title,
            category=category_name,
            description=description,
        )

        final_markdown = (
            frontmatter
            + "\n\n"
            + f"# {title}\n\n"
            + markdown_body
            + "\n"
        )

        markdown_file.write_text(
            final_markdown,
            encoding="utf-8",
        )

        result["status"] = "OK"
        result["images"] = images_count
        result["warnings"] = " | ".join(warnings)

    except Exception as error:
        result["status"] = "ERROR"
        result["warnings"] = str(error)

    return result


def find_html_files() -> list[Path]:
    html_files = []

    for path in SOURCE_DIR.rglob("*"):
        if not path.is_file():
            continue

        if path.suffix.lower() not in {".html", ".htm"}:
            continue

        # Не обрабатываем результаты конвертации.
        if OUTPUT_DIR in path.parents:
            continue

        # Не обрабатываем главную страницу сайта.
        if path.name.lower() in {
            "index.html",
            "index.htm",
        }:
            continue

        html_files.append(path)

    return sorted(html_files)


def save_log(results: list[dict]) -> None:
    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    log_path = OUTPUT_DIR / "_conversion_log.csv"

    with log_path.open(
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "source",
                "destination",
                "status",
                "images",
                "warnings",
            ],
        )

        writer.writeheader()
        writer.writerows(results)

    print(f"\nLog: {log_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert downloaded HTML to Markdown."
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Process only the first N HTML files.",
    )

    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing index.md files.",
    )

    args = parser.parse_args()

    if not SOURCE_DIR.exists():
        print(f"Source folder not found: {SOURCE_DIR}")
        return

    html_files = find_html_files()

    if args.limit > 0:
        html_files = html_files[:args.limit]

    print(f"HTML files found: {len(html_files)}")
    print(f"Source: {SOURCE_DIR}")
    print(f"Output: {OUTPUT_DIR}")
    print("-" * 60)

    results = []

    for number, html_file in enumerate(
        html_files,
        start=1,
    ):
        result = convert_file(
            html_file,
            overwrite=args.overwrite,
        )

        results.append(result)

        print(
            f"[{number}/{len(html_files)}] "
            f"{result['status']}: "
            f"{html_file.name}"
        )

        if result["warnings"]:
            print(
                f"    Warning: {result['warnings']}"
            )

    save_log(results)

    successful = sum(
        result["status"] == "OK"
        for result in results
    )

    skipped = sum(
        result["status"] == "SKIPPED_EXISTS"
        for result in results
    )

    errors = sum(
        result["status"] == "ERROR"
        for result in results
    )

    print("\nConversion completed.")
    print(f"Successful: {successful}")
    print(f"Skipped: {skipped}")
    print(f"Errors: {errors}")
    print(f"Total: {len(results)}")


if __name__ == "__main__":
    main()
