import hashlib
import ipaddress
import os
import socket
import ssl
import struct
from datetime import datetime, timezone
from urllib.parse import urlsplit
import urllib3

LIMIT = 16 * 1024 * 1024
HOSTS = {"RBI": {"rbi.org.in","www.rbi.org.in","rbidocs.rbi.org.in"},
         "SEBI": {"sebi.gov.in","www.sebi.gov.in"}}

def target(regulator, url):
    u = urlsplit(url)
    if (u.scheme != "https" or u.hostname not in HOSTS[regulator] or u.username is not None
            or u.port not in (None,443) or u.fragment or "\\" in url or any(ord(c)<33 for c in url)):
        raise ValueError("OFFICIAL_SOURCE_UNVERIFIED")
    return u

def retrieve(regulator, url):
    u = target(regulator,url)
    addresses = [a[4][0] for a in socket.getaddrinfo(u.hostname,443,type=socket.SOCK_STREAM)]
    if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
        raise ValueError("UNSAFE_ADDRESS")
    with urllib3.HTTPSConnectionPool(addresses[0],443,server_hostname=u.hostname,
            assert_hostname=u.hostname,ssl_context=ssl.create_default_context()) as pool:
        with pool.urlopen("GET",u.path + ("?"+u.query if u.query else ""),
                headers={"Host":u.hostname},redirect=False,retries=False,preload_content=False,
                timeout=urllib3.Timeout(connect=5,read=15)) as response:
            if response.status != 200:
                raise ValueError("OFFICIAL_SOURCE_UNVERIFIED")
            return response.read(LIMIT+1,decode_content=False)

def verify(data, upload, expected):
    digest = hashlib.sha256(data).hexdigest()
    if len(data)>LIMIT or not data.startswith(b"%PDF-"):
        raise ValueError("UNSUPPORTED_DOCUMENT")
    if (upload is not None and data != upload) or (expected is not None and digest != expected):
        raise ValueError("HASH_MISMATCH")
    return digest

def verdict(reply):
    if reply != b"stream: OK":
        raise ValueError("MALWARE_DETECTED" if reply.endswith(b" FOUND") else "SCAN_INCONCLUSIVE")

def command(cmd, data=None):
    with socket.create_connection((os.environ.get("CLAMAV_HOST", "clamav"), 3310), timeout=30) as s:
        s.sendall(b"z"+cmd+b"\0")
        if data is not None:
            for i in range(0,len(data),65536):
                chunk=data[i:i+65536]
                s.sendall(struct.pack(">I",len(chunk))+chunk)
            s.sendall(bytes(4))
        reply=bytearray()
        while len(reply)<1024:
            b=s.recv(1)
            if b==b"\0":
                return bytes(reply)
            if not b:
                break
            reply.extend(b)
        raise ValueError("SCAN_INCONCLUSIVE")

def scan(data):
    version=command(b"VERSION").decode("ascii")
    stamp=datetime.strptime(version.rsplit("/",1)[1],"%a %b %d %H:%M:%S %Y").replace(tzinfo=timezone.utc)
    if not 0 <= (datetime.now(timezone.utc)-stamp).total_seconds() <= 172800:
        raise ValueError("SCAN_INCONCLUSIVE")
    verdict(command(b"INSTREAM",data))
    return version
