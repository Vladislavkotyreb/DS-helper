#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Build a change-log card from audit results rather than from a snapshot delta.

Useful when the DS itself did not change, yet the audit brought news for it:
defects found, prototypes migrated to tokens. The snapshot delta is empty,
but there is something to write.

    python3 bin/card_from_audit.py "Fixed: item" "Fixed: another item"

Writes reports/changelog-card.json in the same format review.py uses.
"""
import json, os, sys, datetime, collections

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from i18n import t
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from review import card_payload, SECTIONS


def main():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    fnd = json.load(open(os.path.join(here, 'snapshots', 'findings.json'),
                         encoding='utf-8'))['findings']
    ds = json.load(open(os.path.join(here, 'snapshots', 'ds-latest.json'), encoding='utf-8'))

    sections = collections.OrderedDict((s, []) for s in SECTIONS)

    # The "Fixed" items arrive as arguments: they are facts about edits, never invented
    for arg in sys.argv[1:]:
        sec, _, text = arg.partition(':')
        sec, text = sec.strip(), text.strip()
        if sec in sections and text:
            sections[sec].append(text)

    # The last rubric holds DS defects found by the check. Unresolved matrices
    # collapse into one line: a change-log card must stay readable, not become
    # a twenty-item wall.
    vague = []
    for f in fnd:
        if f['cat'] != 'DS_DEFECT':
            continue
        if 'слепок не разбирал' in f['msg'] or 'did not resolve which cells' in f['msg']:
            import re as _re
            m = _re.search(r'нарисовано (\d+) из (\d+)|(\d+) of (\d+) drawn', f['msg'])
            if m:
                a, b = (m.group(1) or m.group(3)), (m.group(2) or m.group(4))
                vague.append('%s %s/%s' % (f['subject'], a, b))
            continue
        msg = f['msg'].replace('матрица неполная: ', '').replace('variant matrix incomplete: ', '')
        sections[SECTIONS[4]].append('%s: %s' % (f['subject'], msg))
    if vague:
        sections[SECTIONS[4]].append(
            t('Неполные матрицы ещё у %d компонентов: %s', 'Incomplete matrices in %d more components: %s') % (len(vague), ', '.join(vague)))
    for issue in (ds.get('knownDsIssues') or []):
        sections[SECTIONS[4]].append(issue)

    # deduplicate, keep order
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
    print(t('карточка от %s: %s', 'card for %s: %s') % (today, ', '.join(
        '%s %d' % (k, len(v)) for k, v in sections.items() if v)))
    print(t('символов в теле: %d, диапазонов стилей: %d', 'body chars: %d, style ranges: %d') % (len(payload['body']), len(payload['ranges'])))
    return 0


if __name__ == '__main__':
    sys.exit(main())
