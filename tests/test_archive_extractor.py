"""
Pruebas unitarias para el extractor de archivos comprimidos en memoria (ArchiveExtractor).
"""

import io
import zipfile
import tarfile
import pytest
from prospector_externo.infrastructure.archive_extractor import ArchiveExtractor, ArchiveEntry


def create_sample_zip_bytes() -> bytes:
    """Crea un buffer ZIP en memoria con archivos de prueba."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("reporte_financiero_01_2026.pdf", b"%PDF-1.4 Mock PDF Content")
        zf.writestr("notas_explicativas.txt", b"Texto de notas explicativas")
        zf.writestr("subfolder/resumen_2026.xlsx", b"Mock Excel Content")
    return buf.getvalue()


def create_sample_tar_bytes() -> bytes:
    """Crea un buffer TAR en memoria con archivos de prueba."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        data = b"Contenido CSV simulado"
        ti = tarfile.TarInfo(name="datos_2026.csv")
        ti.size = len(data)
        tf.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


def test_is_archive():
    assert ArchiveExtractor.is_archive("https://example.org/download/archivo.zip") is True
    assert ArchiveExtractor.is_archive("archivo.tar") is True
    assert ArchiveExtractor.is_archive("paquete.tgz") is True
    assert ArchiveExtractor.is_archive("archivo.gz") is True
    assert ArchiveExtractor.is_archive("reporte.pdf") is False
    assert ArchiveExtractor.is_archive("datos.xlsx") is False


def test_inspect_zip_archive_bytes():
    zip_bytes = create_sample_zip_bytes()
    entries = ArchiveExtractor.inspect_archive_bytes(zip_bytes, filename_hint="reportes.zip")

    assert len(entries) == 3
    filenames = [e.filename for e in entries]
    assert "reporte_financiero_01_2026.pdf" in filenames
    assert "subfolder/resumen_2026.xlsx" in filenames
    assert "notas_explicativas.txt" in filenames

    pdf_entry = next(e for e in entries if e.filename == "reporte_financiero_01_2026.pdf")
    assert pdf_entry.extension == ".pdf"
    assert pdf_entry.size_bytes > 0
    assert pdf_entry.is_dir is False


def test_inspect_tar_archive_bytes():
    tar_bytes = create_sample_tar_bytes()
    entries = ArchiveExtractor.inspect_archive_bytes(tar_bytes, filename_hint="datos.tar")

    assert len(entries) == 1
    assert entries[0].filename == "datos_2026.csv"
    assert entries[0].extension == ".csv"
    assert entries[0].size_bytes > 0


def test_archive_zip_rejects_unsafe_members_and_honors_declared_limits():
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('../escape.pdf', b'unsafe')
        zf.writestr('/absolute.xlsx', b'unsafe')
        zf.writestr('documents/report.pdf', b'%PDF')
        zf.writestr('documents/sheet.xlsx', b'Excel')
    members = ArchiveExtractor.inspect_archive_bytes(payload.getvalue(), 'data.zip')
    assert [member.filename for member in members] == ['documents/report.pdf', 'documents/sheet.xlsx']
    with pytest.raises(ValueError, match='ARCHIVE_INSPECTION_LIMIT'):
        ArchiveExtractor.inspect_archive_bytes(payload.getvalue(), 'data.zip', max_entries=1)
    with pytest.raises(ValueError, match='ARCHIVE_INSPECTION_LIMIT'):
        ArchiveExtractor.inspect_archive_bytes(payload.getvalue(), 'data.zip', max_uncompressed_bytes=3)


def test_archive_tar_gz_is_supported_with_decompression_limit():
    import gzip
    raw = create_sample_tar_bytes()
    compressed = gzip.compress(raw)
    assert ArchiveExtractor.can_inspect('https://example.test/archivo.tar.gz?sig=123')
    members = ArchiveExtractor.inspect_archive_bytes(compressed, 'archivo.tar.gz')
    assert [m.filename for m in members] == ['datos_2026.csv']
    with pytest.raises(ValueError, match='ARCHIVE_INSPECTION_LIMIT'):
        ArchiveExtractor.inspect_archive_bytes(
            compressed, 'archivo.tar.gz', max_uncompressed_bytes=1024,
        )


def test_archive_bad_zip_is_controlled():
    with pytest.raises(ValueError, match='INVALID_ARCHIVE'):
        ArchiveExtractor.inspect_archive_bytes(b'bad zip data', 'reports.zip')


def test_archive_allows_harmless_dot_prefix_from_common_tar_tools():
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w') as tf:
        data = b'csv'
        info = tarfile.TarInfo('./datos/2026.csv')
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    result = ArchiveExtractor.inspect_archive_bytes(buf.getvalue(), 'data.tar')
    assert [member.filename for member in result] == ['datos/2026.csv']
