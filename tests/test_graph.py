from uuid import uuid4
import pytest
from agent import service

def test_failed_scan_never_reaches_parser(monkeypatch):
    stages=[]
    monkeypatch.setattr(service,"event",lambda _,stage:stages.append(stage))
    monkeypatch.setattr(service.security,"retrieve",lambda *_:b"%PDF-1.7")
    def deny(_):
        raise ValueError("SCAN_INCONCLUSIVE")
    monkeypatch.setattr(service.security,"scan",deny)
    with pytest.raises(ValueError,match="SCAN_INCONCLUSIVE"):
        service.pipeline.invoke({"job":uuid4(),"source":service.Source(regulator="RBI",source_url="https://rbi.org.in/a.pdf"),"upload":None})
    assert stages==["verification","scan"]
