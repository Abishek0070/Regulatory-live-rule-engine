import pytest

from agent.pdfplumber_parser import page_blocks


def line(text, top, bottom, left=12, right=120):
    return {"text": text, "top": top, "bottom": bottom, "x0": left, "x1": right}


def test_paragraphs_keep_page_coordinates_and_numbered_boundaries():
    blocks = page_blocks([
        line("26. Banks shall", 10, 20),
        line("report monthly.", 22, 32, right=150),
        line("27. Banks shall retain records.", 34, 44),
    ], page=2, first_index=3)
    assert [(block["ref"], block["page"], block["text"]) for block in blocks] == [
        ("#/texts/3", 2, "26. Banks shall report monthly."),
        ("#/texts/4", 2, "27. Banks shall retain records."),
    ]
    assert blocks[0]["bbox"] == {"l": 12, "t": 10, "r": 150, "b": 32, "coord_origin": "TOPLEFT"}


def test_page_without_text_requires_review():
    with pytest.raises(ValueError, match="no extractable text"):
        page_blocks([], page=1, first_index=0)
