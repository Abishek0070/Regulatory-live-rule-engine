import hashlib
import pytest
from agent.security import target, verify, verdict

@pytest.mark.parametrize("url", ["http://rbi.org.in/x","https://rbi.org.in.example/x","https://user@rbi.org.in/x","file:///tmp/a","https://rbi.org.in:444/a"])
def test_bad_target(url):
    with pytest.raises(ValueError):
        target("RBI",url)

def test_integrity():
    data=b"%PDF-1.7"
    assert verify(data,data,None)==hashlib.sha256(data).hexdigest()
    with pytest.raises(ValueError):
        verify(data,b"",None)

@pytest.mark.parametrize("reply",[b"",b"stream: ERROR",b"stream: threat FOUND"])
def test_scan_closed(reply):
    with pytest.raises(ValueError):
        verdict(reply)
