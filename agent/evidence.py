import uuid
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

class Pick(BaseModel):
    model_config=ConfigDict(extra="forbid",strict=True)
    index:int=Field(ge=0)
    quote:str=Field(min_length=1,max_length=12000)
    area:Literal["reporting","payments","customer onboarding","recordkeeping","risk controls","other"]

class Selection(BaseModel):
    model_config=ConfigDict(extra="forbid")
    picks:list[Pick]=Field(max_length=150)
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
                     "citation":{**block,"start":start,"end":start+len(p.quote),"quote":p.quote}}
    return list(result.values())
