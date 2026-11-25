import io
import json
import re
import sys


NUMBERED_PARAGRAPH = re.compile(r"^[“\"]?(?:\d+[A-Z]?\.|\((?:\d+|[a-z]+)\))\s", re.I)


def page_blocks(lines, page, first_index):
    blocks = []
    previous = None
    for line in lines:
        text = " ".join(line["text"].split())
        if not text:
            continue
        bounds = dict(l=line["x0"], t=line["top"], r=line["x1"], b=line["bottom"],
                      coord_origin="TOPLEFT")
        begins_paragraph = (
            previous is None
            or NUMBERED_PARAGRAPH.match(text)
            or line["top"] - previous["bottom"] > max(5, previous["bottom"] - previous["top"])
            or len(blocks[-1]["text"]) + len(text) > 2400
        )
        if begins_paragraph:
            blocks.append(dict(ref=f"#/texts/{first_index + len(blocks)}", page=page,
                               heading=None, kind="text", text=text, bbox=bounds))
        else:
            current = blocks[-1]
            current["text"] += " " + text
            box = current["bbox"]
            box.update(l=min(box["l"], bounds["l"]), t=min(box["t"], bounds["t"]),
                       r=max(box["r"], bounds["r"]), b=max(box["b"], bounds["b"]))
        previous = line
    if not blocks:
        raise ValueError(f"Page {page} has no extractable text")
    return blocks


def parse(data):
    import pdfplumber

    if len(data) > 16 * 1024 * 1024:
        raise ValueError("Oversized PDF")
    blocks = []
    with pdfplumber.open(io.BytesIO(data)) as document:
        if not 0 < len(document.pages) <= 250:
            raise ValueError("Unsupported page count")
        for page in document.pages:
            blocks.extend(page_blocks(page.extract_text_lines(return_chars=False),
                                     page.page_number, len(blocks)))
            page.close()
    return blocks


if __name__ == "__main__":
    json.dump(parse(sys.stdin.buffer.read(16 * 1024 * 1024 + 1)), sys.stdout)
