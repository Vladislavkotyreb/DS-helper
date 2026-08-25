#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Night Watch DS-helper — design-system change review.

Diffs the previous DS snapshot against the current one and sorts the delta
into change-log rubrics: Added / Changed / Fixed / Removed / In progress.

Output:
  reports/DS-REVIEW.md         — the human-readable review
  reports/changelog-card.json  — a ready payload for the Figma card
                                 (node comes from config: figma.changeLogNode)

The actual Figma write is done by an agent via use_figma — here only
deterministic text is produced.
"""
import json, os, sys, datetime, collections

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from i18n import t

# Change-log card rubrics; override the names via config.json →
# changelogSections (your Figma template may call them differently).
SECTIONS = [t('Добавлено', 'Added'), t('Изменено', 'Changed'),
            t('Исправлено', 'Fixed'), t('Удалено', 'Removed'),
            t('В разработке', 'In progress')]


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


def load(p, default=None):
    if not os.path.exists(p):
        return default
    return json.load(open(p, encoding='utf-8'))


def comp_index(ds):
    return {c['name']: c for c in (ds.get('components') or [])}


def review(prev, cur):
    """Delta between DS snapshots → {rubric: [lines]}"""
    out = collections.OrderedDict((s, []) for s in SECTIONS)
    if prev is None:
        out[SECTIONS[0]].append(t('Первый слепок дизайн-системы: %d компонентов, %d переменных',
                                  'First design-system snapshot: %d components, %d variables')
                                % (len(cur.get('components') or []), len(cur.get('variables') or {})))
        return out

    pc, cc = comp_index(prev), comp_index(cur)

    for name in sorted(set(cc) - set(pc)):
        c = cc[name]
        props = c.get('props') or {}
        detail = '; '.join('%s: %s' % (k, ' / '.join(v)) for k, v in props.items())
        out[SECTIONS[0]].append(t('Компонент %s%s', 'Component %s%s') % (name, (' — ' + detail) if detail else ''))

    for name in sorted(set(pc) - set(cc)):
        out[SECTIONS[3]].append(t('Компонент %s', 'Component %s') % name)

    for name in sorted(set(pc) & set(cc)):
        a, b = pc[name], cc[name]
        ap, bp = a.get('props') or {}, b.get('props') or {}
        for prop in sorted(set(bp) - set(ap)):
            out[SECTIONS[0]].append(t('%s: новое свойство %s (%s)', '%s: new property %s (%s)') % (name, prop, ' / '.join(bp[prop])))
        for prop in sorted(set(ap) - set(bp)):
            out[SECTIONS[3]].append(t('%s: убрано свойство %s', '%s: property %s removed') % (name, prop))
        for prop in sorted(set(ap) & set(bp)):
            added = [v for v in bp[prop] if v not in ap[prop]]
            gone = [v for v in ap[prop] if v not in bp[prop]]
            if added:
                out[SECTIONS[0]].append('%s: %s = %s' % (name, prop, ', '.join(added)))
            if gone:
                out[SECTIONS[3]].append(t('%s: убрано %s = %s', '%s: removed %s = %s') % (name, prop, ', '.join(gone)))
        if not a.get('deprecated') and b.get('deprecated'):
            out[SECTIONS[1]].append(t('%s помечен DEPRECATED%s', '%s marked DEPRECATED%s')
                                   % (name, (t(', замена — ', ', replaced by ') + b['replacedBy']) if b.get('replacedBy') else ''))
        pa, pb = a.get('drawnVariants'), b.get('drawnVariants')
        if pa is not None and pb is not None and pa != pb:
            out[SECTIONS[1]].append(t('%s: нарисовано вариантов %d → %d', '%s: drawn variants %d → %d') % (name, pa, pb))
        if (a.get('caseClashes') or {}) and not (b.get('caseClashes') or {}):
            out[SECTIONS[2]].append(t('%s: приведён к одному регистру разнобой в значениях свойств',
                                      '%s: property value casing unified') % name)

    pv, cv = prev.get('variables') or {}, cur.get('variables') or {}

    def val(d, k):
        v = d.get(k)
        if isinstance(v, dict):
            return v.get('any') or v.get('light') or v.get('dark')
        return v

    for name in sorted(set(cv) - set(pv)):
        v = val(cv, name)
        out[SECTIONS[0]].append(t('Переменная %s%s', 'Variable %s%s') % (name, (' = ' + str(v)) if v else ''))
    for name in sorted(set(pv) - set(cv)):
        out[SECTIONS[3]].append(t('Переменная %s', 'Variable %s') % name)
    for name in sorted(set(pv) & set(cv)):
        a, b = val(pv, name), val(cv, name)
        if a and b and a != b:
            out[SECTIONS[1]].append('%s: %s → %s' % (name, a, b))

    # DS defects the bot sees on its own go into the last rubric
    for c in (cur.get('components') or []):
        miss = c.get('missingVariants') or []
        if miss:
            out[SECTIONS[4]].append(t('%s: в матрице не хватает %s (%s)', '%s: matrix is missing %s (%s)')
                % (c['name'], plural(len(miss), t('варианта', 'variant'), t('вариантов', 'variants'), t('вариантов', 'variants')),
                   ', '.join('='.join(kv) for kv in sorted(miss[0].items()))))
        for prop, groups in (c.get('caseClashes') or {}).items():
            for g in groups:
                out[SECTIONS[4]].append(t('%s: свойство %s записано в разных регистрах — %s', '%s: property %s spelled in mixed case — %s')
                                           % (c['name'], prop, ' / '.join(g)))
    for issue in (cur.get('knownDsIssues') or []):
        out[SECTIONS[4]].append(issue)

    return out


def card_payload(sections, date_str):
    """Card text following the change-log template."""
    LS = ' '          # the template's heading line break is U+2028
    heading = t('Обновление' + LS + 'от ', 'Update' + LS + 'from ') + date_str
    body_parts, ranges = [], []
    cursor = 0
    for sec in SECTIONS:
        items = [i for i in sections.get(sec, []) if i]
        if not items:
            continue
        if body_parts:
            body_parts.append('\n\n')
            cursor += 2
        label = sec + ':\n'
        ranges.append({'start': cursor, 'end': cursor + len(label), 'style': 'label'})
        body_parts.append(label)
        cursor += len(label)
        body_parts.append('\n')
        cursor += 1
        block = '\n'.join(items)
        ranges.append({'start': cursor, 'end': cursor + len(block), 'style': 'item'})
        body_parts.append(block)
        cursor += len(block)
    return {'heading': heading, 'body': ''.join(body_parts), 'ranges': ranges,
            'date': date_str, 'year': '20' + date_str.split('.')[-1]}


def main():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    snaps = os.path.join(here, 'snapshots')
    cfg = load_config(here)
    global SECTIONS
    if cfg.get('changelogSections'):
        SECTIONS[:] = cfg['changelogSections']
    cur = load(os.path.join(snaps, 'ds-latest.json'))
    prev = load(os.path.join(snaps, 'ds-previous.json'))
    if cur is None:
        print(t('нет snapshots/ds-latest.json — сначала снять слепок ДС',
                'no snapshots/ds-latest.json — take a DS snapshot first')); return 2

    sections = review(prev, cur)
    # The last rubric holds current DS ailments, not a delta. They alone do not
    # justify a card: otherwise every run would write the same update.
    DELTA = SECTIONS[:4]
    delta = sum(len(sections[s]) for s in DELTA)
    total = sum(len(v) for v in sections.values())
    today = datetime.date.today().strftime('%d.%m.%y')
    payload = card_payload(sections if delta else
                           dict((k, []) for k in SECTIONS), today)

    ds_name = (cfg.get('figma') or {}).get('designSystemName') or cfg.get('project', '')
    node = (cfg.get('figma') or {}).get('changeLogNode')
    L = [t('# Ревью изменений %s', '# DS change review: %s') % (ds_name or t('дизайн-системы', 'design system')), '',
         t('%s · слепок %s vs %s', '%s · snapshot %s vs %s') % (today, (prev or {}).get('generatedAt', '—')[:10],
                                   cur.get('generatedAt', '—')[:10]), '']
    if not delta:
        L.append(t('Изменений в ДС с прошлого прогона нет — карточка в change-log не нужна.',
                   'No DS changes since the last run — no change-log card needed.'))
        if sections[SECTIONS[4]]:
            L.append('')
            L.append(t('Открытые болячки ДС никуда не делись, но это не повод писать обновление:',
                       'Known DS issues remain open, but that alone does not warrant an update card:'))
            L.append('')
            for i in sections[SECTIONS[4]]:
                L.append('- %s' % i)
    else:
        L.append(t('**%s.** Ниже — текст для карточки change-log%s.', '**%s.** Below is the change-log card text%s.')
                 % (plural(delta, t('изменение', 'change'), t('изменения', 'changes'), t('изменений', 'changes')),
                    (t(' (узел `%s`)', ' (node `%s`)') % node) if node else ''))
        L.append('')
        for sec in SECTIONS:
            items = sections[sec]
            if not items:
                continue
            if sec == SECTIONS[4] and not delta:
                continue
            L.append('## %s' % sec)
            L.append('')
            for i in items:
                L.append('- %s' % i)
            L.append('')
    open(os.path.join(here, 'reports', 'DS-REVIEW.md'), 'w', encoding='utf-8').write('\n'.join(L) + '\n')

    payload['hasChanges'] = bool(delta)
    payload['deltaCount'] = delta
    payload['sections'] = sections
    json.dump(payload, open(os.path.join(here, 'reports', 'changelog-card.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=2)
    if delta:
        print(t('в ДС %s → карточка нужна · reports/DS-REVIEW.md', 'DS has %s → card needed · reports/DS-REVIEW.md')
              % plural(delta, t('изменение', 'change'), t('изменения', 'changes'), t('изменений', 'changes')))
    else:
        print(t('в ДС изменений нет — карточка не нужна (открытых болячек: %d)', 'no DS changes — no card needed (open issues: %d)')
              % len(sections[SECTIONS[4]]))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(4)
