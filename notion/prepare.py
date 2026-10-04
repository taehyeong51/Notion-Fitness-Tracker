"""Add display formulas after explicit source-schema inspection; preserve raw fields."""
from .deploy import save
from .source import value

CONDITION_INPUT = 'NFT Measurement Condition'


def formula(expression):
    return {'formula': {'expression': expression}}


def rollup(relation, property_name, function='show_original'):
    return {'rollup': {'relation_property_name': relation, 'rollup_property_name': property_name, 'function': function}}


def add(client, source, definitions):
    current = client.request('GET', '/data_sources/' + source)['properties']
    additions = {}
    for name, definition in definitions.items():
        if name in current:
            kind = next(iter(definition))
            if current[name]['type'] != kind:
                raise ValueError('Existing helper property has another type: ' + name)
            if kind not in {'formula', 'rollup'}:
                continue  # Never overwrite a user-entered condition/baseline field.
            if kind == 'formula' and current[name]['formula']['expression'] == definition['formula']['expression']:
                continue
            if kind == 'rollup' and all(current[name]['rollup'].get(key) == raw for key, raw in definition['rollup'].items()):
                continue
        additions[name] = definition
    # Notion resolves formulas against the existing schema. Create inputs first,
    # then formulas in dependency order; one atomic batch cannot resolve them.
    inputs = {name: definition for name, definition in additions.items() if 'formula' not in definition and 'rollup' not in definition}
    if inputs:
        client.request('PATCH', '/data_sources/' + source, {'properties': inputs})
    for name, definition in additions.items():
        if 'formula' in definition or 'rollup' in definition:
            try:
                client.request('PATCH', '/data_sources/' + source, {'properties': {name: definition}})
            except Exception as error:
                raise ValueError('Cannot apply native formula ' + name + ': ' + str(error)) from None
    return list(additions)


def definitions(config):
    s, p = config['properties']['sessions'], config['properties']['sets']
    # Relations and original scalar dates are verified by source.check_config.
    d = f'prop("{s["date"]}")'
    sessions = {
        'NFT Week': formula(f'if(empty({d}), parseDate(""), dateSubtract({d}, day({d}) - 1, "days"))'),
        'NFT Recent 12 Weeks': formula(f'not empty({d}) and dateBetween(today(), {d}, "days") >= 0 and dateBetween(today(), {d}, "days") <= 83'),
    }
    date = 'prop("Chart Date")'
    exercise = 'prop("Exercise")'
    session = 'prop("Session")'
    load, reps = 'prop("Load (kg)")', 'prop("Reps")'
    pullup_ids = config['pullup_exercise_ids']
    pullup = ' or '.join(f'{exercise}.map(current.id()).first() == "{identifier.replace("-", "")}"' for identifier in pullup_ids) or 'false'
    valid = f'{exercise}.length() == 1'
    done = f'prop("Completed") and {session}.length() == 1 and format(prop("NFT Session Done Input")) == "true" and not empty({date})'
    sets = {
        CONDITION_INPUT: {'rich_text': {}},
        'NFT Condition Confirmed': {'checkbox': {}},
        'NFT Baseline': {'relation': {'data_source_id': config['data_sources']['sets'], 'type': 'single_property', 'single_property': {}}},
        'NFT Baseline Value': {'number': {'format': 'number'}},
        'NFT Baseline Version': {'rich_text': {}},
        'NFT Session Done Input': rollup('Session', 'Completed'),
        'NFT Exercise Label Input': rollup('Exercise', 'Exercise'),
        'NFT Muscle Input': rollup('Exercise', 'Primary Muscle'),
        'NFT Split Input': rollup('Session', 'Split'),
        'NFT Week': formula(f'if(empty({date}), parseDate(""), dateSubtract({date}, day({date}) - 1, "days"))'),
        'NFT Recent 12 Weeks': formula(f'not empty({date}) and dateBetween(today(), {date}, "days") >= 0 and dateBetween(today(), {date}, "days") <= 83'),
        'NFT Done': formula(done),
        'NFT Valid Exercise': formula(valid),
        'NFT Exercise Key': formula(f'if({valid}, {exercise}.map(current.id()).first(), "")'),
        'NFT Session Key': formula(f'if({session}.length() == 1, {session}.map(current.id()).first(), "")'),
        'NFT Exercise': formula(f'if({valid}, format(prop("NFT Exercise Label Input")), "미연결/다중")'),
        'NFT Primary Muscle': formula(f'if({valid} and not empty(format(prop("NFT Muscle Input"))), format(prop("NFT Muscle Input")), "미상")'),
        'NFT Split': formula(f'if({session}.length() == 1 and not empty(format(prop("NFT Split Input"))), format(prop("NFT Split Input")), "미상")'),
        'NFT Set Type': formula('if(empty(prop("Set Type")), "미분류", format(prop("Set Type")))'),
        'NFT Rep Band': formula(f'ifs({reps} >= 1 and {reps} <= 5, "1–5회", {reps} >= 6 and {reps} <= 8, "6–8회", {reps} >= 9 and {reps} <= 12, "9–12회", "")'),
        'NFT Pullup': formula(f'{valid} and ({pullup})'),
        'NFT Condition': formula(f'if(not empty(prop("{CONDITION_INPUT}")), prop("{CONDITION_INPUT}"), if(contains(prop("Set Notes"), "패러럴그립"), "패러럴그립 · 부하모드 미확인", "측정조건 미확인"))'),
        'NFT Comparable': formula(f'prop("NFT Done") and {valid} and prop("NFT Condition Confirmed") and not empty(prop("{CONDITION_INPUT}")) and {reps} > 0 and {reps} == floor({reps})'),
        'NFT e1RM': formula(f'if(prop("NFT Done") and {valid} and not prop("NFT Pullup") and {load} > 0 and {reps} >= 1 and {reps} <= 12 and {reps} == floor({reps}), {load} * (1 + {reps} / 30), toNumber(""))'),
        'NFT e1RM Display': formula('if(prop("NFT e1RM") > 0, round(prop("NFT e1RM") * 10) / 10, toNumber(""))'),
        'NFT Issue': formula(f'if(not {valid}, "종목 미연결/다중 · ", "") + if({session}.length() != 1, "세션 관계 누락/다중 · ", "") + if(empty(prop("Set Type")), "세트 유형 미분류 · ", "") + if(empty(prop("{CONDITION_INPUT}")), "측정조건 미확인", "")'),
    }
    # Formula result types from show_original rollups can be heterogeneous.
    # Scalar format/number/date aggregations avoid unsupported .first().prop().
    for name, target, function in [
        ('NFT Baseline e1RM Seen', 'NFT e1RM', 'max'),
        ('NFT Baseline Reps Seen', 'Reps', 'max'),
        ('NFT Baseline Condition Seen', CONDITION_INPUT, 'show_original'),
        ('NFT Baseline Exercise Seen', 'NFT Exercise Key', 'show_original'),
        ('NFT Baseline Pullup Seen', 'NFT Pullup', 'show_original'),
        ('NFT Baseline Comparable Seen', 'NFT Comparable', 'show_original'),
        ('NFT Baseline Date Seen', 'Chart Date', 'earliest_date'),
    ]:
        # Dependencies must already exist before these rollups are created.
        sets[name] = rollup('NFT Baseline', target, function)
    # A frozen numeric baseline + version protects history. A changed reference
    # suppresses the index rather than silently rebasing all previous values.
    baseline_valid = (
        'prop("NFT Comparable") and prop("NFT Baseline").length() == 1 and '
        'prop("NFT Baseline Value") > 0 and not empty(prop("NFT Baseline Version")) and '
        'format(prop("NFT Baseline Comparable Seen")) == "true" and '
        'format(prop("NFT Baseline Exercise Seen")) == prop("NFT Exercise Key") and '
        f'format(prop("NFT Baseline Condition Seen")) == prop("{CONDITION_INPUT}") and '
        'format(prop("NFT Baseline Pullup Seen")) == format(prop("NFT Pullup")) and '
        'dateBetween(prop("Chart Date"), prop("NFT Baseline Date Seen"), "days") >= 0 and '
        'abs(if(prop("NFT Pullup"), prop("NFT Baseline Reps Seen"), prop("NFT Baseline e1RM Seen")) - prop("NFT Baseline Value")) < 0.000001'
    )
    sets['NFT Metric'] = formula(f'if({baseline_valid}, if(prop("NFT Pullup"), "reps", if(prop("NFT e1RM") > 0, "e1rm", "")), "")')
    sets['NFT Index Display'] = formula('if(not empty(prop("NFT Metric")), round(if(prop("NFT Pullup"), prop("Reps"), prop("NFT e1RM")) / prop("NFT Baseline Value") * 10000) / 100, toNumber(""))')
    return sessions, sets


def prepare(client, config, path):
    sessions, sets = definitions(config)
    changes = {
        'sessions': add(client, config['data_sources']['sessions'], sessions),
        'sets': add(client, config['data_sources']['sets'], sets),
    }
    shared = {'Date': 'Chart Date', 'Exercise': 'NFT Exercise', 'Done': 'NFT Done', 'Recent 12 Weeks': 'NFT Recent 12 Weeks'}
    config['native_properties'] = {
        'sessions': {'Date': 'Date', 'Week': 'NFT Week', 'Split': 'Split', 'Done': 'Completed', 'Recent 12 Weeks': 'NFT Recent 12 Weeks'},
        'sets': {**shared, 'Week': 'NFT Week', 'Primary Muscle': 'NFT Primary Muscle', 'Split': 'NFT Split',
                 'Load': 'Load (kg)', 'Reps': 'Reps', 'Set Type': 'NFT Set Type', 'Condition': 'NFT Condition',
                 'e1RM': 'NFT e1RM', 'e1RM Display': 'NFT e1RM Display', 'Rep Band': 'NFT Rep Band',
                 'Comparable': 'NFT Comparable', 'Pullup': 'NFT Pullup', 'Issue': 'NFT Issue',
                 'Original Exercise': 'Exercise', 'Original Session': 'Session'},
        'membership': {**shared, 'Session Key': 'NFT Session Key', 'Valid Exercise': 'NFT Valid Exercise', 'Original Session': 'Session'},
        'progress': {**shared, 'Metric': 'NFT Metric', 'Index Display': 'NFT Index Display',
                     'Baseline Value': 'NFT Baseline Value', 'Baseline Version': 'NFT Baseline Version', 'Baseline Set': 'NFT Baseline'},
    }
    config['properties']['sets']['condition_confirmed'] = 'NFT Condition Confirmed'
    save(path, config)
    return changes
