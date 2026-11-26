import hashlib
import pytest
from RIA_agent import security
from RIA_agent.security import target, verify, verdict

def test_scanner_can_use_local_daemon(monkeypatch):
    monkeypatch.setenv("CLAMAV_HOST", "127.0.0.1")

    def connect(address, timeout):
        assert address == ("127.0.0.1", 3310)
        raise ConnectionRefusedError

    monkeypatch.setattr(security.socket, "create_connection", connect)
    with pytest.raises(ValueError, match="^SCANNER_UNAVAILABLE$"):
        security.command(b"PING")

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
