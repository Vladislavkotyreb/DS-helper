#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bridge the gap between the DS snapshot and the tokens declared in code.

Two jobs, both mechanical and both reviewable as a plain diff:

  --link     find tokens whose CSS name already matches a Figma Variable name
             but which carry no `/* Figma/Name */` comment, and add it.
             This retro-fits the link onto code that was already correct —
             the bot simply could not see it.

  --missing  list DS variables not declared in a prototype at all and emit a
             ready-to-paste CSS block with values and Figma-name comments.

Matching is by NAME, never by value: --color-text-default-primary maps to
Color/Text/Default/Primary because the slugs are identical. Value matching
would be guesswork — 16px fits Size-16, Gap-XL, Text-L and rounded-medium
at once, and a wrong link is worse than a missing one.

Nothing is written without --apply, and --apply only ever adds comments.
New tokens are printed for a human to place: where a token belongs in a file
is a design decision, not a mechanical one.
"""
import json, os, re, sys, collections

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from i18n import t


def slug(name):
    """Color/Text/Default/Primary → color-text-default-primary"""
    s = re.sub(r'[\s/]+', '-', str(name).strip().lower())
    s = re.sub(r'[^a-z0-9-]+', '-', s)
    return re.sub(r'-+', '-', s).strip('-')


def ds_value(v, theme='light'):
    if not isinstance(v, dict):
        return v
    return v.get('any') or v.get(theme)


def num(x):
    # leading-dot numbers are legal CSS: .3s, .08
    m = re.fullmatch(r'\s*(-?(?:\d+\.?\d*|\.\d+))\s*(px|ms|s|%|rem|em)?\s*', str(x))
    return (float(m.group(1)), m.group(2) or '') if m else (None, '')


def to_rgba8(v):
    """#rgb / #rrggbb / #rrggbbaa / rgb() / rgba() → (r, g, b, a) or None."""
    v = str(v).strip().lower()
    m = re.fullmatch(r'#([0-9a-f]{3,8})', v)
    if m:
        h = m.group(1)
        if len(h) in (3, 4):
            h = ''.join(c * 2 for c in h)
        if len(h) == 6:
            h += 'ff'
        if len(h) != 8:
            return None
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4, 6))
    m = re.fullmatch(r'rgba?\(([^)]*)\)', v)
    if m:
        parts = [p.strip() for p in re.split(r'[,\s/]+', m.group(1)) if p.strip()]
        if len(parts) not in (3, 4):
            return None
        try:
            rgb = [int(round(float(p[:-1]) * 255 / 100)) if p.endswith('%') else int(round(float(p)))
                   for p in parts[:3]]
            a = int(round(float(parts[3]) * 255)) if len(parts) == 4 else 255
        except ValueError:
            return None
        return tuple(rgb) + (a,)
    return None


def canon_fn(v):
    """cubic-bezier(.24,1,.4,1) and cubic-bezier(0.24, 1, 0.4, 1) are one value."""
    v = re.sub(r'\s+', '', str(v).strip().lower())
    return re.sub(r'-?\d*\.?\d+', lambda m: ('%g' % float(m.group(0))), v)


def same_value(code_val, ds_val):
    """
    Equal enough: 90ms vs 90, #FFF vs #ffffff, rgba(0,0,0,.08) vs #00000014.
    Formatting differences are not drift — reporting them as such is how a
    report earns a reputation for crying wolf.
    """
    ca, da = to_rgba8(code_val), to_rgba8(ds_val)
    if ca and da:
        return all(abs(x - y) <= 1 for x, y in zip(ca, da))
    c, cu = num(code_val)
    d, du = num(ds_val)
    if c is not None and d is not None:
        if cu == 's' and du in ('', 'ms'):
            c *= 1000
        if du == 's' and cu in ('', 'ms'):
            d *= 1000
        return abs(c - d) < 1e-6
    return canon_fn(code_val) == canon_fn(ds_val)


def analyse(ds, proto):
    by_slug = {slug(k): k for k in ds.get('variables', {})}
    theme = proto.get('theme') or 'light'
    links, mismatches, declared = [], [], set()

    for name, tok in proto.get('tokens', {}).items():
        if tok.get('figmaName'):
            declared.add(tok['figmaName'])
            continue
        fig = by_slug.get(slug(name))
        if not fig:
            continue
        want = ds_value(ds['variables'][fig], theme)
        rec = {'css': name, 'figma': fig, 'codeValue': tok['value'],
               'dsValue': want, 'file': tok['file'], 'line': tok['line']}
        if want is not None and not same_value(tok['value'], want):
            mismatches.append(rec)
        else:
            links.append(rec)
            declared.add(fig)

    missing = []
    for fig, v in ds.get('variables', {}).items():
        if fig in declared:
            continue
        val = ds_value(v, theme)
        if val is None:
            continue
        missing.append({'figma': fig, 'css': '--' + slug(fig), 'value': val,
                        'collection': v.get('collection') if isinstance(v, dict) else None})
    return links, mismatches, missing


def css_block(missing, limit=None):
    out, fam = [], None
    for m in sorted(missing, key=lambda x: x['figma'])[:limit]:
        f = m['figma'].split('/')[0]
        if f != fam:
            fam = f
            out.append('')
            out.append('  /* ---- %s ---- */' % fam)
        val = m['value']
        if re.fullmatch(r'-?\d+(\.\d+)?', str(val)):
            val = '%spx' % val
        out.append('  %s:%s;%s/* %s */'
                   % (m['css'], val, ' ' * max(1, 44 - len(m['css']) - len(str(val))), m['figma']))
    return '\n'.join(out).lstrip('\n')


def apply_links(root, proto, links):
    """Add the trailing `/* Figma/Name */` comment. Only ever adds."""
    by_file = collections.defaultdict(list)
    for l in links:
        by_file[l['file']].append(l)
    touched, crowded = 0, []
    for fname, items in by_file.items():
        path = os.path.join(root, proto['dir'], fname)
        if not os.path.exists(path):
            continue
        lines = open(path, encoding='utf-8').read().split('\n')
        for l in sorted(items, key=lambda x: -x['line']):
            i = l['line'] - 1
            if i >= len(lines) or '/*' in lines[i]:
                continue
            if re.search(re.escape(l['css']) + r'\s*:', lines[i]) is None:
                continue
            # One trailing comment cannot name six declarations. The scanner
            # attaches it to the last one, so a shared line would gain a false
            # link — worse than none. Those need splitting by hand first.
            if len(re.findall(r'--[A-Za-z0-9_][\w-]*\s*:', lines[i])) > 1:
                crowded.append((fname, l['line']))
                continue
            pad = ' ' * max(1, 46 - len(lines[i].rstrip()))
            lines[i] = lines[i].rstrip() + pad + '/* %s */' % l['figma']
            touched += 1
        open(path, 'w', encoding='utf-8').write('\n'.join(lines))
    return touched, crowded


def main():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = json.load(open(os.path.join(here, 'config.json'), encoding='utf-8'))
    root = os.path.abspath(os.path.join(here, cfg.get('prototypesRoot') or '..'))
    ds = json.load(open(os.path.join(here, 'snapshots', 'ds-latest.json'), encoding='utf-8'))
    code = json.load(open(os.path.join(here, 'snapshots', 'code-latest.json'), encoding='utf-8'))

    args = sys.argv[1:]
    want_link = '--link' in args or not any(a in args for a in ('--link', '--missing'))
    want_missing = '--missing' in args or not any(a in args for a in ('--link', '--missing'))
    apply_ = '--apply' in args
    only = args[args.index('--proto') + 1] if '--proto' in args else None

    total_applied = 0
    for proto in code['prototypes']:
        if not proto.get('exists') or (only and proto['id'] != only):
            continue
        if proto.get('tier') == 'legacy':
            continue
        links, mismatches, missing = analyse(ds, proto)
        if not (links or mismatches or missing):
            continue
        print('\n=== %s ===' % proto['id'])

        if want_link and links:
            print(t('  можно сцепить с Figma по имени: %d',
                    '  linkable to Figma by name: %d') % len(links))
            for l in links[:12]:
                print('    %-34s → %s' % (l['css'], l['figma']))
            if len(links) > 12:
                print('    … +%d' % (len(links) - 12))
            if apply_:
                n, crowded = apply_links(root, proto, links)
                total_applied += n
                print(t('  проставлено комментариев: %d', '  comments added: %d') % n)
                if crowded:
                    print(t('  пропущено — несколько объявлений в строке, разнести вручную: %d',
                            '  skipped — several declarations on one line, split by hand: %d')
                          % len(crowded))
                    for f, ln in crowded[:6]:
                        print('    %s:%d' % (f, ln))

        if mismatches:
            print(t('  имя совпало, значение — нет (сцеплять нельзя, разбирать руками): %d',
                    '  name matches but value does not (needs a human): %d') % len(mismatches))
            for m in mismatches[:8]:
                print('    %-30s код %-12s ДС %s' % (m['css'], m['codeValue'], m['dsValue']))

        if want_missing and missing:
            print(t('  есть в ДС, не заведено в вёрстке: %d',
                    '  in the DS, not declared in code: %d') % len(missing))
            fam = collections.Counter(m['figma'].split('/')[0] for m in missing)
            print('    ' + ', '.join('%s %d' % (k, v) for k, v in fam.most_common(6)))

    if want_missing:
        proto = next((p for p in code['prototypes']
                      if p.get('exists') and p.get('tier') != 'legacy'
                      and (not only or p['id'] == only)), None)
        if proto:
            _, _, missing = analyse(ds, proto)
            fams = args[args.index('--family') + 1].split(',') if '--family' in args else None
            sel = [m for m in missing if not fams or m['figma'].split('/')[0] in fams]
            if sel:
                print(t('\n--- блок для вставки (%d) ---', '\n--- block to paste (%d) ---') % len(sel))
                print(css_block(sel, limit=40))
                if len(sel) > 40:
                    print(t('  … ещё %d, сузьте --family', '  … %d more, narrow with --family')
                          % (len(sel) - 40))

    if apply_ and total_applied:
        print(t('\nПравки внесены. Перепроверьте: python3 bin/nw.py',
                '\nEdits applied. Re-run: python3 bin/nw.py'))
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
