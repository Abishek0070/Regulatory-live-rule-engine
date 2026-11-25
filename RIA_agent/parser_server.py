import asyncio
import json
import os
import signal
import sys
from fastapi import FastAPI, Request, HTTPException

app=FastAPI()
slot=asyncio.Lock()

@app.post("/parse")
async def parse(request:Request):
    if slot.locked():
        raise HTTPException(503)
    async with slot:
        data=bytearray()
        async for chunk in request.stream():
            if len(data)+len(chunk)>16*1024**2:
                raise HTTPException(413)
            data.extend(chunk)
        proc=await asyncio.create_subprocess_exec(sys.executable,"-m","agent.pdfplumber_parser",
            stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,start_new_session=True)
        try:
            output,_=await asyncio.wait_for(proc.communicate(data),timeout=160)
            if proc.returncode or len(output)>800000:
                raise HTTPException(422)
            return json.loads(output)
        finally:
            try:
                os.killpg(proc.pid,signal.SIGKILL)
            except ProcessLookupError:
                pass
            await proc.wait()
