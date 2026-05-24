from __future__ import annotations

from typing import TYPE_CHECKING

import fitz

from pdf_search_downloader_ui.auto_mode import (
    AutoRecord,
    build_auto_parser,
    build_series_queries,
    validate_pdf_path,
    write_auto_reports,
)

if TYPE_CHECKING:
    from pathlib import Path


def _write_pdf(path: Path, text: str) -> None:
    doc = fitz.open()
    try:
        page = doc.new_page()
        page.insert_textbox(fitz.Rect(72, 72, 520, 300), text)
        doc.save(path)
    finally:
        doc.close()


def test_build_series_queries_keeps_default_profile_and_deduplicates() -> None:
    queries = build_series_queries(
        (
            "radici dell'unita numeri complessi esercizi pdf",
            "  query extra   numeri complessi  ",
        )
    )

    assert "query extra numeri complessi" in queries
    assert queries.count("radici dell'unita numeri complessi esercizi pdf") == 1
    assert queries[0].startswith("numeri complessi piano di Gauss")


def test_validate_pdf_path_marks_relevant_complex_numbers_pdf(tmp_path: Path) -> None:
    pdf_path = tmp_path / "complessi.pdf"
    _write_pdf(
        pdf_path,
        (
            "Numeri complessi nel piano di Gauss. Parte reale, parte immaginaria, "
            "coniugato, modulo e argomento. Forma trigonometrica e radici "
            "n-esime dell'unita."
        ),
    )

    result = validate_pdf_path(pdf_path, min_score=6)

    assert result.conformant is True
    assert result.score >= 6
    assert "algebra" in result.matched_categories


def test_validate_pdf_path_rejects_unrelated_pdf(tmp_path: Path) -> None:
    pdf_path = tmp_path / "unrelated.pdf"
    _write_pdf(pdf_path, "Storia medievale e geografia politica europea.")

    result = validate_pdf_path(pdf_path, min_score=6)

    assert result.conformant is False
    assert result.score == 0


def test_write_auto_reports_creates_csv_and_json(tmp_path: Path) -> None:
    record = AutoRecord(
        query="numeri complessi",
        provider="bing",
        title="Dispensa",
        source_url="https://example.com",
        final_url="https://example.com/a.pdf",
        output_path=str(tmp_path / "a.pdf"),
        outcome="downloaded",
        validation_score=8,
        conformant=True,
        matched_categories="algebra",
        matched_terms="numero complesso",
        message="ok",
    )

    write_auto_reports([record], tmp_path / "report")

    assert (tmp_path / "report" / "auto_results.json").is_file()
    assert (tmp_path / "report" / "auto_results.csv").is_file()


def test_auto_parser_rejects_bing_provider() -> None:
    parser = build_auto_parser()

    try:
        parser.parse_args(["--provider", "bing"])
    except SystemExit as exc:
        assert exc.code != 0
    else:  # pragma: no cover - argparse should exit
        raise AssertionError("bing provider should be rejected in auto mode")
