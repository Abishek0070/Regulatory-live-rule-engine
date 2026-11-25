import io
import json
import re
import sys
from statistics import median


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
            or line.get("kind","text") != "text"
            or previous.get("kind","text") != "text"
            or abs(line["x0"]-previous["x0"]) > 18
            or re.match(r"^\d+(?:\.\d+)+\s",text)
            or NUMBERED_PARAGRAPH.match(text)
            or line["top"] - previous["bottom"] > max(5, previous["bottom"] - previous["top"])
            or len(blocks[-1]["text"]) + len(text) > 2400
        )
        if begins_paragraph:
            blocks.append(dict(ref=f"#/texts/{first_index + len(blocks)}", page=page,
                               heading=None, kind=line.get("kind","text"), text=text, bbox=bounds))
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
            lines = page.extract_text_lines(return_chars=True)
            tables = [(t.bbox, t.rows[0].bbox[3]) for t in page.find_tables() if t.rows]
            typical = median([float(c["size"]) for c in page.chars] or [10])
            for line in lines:
                chars = line.pop("chars", [])
                height = median([float(c["size"]) for c in chars] or [typical])
                for (left, top, right, bottom), header_bottom in tables:
                    if top <= (line["top"]+line["bottom"])/2 <= bottom and line["x0"] < right and line["x1"] > left:
                        line["kind"] = "table_header" if line["top"] < header_bottom else "table_row"
                        break
                else:
                    if height > typical*1.15 or chars and all("bold" in c["fontname"].lower() for c in chars):
                        line["kind"] = "heading"
                    elif height < typical*.85 and line["top"] > page.height*.7:
                        line["kind"] = "footnote"
            blocks.extend(page_blocks(lines,
                                     page.page_number, len(blocks)))
            page.close()
    return blocks


if __name__ == "__main__":
    json.dump(parse(sys.stdin.buffer.read(16 * 1024 * 1024 + 1)), sys.stdout)
