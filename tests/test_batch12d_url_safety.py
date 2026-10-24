"""BATCH 12D: DATAX never exports relative/whitespace URLs as downloads."""

from prospector_externo.application.legacy_stats_exporter import LegacyStatsJsonExporter
from prospector_externo.domain.models import ResourceCandidate
from prospector_externo.domain.projection import ProjectionBuilder


def projection(resources):
    return ProjectionBuilder.build(
        source_id="fmi", source_name="FMI", entrypoint="https://data.imf.org/en/",
        run_id="12d", resources_hash="test", resources=resources,
    )


def resource(key, url, raw):
    return ResourceCandidate(
        resource_key=key, source_id="fmi", url=url, raw_url=raw,
        title=f"WEO dataset 2026 {key}", file_extension=".xlsx",
        period_label="2026", discovered_from_url="https://data.imf.org/en/",
    )


def records(doc):
    output = []
    def walk(node):
        for key, value in node.items():
            if key.startswith("Descargar") and isinstance(value, dict):
                output.append(value)
            elif isinstance(value, dict):
                walk(value)
    walk(doc["ESTADISTICAS"])
    return output


def test_relative_raw_href_exports_absolute_normalized_url():
    canonical = "https://data.imf.org/-/media/iData/External-Storage/Documents/f.xlsx"
    proj = projection([resource("a", canonical, "/-/media/iData/External-Storage/Documents/f.xlsx")])
    exported = LegacyStatsJsonExporter.build(proj)
    assert exported.record_count == 1
    assert records(exported.document)[0]["url_descarga"] == canonical


def test_valid_raw_absolute_url_still_preserves_semantic_query_order():
    canonical = "https://data.imf.org/docs/f.xlsx?b=2&a=1"
    original = "https://data.imf.org/docs/f.xlsx?a=1&b=2&token=original"
    exported = LegacyStatsJsonExporter.build(projection([resource("a", canonical, original)]))
    assert records(exported.document)[0]["url_descarga"] == original


def test_invalid_raw_whitespace_uses_valid_canonical_url():
    canonical = "https://data.imf.org/docs/f%20name.xlsx"
    raw = "https://data.imf.org/docs/f name.xlsx"
    exported = LegacyStatsJsonExporter.build(projection([resource("a", canonical, raw)]))
    assert records(exported.document)[0]["url_descarga"] == canonical


def test_historical_path_spaces_are_encoded_without_mutating_raw_or_key():
    invalid = resource("bad", "https://data.imf.org/docs/External Storage/a.xlsx", "../a.xlsx")
    valid = resource("good", "https://data.imf.org/docs/good.xlsx", "../good.xlsx")
    proj = projection([invalid, valid])
    assert proj.total_raw_resources == 2
    assert proj.total_selected_resources == 2
    reasons = {decision.resource_key: decision.reason_codes for decision in proj.decisions}
    assert "download_path_encoded" in reasons["bad"]
    assert invalid.url == "https://data.imf.org/docs/External Storage/a.xlsx"
    assert invalid.raw_url == "../a.xlsx"
    recovered = next(r for f in proj.families for p in f.periods for r in p.representations if r.resource_key == "bad")
    assert recovered.url == "https://data.imf.org/docs/External%20Storage/a.xlsx"
    assert recovered.raw_url == "../a.xlsx"
    exported = LegacyStatsJsonExporter.build(proj)
    assert exported.record_count == 2
    assert {r["url_descarga"] for r in records(exported.document)} == {recovered.url, valid.url}


def test_relative_normalized_url_never_becomes_download():
    bad = resource("bad", "/docs/file.xlsx", "/docs/file.xlsx")
    proj = projection([bad])
    assert proj.total_raw_resources == 1
    assert proj.total_selected_resources == 0
    assert LegacyStatsJsonExporter.build(proj).record_count == 0
