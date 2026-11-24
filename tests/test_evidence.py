import pytest
from agent.evidence import select, Pick

def test_only_source_quote():
    block={"page":1,"ref":"#/texts/2","text":"Banks shall report.","heading":"Returns"}
    p=Pick(index=0,quote=block["text"],area="reporting")
    a=select([block],[p],"a"*64)
    assert a==select([block],[p],"a"*64)
    with pytest.raises(ValueError):
        select([block],[p.model_copy(update={"quote":"Invented deadline"})],"a"*64)
