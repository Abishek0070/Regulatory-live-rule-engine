import json

import pytest

from agent.context import plan, size
from agent.clause_extraction import NeedsReview, run


def fixture(*texts):
    return [dict(ref=f"#/texts/{i}", page=i+1, text=t) for i,t in enumerate(texts)]


def test_plan_keeps_nested_and_continuing_paragraphs_and_links_annex():
    blocks = fixture("1. Banks shall comply with Annexure A.", "(a) Limits are", "calculated daily.",
                     "Annexure A", "Threshold: 500")
    nodes,facts,chunks = plan(blocks,"digest",3000)
    assert nodes[0]["links"] == [3]
    assert nodes[1]["subclause"] == "a"
    assert nodes[2]["group"] == nodes[0]["group"]
    assert len(chunks) == 1


def test_oversize_units_cover_every_character_with_stable_ids():
    blocks = fixture("12345 " * 500)
    _,_,chunks = plan(blocks,"digest",500)
    assert chunks == plan(blocks,"digest",500)[2]
    recovered = set()
    for c in chunks:
        assert size(c["entries"]) <= 500
        for e in c["entries"]:
            recovered.update(range(e["start"],e["start"]+len(e["text"])))
    assert recovered == set(range(len(blocks[0]["text"])))


def exercise(fault=None):
    blocks = fixture("1. Banks must retain 50 records.", "Except for closed accounts.")
    calls, cache = [], {}
    def checkpoint(k,v):
        if v is not None:
            cache.setdefault(k,v)
        return cache.get(k)
    def model(prompt, entries, limit):
        if isinstance(entries,dict):
            return json.dumps(dict(missed_or_unsupported=False,affected=[]))
        calls.append(entries)
        if fault == "truncated":
            return '{"picks":'
        quotes = [dict(index=i,quote=b["text"]) for i,b in enumerate(blocks)]
        if fault == "invented":
            quotes[0]["quote"] = "nonexistent"
        pick = dict(index=0,statement="Retain 50 records except for closed accounts.",
                    area="recordkeeping",evidence=quotes[:1] if fault == "unlinked" else quotes)
        coverage = [dict(index=0,kind="mandatory"),dict(index=1,kind="exception")]
        return json.dumps(dict(picks=[pick,pick],coverage=coverage[:1] if fault == "missing" else coverage,
                               ambiguous=False))
    result = run(blocks,"hash","RBI","test",model,checkpoint)
    assert run(blocks,"hash","RBI","test",model,checkpoint) == result
    assert len(calls) == 1
    return result


def test_multiple_evidence_deduplication_and_checkpoint_replay():
    obligation, = exercise()["obligations"]
    assert obligation["citation"] == obligation["citations"][0]
    assert [c["page"] for c in obligation["citations"]] == [1,2]


@pytest.mark.parametrize("fault", ["invented","truncated","missing","unlinked"])
def test_incomplete_extraction_requires_review(fault):
    with pytest.raises(NeedsReview):
        exercise(fault)



def test_separate_exception_is_present_as_context_across_chunks():
    blocks = fixture("1. Banks shall maintain records subject to clause 9.",
                     *[f"{i}. Administrative details " + "ordinary text " * 30 for i in range(2,9)],
                     "9. Except for closed accounts, clause 1 applies.")
    _,_,chunks = plan(blocks, "hash", 1000)
    first = chunks[0]["entries"]
    assert first[0]["target"] and first[0]["index"] == 0
    assert any(e["index"] == 8 and not e["target"] for e in first)


def test_table_header_and_threshold_are_grounded_in_separate_spans():
    blocks = fixture("Minimum balance (INR)", "Banks must maintain 500.")
    blocks[0]["kind"],blocks[1]["kind"] = "table_header","table_row"
    def model(prompt, data, limit):
        if isinstance(data,dict):
            return json.dumps(dict(missed_or_unsupported=False,affected=[]))
        assert data[0]["kind"] == "table_header"
        return json.dumps(dict(ambiguous=False,
            coverage=[dict(index=0,kind="heading"),dict(index=1,kind="mandatory")],
            picks=[dict(index=1,area="capital_liquidity",statement="Maintain INR 500.",
                        evidence=[dict(index=i,quote=blocks[i]["text"]) for i in (1,0)])]))
    result = run(blocks,"digest","RBI","test",model,lambda *_:None)
    assert len(result["obligations"][0]["citations"]) == 2
