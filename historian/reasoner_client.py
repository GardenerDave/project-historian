"""One-query, journaled local HTTP reasoner client."""
from __future__ import annotations
import argparse,hashlib,json,os,time,urllib.request
from pathlib import Path
from historian.retrieval import load_documents
from historian.structured_librarian import materialize_frozen_retrieval_result,reasoner_view,reasoning_request,structured_index,validate_answer
from historian.grounded_schema import SCHEMA,schema_for_record_ids,validate_schema
MODEL='Qwen3-Coder-30B-A3B-Instruct-Q4_K_M.gguf'
ROOT = Path(__file__).resolve().parents[1]
def durable(path,data):
    tmp=path.with_suffix(path.suffix+'.tmp'); f=open(tmp,'wb'); f.write((json.dumps(data,indent=2,ensure_ascii=False)+'\n').encode()); f.flush(); os.fsync(f.fileno()); f.close(); os.replace(tmp,path)
def durable_bytes(path,data):
    tmp=path.with_suffix(path.suffix+'.tmp'); f=open(tmp,'wb'); f.write(data); f.flush(); os.fsync(f.fileno()); f.close(); os.replace(tmp,path)
def run(query_id,run_dir,endpoint,retrieval_results, max_tokens=1536):
    run_dir=Path(run_dir); run_dir.mkdir(parents=True,exist_ok=True); journal=run_dir/f'{query_id}.transaction.json'
    if any((run_dir/f'{query_id}{s}').exists() for s in ('.transaction.json','.request.json','.raw-http','.response.json','.content.json','.result.json')): raise RuntimeError('prior query artifact exists; refusing duplicate invocation')
    src=json.load(open(retrieval_results)); matches=[x for x in src.get('queries',[]) if x.get('id')==query_id]
    if len(matches)!=1: raise ValueError('query must occur exactly once')
    q=matches[0]
    if q is None: raise ValueError('query not found')
    docs=load_documents(ROOT/'interfaces/khoj/corpus'); nodes,_,_=structured_index(ROOT/'records'); b=materialize_frozen_retrieval_result(q,docs,nodes); v=reasoner_view(b); req=reasoning_request(q['question'],v); payload={'model':MODEL,'messages':[{'role':'system','content':req['system']},{'role':'user','content':req['user']}],'temperature':0,'top_p':1,'seed':42,'max_tokens':max_tokens,'response_format':{'type':'json_schema','json_schema':{'name':'grounded_answer','schema':schema_for_record_ids([x['record_id'] for x in v['records']])}},'stream':False}; raw=json.dumps(payload,sort_keys=True).encode(); durable_bytes(run_dir/f'{query_id}.request.json',raw); state={'query_id':query_id,'state':'PREPARED','request_sha256':hashlib.sha256(raw).hexdigest(),'request_present':True,'retrieval_results_sha256':hashlib.sha256(Path(retrieval_results).read_bytes()).hexdigest(),'evidence_bundle_sha256':hashlib.sha256(json.dumps(v,sort_keys=True).encode()).hexdigest(),'reasoner_view_sha256':hashlib.sha256(json.dumps(v,sort_keys=True).encode()).hexdigest(),'attempt_number':1,'response_present':False}; durable(journal,state); state['state']='REQUEST_PRESERVED'; durable(journal,state); state['state']='REQUEST_STARTING'; durable(journal,state); started=time.time();
    try:
        r=urllib.request.urlopen(urllib.request.Request(endpoint.rstrip('/')+'/chat/completions',data=raw,headers={'Content-Type':'application/json'}),timeout=120); raw_http=r.read(); state.update({'transport_status':f'http_{r.status}','elapsed_seconds':round(time.time()-started,3),'raw_http_sha256':hashlib.sha256(raw_http).hexdigest(),'response_present':True}); durable_bytes(run_dir/f'{query_id}.raw-http',raw_http); state['state']='RAW_RESPONSE_PRESERVED'; durable(journal,state); envelope=json.loads(raw_http); state['state']='RESPONSE_RECEIVED'; durable(journal,state); text=envelope.get('choices',[{}])[0].get('message',{}).get('content',''); durable(run_dir/f'{query_id}.response.json',{'envelope':envelope,'content':text}); durable(run_dir/f'{query_id}.content.json',{'content':text}); state['state']='RESPONSE_PRESERVED'; durable(journal,state)
        try:
            parsed=json.loads(text); sv=validate_schema(parsed); gv=validate_answer(parsed,v); validation={'schema_valid':sv,'grounding_valid':gv,'contract_valid':sv['valid'] and gv['valid']}
        except Exception: parsed=None; validation={'schema_valid':{'valid':False,'errors':['response was not valid JSON']},'grounding_valid':{'valid':False,'errors':['response was not valid JSON']},'contract_valid':False}
        durable(run_dir/f'{query_id}.result.json',{'query_id':query_id,'question':q['question'],'parsed_response':parsed,'validation':validation}); state['state']='COMPLETE'; durable(journal,state)
    except Exception as exc:
        state.update({'state':'SEND_STATE_AMBIGUOUS','transport_status':'ambiguous','error_type':type(exc).__name__}); durable(journal,state)
def main():
    p=argparse.ArgumentParser(); p.add_argument('--query-id',required=True); p.add_argument('--run-dir',required=True); p.add_argument('--retrieval-results',required=True); p.add_argument('--max-tokens',type=int,default=1536); p.add_argument('--endpoint',default=os.environ.get('HISTORIAN_REASONER_ENDPOINT')); a=p.parse_args();
    if not a.endpoint: raise SystemExit('HISTORIAN_REASONER_ENDPOINT is required')
    run(a.query_id,a.run_dir,a.endpoint,a.retrieval_results,a.max_tokens)
if __name__=='__main__': main()
