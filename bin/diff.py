#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Night Watch DS-helper — the comparison engine.

ds-latest.json (what the design system has) × code-latest.json (what the code
has) → findings.json + reports/REPORT.md

Categories (Night Watch turned inside out — the DS is the truth here):
  TOKEN_VALUE_DRIFT  a token's value drifted from its Figma Variable
  TOKEN_UNKNOWN      token references a name the DS no longer has
  ORPHAN_TOKEN       declared and never used
  RAW_VALUE          hardcoded value where a token exists
  DEPRECATED_USE     a DEPRECATED component is in use
  STATE_GAP          the DS draws a state the CSS never covers
  MISSING_COMPONENT  watchlisted DS component absent from the code
  DS_DEFECT          defect in the DS itself (matrix holes, casing mismatch)
"""
import json, os, re, sys, datetime, collections

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from i18n import t

SEV_ORDER = {'high': 0, 'medium': 1, 'low': 2, 'info': 3}
SEV_RU = {'high': t('важно', 'high'), 'medium': t('средне', 'medium'),
          'low': t('мелочь', 'low'), 'info': t('к сведению', 'info')}

CAT_RU = {
    'TOKEN_VALUE_DRIFT': t('Значение токена разошлось с Figma', 'Token value drifted from Figma'),
    'TOKEN_UNKNOWN':     t('Токен ссылается на несуществующее имя ДС', 'Token references a name missing from the DS'),
    'ORPHAN_TOKEN':      t('Объявленный токен нигде не используется', 'Declared token is never used'),
    'RAW_VALUE':         t('Сырое значение вместо токена', 'Raw value where a token exists'),
    'DEPRECATED_USE':    t('Используется DEPRECATED-компонент', 'DEPRECATED component in use'),
    'STATE_GAP':         t('Состояние из ДС не покрыто в CSS', 'DS state not covered in CSS'),
    'MISSING_COMPONENT': t('Компонент ДС не заведён в прототипе', 'DS component absent from the code'),
    'DS_DEFECT':         t('Дефект в самой дизайн-системе', 'Defect in the design system itself'),
    'NOT_CHECKED':       t('Не сверено — нет данных в слепке', 'Not checked — snapshot lacks the data'),
    'ACCENT_MISMATCH':   t('Значение взято из другого акцента ДС', 'Value taken from a different DS accent'),
    'FOREIGN_VARIABLE':  t('Токен назван по переменной чужой коллекции', 'Token named after a foreign-kit variable'),
}


def load_config(here):
    """Config loading with a human error message — the first thing a newcomer sees."""
    p = os.path.join(here, 'config.json')
    if not os.path.exists(p):
        sys.stderr.write(t(
            'Нет config.json.\nСкопируйте пример и впишите свои ключи Figma и пути к прототипам:\n\n    cp config.example.json config.json\n\n',
            'No config.json.\nCopy the example and fill in your Figma keys and prototype paths:\n\n    cp config.example.json config.json\n\n'))
        raise SystemExit(2)
    try:
        return json.load(open(p, encoding='utf-8'))
    except ValueError as e:
        sys.stderr.write(t('config.json — битый JSON: %s\n', 'config.json is broken JSON: %s\n') % e)
        raise SystemExit(2)


def fp(f):
    """
    A finding's fingerprint for the baseline. The line number is deliberately
    excluded: code moves, the finding stays the same. The repeat counter is
    dropped too — otherwise "57 times" and "58 times" would differ.
    """
    subj = re.sub(r'\s*—\s*\d+\s+раз\w*\s*$', '', str(f.get('subject', '')))
    return '|'.join([f['cat'], str(f['proto']), str(f.get('file') or ''), subj])


def apply_baseline(findings, baseline):
    """Marks findings new / baselined. Also returns what got fixed."""
    known = set(baseline.get('fingerprints') or [])
    seen = set()
    for f in findings:
        k = fp(f)
        seen.add(k)
        f['state'] = 'baselined' if k in known else 'new'
    fixed = sorted(known - seen)
    return fixed


SEV_RANK = {'high': 3, 'medium': 2, 'low': 1, 'info': 0}


def decide_exit(findings, mode):
    """
    What fails the run. By default only new findings do: that way the bot can
    be switched on over legacy code with hundreds of findings, and it will
    watch that the count does not grow.
    """
    if mode == 'never':
        return 0
    if mode == 'new':
        return 1 if any(f.get('state') == 'new' for f in findings) else 0
    floor = SEV_RANK.get(mode, 3)
    for f in findings:
        if f.get('state') == 'baselined':
            continue
        if SEV_RANK.get(f['sev'], 0) >= floor:
            return 1
    return 0


def to_sarif(findings, code, cfg, include_baselined=False):
    # GitHub anchors annotations by repo-root paths, so a bare file name is
    # not enough: prepend the source folder.
    prefix = {}
    for p in code.get('prototypes', []):
        d = p.get('dir') or ''
        prefix[p['id']] = '' if os.path.isabs(d) else (d.rstrip('/') + '/' if d else '')
    """
    SARIF, so findings land in GitHub as inline line annotations rather than
    a report someone has to open separately.
    """
    rules, rule_ids = [], set()
    results = []
    for f in findings:
        if f.get('state') == 'baselined' and not include_baselined:
            continue
        rid = f['cat']
        if rid not in rule_ids:
            rule_ids.add(rid)
            rules.append({'id': rid, 'name': rid,
                          'shortDescription': {'text': CAT_RU.get(rid, rid)},
                          'defaultConfiguration': {'level': 'warning'}})
        loc = []
        if f.get('file') and f['file'] != 'Figma':
            uri = prefix.get(f['proto'], '') + str(f['file'])
            loc = [{'physicalLocation': {
                'artifactLocation': {'uri': uri.replace('\\', '/')},
                'region': {'startLine': max(1, int(f.get('line') or 1))}}}]
        results.append({
            'ruleId': rid,
            'level': {'high': 'error', 'medium': 'warning',
                      'low': 'note', 'info': 'note'}[f['sev']],
            'message': {'text': '%s — %s' % (f.get('subject'), f.get('msg'))},
            'locations': loc,
            'partialFingerprints': {'nightWatch/v1': fp(f)},
        })
    return {'$schema': 'https://json.schemastore.org/sarif-2.1.0.json',
            'version': '2.1.0',
            'runs': [{'tool': {'driver': {'name': 'Night Watch DS-helper',
                                          'informationUri': 'https://github.com/Vladislavkotyreb/DS-helper',
                                          'rules': rules}},
                      'results': results}]}


def ref(token_name):
    """How to reference a token in its own syntax: $scss / @less / var(--custom-property)."""
    return token_name if token_name[:1] in ('$', '@') else 'var(%s)' % token_name


def plural(n, one, few, many):
    from i18n import lang
    if lang() != 'ru':
        return '%d %s' % (n, one if n == 1 else many)
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
    """DS variable value for the prototype theme. None — value not captured."""
    v = ds.get('variables', {}).get(name)
    if v is None:
        return None
    if not isinstance(v, dict):
        return v
    return v.get('any') or v.get(theme)


def ds_value_index(ds, theme):
    """value → [DS variable names] for the given theme"""
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
    """"Inputs / Message" equals "Inputs/Message". Legacy cross-markers stripped."""
    n = str(name).replace('\u274c', ' ').strip()
    n = re.sub(r'\s*/\s*', '/', n)
    n = re.sub(r'\s+', ' ', n)
    return n.lower()


def match_sections(ds_name, sections):
    """CSS sections belonging to a DS component"""
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

    # The declared theme is trusted only when the values back it up. A production
    # repo may mix themes, and lying about drift is not an option.
    votes = {'light': 0, 'dark': 0}
    for tok in proto['tokens'].values():
        fig = tok.get('figmaName')
        v = variables.get(fig) if fig else None
        if not isinstance(v, dict) or 'light' not in v or 'dark' not in v:
            continue
        got = norm_color(tok['value']) or as_number(tok['value'])
        for m in ('light', 'dark'):
            cand = norm_color(v[m]) or as_number(v[m])
            if cand is not None and got is not None and cand == got:
                votes[m] += 1
    theme_conflict = False
    if votes['light'] + votes['dark'] >= 3 and votes[theme] < votes['light' if theme == 'dark' else 'dark']:
        other = 'light' if theme == 'dark' else 'dark'
        theme_conflict = True
        out.append(dict(cat='NOT_CHECKED', sev='info', proto=proto['id'],
                        file=None, line=None, subject=t('тема источника', 'source theme'),
                        msg=t('в конфиге заявлена %s, но значения токенов сходятся с %s (%d против %d) — сверку значений не делаю, поправьте theme или разнесите источник по темам',
                              'config declares %s but token values match %s (%d vs %d) — skipping value checks; fix theme or split the source by theme')
                            % (theme, other, votes[other], votes[theme])))
        theme = other
    for tname, tok in sorted(proto['tokens'].items()):
        fig = tok.get('figmaName')
        if not fig:
            continue
        if fig not in variables:
            foreign = next((v for k, v in (cfg.get('foreignVariablePrefixes') or {}).items()
                            if fig.startswith(k)), None)
            if foreign:
                out.append(dict(cat='FOREIGN_VARIABLE', sev='medium', proto=proto['id'],
                                file=tok['file'], line=tok['line'], subject=tname,
                                msg=t('«%s» — не переменная %s: %s', '"%s" is not a %s variable: %s')
                                    % (fig, cfg.get('project', t('вашей ДС', 'your DS')), foreign)))
            elif complete:
                out.append(dict(cat='TOKEN_UNKNOWN', sev='medium', proto=proto['id'],
                                file=tok['file'], line=tok['line'], subject=tname,
                                msg=t('комментарий указывает на «%s», такой переменной в ДС нет — переименована или удалена',
                                      'comment points to "%s", no such variable in the DS — renamed or removed') % fig))
            else:
                out.append(dict(cat='TOKEN_UNKNOWN', sev='info', proto=proto['id'],
                                file=tok['file'], line=tok['line'], subject=tname,
                                msg=t('«%s» не подтверждён текущим слепком ДС — дособрать слепок, прежде чем считать это расхождением',
                                      '"%s" is not confirmed by the current DS snapshot — complete the snapshot before treating this as drift') % fig))
            continue
        if theme_conflict:
            unchecked += 1
            continue
        want, got = var_value(ds, fig, theme), tok['value']
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
                                    file=tok['file'], line=tok['line'], subject=tname,
                                    msg=t('%s: %s — это акцент %s, а прототип на %s (%s)',
                                        '%s: %s belongs to accent %s, but this prototype uses %s (%s)')
                                        % (fig, gc, ' / '.join(other), mine, wc),
                                    fix='%s:%s' % (tname, wc)))
                else:
                    out.append(dict(cat='TOKEN_VALUE_DRIFT', sev='high', proto=proto['id'],
                                    file=tok['file'], line=tok['line'], subject=tname,
                                    msg=t('%s: в Figma %s, в коде %s', '%s: Figma has %s, code has %s') % (fig, wc, gc),
                                    fix='%s:%s' % (tname, wc)))
            continue
        wn, gn = as_number(want), as_number(got)
        if wn is not None and gn is not None and wn != gn:
            out.append(dict(cat='TOKEN_VALUE_DRIFT', sev='high', proto=proto['id'],
                            file=tok['file'], line=tok['line'], subject=tname,
                            msg=t('%s: в Figma %g, в коде %g', '%s: Figma has %g, code has %g') % (fig, wn, gn),
                            fix='%s:%gpx' % (tname, wn)))

    if unchecked:
        out.append(dict(cat='NOT_CHECKED', sev='info', proto=proto['id'],
                        file=None, line=None,
                        subject=t('%d токенов', '%d tokens') % unchecked,
                        msg=t('значение для режима %s в слепке ДС не снято — сверить не с чем',
                              'the DS snapshot has no value for mode %s — nothing to compare against') % theme))

    for tname, tok in sorted(proto['tokens'].items()):
        if proto['usage'].get(tname, 0) == 0:
            out.append(dict(cat='ORPHAN_TOKEN', sev='low', proto=proto['id'],
                            file=tok['file'], line=tok['line'], subject=tname,
                            msg=t('объявлен, ни одного обращения к %s%s', 'declared, no reference to %s%s') % (
                                tname, t(' в источнике', ' in the source') if proto.get('tier') == 'production'
                                else t(' в прототипе', ' in the prototype'))))


def check_raw(proto, ds, cfg, out):
    """
    Raw values. Output rule: what one substitution fixes is shown per line;
    what occurs by the hundreds collapses into a counter line. Otherwise the
    report on a production repo becomes a wall nobody reads.
    """
    theme = proto.get('theme') or 'light'
    by_color, by_num = ds_value_index(ds, theme)

    # value → local tokens holding it. There may be several, and then naming
    # one is wrong: #ffffff can be a background or dark-theme text.
    local_by_color, local_by_num = collections.defaultdict(list), collections.defaultdict(list)
    for tn, tok in proto['tokens'].items():
        c = norm_color(tok['value'])
        if c:
            local_by_color[c].append(tn)
        else:
            n = as_number(tok['value'])
            if n is not None:
                local_by_num[n].append(tn)

    def phrase(local_list, ds_names):
        if len(local_list) == 1:
            return t('есть токен %s', 'token %s exists') % local_list[0], ref(local_list[0])
        if ds_names:
            head = ds_names[0]
            tail = (t(' (или %s — значение неоднозначное)', ' (or %s — the value is ambiguous)') % ', '.join(ds_names[1:3])) \
                if len(ds_names) > 1 else ''
            return t('это %s%s', 'this is %s%s') % (head, tail), None
        if local_list:
            return (t('совпадает с %s — токенов несколько, выбирать по смыслу',
                      'matches %s — several tokens share the value, pick by meaning')
                    % ', '.join(local_list[:3])), None
        return None, None

    buckets = collections.Counter()
    agg = collections.OrderedDict()   # (kind, value, reason) → [count, first seen]

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
                                msg=t('сырой %s — %s', 'raw %s — %s') % (v, msg), fix=fix))
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
                                msg=t('сырые %s — %s', 'raw %s — %s') % (r['value'], msg), fix=fix))
            elif names:
                bump('length', r['value'], 'known', r)
            else:
                bump('length', r['value'], 'unknown', r)

    complete = ds.get('variablesComplete', False)
    for (kind, value, reason), (cnt, where) in agg.items():
        fl, ln, sel = where
        many = cnt > 1
        head = ('%s — %s' % (value, plural(cnt, *t(('раз', 'раза', 'раз'), ('time', 'times', 'times'))))
                if many else sel)
        if reason == 'known':
            names = (by_color if kind == 'color' else by_num).get(
                norm_color(value) if kind == 'color' else as_number(value), [])
            msg = t('значение есть в ДС (%s%s), но записано числом',
                    'the DS has this value (%s%s), but it is hardcoded') % (
                names[0] if names else '—',
                t(' и ещё %d', ' and %d more') % (len(names) - 1) if len(names) > 1 else '')
            sev = 'medium'
        else:
            msg = (t('в палитре ДС такого нет', 'not in the DS palette') if kind == 'color'
                   else t('вне шкалы ДС', 'outside the DS scale')) + \
                  ('' if complete else t('; слепок ДС неполон', '; the DS snapshot is incomplete'))
            sev = 'medium' if (kind == 'color' and complete) else 'low'
        if many:
            msg += t('. Первое вхождение %s: %s', '. First occurrence %s: %s') % (fl, sel)
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

    # DS component → CSS classes declared in config.componentMap
    classes_for = collections.defaultdict(list)
    for cls, ds_name in (cfg.get('componentMap') or {}).items():
        classes_for[nrm(ds_name)].append(cls)

    def rules_by_class(classes):
        """Rules whose selector starts with one of the component's classes."""
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
        # DEPRECATED may be mentioned in a section comment without its own section
        mentioned = any(name.lower() in (s['title'] or '').lower() for s in sections)

        if comp.get('deprecated') and (used or mentioned):
            if secs:
                where = secs[0]
            elif cls_rules:
                where = {'file': cls_rules[0].get('file'), 'line': cls_rules[0]['line']}
            else:
                where = next(s for s in sections if name.lower() in (s['title'] or '').lower())
            via = (t(' — через %s', ' — via %s') % ', '.join(sorted({r['selector'].split(':')[0].split('[')[0]
                                                     for r in cls_rules}))) if cls_rules and not secs else ''
            out.append(dict(cat='DEPRECATED_USE', sev='high', proto=proto['id'],
                            file=where.get('file'), line=where.get('line'), subject=name,
                            msg=t('помечен DEPRECATED в ДС, заменён на %s%s', 'marked DEPRECATED in the DS, replaced by %s%s')
                                % (comp.get('replacedBy', '?'), via)))
            continue

        if not used:
            # legacy prototypes are frozen — their gaps are not debts
            if name in watchlist and not comp.get('deprecated') and proto.get('tier') != 'legacy':
                out.append(dict(cat='MISSING_COMPONENT', sev='info', proto=proto['id'],
                                file=None, line=None, subject=name,
                                msg=t('есть в ДС (обновлён %s), в коде не найден', 'exists in the DS (updated %s), not found in the code')
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
                                msg=t('в ДС состояние есть, в CSS ни одного из %s', 'the DS draws this state, the CSS has none of %s')
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
                            msg=t('матрица неполная: нарисовано %d из %d. Нет: %s%s',
                                'variant matrix incomplete: %d of %d drawn. Missing: %s%s')
                                % (drawn, size, sample, ' …' if len(miss) > 3 else '')))
        elif size and drawn < size and not comp.get('missingKnown'):
            out.append(dict(cat='DS_DEFECT', sev='low', proto='—',
                            file='Figma', line=comp.get('page'), subject=name,
                            msg=t('матрица неполная: нарисовано %d из %d — какие ячейки пустые, слепок не разбирал',
                                  'variant matrix incomplete: %d of %d drawn — the snapshot did not resolve which cells are empty') % (drawn, size)))

        # junk in names and properties
        if name != name.strip():
            out.append(dict(cat='DS_DEFECT', sev='low', proto='—',
                            file='Figma', line=comp.get('page'), subject=repr(name),
                            msg=t('в имени лидирующий или хвостовой пробел', 'leading or trailing space in the name')))
        if seen[nrm(name)] > 1 and nrm(name) not in reported_dup:
            reported_dup.add(nrm(name))
            out.append(dict(cat='DS_DEFECT', sev='medium', proto='—',
                            file='Figma', line=comp.get('page'), subject=name,
                            msg=t('одно имя носят %d разных компонент-сета — при вставке из панели ассетов не отличить',
                                  '%d different component sets share this name — indistinguishable in the assets panel') % seen[nrm(name)]))
        for prop in (comp.get('props') or {}):
            if re.fullmatch(r'Property \d+', prop):
                out.append(dict(cat='DS_DEFECT', sev='low', proto='—',
                                file='Figma', line=comp.get('page'), subject=name,
                                msg=t('свойство осталось с именем по умолчанию «%s»', 'property still has its default name "%s"') % prop))
        for prop, groups in (comp.get('caseClashes') or {}).items():
            for g in groups:
                out.append(dict(cat='DS_DEFECT', sev='medium', proto='—',
                                file='Figma', line=comp.get('nodeId'), subject=comp['name'],
                                msg=t('свойство %s: одно значение записано по-разному — %s', 'property %s: one value spelled differently — %s')
                                    % (prop, ' / '.join(g))))


def render(findings, code, ds, cfg, chrome_counts, baseline=None, fixed=None):
    ts = datetime.datetime.now().strftime('%d.%m.%Y %H:%M')
    fresh = [f for f in findings if f.get('state') != 'baselined']
    based = [f for f in findings if f.get('state') == 'baselined']
    by_cat = collections.defaultdict(list)
    for f in fresh:
        by_cat[f['cat']].append(f)
    counts = {c: len(v) for c, v in by_cat.items()}
    high = sum(1 for f in fresh if f['sev'] == 'high')

    L = []
    L.append(t('# Night Watch %s — сводка прогона', '# Night Watch %s — run summary') % cfg.get('project', ''))
    L.append('')
    L.append(t('%s · ДС `%s` · слепок ДС от %s', '%s · DS `%s` · DS snapshot from %s')
             % (ts, ds['source']['designSystemFileKey'], ds.get('generatedAt', '—')[:10]))
    L.append('')
    if baseline:
        L.append(t('**Новых расхождений: %d, из них важных %d.** В базовой линии — %d, они приняты и провал прогона не вызывают.',
                   '**New findings: %d, %d of them high.** Baseline holds %d — accepted, they do not fail the run.') % (len(fresh), high, len(based)))
        if fixed:
            L.append('')
            L.append(t('С прошлой базовой линии **исправлено %d** — их можно вычеркнуть командой `python3 bin/nw.py --accept`.',
                       '**%d fixed** since the last baseline — run `python3 bin/nw.py --accept` to retire them.') % len(fixed))
    else:
        L.append(t('**%d расхождений, из них %d важных.** Базовой линии нет: включить бота на существующем коде удобнее командой `python3 bin/nw.py --accept`, тогда он станет следить, чтобы расхождений не прибавлялось.',
                   '**%d findings, %d of them high.** No baseline yet: on existing code run `python3 bin/nw.py --accept` once, and the bot will watch that the count does not grow.')
                 % (len(fresh), high))
    L.append('')
    L.append(t('Правки не вносились — режим только отчёт.', 'No files were modified — report-only mode.'))
    L.append('')
    if not ds.get('variablesComplete'):
        L.append(t('> Слепок ДС неполный: %s', '> The DS snapshot is incomplete: %s') % ds.get('variablesNote', ''))
        L.append(t('> Пока он не дособран, «не подтверждён слепком» читать как «проверить вручную», а не как ошибку.',
                   '> Until it is completed, read "not confirmed by the snapshot" as "verify manually", not as an error.'))
        L.append('')
    L.append(t('| Категория | Штук |', '| Category | Count |'))
    L.append('|---|---:|')
    for cat in ['TOKEN_VALUE_DRIFT','ACCENT_MISMATCH','FOREIGN_VARIABLE','TOKEN_UNKNOWN',
                'DEPRECATED_USE','STATE_GAP','RAW_VALUE','MISSING_COMPONENT','ORPHAN_TOKEN',
                'DS_DEFECT','NOT_CHECKED']:
        if counts.get(cat):
            L.append('| %s | %d |' % (CAT_RU[cat], counts[cat]))
    L.append('')

    L.append(t('## Прототипы', '## Prototypes'))
    L.append('')
    L.append(t('| Прототип | Уровень | Токенов | Сырых значений | Расхождений |',
               '| Prototype | Tier | Tokens | Raw values | Findings |'))
    L.append('|---|---|---:|---:|---:|')
    for p in code['prototypes']:
        if not p['exists']:
            L.append(t('| %s | — | — | — | папки нет |', '| %s | — | — | — | folder missing |') % p['id']); continue
        n = sum(1 for f in fresh if f['proto'] == p['id'])
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
            L.append(t('Не признак ошибки: экран может просто не использовать компонент. Список полезен, когда компонент нужен, а его собрали заново вручную.',
                       'Not necessarily a problem: a screen may simply not use the component. The list matters when the component was needed but rebuilt by hand.'))
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
            L.append(t('- … и ещё %d — полный список в `findings.json`', '- … and %d more — full list in `findings.json`') % (len(items) - 40))
        L.append('')

    if chrome_counts:
        L.append(t('## Вне периметра ДС', '## Outside the DS perimeter'))
        L.append('')
        L.append((cfg.get('outOfScope') or {}).get('comment',
                 t('Селекторы из outOfScope — чужой хром, дрейфом ДС не считаются.',
                   'outOfScope selectors are foreign chrome, never counted as DS drift.')))
        L.append('')
        for pid, n in chrome_counts.items():
            if n:
                L.append(t('- %s — %d сырых значений в чужом хроме, не считаются дрейфом',
                           '- %s — %d raw values inside foreign chrome, not counted as drift') % (pid, n))
        L.append('')

    gaps = cfg.get('knownGaps') or []
    if gaps:
        L.append(t('## Известные дыры в макетах', '## Known gaps in the mockups'))
        L.append('')
        L.append(t('Кадра нет в Figma — не выдумывать, спросить дизайнера.',
                   'The frame does not exist in Figma — do not invent it, ask the designer.'))
        L.append('')
        for g in gaps:
            L.append('- %s' % g)
        L.append('')

    if based:
        L.append(t('## Принято в базовую линию — %d', '## Accepted into the baseline — %d') % len(based))
        L.append('')
        L.append(t('Эти расхождения уже были, когда бота включали. Он их помнит и не считает провалом, но следит, чтобы список не рос.',
                   'These findings existed when the bot was switched on. It remembers them, does not fail the run over them, and watches that the list does not grow.'))
        L.append('')
        bc = collections.Counter(f['cat'] for f in based)
        L.append(t('| Категория | Штук |', '| Category | Count |'))
        L.append('|---|---:|')
        for c, n in bc.most_common():
            L.append('| %s | %d |' % (CAT_RU.get(c, c), n))
        L.append('')

    if fixed:
        L.append(t('## Исправлено с прошлого раза — %d', '## Fixed since last time — %d') % len(fixed))
        L.append('')
        for k in fixed[:20]:
            cat, proto, fl, subj = (k.split('|') + ['', '', '', ''])[:4]
            L.append('- %s · %s · %s' % (CAT_RU.get(cat, cat), proto, subj or fl))
        if len(fixed) > 20:
            L.append(t('- … и ещё %d', '- … and %d more') % (len(fixed) - 20))
        L.append('')

    L.append('---')
    L.append('')
    L.append(t('Отчёт собран `night-watch/bin/diff.py`. Публикацию библиотеки бот не трогает, файлы Figma не редактирует, правки в CSS вносит только по команде `--fix` и только после чекпоинта.',
               'Report built by `bin/diff.py`. The bot never publishes the library, never edits Figma files, and touches CSS only on an explicit `--fix` — and only after a checkpoint.'))
    return '\n'.join(L)


def main():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = load_config(here)
    ds = json.load(open(os.path.join(here, 'snapshots', 'ds-latest.json'), encoding='utf-8'))
    code = json.load(open(os.path.join(here, 'snapshots', 'code-latest.json'), encoding='utf-8'))

    args = sys.argv[1:]
    def opt(name, default=None):
        return args[args.index(name) + 1] if name in args else default
    fail_on = opt('--fail-on', cfg.get('failOn', 'new'))

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

    bl_path = os.path.join(here, 'snapshots', 'baseline.json')
    baseline = json.load(open(bl_path, encoding='utf-8')) if os.path.exists(bl_path) else {}
    fixed = apply_baseline(findings, baseline) if baseline else []
    if not baseline:
        for f in findings:
            f['state'] = 'new'

    json.dump({'generatedAt': datetime.datetime.now().isoformat(timespec='seconds'),
               'baseline': bool(baseline), 'fixedSinceBaseline': fixed,
               'findings': findings},
              open(os.path.join(here, 'snapshots', 'findings.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=2)

    sarif_path = opt('--sarif')
    if sarif_path:
        payload = to_sarif(findings, code, cfg, include_baselined='--sarif-all' in args)
        json.dump(payload, open(sarif_path, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
        print(t('SARIF → %s (%d результатов%s)', 'SARIF → %s (%d results%s)')
              % (sarif_path, len(payload['runs'][0]['results']),
                 t(', включая базовую линию', ', baseline included') if '--sarif-all' in args
                 else t(', только новые', ', new only')))

    report = render(findings, code, ds, cfg, chrome, baseline, fixed)
    open(os.path.join(here, 'reports', 'REPORT.md'), 'w', encoding='utf-8').write(report + '\n')

    new_n = sum(1 for f in findings if f.get('state') == 'new')
    base_n = len(findings) - new_n
    parts = [t('новых %d', 'new %d') % new_n]
    if baseline:
        parts.append(t('в базовой линии %d', 'baselined %d') % base_n)
    if fixed:
        parts.append(t('исправлено %d', 'fixed %d') % len(fixed))
    print(t('расхождений: %d (%s) → reports/REPORT.md', 'findings: %d (%s) → reports/REPORT.md') % (len(findings), ', '.join(parts)))
    return decide_exit(findings, fail_on)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(4)   # a crash is not "findings exist"; nw.py will not swallow it
