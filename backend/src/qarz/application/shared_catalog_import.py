"""Loading the seed of the shared catalogue: rows into the database, photos into the file store.

For the import command (`qarz.interface.import_shared_catalog`), run by hand by whoever operates the
platform; it is not a migration and nothing runs it by itself. It can be run again at any time: a row is
known by the stable key the seed file gives it, so a second run adds nothing and brings names, sizes,
categories and prices up to date, and a photo already kept is not sent again.

The photos are copied into our own file store and served by our own host: no address of the place a
photo came from is stored or shown anywhere. That they are copied is the founder's decision
(docs/09-development-plan/EXPANSION.md).
"""

import hashlib
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from qarz.application.ports import FileStore, Storage
from qarz.application.shared_catalog_ports import SeedItem
from qarz.domain import shared_catalog
from qarz.domain.files import FileRefusal, clean_receipt

BATCH = 500
_SOURCE_KEY = re.compile(r"[0-9a-f]{16}")
_PATH_SEPARATOR = ">"


@dataclass
class ImportResult:
    rows: int = 0
    added: int = 0
    updated: int = 0
    skipped: int = 0  # rows without a usable key or name
    images_stored: int = 0
    images_kept: int = 0  # the photo the item already had
    images_missing: int = 0  # no file for the row: the item simply has no photo
    images_refused: int = 0  # a file that is not a whole image of an accepted type


def sections_of(categories: Any) -> dict[str, str]:
    """The seed's own top-level sections with the unified category each belongs to, out of the
    categories file. Raises ValueError for a file that is not one, or names a category we do not have."""
    listed = categories.get("categories") if isinstance(categories, Mapping) else None
    if not isinstance(listed, Mapping):
        raise ValueError("the categories file has no `categories`")
    sections: dict[str, str] = {}
    for key, entry in listed.items():
        if key not in shared_catalog.CATEGORIES:
            raise ValueError(f"the categories file names a category the catalogue does not have: {key}")
        names = entry.get("from") if isinstance(entry, Mapping) else None
        for name in names if isinstance(names, list) else []:
            if isinstance(name, str) and name != "*":
                sections[" ".join(name.split())] = key
    return sections


def _text(raw: Any, limit: int) -> str | None:
    text = " ".join(raw.split()) if isinstance(raw, str) else ""
    return text[:limit].rstrip() or None


def seed_item(row: Any, sections: Mapping[str, str]) -> SeedItem | None:
    """A row of the seed file as it is stored, without its photo; None when it has no usable key or name."""
    if not isinstance(row, Mapping):
        return None
    key = row.get("id")
    if not isinstance(key, str) or not _SOURCE_KEY.fullmatch(key):
        return None
    try:
        name_ru = shared_catalog.shared_name(_text(row.get("name_ru"), shared_catalog.MAX_SHARED_NAME))
        name_uz = shared_catalog.shared_name(_text(row.get("name_uz"), shared_catalog.MAX_SHARED_NAME))
    except ValueError:
        return None
    if name_ru is None and name_uz is None:
        return None
    path = row.get("path")
    parts = [" ".join(part.split()) for part in path.split(_PATH_SEPARATOR)] if isinstance(path, str) else []
    parts = [part for part in parts if part]
    amount = _text(row.get("amount"), shared_catalog.MAX_AMOUNT)
    return SeedItem(
        source_key=key,
        name_ru=name_ru,
        name_uz=name_uz,
        search_norm=shared_catalog.search_norm(name_ru, name_uz, amount),
        category=sections.get(parts[0], shared_catalog.OTHER) if parts else shared_catalog.OTHER,
        subcategory=_text(" > ".join(parts[1:]), shared_catalog.MAX_SUBCATEGORY),
        amount=amount,
        price_hint=shared_catalog.price_hint(row.get("price_hint")),
        image_key=None,
    )


async def import_catalog(
    storage: Storage,
    store: FileStore | None,
    rows: Iterable[Any],
    sections: Mapping[str, str],
    read_image: Callable[[str], bytes | None],
) -> ImportResult:
    """Store the rows and their photos. `read_image` gives the photo of a row by its key, or None.

    `storage` connects as the administrators' role, the only one that may write the catalogue. Without a
    file store the rows are loaded without photos.
    """
    result = ImportResult()
    async with storage.platform() as session:
        known = await session.shared_image_keys()
    batch: list[SeedItem] = []

    async def flush() -> None:
        if batch:
            async with storage.platform() as session:
                added = await session.upsert_seed_items(batch)
            result.added += added
            result.updated += len(batch) - added
            batch.clear()

    for row in rows:
        result.rows += 1
        item = seed_item(row, sections)
        if item is None:
            result.skipped += 1
            continue
        data = None if store is None else read_image(item.source_key)
        if data is None:
            result.images_missing += 1
        else:
            cleaned = clean_receipt(data)
            if isinstance(cleaned, FileRefusal) or cleaned[0] not in shared_catalog.IMAGE_TYPES:
                result.images_refused += 1
            else:
                # Kept as it will be served: without its metadata, under the SHA-256 of that content.
                mime, content = cleaned
                image_key = hashlib.sha256(content).hexdigest()
                if known.get(item.source_key) == image_key:
                    result.images_kept += 1
                else:
                    assert store is not None
                    await store.put(shared_catalog.image_object_key(image_key), content, mime)
                    result.images_stored += 1
                item = replace(item, image_key=image_key)
        batch.append(item)
        if len(batch) >= BATCH:
            await flush()
    await flush()
    return result
