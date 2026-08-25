#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Production-code scanner — for code with no Figma link in it.

Prototypes are read through the link kept inside them: the Figma Variable name
in a token's trailing comment, the component name in a section header.
Production code has neither, so two other mechanisms apply:

  1. value matching — a raw colour or size equal to a DS variable's value is a
     token candidate;
  2. componentMap from the config — which CSS class maps to which DS component.

The output matches scan.py records exactly, so diff.py needs no changes.
Handles CSS, SCSS, LESS, <style> blocks and style="" attributes in html/php
templates. Styles inside JS/JSX are not parsed — the skipped file count is
reported honestly.
"""
import json, sys, os, re, sys, fnmatch, hashlib, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scan as base
from i18n import t

RE_STYLE_BLOCK = re.compile(r'<style[^>]*>(.*?)</style>', re.S | re.I)
RE_STYLE_ATTR = re.compile(r'style\s*=\s*"([^"]*)"')
RE_SCSS_VAR = re.compile(r'(?m)^\s*([$@])([A-Za-z0-9_][\w-]*)\s*:\s*([^;{}]+?)\s*;')
RE_CLASS_ATTR = re.compile(r'class(?:Name)?\s*=\s*["\']([^"\']*)["\']')

JS_EXT = {'.js', '.jsx', '.ts', '.tsx', '.vue', '.svelte'}
CSS_EXT = {'.css', '.scss', '.less', '.sass'}
MARKUP_EXT = {'.html', '.htm', '.php', '.twig', '.tpl', '.erb', '.blade'}


def walk(root, include, exclude):
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        if any(fnmatch.fnmatch(rel_dir + '/', p.rstrip('*') + '*') for p in exclude if p.endswith('/**')):
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if not d.startswith('.')]
        for fn in filenames:
            rel = os.path.normpath(os.path.join(rel_dir, fn)) if rel_dir != '.' else fn
            if any(fnmatch.fnmatch(rel, p) for p in exclude):
                continue
            if include and not any(fnmatch.fnmatch(rel, p) for p in include):
                continue
            yield os.path.join(dirpath, fn), rel


def scan_source(src):
    """src — an entry from config.sources. Returns a scan.py-format record."""
    root = src['root']
    include = src.get('include') or ['**/*.css', '**/*.scss', '**/*.less',
                                     '**/*.html', '**/*.php', '**/*.js', '**/*.jsx', '**/*.tsx']
    exclude = src.get('exclude') or ['node_modules/**', 'vendor/**', 'dist/**', 'build/**',
                                     '**/*.min.css', '**/*.min.js']
    oos = src.get('outOfScopePrefixes') or []

    out = {'id': src['id'], 'dir': root, 'tier': src.get('tier', 'production'),
           'theme': src.get('theme', 'light'), 'accent': src.get('accent', 'Green'),
           'conventions': bool(src.get('conventions')), 'exists': os.path.isdir(root),
           'files': [], 'tokens': {}, 'usage': {}, 'rules': [], 'raws': [], 'sections': [],
           'classes': {}, 'jsClasses': [], 'aria': [], 'nodeRefs': [], 'fonts': [],
           'ignored': 0, 'skippedJs': 0, 'skippedJsFiles': []}
    if not out['exists']:
        return out

    for path, rel in walk(root, include, exclude):
        ext = os.path.splitext(path)[1].lower()
        try:
            text = open(path, encoding='utf-8', errors='replace').read()
        except Exception:
            continue
        out['files'].append({'name': rel, 'bytes': len(text),
                             'sha': hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]})

        if ext in CSS_EXT:
            _absorb(out, base.scan_css_text(text, rel, oos), rel)
            _scss_vars(out, text, rel)
        elif ext in MARKUP_EXT:
            for m in RE_STYLE_BLOCK.finditer(text):
                off = text.count('\n', 0, m.start(1))
                _absorb(out, base.scan_css_text(m.group(1), rel, oos, off), rel)
            _style_attrs(out, text, rel, oos)
            _classes(out, text)
        elif ext in JS_EXT:
            _classes(out, text)
            if re.search(r'styled\.|css`|makeStyles|StyleSheet\.create|style=\{\{', text):
                out['skippedJs'] += 1
                if len(out['skippedJsFiles']) < 20:
                    out['skippedJsFiles'].append(rel)

    return out


def _absorb(out, blk, rel):
    out['tokens'].update(blk['tokens'])
    for k, v in blk['usage'].items():
        out['usage'][k] = out['usage'].get(k, 0) + v
    out['rules'] += [dict(r, file=rel) for r in blk['rules']]
    out['raws'] += blk['raws']
    out['sections'] += [dict(s, file=rel) for s in blk['sections']]
    out['ignored'] += len(blk['ignored'])


def _scss_vars(out, text, rel):
    """$var / @var are token declarations too, just in another syntax."""
    for m in RE_SCSS_VAR.finditer(text):
        name = m.group(1) + m.group(2)
        val = m.group(3).strip()
        out['tokens'][name] = {'value': val, 'color': base.norm_color(val),
                               'figmaName': None, 'file': rel,
                               'line': text.count('\n', 0, m.start()) + 1}
    for m in re.finditer(r'(?<![\w-])([$@])([A-Za-z0-9_][\w-]*)', text):
        name = m.group(1) + m.group(2)
        if name in out['tokens']:
            out['usage'][name] = out['usage'].get(name, 0) + 1


def _style_attrs(out, text, rel, oos):
    """Inline style="" attrs — production templates are full of them, all raw."""
    for m in RE_STYLE_ATTR.finditer(text):
        line = text.count('\n', 0, m.start()) + 1
        blk = base.scan_css_text('.inline{%s}' % m.group(1), rel, oos, line - 1)
        for r in blk['raws']:
            r['selector'] = 'style=""'
            r['line'] = line
            out['raws'].append(r)


def _classes(out, text):
    for m in RE_CLASS_ATTR.finditer(text):
        for c in m.group(1).split():
            if '{' in c or '$' in c:
                continue
            out['classes'][c] = out['classes'].get(c, 0) + 1


def main():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = base.load_config(here)
    sources = cfg.get('sources') or []
    if not sources:
        print(t('в config.json нет sources — сканировать нечего', 'no sources in config.json — nothing to scan')); return 0

    snap_path = os.path.join(here, 'snapshots', 'code-latest.json')
    snap = json.load(open(snap_path, encoding='utf-8')) if os.path.exists(snap_path) else \
        {'kind': 'code-snapshot', 'prototypes': []}
    keep = [p for p in snap['prototypes'] if p.get('tier') != 'production']

    added = []
    for src in sources:
        r = scan_source(src)
        added.append(r)
        if not r['exists']:
            print(t('  %-24s — нет папки %s', '  %-24s — missing folder %s') % (r['id'], r['dir'])); continue
        print(t('  %-24s файлов %4d · правил %5d · сырых %5d · токенов %4d%s',
                '  %-24s files %4d · rules %5d · raw %5d · tokens %4d%s')
              % (r['id'], len(r['files']), len(r['rules']),
                 len([x for x in r['raws'] if not x['outOfScope']]), len(r['tokens']),
                 (t('  · пропущено JS-стилей: %d', '  · JS style files skipped: %d') % r['skippedJs']) if r['skippedJs'] else ''))

    snap['prototypes'] = keep + added
    snap['generatedAt'] = datetime.datetime.now().isoformat(timespec='seconds')
    json.dump(snap, open(snap_path, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    print('→ snapshots/code-latest.json')
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
