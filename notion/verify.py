"""Read-only, real-account formula and chart/source reconciliation."""
from collections import Counter, defaultdict
from datetime import datetime, timedelta
import json
import math
from pathlib import Path

from .metrics import SEOUL, day, week, rep_band
from .source import value


def scalar(row, name):
    return value(row['properties'][name])


def close(actual, expected, context):
    if expected is None:
        if actual is not None:
            raise ValueError(context + ': expected an empty value')
    elif isinstance(expected, (int, float)) and not isinstance(expected, bool):
        if not isinstance(actual, (int, float)) or not math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-8):
            raise ValueError(context + ': numeric mismatch')
    elif actual != expected:
        raise ValueError(context + ': value mismatch')


def run(client, config, state_path, before_path=None):
    state = json.loads(Path(state_path).read_text())
    records = {role: client.data(source) for role, source in config['data_sources'].items()}
    sessions = {row['id']: row for row in records['sessions']}
    exercises = {row['id']: row for row in records['exercises']}
    now = datetime.now(SEOUL).date()
    lower = now - timedelta(days=83)
    checks, observations = 0, {}
    for row in records['sets']:
        session_ids, exercise_ids = scalar(row, 'Session'), scalar(row, 'Exercise')
        session = sessions.get(session_ids[0]) if len(session_ids) == 1 else None
        exercise = exercises.get(exercise_ids[0]) if len(exercise_ids) == 1 else None
        observed = day(scalar(session, 'Date')) if session else None
        done = bool(scalar(row, 'Completed') and session and scalar(session, 'Completed') and observed)
        load, reps = scalar(row, 'Load (kg)'), scalar(row, 'Reps')
        pullup = bool(exercise and exercise['id'] in config['pullup_exercise_ids'])
        condition = scalar(row, 'NFT Measurement Condition') or ('패러럴그립 · 부하모드 미확인' if '패러럴그립' in scalar(row, 'Set Notes') else '측정조건 미확인')
        comparable = bool(done and exercise and scalar(row, 'NFT Condition Confirmed') and scalar(row, 'NFT Measurement Condition') and reps and reps > 0 and reps == int(reps))
        e1rm = load * (1 + reps / 30) if done and exercise and not pullup and load and load > 0 and reps and 1 <= reps <= 12 and reps == int(reps) else None
        expected = {
            'NFT Done': done, 'NFT Valid Exercise': bool(exercise),
            'NFT Exercise': scalar(exercise, 'Exercise') if exercise else '미연결/다중',
            'NFT Primary Muscle': (scalar(exercise, 'Primary Muscle') or '미상') if exercise else '미상',
            'NFT Split': (scalar(session, 'Split') or '미상') if session else '미상',
            'NFT Set Type': scalar(row, 'Set Type') or '미분류',
            'NFT Week': week(observed), 'NFT Session Key': session['id'].replace('-', '') if session else '',
            'NFT Recent 12 Weeks': bool(observed and lower <= observed <= now),
            'NFT Pullup': pullup, 'NFT Condition': condition, 'NFT Comparable': comparable,
            'NFT e1RM': e1rm, 'NFT e1RM Display': round(e1rm, 1) if e1rm is not None else None,
            'NFT Rep Band': rep_band(float(reps)) if reps is not None else '',
        }
        # Empty strings are represented as null by the API on some formulas.
        for name, raw in expected.items():
            actual = scalar(row, name)
            if raw == '' and actual is None:
                actual = ''
            if name == 'NFT Rep Band' and raw is None and actual == '':
                actual = None
            close(actual, raw, name + ' on source ' + row['id'])
            checks += 1
        observations[row['id']] = {'row': row, 'session': session, 'exercise': exercise,
            'date': observed, 'done': done, 'recent': bool(observed and lower <= observed <= now),
            'load': load, 'reps': reps, 'pullup': pullup, 'condition': condition,
            'comparable': comparable, 'e1rm': e1rm}
    for row in records['sessions']:
        observed = day(scalar(row, 'Date'))
        close(scalar(row, 'NFT Week'), week(observed), 'Session week')
        close(scalar(row, 'NFT Recent 12 Weeks'), bool(observed and lower <= observed <= now), 'Session recent period')
        checks += 2
    preservation = None
    if before_path:
        before = json.loads(Path(before_path).read_text())
        for role, original in before.items():
            current = {row['id']: row for row in records[role]}
            if set(current) != {row['id'] for row in original}:
                raise ValueError('Original record IDs changed: ' + role)
            for row in original:
                for name, prop in row['properties'].items():
                    # Derived formulas/rollups are expected to reevaluate.
                    if prop['type'] in {'formula', 'rollup'} or name.startswith('NFT '):
                        continue
                    new = current[row['id']]['properties'][name]
                    if prop['type'] == 'multi_select':
                        same = [x['name'] for x in prop['multi_select']] == [x['name'] for x in new['multi_select']]
                    else:
                        same = value(prop) == value(new)
                    if not same:
                        raise ValueError('Original property changed: ' + role + '.' + name)
        preservation = 'All original IDs and input properties match the before snapshot'
    results = {}
    for identity, view_id in state['views'].items():
        view = client.request('GET', '/views/' + view_id)
        if view['type'] != 'chart':
            continue
        selected = list(client.pages('/data_sources/' + view['data_source_id'] + '/query', {'filter': view['filter']}))
        chart_id = identity.split('_')[0]
        if chart_id in {'C04A', 'C09A', 'H01'}:
            expected_ids = {row['id'] for row in records['sessions'] if scalar(row, 'Completed') and day(scalar(row, 'Date')) and lower <= day(scalar(row, 'Date')) <= now}
        else:
            eligible = [o for o in observations.values() if o['done'] and o['recent']]
            if chart_id in {'C01', 'C02', 'C03', 'C08'}:
                scope = dict(config['pullup_scope'] if chart_id == 'C08' else config['strength_scope'])
                if identity.startswith('C01_'):
                    scope['exercise_id'] = config['strength_exercise_ids'][int(identity.split('_')[1])]
                if identity == 'C08_Parallel':
                    scope['condition'] = '패러럴그립 · 부하모드 미확인'
                eligible = [o for o in eligible if o['exercise'] and o['exercise']['id'] == scope['exercise_id'] and o['condition'] == scope['condition'] and o['pullup'] == (chart_id == 'C08') and o['reps'] and o['reps'] > 0 and (scope.get('observational') or o['comparable'])]
                if chart_id in {'C01', 'C03'}:
                    eligible = [o for o in eligible if o['e1rm'] and o['load'] > 0 and 1 <= o['reps'] <= 12]
                if chart_id == 'C02':
                    eligible = [o for o in eligible if o['load'] == scope['fixed_load']]
            elif chart_id == 'C06T':
                eligible = [o for o in eligible if o['exercise'] and o['exercise']['id'] in config['top10_exercise_ids']]
            elif chart_id == 'C07':
                eligible = [o for o in eligible if o['exercise']]
            elif chart_id in {'C10', 'C10P'}:
                metric = 'e1rm' if chart_id == 'C10' else 'reps'
                eligible = [o for o in eligible if scalar(o['row'], 'NFT Metric') == metric]
            expected_ids = {o['row']['id'] for o in eligible}
        if expected_ids != {row['id'] for row in selected}:
            raise ValueError('Live chart filter disagrees with independent raw-source predicate: ' + identity)
        aggregate = {}
        if chart_id == 'C07':
            groups = defaultdict(set)
            for row in selected:
                groups[scalar(row, 'NFT Exercise')].add(scalar(row, 'NFT Session Key'))
            aggregate = {label: len(keys) for label, keys in groups.items()}
        elif chart_id in {'C01','C02','C03','C08'}:
            for row in selected:
                obs = observations[row['id']]
                key = obs['date'].isoformat()
                if chart_id == 'C03':
                    key = key[:7] + ' · ' + rep_band(float(obs['reps']))
                raw = round(obs['e1rm'], 1) if chart_id == 'C01' else obs['load'] if chart_id == 'C03' else obs['reps']
                aggregate[key] = max(aggregate.get(key, raw), raw)
        else:
            category = {'C04A':'NFT Week','C04B':'NFT Week','C05':'NFT Primary Muscle','C06':'NFT Exercise','C06T':'NFT Exercise','C09A':'Split','C09B':'NFT Split'}.get(chart_id)
            if category:
                aggregate = dict(Counter(str(scalar(row,category)) for row in selected))
        if identity in {'H01', 'H02'}:
            aggregate = {'count':len(selected)}
        results[identity] = {'source_rows':len(selected),'aggregation': view['configuration'].get('y_axis', view['configuration'].get('value')), 'groups':aggregate}
    return {'source_counts':{role:len(rows) for role,rows in records.items()}, 'formula_checks':checks,
        'original_preservation':preservation, 'native_chart_checks':len(results), 'charts':results}
