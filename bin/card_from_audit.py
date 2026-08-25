#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Собрать карточку change-log из результатов аудита, а не из дельты слепков.

Нужно, когда сама ДС не менялась, но аудит принёс для неё новое: найденные дефекты
и приведение прототипов к токенам. Дельта слепков тут пустая, а писать есть о чём.

    python3 bin/card_from_audit.py "Исправлено: пункт" "Исправлено: ещё пункт"

Кладёт reports/changelog-card.json в том же формате, что и review.py.
"""
import json, os, sys, datetime, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from review import card_payload, SECTIONS


def main():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    fnd = json.load(open(os.path.join(here, 'snapshots', 'findings.json'),
                         encoding='utf-8'))['findings']
    ds = json.load(open(os.path.join(here, 'snapshots', 'ds-latest.json'), encoding='utf-8'))

    sections = collections.OrderedDict((s, []) for s in SECTIONS)

    # «Исправлено» приходит аргументами: это факты о правках, машина их не выдумывает
    for arg in sys.argv[1:]:
        sec, _, text = arg.partition(':')
        sec, text = sec.strip(), text.strip()
        if sec in sections and text:
            sections[sec].append(text)

    # «В разработке» — дефекты самой ДС, найденные сверкой.
    # Неразобранные матрицы схлопываем в одну строку: карточка change-log должна
    # читаться, а не быть простынёй на двадцать пунктов.
    vague = []
    for f in fnd:
        if f['cat'] != 'DS_DEFECT':
            continue
        if 'слепок не разбирал' in f['msg']:
            import re as _re
            m = _re.search(r'нарисовано (\d+) из (\d+)', f['msg'])
            if m:
                vague.append('%s %s/%s' % (f['subject'], m.group(1), m.group(2)))
            continue
        msg = f['msg'].replace('матрица неполная: ', '')
        sections['В разработке'].append('%s: %s' % (f['subject'], msg))
    if vague:
        sections['В разработке'].append(
            'Неполные матрицы ещё у %d компонентов: %s' % (len(vague), ', '.join(vague)))
    for issue in (ds.get('knownDsIssues') or []):
        sections['В разработке'].append(issue)

    # схлопнуть повторы, сохранив порядок
    for k, v in sections.items():
        seen, out = set(), []
        for i in v:
            if i not in seen:
                seen.add(i); out.append(i)
        sections[k] = out

    today = datetime.date.today().strftime('%d.%m.%y')
    payload = card_payload(sections, today)
    payload['sections'] = sections
    payload['hasChanges'] = any(sections.values())
    payload['source'] = 'audit'
    p = os.path.join(here, 'reports', 'changelog-card.json')
    json.dump(payload, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    print('карточка от %s: %s' % (today, ', '.join(
        '%s %d' % (k, len(v)) for k, v in sections.items() if v)))
    print('символов в теле: %d, диапазонов стилей: %d' % (len(payload['body']), len(payload['ranges'])))
    return 0


if __name__ == '__main__':
    sys.exit(main())
