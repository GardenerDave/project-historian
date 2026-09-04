import json
SCHEMA={"type":"object","required":["answer","cited_record_ids","evidence_used","uncertainty_or_limitations","contradictions_or_missing_evidence"],"additionalProperties":False,"properties":{"answer":{"type":"string"},"cited_record_ids":{"type":"array","items":{"type":"string"}},"evidence_used":{"type":"array","items":{"type":"string"}},"uncertainty_or_limitations":{"type":"string"},"contradictions_or_missing_evidence":{"type":"array","items":{"type":"string"}}}}
def schema_for_record_ids(record_ids):
    ids=sorted(set(record_ids))
    if len(ids)!=len(record_ids): raise ValueError('duplicate supplied record ID')
    s=json.loads(json.dumps(SCHEMA))
    for k in ('cited_record_ids','evidence_used'): s['properties'][k]['items']={'type':'string','enum':ids}
    return s
def validate_schema(x):
    if not isinstance(x,dict) or set(x)!=set(SCHEMA['required']): return {'valid':False,'errors':['exact top-level schema mismatch']}
    errs=[]
    for k,t in [('answer',str),('uncertainty_or_limitations',str)]:
        if type(x[k]) is not t: errs.append(k+' must be string')
    for k in ('cited_record_ids','evidence_used','contradictions_or_missing_evidence'):
        if type(x[k]) is not list or any(type(v) is not str for v in x[k]): errs.append(k+' must be list[str]')
    return {'valid':not errs,'errors':errs}
