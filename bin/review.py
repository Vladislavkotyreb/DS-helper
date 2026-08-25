#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Night Watch DS-helper — ревью изменений в дизайн-системе.

Сравнивает предыдущий слепок ДС с текущим и раскладывает разницу
по рубрикам change-log'а: Добавлено / Изменено / Исправлено / Удалено / В разработке.

Выход:
  reports/DS-REVIEW.md         — человекочитаемое ревью
  reports/changelog-card.json  — готовая полезная нагрузка для карточки в Figma
                                 (узел берётся из config: figma.changeLogNode)

Саму запись в Figma делает агент через use_figma — здесь только детерминированный текст.
"""
import json, sys, os, sys, datetime, collections

SECTIONS = ['Добавлено', 'Изменено', 'Исправлено', 'Удалено', 'В разработке']


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


def plural(n, one, few, many):
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
    """Разница между слепками ДС → {рубрика: [строки]}"""
    out = collections.OrderedDict((s, []) for s in SECTIONS)
    if prev is None:
        out['Добавлено'].append('Первый слепок дизайн-системы: %d компонентов, %d переменных'
                                % (len(cur.get('components') or []), len(cur.get('variables') or {})))
        return out

    pc, cc = comp_index(prev), comp_index(cur)

    for name in sorted(set(cc) - set(pc)):
        c = cc[name]
        props = c.get('props') or {}
        detail = '; '.join('%s: %s' % (k, ' / '.join(v)) for k, v in props.items())
        out['Добавлено'].append('Компонент %s%s' % (name, (' — ' + detail) if detail else ''))

    for name in sorted(set(pc) - set(cc)):
        out['Удалено'].append('Компонент %s' % name)

    for name in sorted(set(pc) & set(cc)):
        a, b = pc[name], cc[name]
        ap, bp = a.get('props') or {}, b.get('props') or {}
        for prop in sorted(set(bp) - set(ap)):
            out['Добавлено'].append('%s: новое свойство %s (%s)' % (name, prop, ' / '.join(bp[prop])))
        for prop in sorted(set(ap) - set(bp)):
            out['Удалено'].append('%s: убрано свойство %s' % (name, prop))
        for prop in sorted(set(ap) & set(bp)):
            added = [v for v in bp[prop] if v not in ap[prop]]
            gone = [v for v in ap[prop] if v not in bp[prop]]
            if added:
                out['Добавлено'].append('%s: %s = %s' % (name, prop, ', '.join(added)))
            if gone:
                out['Удалено'].append('%s: убрано %s = %s' % (name, prop, ', '.join(gone)))
        if not a.get('deprecated') and b.get('deprecated'):
            out['Изменено'].append('%s помечен DEPRECATED%s'
                                   % (name, ', замена — ' + b['replacedBy'] if b.get('replacedBy') else ''))
        pa, pb = a.get('drawnVariants'), b.get('drawnVariants')
        if pa is not None and pb is not None and pa != pb:
            out['Изменено'].append('%s: нарисовано вариантов %d → %d' % (name, pa, pb))
        if (a.get('caseClashes') or {}) and not (b.get('caseClashes') or {}):
            out['Исправлено'].append('%s: приведён к одному регистру разнобой в значениях свойств' % name)

    pv, cv = prev.get('variables') or {}, cur.get('variables') or {}

    def val(d, k):
        v = d.get(k)
        if isinstance(v, dict):
            return v.get('any') or v.get('light') or v.get('dark')
        return v

    for name in sorted(set(cv) - set(pv)):
        v = val(cv, name)
        out['Добавлено'].append('Переменная %s%s' % (name, (' = ' + str(v)) if v else ''))
    for name in sorted(set(pv) - set(cv)):
        out['Удалено'].append('Переменная %s' % name)
    for name in sorted(set(pv) & set(cv)):
        a, b = val(pv, name), val(cv, name)
        if a and b and a != b:
            out['Изменено'].append('%s: %s → %s' % (name, a, b))

    # дефекты ДС, которые бот видит сам — в «В разработке»
    for c in (cur.get('components') or []):
        miss = c.get('missingVariants') or []
        if miss:
            out['В разработке'].append('%s: в матрице не хватает %s (%s)'
                % (c['name'], plural(len(miss), 'варианта', 'вариантов', 'вариантов'),
                   ', '.join('='.join(kv) for kv in sorted(miss[0].items()))))
        for prop, groups in (c.get('caseClashes') or {}).items():
            for g in groups:
                out['В разработке'].append('%s: свойство %s записано в разных регистрах — %s'
                                           % (c['name'], prop, ' / '.join(g)))
    for issue in (cur.get('knownDsIssues') or []):
        out['В разработке'].append(issue)

    return out


def card_payload(sections, date_str):
    """Текст карточки по шаблону change-log'а."""
    LS = ' '          # в шаблоне перенос в заголовке — U+2028
    heading = 'Обновление' + LS + 'от ' + date_str
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
    cur = load(os.path.join(snaps, 'ds-latest.json'))
    prev = load(os.path.join(snaps, 'ds-previous.json'))
    if cur is None:
        print('нет snapshots/ds-latest.json — сначала снять слепок ДС'); return 2

    sections = review(prev, cur)
    # «В разработке» — это текущие болячки ДС, а не дельта. Карточку они не оправдывают:
    # иначе каждый прогон писал бы в change-log одно и то же.
    DELTA = ['Добавлено', 'Изменено', 'Исправлено', 'Удалено']
    delta = sum(len(sections[s]) for s in DELTA)
    total = sum(len(v) for v in sections.values())
    today = datetime.date.today().strftime('%d.%m.%y')
    payload = card_payload(sections if delta else
                           dict((k, []) for k in SECTIONS), today)

    ds_name = (cfg.get('figma') or {}).get('designSystemName') or cfg.get('project', '')
    node = (cfg.get('figma') or {}).get('changeLogNode')
    L = ['# Ревью изменений %s' % (ds_name or 'дизайн-системы'), '',
         '%s · слепок %s vs %s' % (today, (prev or {}).get('generatedAt', '—')[:10],
                                   cur.get('generatedAt', '—')[:10]), '']
    if not delta:
        L.append('Изменений в ДС с прошлого прогона нет — карточка в change-log не нужна.')
        if sections['В разработке']:
            L.append('')
            L.append('Открытые болячки ДС никуда не делись, но это не повод писать обновление:')
            L.append('')
            for i in sections['В разработке']:
                L.append('- %s' % i)
    else:
        L.append('**%s.** Ниже — текст для карточки change-log%s.'
                 % (plural(delta, 'изменение', 'изменения', 'изменений'),
                    ' (узел `%s`)' % node if node else ''))
        L.append('')
        for sec in SECTIONS:
            items = sections[sec]
            if not items:
                continue
            if sec == 'В разработке' and not delta:
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
        print('в ДС %s → карточка нужна · reports/DS-REVIEW.md'
              % plural(delta, 'изменение', 'изменения', 'изменений'))
    else:
        print('в ДС изменений нет — карточка не нужна (открытых болячек: %d)'
              % len(sections['В разработке']))
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
