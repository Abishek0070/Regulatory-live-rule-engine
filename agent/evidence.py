import uuid
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

class IndexedPick(BaseModel):
    model_config=ConfigDict(extra="forbid",strict=True)
    index:int=Field(ge=0)
    area:Literal["reporting","payments","customer onboarding","recordkeeping","risk controls","other"]

class Pick(IndexedPick):
    quote:str=Field(min_length=1,max_length=12000)

class SummarizedPick(IndexedPick):
    statement: str = Field(min_length=1, max_length=2000)

class Selection(BaseModel):
    model_config=ConfigDict(extra="forbid",strict=True)
    picks:list[Pick]=Field(max_length=150)
    ambiguous:bool

class IndexedSelection(BaseModel):
    model_config=ConfigDict(extra="forbid",strict=True)
    picks:list[SummarizedPick]=Field(max_length=150)
    ambiguous:bool

def select(blocks,picks,digest):
    result={}
    for p in picks:
        if p.index>=len(blocks):
            raise ValueError("INVALID_EVIDENCE")
        block=blocks[p.index]
        if block["text"].count(p.quote)!=1:
            raise ValueError("INVALID_EVIDENCE")
        start=block["text"].index(p.quote)
        key=str(uuid.uuid5(uuid.NAMESPACE_URL,f"{digest}/{block['ref']}/{start}/{p.quote}"))
        result[key]={"obligation_id":key,"statement":p.quote,"deadline":None,
                     "potential_change":{"area":p.area,"action":"Assess controls against cited requirement"},
                     "citation":{**block,"document_sha256":digest,"start":start,
                                 "end":start+len(p.quote),"quote":p.quote}}
    return list(result.values())


def summarize(blocks, picks, digest):
    results = {}
    for pick in picks:
        if pick.index >= len(blocks) or not pick.statement.strip():
            raise ValueError("INVALID_EVIDENCE")
        block = blocks[pick.index]
        evidence = Pick(index=pick.index, area=pick.area, quote=block["text"])
        obligation, = select(blocks, [evidence], digest)
        statement = pick.statement.strip()
        identifier = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{obligation['obligation_id']}/{statement}"))
        obligation.update(obligation_id=identifier, statement=statement)
        results[identifier] = obligation
    return list(results.values())
