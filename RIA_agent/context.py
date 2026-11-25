"""Source-backed document structure and budgeted extraction units."""
import hashlib
import json
import re
from functools import lru_cache

CLAUSE = re.compile(r"^\s*(\d+(?:\.\d+)*[A-Z]?)(?:[.)]|\s)\s*")
NESTED = re.compile(r"^\s*\(([a-z0-9]+)\)", re.I)
LINK = re.compile(r"\b(clause|paragraph|section|annexure|annex)\s+([A-Z\d]+(?:\.\d+)*)\b", re.I)
MANDATE = re.compile(r"\b(shall|must|required|mandatory|prohibited|ensure|directed to)\b", re.I)
EXCEPTION = re.compile(r"\b(provided that|except|unless|notwithstanding|exempt)\b", re.I)
FACTS = {
    "reference": r"(?:RBI|SEBI)/[\w/-]+",
    "publication_date": r"\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w* \d{1,2},? \d{4})",
    "effective_dates": r"effective|come into force|with effect",
    "entities": r"applicable to|applies to|shall apply",
    "definitions": r"\b(means|defined as|definition)\b",
    "conditions": r"\b(subject to|where|if)\b",
    "exceptions": EXCEPTION.pattern,
    "regulations": r"\b(regulations?|directions?|act),?\s+(?:19|20)\d\d",
}


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


@lru_cache(maxsize=1)
def tokenizer():
    import os
    path = os.getenv("LLM_TOKENIZER_FILE")
    if path:
        from tokenizers import Tokenizer
        return Tokenizer.from_file(path)
    return None


def size(value):
    # One token per UTF-8 byte is conservative for byte-based model tokenizers.
    codec = tokenizer()
    return len(codec.encode(encode(value)).ids) if codec else len(encode(value).encode())


def reconstruct(blocks):
    nodes, labels, facts = [], {}, {}
    group = heading = table_header = None
    for i, block in enumerate(blocks):
        text = block["text"]
        clause = CLAUSE.match(text)
        annex = re.match(r"^(annex(?:ure)?)\s+([\w]+)", text, re.I)
        title = block.get("kind") == "heading" or text.isupper() and len(text) < 120
        if title or annex:
            heading = i
        if clause or annex or title or group is None:
            group = i
        label = annex[2] if annex else clause[1] if clause else None
        if label:
            labels.setdefault(("annex" if annex else "clause", label.lower()), []).append(i)
        for key, pattern in FACTS.items():
            if re.search(pattern, text, re.I):
                facts.setdefault(key, []).append(i)
        if block.get("kind") == "table_header":
            table_header = i
        elif block.get("kind") != "table_row":
            table_header = None
        nodes.append(dict(table_header=table_header, group=group, heading=heading, label=label,
                          subclause=NESTED.match(text)[1] if NESTED.match(text) else None,
                          links=[], unresolved=[], uncertain=not label and heading is None and group == i))
    if nodes and nodes[0]["heading"] == 0:
        facts["title"] = [0]
    for i, block in enumerate(blocks):
        for kind, label in LINK.findall(block["text"]):
            hits = labels.get(("annex" if kind.lower().startswith("annex") else "clause", label.lower()), [])
            if len(hits) == 1:
                nodes[i]["links"].append(hits[0])
            else:
                nodes[i]["unresolved"].append(f"{kind} {label}")
    return nodes, facts


def plan(blocks, digest, budget):
    nodes, facts = reconstruct(blocks)
    def entry(i, target, start=0, end=None):
        n = nodes[i]
        return dict(index=i, target=target, start=start, text=blocks[i]["text"][start:end],
                    kind=blocks[i].get("kind","text"), group=n["group"], parent_heading=n["heading"],
                    subclause=n["subclause"], references=n["links"])
    def pack(targets):
        ids = {e["index"] for e in targets}
        context = []
        for i in sorted(ids):
            n = nodes[i]
            context.extend([*n["links"], n["table_header"], n["heading"], n["group"]])
            context.extend(j for j, other in enumerate(nodes) if i in other["links"])
            context.extend([i-1, i+1])
        for refs in facts.values():
            context.extend(refs)
        entries = targets.copy()
        for i in dict.fromkeys(context):
            if i is not None and 0 <= i < len(nodes) and i not in ids:
                candidate = entry(i, False)
                if size(entries+[candidate]) <= budget:
                    entries.append(candidate)
        return dict(id=hashlib.sha256((digest+encode(entries)).encode()).hexdigest(), entries=entries)
    full = [entry(i, True) for i in range(len(blocks))]
    if size(full) <= budget:
        return nodes,facts,[pack(full)]
    target_budget = max(200,budget*2//3)
    chunks, pending = [], []
    for i, block in enumerate(blocks):
        e = entry(i, True)
        if pending and size(pending+[e]) > target_budget:
            # Move the unfinished clause to the next chunk if it fits there.
            cut = next((k for k,p in enumerate(pending) if nodes[p["index"]]["group"] == nodes[i]["group"]), len(pending))
            if cut and size(pending[cut:]+[e]) <= target_budget:
                chunks.append(pack(pending[:cut]))
                pending = pending[cut:]
            else:
                chunks.append(pack(pending))
                pending = []
        if size([e]) <= target_budget:
            pending.append(e)
            continue
        start = 0
        while start < len(block["text"]):
            lo, hi = start, len(block["text"])
            while lo < hi:
                mid = (lo+hi+1)//2
                if size([entry(i, True, start, mid)]) <= target_budget:
                    lo = mid
                else:
                    hi = mid-1
            if lo == start:
                raise ValueError("Insufficient chunk budget")
            chunks.append(pack([entry(i, True, start, lo)]))
            if lo == len(block["text"]):
                break
            start = lo - min(64, (lo-start)//4)
    if pending:
        chunks.append(pack(pending))
    return nodes, facts, chunks
