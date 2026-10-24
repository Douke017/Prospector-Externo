"""Inspección de índices ZIP/TAR en memoria, sin extraer binarios a disco."""

from __future__ import annotations

import gzip
import io
import os
import stat
import tarfile
import zipfile
from urllib.parse import urlparse


class ArchiveEntry:
    def __init__(self, filename: str, size_bytes: int, is_dir: bool = False):
        self.filename = filename
        self.size_bytes = size_bytes
        self.is_dir = is_dir
        self.extension = os.path.splitext(filename)[1].lower()


class ArchiveExtractor:
    """Lee metadatos de miembros, nunca sus contenidos ni archivos en disco."""

    COMPRESSED_EXTENSIONS = {".zip", ".tar", ".gz", ".tgz", ".bz2"}
    INSPECTABLE_SUFFIXES = (".zip", ".tar", ".tar.gz", ".tgz")

    @classmethod
    def is_archive(cls, url_or_filename: str) -> bool:
        path = urlparse(url_or_filename).path.lower()
        return any(path.endswith(ext) for ext in cls.COMPRESSED_EXTENSIONS)

    @classmethod
    def can_inspect(cls, url_or_filename: str) -> bool:
        path = urlparse(url_or_filename).path.lower()
        return path.endswith(cls.INSPECTABLE_SUFFIXES)

    @staticmethod
    def _safe_member_name(raw_name: str) -> str | None:
        """Rechazar rutas absolutas/traversal sin cambiar la identidad interna válida."""
        name = raw_name.replace("\\", "/")
        # TAR generado con `tar -cf ... .` suele anteponer './'.
        while name.startswith("./"):
            name = name[2:]
        parts = name.split("/")
        if (not name or name.startswith("/") or any(p in {"", ".", ".."} for p in parts)
                or ":" in parts[0] or "\x00" in name):
            return None
        return name

    @classmethod
    def inspect_archive_bytes(
        cls, archive_bytes: bytes, filename_hint: str = "", *,
        max_entries: int = 250, max_uncompressed_bytes: int = 32_000_000,
    ) -> list[ArchiveEntry]:
        """Devuelve entradas seguras verificando el tamaño *declarado* acumulado.

        Los ZIP solo se inspeccionan por directorio central. Un TAR.GZ se
        descomprime a RAM con un tope para impedir bombas y nunca a disco.
        """
        if max_entries < 1 or max_uncompressed_bytes < 1:
            raise ValueError("Límites de inspección inválidos")
        entries: list[ArchiveEntry] = []
        total_size = 0

        def register(name: str, size: int) -> None:
            nonlocal total_size
            safe_name = cls._safe_member_name(name)
            if safe_name is None:
                return
            if len(entries) >= max_entries or size < 0 or total_size + size > max_uncompressed_bytes:
                raise ValueError("ARCHIVE_INSPECTION_LIMIT")
            total_size += size
            entries.append(ArchiveEntry(safe_name, size))

        hint = urlparse(filename_hint).path.lower()
        if hint.endswith(".zip") or zipfile.is_zipfile(io.BytesIO(archive_bytes)):
            try:
                with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf:
                    for info in zf.infolist():
                        if info.is_dir() or stat.S_ISLNK(info.external_attr >> 16):
                            continue
                        register(info.filename, info.file_size)
                return entries
            except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
                raise ValueError("INVALID_ARCHIVE") from exc

        try:
            # No permitir que gzip materialice un TAR descomprimido ilimitado.
            if hint.endswith((".tgz", ".tar.gz")) or archive_bytes[:2] == b"\x1f\x8b":
                with gzip.GzipFile(fileobj=io.BytesIO(archive_bytes)) as gf:
                    tar_bytes = gf.read(max_uncompressed_bytes + 1)
                if len(tar_bytes) > max_uncompressed_bytes:
                    raise ValueError("ARCHIVE_INSPECTION_LIMIT")
            else:
                tar_bytes = archive_bytes
            with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:") as tf:
                for member in tf:
                    if member.isfile():
                        register(member.name, member.size)
            return entries
        except (OSError, EOFError, tarfile.TarError) as exc:
            raise ValueError("INVALID_ARCHIVE") from exc
