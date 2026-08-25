#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Разбор XML-дампа get_metadata со страницы компонентов ДС в матрицу вариантов.

Вход  : snapshots/raw/<page>.xml — то, что вернул get_metadata(fileKey, nodeId страницы)
Выход : JSON-фрагмент components[] для ds-latest.json

Опирается на то, что варианты компонент-сета лежат как
<symbol name="Size=Medium, Type=Primary, State=Hover">
внутри <frame name="ИмяКомпонента">.
"""
import json, re, sys, collections

RE_NODE = re.compile(r'<(frame|symbol|instance|component)\s+id="([^"]+)"\s+name="([^"]*)"')
RE_VARIANT = re.compile(r'^\s*([A-Za-z][\w \-]*)\s*=\s*(.+?)\s*$')


def parse(xml):
    """Возвращает {имя компонента: {'variants':[{prop:val}], 'nodeId':..}}."""
    lines = xml.split('\n')
    stack = []          # [(indent, kind, name)]
    comps = collections.OrderedDict()
    for raw in lines:
        m = RE_NODE.search(raw)
        if not m:
            continue
        indent = len(raw) - len(raw.lstrip())
        kind, node_id, name = m.group(1), m.group(2), m.group(3)
        while stack and stack[-1][0] >= indent:
            stack.pop()

        parts = [p for p in name.split(',')]
        variant = {}
        for p in parts:
            vm = RE_VARIANT.match(p)
            if vm:
                variant[vm.group(1).strip()] = vm.group(2).strip()
        if variant and len(variant) == len(parts) and stack:
            owner = stack[-1][2]
            c = comps.setdefault(owner, {'name': owner, 'nodeId': stack[-1][1], 'variants': []})
            c['variants'].append(variant)
        else:
            stack.append((indent, node_id, name.strip()))
    return comps


def summarise(comps):
    out = []
    for name, c in comps.items():
        if not c['variants']:
            continue
        props = collections.OrderedDict()
        for v in c['variants']:
            for k, val in v.items():
                props.setdefault(k, [])
                if val not in props[k]:
                    props[k].append(val)
        # полнота матрицы: сколько ячеек из декартова произведения реально нарисовано
        total = 1
        for vals in props.values():
            total *= len(vals)
        drawn = {tuple(sorted(v.items())) for v in c['variants']}
        missing = []
        if total <= 4096:
            keys = list(props.keys())

            def walk(i, acc):
                if i == len(keys):
                    t = tuple(sorted(acc.items()))
                    if t not in drawn:
                        missing.append(dict(acc))
                    return
                for val in props[keys[i]]:
                    acc[keys[i]] = val
                    walk(i + 1, acc)
                del acc[keys[i]]
            walk(0, {})
        # опечатки регистра в значениях свойств (link / Link)
        case_clashes = {}
        for k, vals in props.items():
            groups = collections.defaultdict(list)
            for v in vals:
                groups[v.lower()].append(v)
            for lv, g in groups.items():
                if len(g) > 1:
                    case_clashes.setdefault(k, []).append(g)
        out.append({
            'name': name, 'nodeId': c['nodeId'], 'assetType': 'component_set',
            'props': props, 'drawnVariants': len(drawn), 'matrixSize': total,
            'missingVariants': missing, 'caseClashes': case_clashes,
        })
    return out


def main():
    if len(sys.argv) < 2:
        print('usage: parse_ds_metadata.py <dump.xml> [dump2.xml ...]'); sys.exit(2)
    all_out = []
    for path in sys.argv[1:]:
        all_out += summarise(parse(open(path, encoding='utf-8').read()))
    print(json.dumps(all_out, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
