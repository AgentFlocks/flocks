import asyncio
import ast
import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import shutil
import pytest
import yaml
import jsonschema

PLUGIN = Path(__file__).resolve().parents[2]/'.flocks/plugins/tools/device/sangfor_xdr_v2_2'

@pytest.fixture
def handler(monkeypatch):
    spec = importlib.util.spec_from_file_location('xdr_patch_under_test', PLUGIN/'sangfor_xdr.handler.py')
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    calls = []
    async def capture(method, path, data=None, params=None):
        calls.append((method, path, data, params))
        return m.ToolResult(success=True, output={'code':'Success','data':{'item':[], 'total':0}})
    monkeypatch.setattr(m, '_run_request', capture)
    monkeypatch.setattr(m, '_resolve_runtime_config', lambda: pytest.fail('Credentials must not be read'))
    monkeypatch.setattr(m.aiohttp, 'ClientSession', lambda *a, **kw: pytest.fail('Network forbidden'))
    return m,calls

def invoke(handler, tool, params):
    m,calls=handler
    return asyncio.run(getattr(m,'run_'+tool)(SimpleNamespace(params=params)))

@pytest.mark.parametrize('tool,params,method,path,expected',[
 ('incidents',{'action':'list','start_time':100,'end_time':200,'page_num':2,'page_size':100,'deal_statuses':[0,10],'white_status':['未加白','部分加白']},'POST','/incidents/list',{'startTimestamp':100,'endTimestamp':200,'page':2,'pageSize':100,'dealStatus':[0,10],'whiteStatus':['未加白','部分加白']}),
 ('alerts',{'action':'list','start_time':100,'end_time':200,'page_num':2,'deal_statuses':[1,2],'api_params':{'srcIps':['192.0.2.1'],'read':False}},'POST','/alerts/list',{'startTimestamp':100,'endTimestamp':200,'page':2,'pageSize':20,'alertDealStatus':[1,2],'srcIps':['192.0.2.1'],'read':False}),
 ('incidents',{'action':'status_list','uuids':['i-1']},'POST','/incidents/dealstatus/list',{'ids':['i-1']}),
 ('alerts',{'action':'status_list','uuids':['a-1']},'POST','/alerts/dealstatus/list',['a-1']),
 ('incidents',{'action':'update_status','uuids':['i-1'],'deal_status':0},'POST','/incidents/dealstatus',{'uuIds':['i-1'],'dealStatus':0}),
 ('alerts',{'action':'update_status','uuids':['a-1'],'deal_status':2},'POST','/alerts/dealstatus',{'uuIds':['a-1'],'dealStatus':2}),
 ('responses',{'action':'unisolate','ids':['policy-1']},'POST','/responses/host/unisolate',{'ids':['policy-1']}),
 ('responses',{'action':'isolate_list','isolate_status':0,'host_ip':'192.0.2.1'},'POST','/responses/host/isolate/list',{'page':1,'pageSize':20,'isolateStatus':0,'hostIp':'192.0.2.1'}),
 ('whitelists',{'action':'list','api_params':{'keyword':'demo','order':'desc','sortKey':'createTime'}},'POST','/whitelists/list',{'page':1,'pageSize':20,'keyword':'demo','order':'desc','sortKey':'createTime'}),
 ('whitelists',{'action':'delete','ids':['white-1']},'DELETE','/whitelists',{'whiteIdList':['white-1']}),
 ('whitelists',{'action':'toggle_status','id':'w-1','status':'disable'},'PUT','/whitelists/w-1/status',{'status':'disable'}),
 ('assets',{'action':'list','api_params':{'ipRanges':['192.0.2.0/24'],'connectStatus':[0],'branchIds':[1]}},'POST','/assets/list',{'page':1,'pageSize':20,'ipRanges':['192.0.2.0/24'],'connectStatus':[0],'branchIds':[1]}),
 ('assets',{'action':'ip_segment_tree','api_params':{'search':'lab','magnitude':'core'}},'POST','/assets/ipsegmenttree',{'search':'lab','magnitude':'core'}),
 ('assets',{'action':'delete','ids':['1']},'DELETE','/assets/list',{'ids':['1']}),
 ('vulns',{'action':'baseline','page_num':1,'last_id':'cursor-1','api_params':{'level':[1,2]}},'POST','/vuls/baseline/list',{'lastId':'cursor-1','level':[1,2],'pageSize':20}),
 ('vulns',{'action':'vuln_list','data_type':'weakpwd','last_id':'cursor-2'},'POST','/vuls/risk/list',{'lastId':'cursor-2','dataType':'weak_pwd','pageSize':20}),
 ('vulns',{'action':'update_status','vuln_handler':[{'fixStatus':2,'md5Repeat':'sample-hash'}]},'PATCH','/vuls/fixstatus',{'vulnHandler':[{'fixStatus':2,'md5Repeat':'sample-hash'}]}),
])
def test_documented_request_shapes(handler,tool,params,method,path,expected):
    before=copy.deepcopy(params)
    result=invoke(handler,tool,params)
    assert result.success,result.error
    assert handler[1]==[(method,'/api/xdr/v1'+path,expected,None)]
    assert params==before

@pytest.mark.parametrize('tool,params,path,query',[
 ('alerts',{'action':'get_proof','uuid':'a-1'},'/alerts/a-1/proof',None),
 ('incidents',{'action':'get_proof','uuid':'i-1'},'/incidents/i-1/proof',None),
 ('alerts',{'action':'attck_techniques'},'/alerts/attcktechniques',None),
 ('assets',{'action':'asset_class','is_filter':False,'need_auto':True},'/assets/assetclass',{'isFilter':'false','needAuto':'true'}),
 ('assets',{'action':'device_list','name':'EDR'},'/assets/assetadapter',{'name':'EDR'}),
 ('assets',{'action':'department_tree','get_undistributed':True,'exclude_gid':2},'/assets/department',{'getUndistributed':1,'excludeGid':2}),
 ('vulns',{'action':'source_device'},'/vuls/sourcedevice',None),
])
def test_get_methods_and_query_normalization(handler,tool,params,path,query):
    result=invoke(handler,tool,params)
    assert result.success,result.error
    assert handler[1]==[('GET','/api/xdr/v1'+path,None,query)]

@pytest.mark.parametrize('entity',['host','dns','innerip','ip','file','process'])
def test_six_entities(handler,entity):
    assert invoke(handler,'incidents',{'action':'get_entities','uuid':'i-1','entity_type':entity}).success
    assert handler[1][0]==('GET','/api/xdr/v1/incidents/i-1/entities/'+entity,None,None)

@pytest.mark.parametrize('action,unlimited',[('create',1),('update',True)])
def test_whitelist_documented_rule_structure(handler,action,unlimited):
    rule={'type':'srcIp','mode':'in','view':['192.0.2.1'],'isIgnorecase':False}
    body={'status':'enable','isHostAll':True,'name':'demo','isUnlimited':unlimited,'ruleList':[rule],'threatSubTypeIds':['all']}
    params={'action':action,'api_params':body}
    if action=='update':params['id']='w-1'
    result=invoke(handler,'whitelists',params)
    assert result.success,result.error
    assert handler[1][0][2]==body

@pytest.mark.parametrize('tool,params',[
 ('incidents',{'action':'list','deal_status':1}),
 ('incidents',{'action':'list','deal_statuses':[1,2]}),
 ('incidents',{'action':'list','deal_statuses':['0']}),
 ('incidents',{'action':'list','white_status':['unwhitened']}),
 ('incidents',{'action':'list','white_status':None}),
 ('incidents',{'action':'list','page_num':0}),
 ('incidents',{'action':'list','page_num':True}),
 ('incidents',{'action':'list','page_size':201}),
 ('incidents',{'action':'list','page_size':4}),
 ('incidents',{'action':'list','start_time':200,'end_time':100}),
 ('incidents',{'action':'list','api_params':{'whiteStatus':[]},'white_status':['未加白']}),
 ('incidents',{'action':'list','api_params':{'inventedFilter':'do-not-leak'}}),
 ('incidents',{'action':'status_list','uuids':[]}),
 ('alerts',{'action':'status_list','uuids':[]}),
 ('incidents',{'action':'update_status','uuids':['i-1'],'deal_status':1}),
 ('incidents',{'action':'update_status','uuids':['i-1']}),
 ('incidents',{'action':'get_proof','uuid':'../admin'}),
 ('responses',{'action':'unisolate','host_ips':['192.0.2.1']}),
 ('responses',{'action':'unisolate','ids':[]}),
 ('whitelists',{'action':'toggle_status','id':'1','status':1}),
 ('whitelists',{'action':'create','name':'demo','type':'ip','value':'192.0.2.1'}),
 ('assets',{'action':'delete'}),
 ('vulns',{'action':'vuln_list','page_num':2}),
 ('vulns',{'action':'baseline','api_params':{'page':2}}),
 ('vulns',{'action':'update_status','ids':['v-1'],'fixStatus':'2'}),
 ('vulns',{'action':'update_status','vuln_handler':[{'fixStatus':2}]}),
])
def test_invalid_inputs_fail_before_credentials_or_network(handler,tool,params):
    result=invoke(handler,tool,params)
    assert not result.success
    assert not handler[1]
    assert 'do-not-leak' not in result.error

@pytest.mark.parametrize('tool',['alerts','incidents'])
def test_empty_filter_is_distinct_from_omitted(handler,tool):
    result=invoke(handler,tool,{'action':'list'})
    assert result.success
    assert 'whiteStatus' not in handler[1][-1][2]
    assert 'dealStatus' not in handler[1][-1][2]
    result=invoke(handler,tool,{'action':'list','white_status':[],'deal_statuses':[]})
    assert result.success
    assert handler[1][-1][2]['whiteStatus']==[]
    assert handler[1][-1][2]['dealStatus' if tool=='incidents' else 'alertDealStatus']==[]


def test_three_numbered_pages_and_cursor_pages(handler):
    for page in [1,2,3]:
        assert invoke(handler,'incidents',{'action':'list','page_num':page}).success
    assert [c[2]['page'] for c in handler[1]]==[1,2,3]
    handler[1].clear()
    for cursor in ['', 'cursor-1', 'cursor-2']:
        p={'action':'vuln_list'}
        if cursor:p['last_id']=cursor
        assert invoke(handler,'vulns',p).success
    assert all('page' not in c[2] for c in handler[1])
    assert handler[1][-1][2]['lastId']=='cursor-2'


def test_original_result_and_error_preserved(handler,monkeypatch):
    m,calls=handler
    for result in [m.ToolResult(success=True,output={'code':'Success','data':{'item':[{'uuId':'i-1','whiteStatus':'未加白'}],'total':1}}),m.ToolResult(success=False,error='fixture HTTP error')]:
        async def capture(*a,**kw):return result
        monkeypatch.setattr(m,'_run_request',capture)
        assert invoke(handler,'incidents',{'action':'list'}) is result


def test_non_json_response_behavior_unchanged(handler):
    with pytest.raises(RuntimeError):handler[0]._parse_response_body(b'<html>fixture</html>',502)


def test_schemas_load_through_real_flocks_loader(tmp_path,monkeypatch):
    from flocks.tool.tool_loader import yaml_to_tool
    project=tmp_path/'project';dest=project/'.flocks/plugins/tools/device/sangfor_xdr_v2_2'
    shutil.copytree(PLUGIN,dest)
    monkeypatch.chdir(project)
    for path in dest.glob('sangfor_xdr_*.yaml'):
        raw=yaml.safe_load(path.read_text())
        jsonschema.Draft7Validator.check_schema(raw['inputSchema'])
        tool=yaml_to_tool(raw,path)
        assert tool.info.provider=='sangfor_xdr_v2_2'
        assert tool.info.source=='device'
        assert tool.info.requires_confirmation==raw['requires_confirmation']
        # Tool construction imports the handler without resolving configuration.
        assert callable(tool.handler)
        if raw['name'] not in {f'sangfor_xdr_{group}' for group in ('alerts', 'incidents', 'assets', 'vulns', 'whitelists', 'responses')}:
            continue  # Additional actions have their own required request fields.
        import inspect
        fn = inspect.getclosurevars(tool.handler).nonlocals['fn']
        calls = []
        async def capture(method, endpoint, data=None, params=None):
            calls.append((method,endpoint,data,params))
            from flocks.tool.registry import ToolResult
            return ToolResult(success=True,output={'code':'Success','data':{'item':[],'total':0}})
        monkeypatch.setitem(fn.__globals__, '_run_request', capture)
        monkeypatch.setitem(fn.__globals__, '_resolve_runtime_config', lambda: pytest.fail('No credentials'))
        action = 'isolate_list' if 'responses' in raw['name'] else 'baseline' if 'vulns' in raw['name'] else 'list'
        result = asyncio.run(tool.handler(SimpleNamespace(), action=action))
        assert result.success, result.error
        assert len(calls)==1
        assert raw['inputSchema']['properties']['api_params']['type']=='object'
