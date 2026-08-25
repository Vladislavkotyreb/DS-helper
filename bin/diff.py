#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Night Watch R4S Librarian — сверка.

ds-latest.json (что в дизайн-системе) × code-latest.json (что в прототипах)
→ findings.json + reports/REPORT.md

Категории (по образцу Night Watch, развёрнутому в сторону «ДС — истина»):
  TOKEN_VALUE_DRIFT  значение токена в коде разошлось с Figma Variable
  TOKEN_UNKNOWN      токен ссылается на имя, которого в ДС больше нет
  ORPHAN_TOKEN       токен объявлен и не используется
  RAW_VALUE          сырое значение там, где есть токен
  DEPRECATED_USE     используется компонент, помеченный DEPRECATED
  STATE_GAP          у компонента ДС есть состояние, в CSS его нет
  MISSING_COMPONENT  компонент ДС из watchlist не заведён в прототипе
  DS_DEFECT          дефект в самой ДС (дыра в матрице, разнобой регистра)
"""
import json, os, re, sys, datetime, collections

SEV_ORDER = {'high': 0, 'medium': 1, 'low': 2, 'info': 3}
SEV_RU = {'high': 'важно', 'medium': 'средне', 'low': 'мелочь', 'info': 'к сведению'}

CAT_RU = {
    'TOKEN_VALUE_DRIFT': 'Значение токена разошлось с Figma',
    'TOKEN_UNKNOWN':     'Токен ссылается на несуществующее имя ДС',
    'ORPHAN_TOKEN':      'Объявленный токен нигде не используется',
    'RAW_VALUE':         'Сырое значение вместо токена',
    'DEPRECATED_USE':    'Используется DEPRECATED-компонент',
    'STATE_GAP':         'Состояние из ДС не покрыто в CSS',
    'MISSING_COMPONENT': 'Компонент ДС не заведён в прототипе',
    'DS_DEFECT':         'Дефект в самой дизайн-системе',
    'NOT_CHECKED':       'Не сверено — нет данных в слепке',
    'ACCENT_MISMATCH':   'Значение взято из другого акцента ДС',
    'FOREIGN_VARIABLE':  'Токен назван по переменной чужой коллекции',
}


def load_config(here):
    """Конфиг с внятной ошибкой вместо трейсбека — это первое, что видит новый человек."""
    p = os.path.join(here, 'config.json')
    if not os.path.exists(p):
        sys.stderr.write(
            'Нет config.json.\n'
            'Скопируйте пример и впишите свои ключи Figma и пути к прототипам:\n\n'
            '    cp config.example.json config.json\n\n')
        raise SystemExit(2)
    try:
        return json.load(open(p, encoding='utf-8'))
    except ValueError as e:
        sys.stderr.write('config.json — битый JSON: %s\n' % e)
        raise SystemExit(2)


def ref(token_name):
    """Как сослаться на токен в этом синтаксисе: $scss / @less / var(--custom-property)."""
    return token_name if token_name[:1] in ('$', '@') else 'var(%s)' % token_name


def plural(n, one, few, many):
    n10, n100 = n % 10, n % 100
    if n10 == 1 and n100 != 11:
        return '%d %s' % (n, one)
    if 2 <= n10 <= 4 and not 12 <= n100 <= 14:
        return '%d %s' % (n, few)
    return '%d %s' % (n, many)


def norm_color(v):
    if not v:
        return None
    v = str(v).strip().lower()
    m = re.fullmatch(r'#([0-9a-f]{3,8})', v)
    if not m:
        return None
    h = m.group(1)
    if len(h) in (3, 4):
        h = ''.join(c * 2 for c in h)
    if len(h) == 8 and h[6:] == 'ff':
        h = h[:6]
    return '#' + h


def as_number(v):
    if v is None:
        return None
    m = re.fullmatch(r'\s*(-?\d+(?:\.\d+)?)(?:px)?\s*', str(v))
    return float(m.group(1)) if m else None


def var_value(ds, name, theme):
    """Значение переменной ДС для темы прототипа. None — значение не снято."""
    v = ds.get('variables', {}).get(name)
    if v is None:
        return None
    if not isinstance(v, dict):
        return v
    return v.get('any') or v.get(theme)


def ds_value_index(ds, theme):
    """значение → [имена переменных ДС] для нужной темы"""
    by_color, by_num = collections.defaultdict(list), collections.defaultdict(list)
    for name in ds.get('variables', {}):
        val = var_value(ds, name, theme)
        c = norm_color(val)
        if c:
            by_color[c].append(name)
            continue
        n = as_number(val)
        if n is not None:
            by_num[n].append(name)
    return by_color, by_num


def nrm(name):
    """«Inputs / Message» и «Inputs/Message» — одно и то же. Крестики-маркеры легаси снимаются."""
    n = str(name).replace('\u274c', ' ').strip()
    n = re.sub(r'\s*/\s*', '/', n)
    n = re.sub(r'\s+', ' ', n)
    return n.lower()


def match_sections(ds_name, sections):
    """секции CSS, относящиеся к компоненту ДС"""
    target = nrm(ds_name)
    out = []
    for s in sections:
        nm = nrm(s['name'])
        if nm == target:
            out.append(s); continue
        parts = [x.strip() for x in nm.split('/')]
        if target in parts and '/' not in target:
            out.append(s)
    return out


def check_tokens(proto, ds, cfg, out):
    variables = ds.get('variables', {})
    complete = ds.get('variablesComplete', False)
    theme = proto.get('theme') or 'light'
    unchecked = 0

    # Заявленной теме верим только если она подтверждается значениями.
    # В продовом репозитории тем может оказаться несколько, и врать про дрейф нельзя.
    votes = {'light': 0, 'dark': 0}
    for t in proto['tokens'].values():
        fig = t.get('figmaName')
        v = variables.get(fig) if fig else None
        if not isinstance(v, dict) or 'light' not in v or 'dark' not in v:
            continue
        got = norm_color(t['value']) or as_number(t['value'])
        for m in ('light', 'dark'):
            cand = norm_color(v[m]) or as_number(v[m])
            if cand is not None and got is not None and cand == got:
                votes[m] += 1
    theme_conflict = False
    if votes['light'] + votes['dark'] >= 3 and votes[theme] < votes['light' if theme == 'dark' else 'dark']:
        other = 'light' if theme == 'dark' else 'dark'
        theme_conflict = True
        out.append(dict(cat='NOT_CHECKED', sev='info', proto=proto['id'],
                        file=None, line=None, subject='тема источника',
                        msg='в конфиге заявлена %s, но значения токенов сходятся с %s '
                            '(%d против %d) — сверку значений не делаю, поправьте theme '
                            'или разнесите источник по темам'
                            % (theme, other, votes[other], votes[theme])))
        theme = other
    for tname, t in sorted(proto['tokens'].items()):
        fig = t.get('figmaName')
        if not fig:
            continue
        if fig not in variables:
            foreign = next((v for k, v in (cfg.get('foreignVariablePrefixes') or {}).items()
                            if fig.startswith(k)), None)
            if foreign:
                out.append(dict(cat='FOREIGN_VARIABLE', sev='medium', proto=proto['id'],
                                file=t['file'], line=t['line'], subject=tname,
                                msg='«%s» — не переменная R4S: %s' % (fig, foreign)))
            elif complete:
                out.append(dict(cat='TOKEN_UNKNOWN', sev='medium', proto=proto['id'],
                                file=t['file'], line=t['line'], subject=tname,
                                msg='комментарий указывает на «%s», такой переменной в ДС нет '
                                    '— переименована или удалена' % fig))
            else:
                out.append(dict(cat='TOKEN_UNKNOWN', sev='info', proto=proto['id'],
                                file=t['file'], line=t['line'], subject=tname,
                                msg='«%s» не подтверждён текущим слепком ДС — дособрать слепок, '
                                    'прежде чем считать это расхождением' % fig))
            continue
        if theme_conflict:
            unchecked += 1
            continue
        want, got = var_value(ds, fig, theme), t['value']
        if want is None:
            unchecked += 1
            continue
        wc, gc = norm_color(want), norm_color(got)
        if wc and gc:
            if wc != gc:
                acc = (ds.get('accentVariants') or {}).get(fig) or {}
                mine = proto.get('accent') or 'Green'
                other = [a for a, v in acc.items() if a != mine and norm_color(v) == gc]
                if other:
                    out.append(dict(cat='ACCENT_MISMATCH', sev='high', proto=proto['id'],
                                    file=t['file'], line=t['line'], subject=tname,
                                    msg='%s: %s — это акцент %s, а прототип на %s (%s)'
                                        % (fig, gc, ' / '.join(other), mine, wc),
                                    fix='%s:%s' % (tname, wc)))
                else:
                    out.append(dict(cat='TOKEN_VALUE_DRIFT', sev='high', proto=proto['id'],
                                    file=t['file'], line=t['line'], subject=tname,
                                    msg='%s: в Figma %s, в коде %s' % (fig, wc, gc),
                                    fix='%s:%s' % (tname, wc)))
            continue
        wn, gn = as_number(want), as_number(got)
        if wn is not None and gn is not None and wn != gn:
            out.append(dict(cat='TOKEN_VALUE_DRIFT', sev='high', proto=proto['id'],
                            file=t['file'], line=t['line'], subject=tname,
                            msg='%s: в Figma %g, в коде %g' % (fig, wn, gn),
                            fix='%s:%gpx' % (tname, wn)))

    if unchecked:
        out.append(dict(cat='NOT_CHECKED', sev='info', proto=proto['id'],
                        file=None, line=None, subject='%d токенов' % unchecked,
                        msg='значение для режима %s в слепке ДС не снято — сверить не с чем' % theme))

    for tname, t in sorted(proto['tokens'].items()):
        if proto['usage'].get(tname, 0) == 0:
            out.append(dict(cat='ORPHAN_TOKEN', sev='low', proto=proto['id'],
                            file=t['file'], line=t['line'], subject=tname,
                            msg='объявлен, ни одного обращения к %s%s' % (
                                tname, ' в источнике' if proto.get('tier') == 'production'
                                else ' в прототипе')))


def check_raw(proto, ds, cfg, out):
    """
    Сырые значения. Правило вывода: то, что чинится одной заменой, показываем
    построчно; то, что встречается сотнями, схлопываем в одну строку со счётчиком.
    Иначе на продовом репозитории отчёт превращается в простыню и его не читают.
    """
    theme = proto.get('theme') or 'light'
    by_color, by_num = ds_value_index(ds, theme)

    # значение → локальные токены с ним. Их может быть несколько, и тогда
    # называть один нельзя: #ffffff бывает и фоном, и текстом на тёмном.
    local_by_color, local_by_num = collections.defaultdict(list), collections.defaultdict(list)
    for tn, t in proto['tokens'].items():
        c = norm_color(t['value'])
        if c:
            local_by_color[c].append(tn)
        else:
            n = as_number(t['value'])
            if n is not None:
                local_by_num[n].append(tn)

    def phrase(local_list, ds_names):
        if len(local_list) == 1:
            return 'есть токен %s' % local_list[0], ref(local_list[0])
        if ds_names:
            head = ds_names[0]
            tail = (' (или %s — значение неоднозначное)' % ', '.join(ds_names[1:3])) \
                if len(ds_names) > 1 else ''
            return 'это %s%s' % (head, tail), None
        if local_list:
            return ('совпадает с %s — токенов несколько, выбирать по смыслу'
                    % ', '.join(local_list[:3])), None
        return None, None

    buckets = collections.Counter()
    agg = collections.OrderedDict()   # (kind, value, повод) → [счётчик, где впервые]

    def bump(kind, value, reason, r):
        key = (kind, value, reason)
        if key not in agg:
            agg[key] = [0, (r['file'], r['line'], '%s { %s }' % (r['selector'], r['prop']))]
        agg[key][0] += 1

    for r in proto['raws']:
        if r['outOfScope']:
            buckets['chrome'] += 1
            continue
        subject = '%s { %s }' % (r['selector'], r['prop'])

        if r['kind'] in ('color', 'color-fn'):
            v = r['value']
            names = by_color.get(v, []) if r['kind'] == 'color' else []
            local_list = local_by_color.get(v, []) if r['kind'] == 'color' else []
            if len(local_list) == 1:
                msg, fix = phrase(local_list, names)
                out.append(dict(cat='RAW_VALUE', sev='high', proto=proto['id'],
                                file=r['file'], line=r['line'], subject=subject,
                                msg='сырой %s — %s' % (v, msg), fix=fix))
            elif names or local_list:
                bump('color', v, 'known', r)
            else:
                bump('color', v, 'unknown', r)

        elif r['kind'] == 'length':
            n = as_number(r['value'])
            local_list = local_by_num.get(n, [])
            names = by_num.get(n, [])
            if len(local_list) == 1:
                msg, fix = phrase(local_list, names)
                out.append(dict(cat='RAW_VALUE', sev='medium', proto=proto['id'],
                                file=r['file'], line=r['line'], subject=subject,
                                msg='сырые %s — %s' % (r['value'], msg), fix=fix))
            elif names:
                bump('length', r['value'], 'known', r)
            else:
                bump('length', r['value'], 'unknown', r)

    complete = ds.get('variablesComplete', False)
    for (kind, value, reason), (cnt, where) in agg.items():
        fl, ln, sel = where
        many = cnt > 1
        head = '%s — %s' % (value, plural(cnt, 'раз', 'раза', 'раз')) if many else sel
        if reason == 'known':
            names = (by_color if kind == 'color' else by_num).get(
                norm_color(value) if kind == 'color' else as_number(value), [])
            msg = 'значение есть в ДС (%s%s), но записано числом' % (
                names[0] if names else '—',
                ' и ещё %d' % (len(names) - 1) if len(names) > 1 else '')
            sev = 'medium'
        else:
            msg = ('в палитре ДС такого нет' if kind == 'color' else 'вне шкалы ДС') + \
                  ('' if complete else '; слепок ДС неполон')
            sev = 'medium' if (kind == 'color' and complete) else 'low'
        if many:
            msg += '. Первое вхождение %s: %s' % (fl, sel)
        out.append(dict(cat='RAW_VALUE', sev=sev, proto=proto['id'],
                        file=fl, line=ln, subject=head, msg=msg))

    return buckets


def check_components(proto, ds, cfg, out):
    state_map = cfg.get('stateMap', {})
    watchlist = set(cfg.get('watchlist', []))
    sections = proto['sections']
    rules_by_section = collections.defaultdict(list)
    for r in proto['rules']:
        rules_by_section[r['section']].append(r)

    # DS-компонент → CSS-классы, объявленные в config.componentMap
    classes_for = collections.defaultdict(list)
    for cls, ds_name in (cfg.get('componentMap') or {}).items():
        classes_for[nrm(ds_name)].append(cls)

    def rules_by_class(classes):
        """Правила, чей селектор начинается с одного из классов компонента."""
        hits = []
        for r in proto['rules']:
            sel = r['selector']
            for cls in classes:
                if re.search(r'(^|[\s,>+~])' + re.escape(cls) + r'(?![\w-])', sel):
                    hits.append(r); break
        return hits

    for comp in ds.get('components', []):
        name = comp['name']
        secs = match_sections(name, sections)
        cls_rules = rules_by_class(classes_for.get(nrm(name), []))
        used = bool(secs) or bool(cls_rules)
        # DEPRECATED упомянут в комментарии секции даже без своей секции
        mentioned = any(name.lower() in (s['title'] or '').lower() for s in sections)

        if comp.get('deprecated') and (used or mentioned):
            if secs:
                where = secs[0]
            elif cls_rules:
                where = {'file': cls_rules[0].get('file'), 'line': cls_rules[0]['line']}
            else:
                where = next(s for s in sections if name.lower() in (s['title'] or '').lower())
            via = (' — через %s' % ', '.join(sorted({r['selector'].split(':')[0].split('[')[0]
                                                     for r in cls_rules}))) if cls_rules and not secs else ''
            out.append(dict(cat='DEPRECATED_USE', sev='high', proto=proto['id'],
                            file=where.get('file'), line=where.get('line'), subject=name,
                            msg='помечен DEPRECATED в ДС, заменён на %s%s'
                                % (comp.get('replacedBy', 'Button (Size=…)'), via)))
            continue

        if not used:
            # легаси-прототипы развивать не планируется — не считать их дырами
            if name in watchlist and not comp.get('deprecated') and proto.get('tier') != 'legacy':
                out.append(dict(cat='MISSING_COMPONENT', sev='info', proto=proto['id'],
                                file=None, line=None, subject=name,
                                msg='есть в ДС (обновлён %s), в коде не найден'
                                    % (comp.get('updatedAt', '')[:10] or '—')))
            continue

        if proto.get('tier') == 'legacy':
            continue
        states = comp.get('props', {}).get('State', [])
        have = set()
        sec_line, sec_file = None, None
        for s in secs:
            sec_line, sec_file = s.get('line'), s.get('file')
            for r in rules_by_section[s['name']]:
                have |= set(r['states'])
        for r in cls_rules:
            have |= set(r['states'])
            if sec_line is None:
                sec_line, sec_file = r['line'], r.get('file')
        wrapper_states = set(cfg.get('statesProvidedByWrapper') or [])
        have_anywhere = set()
        for r in proto['rules']:
            have_anywhere |= set(r['states'])
        for st in states:
            if st == 'Default':
                continue
            over = (cfg.get('stateMapByComponent') or {}).get(name, {})
            wants = over.get(st) or state_map.get(st, [])
            if not wants:
                continue
            pool = have_anywhere if st in wrapper_states else have
            if not any(w in pool for w in wants):
                out.append(dict(cat='STATE_GAP', sev='medium', proto=proto['id'],
                                file=sec_file, line=sec_line, subject='%s / State=%s' % (name, st),
                                msg='в ДС состояние есть, в CSS ни одного из %s'
                                    % ', '.join(wants)))


def check_ds_defects(ds, out):
    seen = collections.Counter(nrm(c['name']) for c in ds.get('components', []))
    reported_dup = set()
    for comp in ds.get('components', []):
        name = comp['name']
        drawn, size = comp.get('drawnVariants', 0), comp.get('matrixSize', 0)
        miss = comp.get('missingVariants') or []
        if miss:
            sample = '; '.join(', '.join('%s=%s' % kv for kv in sorted(m.items())) for m in miss[:3])
            out.append(dict(cat='DS_DEFECT', sev='medium', proto='—',
                            file='Figma', line=comp.get('nodeId') or comp.get('page'), subject=name,
                            msg='матрица неполная: нарисовано %d из %d. Нет: %s%s'
                                % (drawn, size, sample, ' …' if len(miss) > 3 else '')))
        elif size and drawn < size and not comp.get('missingKnown'):
            out.append(dict(cat='DS_DEFECT', sev='low', proto='—',
                            file='Figma', line=comp.get('page'), subject=name,
                            msg='матрица неполная: нарисовано %d из %d — какие ячейки пустые, '
                                'слепок не разбирал' % (drawn, size)))

        # мусор в именах и свойствах
        if name != name.strip():
            out.append(dict(cat='DS_DEFECT', sev='low', proto='—',
                            file='Figma', line=comp.get('page'), subject=repr(name),
                            msg='в имени лидирующий или хвостовой пробел'))
        if seen[nrm(name)] > 1 and nrm(name) not in reported_dup:
            reported_dup.add(nrm(name))
            out.append(dict(cat='DS_DEFECT', sev='medium', proto='—',
                            file='Figma', line=comp.get('page'), subject=name,
                            msg='одно имя носят %d разных компонент-сета — при вставке из '
                                'панели ассетов не отличить' % seen[nrm(name)]))
        for prop in (comp.get('props') or {}):
            if re.fullmatch(r'Property \d+', prop):
                out.append(dict(cat='DS_DEFECT', sev='low', proto='—',
                                file='Figma', line=comp.get('page'), subject=name,
                                msg='свойство осталось с именем по умолчанию «%s»' % prop))
        for prop, groups in (comp.get('caseClashes') or {}).items():
            for g in groups:
                out.append(dict(cat='DS_DEFECT', sev='medium', proto='—',
                                file='Figma', line=comp.get('nodeId'), subject=comp['name'],
                                msg='свойство %s: одно значение записано по-разному — %s'
                                    % (prop, ' / '.join(g))))


def render(findings, code, ds, cfg, chrome_counts):
    ts = datetime.datetime.now().strftime('%d.%m.%Y %H:%M')
    by_cat = collections.defaultdict(list)
    for f in findings:
        by_cat[f['cat']].append(f)
    counts = {c: len(v) for c, v in by_cat.items()}
    high = sum(1 for f in findings if f['sev'] == 'high')

    L = []
    L.append('# Night Watch R4S — сводка прогона')
    L.append('')
    L.append('%s · ДС `%s` · слепок ДС от %s'
             % (ts, ds['source']['designSystemFileKey'], ds.get('generatedAt', '—')[:10]))
    L.append('')
    L.append('**%d расхождений, из них %d важных.** Правки не вносились — режим только отчёт.' % (len(findings), high))
    L.append('')
    if not ds.get('variablesComplete'):
        L.append('> Слепок ДС неполный: %s' % ds.get('variablesNote', ''))
        L.append('> Пока он не дособран, «не подтверждён слепком» читать как «проверить вручную», а не как ошибку.')
        L.append('')
    L.append('| Категория | Штук |')
    L.append('|---|---:|')
    for cat in ['TOKEN_VALUE_DRIFT','ACCENT_MISMATCH','FOREIGN_VARIABLE','TOKEN_UNKNOWN',
                'DEPRECATED_USE','STATE_GAP','RAW_VALUE','MISSING_COMPONENT','ORPHAN_TOKEN',
                'DS_DEFECT','NOT_CHECKED']:
        if counts.get(cat):
            L.append('| %s | %d |' % (CAT_RU[cat], counts[cat]))
    L.append('')

    L.append('## Прототипы')
    L.append('')
    L.append('| Прототип | Уровень | Токенов | Сырых значений | Расхождений |')
    L.append('|---|---|---:|---:|---:|')
    for p in code['prototypes']:
        if not p['exists']:
            L.append('| %s | — | — | — | папки нет |' % p['id']); continue
        n = sum(1 for f in findings if f['proto'] == p['id'])
        raw = len([r for r in p['raws'] if not r['outOfScope']])
        L.append('| %s | %s | %d | %d | %d |' % (p['id'], p['tier'], len(p['tokens']), raw, n))
    L.append('')

    for cat in ['TOKEN_VALUE_DRIFT','ACCENT_MISMATCH','DEPRECATED_USE','STATE_GAP','DS_DEFECT',
                'FOREIGN_VARIABLE','RAW_VALUE','ORPHAN_TOKEN','TOKEN_UNKNOWN','MISSING_COMPONENT',
                'NOT_CHECKED']:
        items = by_cat.get(cat)
        if not items:
            continue
        L.append('## %s — %d' % (CAT_RU[cat], len(items)))
        L.append('')
        if cat == 'MISSING_COMPONENT':
            byp = collections.defaultdict(list)
            for f in items:
                byp[f['proto']].append(f['subject'])
            L.append('Не признак ошибки: экран может просто не использовать компонент. '
                     'Список полезен, когда компонент нужен, а его собрали заново вручную.')
            L.append('')
            for pid, names in byp.items():
                L.append('- **%s** — %s' % (pid, ', '.join(sorted(names))))
            L.append('')
            continue
        items.sort(key=lambda f: (SEV_ORDER[f['sev']], f['proto'], str(f.get('file')), f.get('line') or 0))
        shown = items if cat != 'RAW_VALUE' else items[:40]
        for f in shown:
            loc = ''
            if f.get('file'):
                loc = ' `%s%s`' % (f['file'], (':%s' % f['line']) if f.get('line') else '')
            proto = '' if f['proto'] == '—' else ' · %s' % f['proto']
            L.append('- **%s** — %s  \n  %s%s%s%s'
                     % (f['subject'], f['msg'], SEV_RU[f['sev']], proto, loc,
                        ('  \n  → `%s`' % f['fix']) if f.get('fix') else ''))
        if cat == 'RAW_VALUE' and len(items) > 40:
            L.append('- … и ещё %d — полный список в `findings.json`' % (len(items) - 40))
        L.append('')

    if chrome_counts:
        L.append('## Вне периметра ДС')
        L.append('')
        L.append(cfg['outOfScope']['comment'])
        L.append('')
        for pid, n in chrome_counts.items():
            if n:
                L.append('- %s — %d сырых значений в битрикс-хроме, не считаются дрейфом' % (pid, n))
        L.append('')

    gaps = cfg.get('knownGaps') or []
    if gaps:
        L.append('## Известные дыры в макетах')
        L.append('')
        L.append('Кадра нет в Figma — не выдумывать, спросить дизайнера.')
        L.append('')
        for g in gaps:
            L.append('- %s' % g)
        L.append('')

    L.append('---')
    L.append('')
    L.append('Отчёт собран `night-watch/bin/diff.py`. Публикацию библиотеки бот не трогает, '
             'файлы Figma не редактирует, правки в CSS вносит только по команде `--fix` '
             'и только после чекпоинта.')
    return '\n'.join(L)


def main():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = load_config(here)
    ds = json.load(open(os.path.join(here, 'snapshots', 'ds-latest.json'), encoding='utf-8'))
    code = json.load(open(os.path.join(here, 'snapshots', 'code-latest.json'), encoding='utf-8'))

    findings = []
    chrome = collections.OrderedDict()
    for p in code['prototypes']:
        if not p['exists']:
            continue
        check_tokens(p, ds, cfg, findings)
        b = check_raw(p, ds, cfg, findings)
        chrome[p['id']] = b['chrome']
        check_components(p, ds, cfg, findings)
    check_ds_defects(ds, findings)

    json.dump({'generatedAt': datetime.datetime.now().isoformat(timespec='seconds'),
               'findings': findings},
              open(os.path.join(here, 'snapshots', 'findings.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=2)
    report = render(findings, code, ds, cfg, chrome)
    rp = os.path.join(here, 'reports', 'REPORT.md')
    open(rp, 'w', encoding='utf-8').write(report + '\n')
    high = sum(1 for f in findings if f['sev'] == 'high')
    print('расхождений: %d (важных %d) → reports/REPORT.md' % (len(findings), high))
    return 1 if high else 0


if __name__ == '__main__':
    sys.exit(main())
