"""Actual XDR 2.3 process/file response shapes remain structured evidence."""
from flocks.monitoring.event_details import build_story, normalize_basics, safe_facts


def test_alert_list_process_names_and_sources_remain_arrays():
    raw = {'data': {'item': [{'uuId': 'alert-1', 'pName': ['miner.exe', 'cmd.exe'],
                             'devUidDesc': ['EDR', 'STA'], 'fileMd5': ['md5-one', 'md5-two']}], 'total': 1}}
    facts, partial = safe_facts(raw)
    assert not partial
    alert = facts['data']['item'][0]
    assert alert['pName'] == ['miner.exe', 'cmd.exe']
    basics = normalize_basics(alert)
    assert basics['process_name'] == ['miner.exe', 'cmd.exe']
    assert basics['data_source'] == ['EDR', 'STA']
    assert basics['file_md5'] == ['md5-one', 'md5-two']
    story = build_story({'id': 'incident-1', 'name': '主机异常'}, [{
        'id': 'evidence-1', 'kind': 'alerts', 'success': True, 'partial': partial,
        'device': 'xdr', 'facts': facts}])
    assert any('进程名称：miner.exe、cmd.exe' in item['text'] for item in story['key_evidence'])


def test_related_ip_entities_keep_process_pid_md5_binding():
    process = {'pName': 'miner.exe', 'pid': 1442, 'md5': 'miner.exe-md5-one'}
    raw = {'data': {'item': [{'id': 'entity-ip', 'ip': '192.0.2.1', 'srcProcess': [process]}]}}
    facts, partial = safe_facts(raw)
    assert not partial
    assert facts['data']['item'][0]['srcProcess'] == [process]
    assert normalize_basics(facts['data']['item'][0]['srcProcess'][0])['process_name'] == 'miner.exe'
    story = build_story({'id': 'incident-1', 'name': '主机异常'}, [{
        'id': 'evidence-1', 'kind': 'ip', 'success': True, 'partial': partial,
        'device': 'xdr', 'facts': facts}])
    assert any('进程名称：miner.exe' in item['text'] for item in story['key_evidence'])


def test_process_entities_keep_aggregations_and_documented_context():
    raw = {'data': {'item': [{
        'id': 'process-1', 'name': 'cmd.exe', 'cmdLine': 'cmd.exe /c whoami',
        'path': r'C:\Windows\System32\cmd.exe', 'pid': 1442, 'processType': 0,
        'nodeId': 'node-1', 'guid': 'process-guid', 'md5': 'md5-one',
        'aggregations': [{'id': 'aggregation-1', 'pid': 1442, 'nodeId': 'node-1', 'status': 'WAIT_DEAL'}],
        'intelligenceTag': ['malware'], 'threatLevel': 3,
        'edrDealStatusInfo': {'status': 'WAIT_DEAL'}}]}}
    facts, partial = safe_facts(raw)
    assert not partial
    assert facts == raw


def test_file_creation_context_and_virus_hash_array_remain_facts():
    raw = {'data': {'item': [{
        'fileId': 'file-1', 'path': r'C:\temp\payload.dll', 'md5': 'md5-one',
        'createTime': 1791689000, 'fileModifyTime': 1791689900,
        'parentPath': r'C:\Windows\System32\cmd.exe', 'fileModifier': 'powershell.exe',
        'intelligenceTag': ['malware']}],
        'alertTimeLine': [{'alertId': 'alert-1', 'proof': {'virusFileMd5': ['md5-one', 'md5-two']}}]}}
    facts, partial = safe_facts(raw)
    assert not partial
    assert facts == raw
    basics = normalize_basics(facts['data']['alertTimeLine'][0]['proof'])
    assert basics['file_md5'] == ['md5-one', 'md5-two']
