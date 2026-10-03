"""Explicit live validation in temporary DBs, never in the user's workout DBs."""
from copy import deepcopy
from datetime import datetime, timedelta
import math
from pathlib import Path

from .charts import SPECS, payload
from .deploy import paragraph, text
from .metrics import SEOUL, week
from .native import adapt_filters, bind
from .prepare import prepare
from .source import value


def run(client, config, parent):
    page = client.request('POST', '/pages', {'parent': {'page_id': parent}, 'properties': {'title': {'title': text('NFT 검증 자료 · 실제 운동 제외')}}, 'children': [paragraph('네이티브 수식 검증용 임시 자료입니다. 종료 시 이 페이지를 보관합니다. 실제 운동 원본을 변경하지 않습니다.')]})
    passed = []
    try:
        def database(label, props):
            d = client.request('POST', '/databases', {'parent': {'type': 'page_id', 'page_id': page['id']}, 'title': text(label), 'initial_data_source': {'properties': props}})
            return d['data_sources'][0]['id']
        sessions = database('NFT Verification Sessions', {'Session': {'title': {}}, 'Date': {'date': {}}, 'Completed': {'checkbox': {}}, 'Split': {'select': {'options':[{'name':'Pull'}]}}})
        sets = database('NFT Verification Sets', {
            'Set': {'title': {}}, 'Completed': {'checkbox': {}}, 'Load (kg)': {'number': {}}, 'Reps': {'number': {}},
            'Set Notes': {'rich_text': {}}, 'Set Type': {'select': {'options':[{'name':'Working'}]}},
            'Session': {'relation': {'data_source_id':sessions, 'type':'single_property','single_property':{}}},
            'Exercise': {'relation': {'data_source_id':config['data_sources']['exercises'], 'type':'single_property','single_property':{}}},
            'Session Date': {'rollup': {'relation_property_name':'Session','rollup_property_name':'Date','function':'earliest_date'}},
        })
        client.request('PATCH','/data_sources/'+sets,{'properties':{'Chart Date':{'formula':{'expression':'if(prop("Session").length() == 1, prop("Session Date"), parseDate(""))'}}}})
        fixture = deepcopy(config)
        fixture['data_sources'] = {**config['data_sources'], 'sessions':sessions, 'sets':sets}
        fixture['strength_scope'] = {**fixture['strength_scope'], 'condition':'검증용 동일 조건', 'observational':False}
        prepare(client,fixture,Path('.local/validation/config.json'))
        print('Temporary native formulas created', flush=True)
        now = datetime.now(SEOUL).date()
        def session(label, observed):
            return client.request('POST','/pages',{'parent':{'data_source_id':sessions},'properties':{'Session':{'title':text(label)},'Date':{'date':{'start':observed.isoformat()}},'Completed':{'checkbox':True},'Split':{'select':{'name':'Pull'}}}})['id']
        baseline_session = session('검증 기준',now-timedelta(days=8))
        latest_session = session('검증 최근',now-timedelta(days=1))
        baseline_value = 100*(1+5/30)
        exercise_id = config['strength_scope']['exercise_id']
        def observation(label, session_id, load, reps):
            return client.request('POST','/pages',{'parent':{'data_source_id':sets},'properties':{
                'Set':{'title':text(label)},'Session':{'relation':[{'id':session_id}]},'Exercise':{'relation':[{'id':exercise_id}]},
                'Completed':{'checkbox':True},'Load (kg)':{'number':load},'Reps':{'number':reps},'Set Type':{'select':{'name':'Working'}},
                'NFT Measurement Condition':{'rich_text':text('검증용 동일 조건')},'NFT Condition Confirmed':{'checkbox':True},
                'NFT Baseline Value':{'number':baseline_value},'NFT Baseline Version':{'rich_text':text('validation-v1')},
            }})['id']
        baseline = observation('검증 기준 100×5',baseline_session,100,5)
        lesser = observation('검증 보조 140×3',latest_session,140,3)
        peak = observation('검증 최고 140×4',latest_session,140,4)
        for identifier in [baseline,lesser,peak]:
            client.request('PATCH','/pages/'+identifier,{'properties':{'NFT Baseline':{'relation':[{'id':baseline}]}}})
        def read(identifier,name):
            return value(client.request('GET','/pages/'+identifier)['properties'][name])
        def check(name, actual, expected):
            ok = isinstance(actual,(int,float)) and math.isclose(actual,expected,rel_tol=1e-8,abs_tol=1e-7) if isinstance(expected,float) else actual == expected
            if not ok:
                raise ValueError('Live validation failed: '+name+f' (actual={actual}, expected={expected})')
            passed.append(name)
        native = bind(client,fixture)['sets']
        body = adapt_filters(payload(SPECS[0],native['schema'],fixture,native=True),native['schema'])
        view = client.request('POST','/views',{'data_source_id':sets,'create_database':{'parent':{'type':'page_id','page_id':page['id']}},**body})
        def maxima():
            rows = list(client.pages('/data_sources/'+sets+'/query',{'filter':body['filter']}))
            groups = {}
            for row in rows:
                date = value(row['properties']['Chart Date'])
                numeric = value(row['properties']['NFT e1RM Display'])
                groups[date] = max(groups.get(date,numeric),numeric)
            return groups
        recent_date = (now-timedelta(days=1)).isoformat()
        check('140×4 → 158.7 and live chart daily max',maxima()[recent_date],158.7)
        check('Confirmed baseline = 100',read(baseline,'NFT Index Display'),100.0)
        client.request('PATCH','/pages/'+peak,{'properties':{'Load (kg)':{'number':150}}})
        check('Load update recalculates chart maximum',maxima()[recent_date],170.0)
        check('Load update recalculates progress',read(peak,'NFT Index Display'),round(170/baseline_value*100,2))
        client.request('PATCH','/pages/'+peak,{'in_trash':True})
        check('Archived maximum falls back to remaining set',maxima()[recent_date],154.0)
        client.request('PATCH','/pages/'+peak,{'in_trash':False})
        check('Restore returns original maximum',maxima()[recent_date],170.0)
        moved = now-timedelta(days=6)
        client.request('PATCH','/pages/'+latest_session,{'properties':{'Date':{'date':{'start':moved.isoformat()}}}})
        check('Session date move changes chart date',read(peak,'Chart Date'),moved.isoformat())
        check('Session date move changes Monday week',read(peak,'NFT Week'),week(moved))
        client.request('PATCH','/pages/'+baseline,{'properties':{'Reps':{'number':6}}})
        check('Modified baseline suppresses old-version index',read(peak,'NFT Index Display'),None)
        client.request('PATCH','/pages/'+baseline,{'properties':{'Reps':{'number':5}}})
        check('Restored baseline restores valid index',read(peak,'NFT Index Display'),round(170/baseline_value*100,2))
        client.request('PATCH','/pages/'+peak,{'properties':{'NFT Measurement Condition':{'rich_text':text('검증용 다른 조건')}}})
        check('Condition mismatch suppresses index',read(peak,'NFT Index Display'),None)
        client.request('PATCH','/pages/'+peak,{'properties':{'Exercise':{'relation':[]}}})
        check('Missing exercise excludes comparable performance',read(peak,'NFT Comparable'),False)
        check('Missing exercise removes e1RM',read(peak,'NFT e1RM'),None)
        return {'passed':len(passed),'checks':passed,'original_workout_writes':0,'validation_page_archived':True}
    finally:
        archived = client.request('PATCH','/pages/'+page['id'],{'in_trash':True})
        if not archived.get('in_trash'):
            raise ValueError('Temporary validation page archive was not confirmed')
        Path('.local/validation/config.json').unlink(missing_ok=True)
