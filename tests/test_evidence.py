import pytest
from RIA_agent.evidence import select, Pick, Selection

def test_model_cannot_supply_a_string_ambiguity_flag():
    with pytest.raises(ValueError):
        Selection.model_validate_json('{"picks":[],"ambiguous":"false"}')

def test_only_source_quote():
    block={"page":1,"ref":"#/texts/2","text":"Banks shall report.","heading":"Returns"}
    p=Pick(index=0,quote=block["text"],area="reporting")
    a=select([block],[p],"a"*64)
    assert a==select([block],[p],"a"*64)
    assert a[0]["citation"]["document_sha256"]=="a"*64
    revised=select([block],[p],"b"*64)
    assert revised[0]["obligation_id"]!=a[0]["obligation_id"]
    assert revised[0]["citation"]["document_sha256"]=="b"*64
    with pytest.raises(ValueError):
        select([block],[p.model_copy(update={"quote":"Invented deadline"})],"a"*64)
