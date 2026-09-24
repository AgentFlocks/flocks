"""Real loopback HTTP, synthetic AK/SK, independent official signed-byte oracle."""
import asyncio
import hashlib
import hmac
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote
import aiohttp
from aiohttp import web
import pytest

PLUGIN=Path(__file__).resolve().parents[2]/'.flocks/plugins/tools/device/sangfor_xdr_v2_2'
KEY='offline-only-fixture-sk'

def load_handler(subdir):
    spec=importlib.util.spec_from_file_location('wire_'+subdir,PLUGIN/'sangfor_xdr.handler.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def official_body_digest(raw):
    # Official Go payloadTransform converts to int8, sorts, converts back to byte.
    # Official Java getPayload uses Arrays.sort(byte[]) with signed byte values.
    signed=[b if b<128 else b-256 for b in raw if b!=32]
    return hashlib.sha256(bytes(v%256 for v in sorted(signed))).hexdigest().upper()


def reference_signature(request,raw):
    auth=request.headers['Authorization']
    marker='SignedHeaders='
    signed_names=auth.split(marker,1)[1].split(',',1)[0]
    header_text=''.join(name+':'+request.headers[name]+'\n' for name in signed_names.split(';'))
    path=request.path
    if not path.endswith('/'):path+='/'
    canonical=request.method+'\n'+quote(path)+'\n\n'+header_text+signed_names+'\n'+official_body_digest(raw)
    hashed=hashlib.sha256(canonical.encode()).hexdigest().upper()
    total='HMAC-SHA256\n'+request.headers['sign-date']+'\n'+hashed
    return hmac.new(KEY.encode(),total.encode(),hashlib.sha256).hexdigest().upper()

@pytest.mark.parametrize('body',[
 {'dealStatus':[0,10],'page':1,'pageSize':20},
 {'whiteStatus':['未加白','部分加白'],'dealStatus':[0,10]},
 {'whiteStatus':[],'dealStatus':[]},
 {'name':'中文 测试 with spaces','nested':{'text':'引号"、反斜杠\\、换行\n😀'}},
 {'flags':[False,True],'count':0,'items':[{'keyword':'北京'}]},
])
def test_body_semantics_and_official_digest(body):
    m=load_handler('plugin')
    payload=json.dumps(body,ensure_ascii=True)
    assert json.loads(payload)==body
    assert payload.isascii()
    assert m._payload_transform(payload)==official_body_digest(payload.encode())


def test_exact_chinese_regression_and_real_http_boundary():
    async def run():
        captured=[]
        async def handle(req):
            raw=await req.read();body=json.loads(raw)
            assert req.method=='POST'
            assert req.path=='/api/xdr/v1/incidents/list'
            assert req.query_string==''  # Not a URL-array/doseq issue.
            signature=req.headers['Authorization'].split('Signature=',1)[1]
            valid=hmac.compare_digest(signature,reference_signature(req,raw))
            captured.append({'valid':valid,'ascii':raw.isascii(),'body':body})
            if not valid:
                return web.json_response({'code':-1,'message':'Err - code: 101, message: Check auth failed'})
            return web.json_response({'code':'Success','data':{'total':0,'item':[]}})
        app=web.Application();app.router.add_post('/api/xdr/v1/incidents/list',handle)
        runner=web.AppRunner(app);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
        port=site._server.sockets[0].getsockname()[1]
        try:
            cfg=SimpleNamespace(base_url=f'http://127.0.0.1:{port}',timeout=5,auth_code='synthetic-only',verify_ssl=False)
            new=load_handler('plugin')
            new._decode_auth_code=lambda _:('offline-only-fixture-ak',KEY)
            plain={'action':'list','start_time':100,'end_time':200,'page_num':1,'page_size':20}
            numeric={**plain,'deal_statuses':[0,10]}
            chinese={**numeric,'white_status':['未加白','部分加白']}
            async with aiohttp.ClientSession() as session:
                for m,params,expect_valid in [(new,plain,True),(new,numeric,True),(new,chinese,True)]:
                    # Exercise public tool entry -> real preserved request signer/HTTP.
                    async def request(method,path,data=None,params=None):
                        return await m._request(cfg,session,method,path,data=data,params=params)
                    m._run_request=request
                    if expect_valid:
                        response=await m.run_incidents(SimpleNamespace(params=params))
                        assert response['code']=='Success'
                    else:
                        with pytest.raises(RuntimeError,match='Check auth failed'):
                            await m.run_incidents(SimpleNamespace(params=params))
            assert [r['valid'] for r in captured]==[True,True,True]
            assert captured[2]['ascii'] is True
            assert captured[2]['body']['whiteStatus']==['未加白','部分加白']
            assert captured[2]['body']['dealStatus']==[0,10]
        finally:await runner.cleanup()
    asyncio.run(run())
