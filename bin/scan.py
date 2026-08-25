#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Night Watch DS-helper — prototype scanner.

Reads the prototypes' HTML/CSS/JS and builds code-snapshot.json: what is
declared, what is used, where raw values live, which states are covered.
No network. Deterministic: same input, same output.
"""
import json, os, re, sys, hashlib, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from i18n import t

# ---------- regexes ----------
RE_DECL       = re.compile(r'--([A-Za-z0-9_][A-Za-z0-9_-]*)\s*:\s*([^;{}]+?)\s*(?=;|$)')
RE_LINE_COMM  = re.compile(r'/\*\s*(.*?)\s*\*/\s*$')
RE_VAR_USE    = re.compile(r'var\(\s*--([A-Za-z0-9_][A-Za-z0-9_-]*)')
RE_SECTION    = re.compile(r'/\*\s*-{5,}\s*(.+?)\s*(?:-{5,}\s*\*/|-{5,}\s*$|\*/)', re.S)
RE_NODE_ID    = re.compile(r'\b(I?\d+[:-]\d+(?:;\d+[:-]\d+)*)\b')
RE_HEX        = re.compile(r'#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\b')
RE_RGBA       = re.compile(r'rgba?\([^)]*\)')
RE_PX         = re.compile(r'(?<![\w.-])(\d+(?:\.\d+)?)px\b')
RE_CLASS_ATTR = re.compile(r'class\s*=\s*"([^"]*)"')
RE_IGNORE     = re.compile(r'/\*\s*nw:ignore\s*(.*?)\s*\*/')

# states we can recognise in a selector at all
STATE_PATTERNS = [
    (':hover', ':hover'), (':active', ':active'),
    (':focus-visible', ':focus-visible'), (':focus', ':focus'),
    (':disabled', ':disabled'), (':checked', ':checked'),
    ('[aria-disabled="true"]', '[aria-disabled="true"]'),
    ('[aria-pressed="true"]', '[aria-pressed="true"]'),
    ('[aria-pressed="false"]', '[aria-pressed="false"]'),
    ('[aria-selected="true"]', '[aria-selected="true"]'),
    ('[aria-selected="false"]', '[aria-selected="false"]'),
    ('[aria-expanded="true"]', '[aria-expanded="true"]'),
    ('[aria-invalid="true"]', '[aria-invalid="true"]'),
    ('[aria-busy="true"]', '[aria-busy="true"]'),
    ('[aria-checked="true"]', '[aria-checked="true"]'),
    ('[aria-checked="mixed"]', '[aria-checked="mixed"]'),
    ('[aria-current', '[aria-current]'),
    ('.is-active', '.is-active'), ('.is-selected', '.is-selected'),
    ('.is-disabled', '.is-disabled'), ('.is-loading', '.is-loading'),
    ('.is-error', '.is-error'), ('.is-open', '.is-open'),
    ('.is-invalid', '.is-invalid'), ('.is-filled', '.is-filled'),
    ('.is-focused', '.is-focused'), ('.field--error', '.field--error'),
    ('[data-filled]', '[data-filled]'),
]

# properties where a length is screen layout, not a DS token
LAYOUT_ONLY_PROPS = {
    'width', 'max-width', 'min-width', 'height', 'max-height', 'min-height',
    'top', 'right', 'bottom', 'left', 'flex-basis', 'grid-template-columns',
    'grid-template-rows', 'background-position', 'background-size', 'transform',
    'stroke-width', 'outline-offset', 'letter-spacing',
}
# properties that must go through tokens
TOKENISED_PROPS = {
    'color', 'background', 'background-color', 'border', 'border-color', 'fill', 'stroke',
    'padding', 'padding-top', 'padding-right', 'padding-bottom', 'padding-left',
    'margin', 'margin-top', 'margin-right', 'margin-bottom', 'margin-left',
    'gap', 'row-gap', 'column-gap', 'border-radius', 'font', 'font-size', 'line-height',
    'box-shadow', 'outline',
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


def norm_color(v):
    """#ABC -> #aabbcc, #rrggbbff -> #rrggbb. Returns None when not a colour."""
    v = v.strip().lower()
    m = re.fullmatch(r'#([0-9a-f]{3,8})', v)
    if not m:
        return None
    h = m.group(1)
    if len(h) == 3:
        h = ''.join(c * 2 for c in h)
    elif len(h) == 4:
        h = ''.join(c * 2 for c in h)
    if len(h) == 8 and h[6:] == 'ff':
        h = h[:6]
    return '#' + h


def strip_comments(css):
    """Strips comments preserving text length — line numbers must not shift."""
    out, i, n = [], 0, len(css)
    while i < n:
        if css.startswith('/*', i):
            j = css.find('*/', i + 2)
            j = n if j == -1 else j + 2
            out.append(''.join('\n' if c == '\n' else ' ' for c in css[i:j]))
            i = j
        else:
            out.append(css[i]); i += 1
    return ''.join(out)


def line_of(text, pos):
    return text.count('\n', 0, pos) + 1


def parse_tokens(css_text, fname, line_offset=0):
    """Tokens from :root. Value plus the Figma name from the trailing comment."""
    tokens = {}
    for ln, raw in enumerate(css_text.split('\n'), 1):
        decls = list(RE_DECL.finditer(raw))
        if not decls:
            continue
        comm = RE_LINE_COMM.search(raw)
        figma_name = None
        if comm:
            c = comm.group(1).strip()
            # a Figma name looks like Color/Text/Default/Primary or Layout/Font-size/Text-M
            if re.fullmatch(r'[A-Za-zА-Яа-я0-9 _./+-]+', c) and '/' in c and len(c) < 80:
                figma_name = c
        for k, d in enumerate(decls):
            name = '--' + d.group(1)
            value = d.group(2).strip()
            tokens[name] = {
                'value': value,
                'color': norm_color(value),
                'figmaName': figma_name if k == len(decls) - 1 else None,
                'file': fname,
                'line': ln + line_offset,
            }
    return tokens


def split_sections(css_text):
    """[(section name, node-ids, start, end)] from header comments /* ----- X ----- */"""
    marks = []
    for m in re.finditer(r'/\*\s*-{5,}\s*(.+?)(?:\n|\*/)', css_text):
        title = m.group(1).strip().rstrip('-').strip()
        marks.append((title, m.start()))
    out = []
    for i, (title, start) in enumerate(marks):
        end = marks[i + 1][1] if i + 1 < len(marks) else len(css_text)
        nodes = RE_NODE_ID.findall(css_text[start:min(start + 400, end)])
        name = re.sub(r'\s*\([^)]*\)\s*$', '', title).strip()
        out.append({'title': title, 'name': name, 'nodes': nodes, 'start': start, 'end': end})
    return out


def scan_css(path, rel, out_of_scope_prefixes):
    return scan_css_text(open(path, encoding='utf-8').read(), rel, out_of_scope_prefixes)


def scan_css_text(src, rel, out_of_scope_prefixes, line_offset=0):
    bare = strip_comments(src)
    sections = split_sections(src)
    ignore_lines = set()
    for m in RE_IGNORE.finditer(src):
        ignore_lines.add(line_of(src, m.start()) + line_offset)

    tokens = parse_tokens(src, rel, line_offset) if ':root' in src else {}
    usage = {}
    for m in RE_VAR_USE.finditer(bare):
        usage['--' + m.group(1)] = usage.get('--' + m.group(1), 0) + 1

    rules, raws, sels_all = [], [], []
    for m in re.finditer(r'([^{}]+)\{([^{}]*)\}', bare):
        sel = ' '.join(m.group(1).split())
        body = m.group(2)
        if not sel or sel.startswith('@'):
            continue
        sel_line = line_of(bare, m.start()) + line_offset
        sels_all.append(sel)
        if ':root' in sel:
            continue
        out_of_scope = any(p in sel for p in out_of_scope_prefixes)

        states = sorted({label for pat, label in STATE_PATTERNS if pat in sel})
        sec = next((s['name'] for s in sections if s['start'] <= m.start(1) < s['end']), None)
        rules.append({'selector': sel, 'line': sel_line, 'states': states, 'file': rel,
                      'section': sec, 'outOfScope': out_of_scope})

        for dm in re.finditer(r'([-a-z]+)\s*:\s*([^;]+)', body):
            prop, val = dm.group(1).strip(), dm.group(2).strip()
            dline = line_of(bare, m.start(2) + dm.start()) + line_offset
            if dline in ignore_lines:
                continue
            stripped = re.sub(r'var\([^)]*\)', '', val)
            hexes = [h for h in RE_HEX.findall(stripped)]
            rgbas = RE_RGBA.findall(stripped)
            pxs = [p for p in RE_PX.findall(stripped) if float(p) not in (0.0,)]
            if prop in LAYOUT_ONLY_PROPS:
                pxs = []
            if prop not in TOKENISED_PROPS and prop not in LAYOUT_ONLY_PROPS:
                pxs = []
            for h in hexes:
                raws.append({'file': rel, 'line': dline, 'selector': sel, 'prop': prop,
                             'kind': 'color', 'value': norm_color(h), 'raw': h,
                             'section': sec, 'outOfScope': out_of_scope})
            for r in rgbas:
                raws.append({'file': rel, 'line': dline, 'selector': sel, 'prop': prop,
                             'kind': 'color-fn', 'value': ' '.join(r.split()), 'raw': r,
                             'section': sec, 'outOfScope': out_of_scope})
            for p in pxs:
                raws.append({'file': rel, 'line': dline, 'selector': sel, 'prop': prop,
                             'kind': 'length', 'value': p + 'px', 'raw': p + 'px',
                             'section': sec, 'outOfScope': out_of_scope})

    return {'tokens': tokens, 'usage': usage, 'rules': rules, 'raws': raws,
            'sections': [{'name': s['name'], 'title': s['title'], 'nodes': s['nodes'],
                          'line': line_of(src, s['start']) + line_offset} for s in sections],
            'nodeRefs': sorted(set(RE_NODE_ID.findall(src))),
            'ignored': sorted(ignore_lines)}


def scan_html(path, rel, oos_prefixes=()):
    src = open(path, encoding='utf-8').read()
    inline = []
    for m in re.finditer(r'<style[^>]*>(.*?)</style>', src, re.S | re.I):
        offset = src.count('\n', 0, m.start(1))
        inline.append(scan_css_text(m.group(1), rel, list(oos_prefixes), offset))
    classes = {}
    for m in RE_CLASS_ATTR.finditer(src):
        for c in m.group(1).split():
            classes[c] = classes.get(c, 0) + 1
    fonts = sorted(set(re.findall(r'family=([A-Za-z+]+)', src)))
    return {'classes': classes, 'fonts': fonts, 'inline': inline,
            'nodeRefs': sorted(set(RE_NODE_ID.findall(src))),
            'varUse': sorted({'--' + m.group(1) for m in RE_VAR_USE.finditer(src)})}


def scan_js(path, rel):
    src = open(path, encoding='utf-8').read()
    return {'classes': sorted(set(re.findall(r'classList\.(?:add|remove|toggle)\(\s*[\'"]([\w-]+)', src))),
            'aria': sorted(set(re.findall(r'[\'"](aria-[\w-]+)[\'"]', src))),
            'varUse': sorted({'--' + m.group(1) for m in RE_VAR_USE.finditer(src)}),
            'nodeRefs': sorted(set(RE_NODE_ID.findall(src)))}


def scan_prototype(root, proto, oos_prefixes):
    pdir = os.path.join(root, proto['dir'])
    result = {'id': proto['id'], 'dir': proto['dir'], 'tier': proto['tier'],
              'theme': proto.get('theme'), 'exists': os.path.isdir(pdir),
              'files': [], 'tokens': {}, 'usage': {}, 'rules': [], 'raws': [],
              'sections': [], 'classes': {}, 'jsClasses': [], 'aria': [],
              'nodeRefs': [], 'fonts': [], 'ignored': 0}
    if not result['exists']:
        return result
    node_refs = set()
    for fn in sorted(os.listdir(pdir)):
        fp = os.path.join(pdir, fn)
        if not os.path.isfile(fp):
            continue
        ext = os.path.splitext(fn)[1].lower()
        if ext not in ('.css', '.html', '.js'):
            continue
        digest = hashlib.sha256(open(fp, 'rb').read()).hexdigest()[:16]
        result['files'].append({'name': fn, 'sha': digest, 'bytes': os.path.getsize(fp)})
        if ext == '.css':
            r = scan_css(fp, fn, oos_prefixes)
            result['tokens'].update(r['tokens'])
            for k, v in r['usage'].items():
                result['usage'][k] = result['usage'].get(k, 0) + v
            result['rules'] += r['rules']
            result['raws'] += r['raws']
            result['sections'] += [dict(s, file=fn) for s in r['sections']]
            result['ignored'] += len(r['ignored'])
            node_refs |= set(r['nodeRefs'])
        elif ext == '.html':
            r = scan_html(fp, fn, oos_prefixes)
            for blk in r['inline']:
                result['tokens'].update(blk['tokens'])
                for k, v in blk['usage'].items():
                    result['usage'][k] = result['usage'].get(k, 0) + v
                result['rules'] += blk['rules']
                result['raws'] += blk['raws']
                result['sections'] += [dict(sec, file=fn) for sec in blk['sections']]
                result['ignored'] += len(blk['ignored'])
            for k, v in r['classes'].items():
                result['classes'][k] = result['classes'].get(k, 0) + v
            result['fonts'] = sorted(set(result['fonts']) | set(r['fonts']))
            node_refs |= set(r['nodeRefs'])
            for v in r['varUse']:
                result['usage'].setdefault(v, 0)
        else:
            r = scan_js(fp, fn)
            result['jsClasses'] = sorted(set(result['jsClasses']) | set(r['classes']))
            result['aria'] = sorted(set(result['aria']) | set(r['aria']))
            node_refs |= set(r['nodeRefs'])
    result['nodeRefs'] = sorted(node_refs)
    return result


def main():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    root = os.path.dirname(here)
    cfg = load_config(here)
    root = os.path.abspath(os.path.join(here, cfg['prototypesRoot'])) \
        if cfg.get('prototypesRoot') else root
    oos = cfg.get('outOfScope', {}).get('selectorPrefixes', [])
    snap = {
        'kind': 'code-snapshot',
        'generatedAt': datetime.datetime.now().isoformat(timespec='seconds'),
        'root': root,
        'prototypes': [scan_prototype(root, p, oos) for p in cfg['prototypes']],
    }
    out = os.path.join(here, 'snapshots', 'code-latest.json')
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)
    for p in snap['prototypes']:
        if not p['exists']:
            print(t('  %-24s — папки нет', '  %-24s — folder missing') % p['id']); continue
        print(t('  %-24s токенов %3d · правил %4d · сырых %4d · секций %2d · узлов %3d',
                '  %-24s tokens %3d · rules %4d · raw %4d · sections %2d · nodes %3d')
              % (p['id'], len(p['tokens']), len(p['rules']),
                 len([r for r in p['raws'] if not r['outOfScope']]),
                 len(p['sections']), len(p['nodeRefs'])))
    print('→ %s' % out)


if __name__ == '__main__':
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        import sys, traceback
        traceback.print_exc()
        sys.exit(4)
