import io
import sys
import json
from docling.datamodel.base_models import DocumentStream, InputFormat, ConversionStatus
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption


options=PdfPipelineOptions(artifacts_path="/models")
converter=DocumentConverter(allowed_formats=[InputFormat.PDF],format_options={
    InputFormat.PDF:PdfFormatOption(pipeline_options=options)})

def parse(data):
    if len(data)>16*1024**2:
        raise ValueError("oversized input")
    r=converter.convert(DocumentStream(name="source.pdf",stream=io.BytesIO(data)),max_num_pages=250)
    if r.status!=ConversionStatus.SUCCESS:
        raise ValueError("incomplete provenance")
    blocks=[]
    heading=None
    for item,_ in r.document.iterate_items():
        label=item.label.value
        if label=="section_header":
            heading=item.text
        if label=="table":
            text=item.export_to_markdown(doc=r.document)
        else:
            text=getattr(item,"text","")
        if not text.strip():
            continue
        if len(item.prov)!=1:
            raise ValueError("incomplete provenance")
        blocks.append(dict(ref=item.self_ref,page=item.prov[0].page_no,heading=heading,
                           kind=label,text=text,bbox=item.prov[0].bbox.model_dump(mode="json")))
    if {b["page"] for b in blocks}!=set(r.document.pages):
        raise ValueError("incomplete provenance")
    return blocks

if __name__=="__main__":
    json.dump(parse(sys.stdin.buffer.read(16*1024**2+1)),sys.stdout)
