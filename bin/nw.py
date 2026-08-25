#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Night Watch DS-helper — the runner.

    python3 bin/nw.py            scan the code, compare, build review and report
    python3 bin/nw.py --fix      same + mechanical CSS fixes (checkpoint first)
    python3 bin/nw.py --promote  make the current DS snapshot the review base

The principle inherited from the original: a checkpoint precedes any edit.
Checkpoint failed — no edits, report only.
"""
import json, os, shutil, subprocess, sys, datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '.'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from i18n import t

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def proto_root(cfg):
    """Where the prototypes live. Defaults to the folder one level above the bot."""
    rel = cfg.get('prototypesRoot')
    return os.path.abspath(os.path.join(HERE, rel)) if rel else os.path.dirname(HERE)


ROOT = os.path.dirname(HERE)


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


def run(script, extra=None):
    r = subprocess.run([sys.executable, os.path.join(HERE, 'bin', script)] + (extra or []),
                       cwd=HERE, capture_output=True, text=True)
    sys.stdout.write(r.stdout)
    if r.stderr:
        sys.stderr.write(r.stderr)
    if r.returncode not in (0, 1):
        # 1 means "findings exist", not an error; anything else is a step crash
        raise SystemExit(t('%s упал с кодом %d — выше его stderr', '%s crashed with code %d — its stderr is above') % (script, r.returncode))
    return r.returncode


def checkpoint(cfg, label):
    """A copy of every editable file plus snapshots. Returns the path or None."""
    root = proto_root(cfg)
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    dest = os.path.join(HERE, 'snapshots', 'runs', '%s-%s' % (stamp, label))
    try:
        os.makedirs(dest, exist_ok=False)
        for proto in cfg.get('prototypes', []):
            src = os.path.join(root, proto['dir'])
            if not os.path.isdir(src):
                continue
            d = os.path.join(dest, proto['id'])
            os.makedirs(d, exist_ok=True)
            for fn in os.listdir(src):
                if os.path.splitext(fn)[1].lower() in ('.css', '.html', '.js'):
                    shutil.copy2(os.path.join(src, fn), os.path.join(d, fn))
        for snap in ('ds-latest.json', 'code-latest.json', 'findings.json'):
            p = os.path.join(HERE, 'snapshots', snap)
            if os.path.exists(p):
                shutil.copy2(p, os.path.join(dest, snap))
        return dest
    except Exception as e:
        sys.stderr.write(t('чекпоинт не записан: %s\n', 'checkpoint not written: %s\n') % e)
        return None


def apply_fixes(cfg):
    """Mechanical only: token values drifted from Figma. Nothing smarter."""
    findings = json.load(open(os.path.join(HERE, 'snapshots', 'findings.json'),
                              encoding='utf-8'))['findings']
    todo = [f for f in findings if f['cat'] == 'TOKEN_VALUE_DRIFT' and f.get('fix')]
    protos = {p['id']: p for p in cfg.get('prototypes', [])}
    sources = {s['id']: s for s in cfg.get('sources', [])}

    skipped = [f for f in todo if f['proto'] in sources]
    if skipped:
        print(t('   продовые источники правлю не здесь: %d находок в %s.', '   production sources are not fixed here: %d findings in %s.')
              % (len(skipped), ', '.join(sorted({f['proto'] for f in skipped}))))
        print(t('   в чужой репозиторий бот ходит пул-реквестом, а не правкой на месте.',
                '   foreign repositories get pull requests, not in-place edits.'))
    todo = [f for f in todo if f['proto'] in protos]
    if not todo:
        print(t('механических правок нет', 'no mechanical fixes to apply'))
        return 0
    by_file = {}
    for f in todo:
        proto = protos[f['proto']]
        by_file.setdefault(os.path.join(proto_root(cfg), proto['dir'], f['file']), []).append(f)
    n = 0
    for path, items in by_file.items():
        lines = open(path, encoding='utf-8').read().split('\n')
        for f in items:
            i = f['line'] - 1
            if i >= len(lines):
                continue
            name, newval = f['fix'].split(':', 1)
            old = lines[i]
            import re
            new = re.sub(r'(%s\s*:\s*)[^;]+' % re.escape(name), r'\g<1>' + newval, old, count=1)
            if new != old:
                lines[i] = new
                n += 1
                print('  %s:%d  %s → %s' % (os.path.basename(path), f['line'], name, newval))
        open(path, 'w', encoding='utf-8').write('\n'.join(lines))
    return n


def main():
    args = sys.argv[1:]
    cfg = load_config(HERE)

    if '--accept' in args:
        fp_src = os.path.join(HERE, 'snapshots', 'findings.json')
        if not os.path.exists(fp_src):
            print(t('нет snapshots/findings.json — сначала прогон', 'no snapshots/findings.json — run the check first')); return 2
        sys.path.insert(0, os.path.join(HERE, 'bin'))
        import diff as _diff
        data = json.load(open(fp_src, encoding='utf-8'))
        keys = sorted({_diff.fp(f) for f in data['findings']})
        json.dump({'acceptedAt': datetime.datetime.now().isoformat(timespec='seconds'),
                   'count': len(keys),
                   'note': ('Findings that existed when the bot was switched on. It remembers '
                            'them, does not fail the run over them, and watches that the list '
                            'does not grow. To retire fixed ones — run and repeat --accept.'),
                   'fingerprints': keys},
                  open(os.path.join(HERE, 'snapshots', 'baseline.json'), 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=2)
        print(t('базовая линия принята: %d расхождений', 'baseline accepted: %d findings') % len(keys))
        print(t('дальше прогон падает только на новых — порог меняется --fail-on',
                'from now on the run fails only on new findings — adjust with --fail-on'))
        return 0

    if '--promote' in args:
        src = os.path.join(HERE, 'snapshots', 'ds-latest.json')
        dst = os.path.join(HERE, 'snapshots', 'ds-previous.json')
        shutil.copy2(src, dst)
        print(t('слепок ДС принят за базовый — следующее ревью будет считать дельту от него',
                'DS snapshot promoted — the next review will diff against it'))
        return 0

    ds = os.path.join(HERE, 'snapshots', 'ds-latest.json')
    if not os.path.exists(ds):
        print(t('нет snapshots/ds-latest.json.', 'no snapshots/ds-latest.json.'))
        print(t('Слепок ДС снимает агент через Figma MCP — см. skills/night-watch-r4s.',
                'The DS snapshot is taken by an agent with Figma MCP — see skill/ for the recipe.'))
        print(t('Без него сверяется только код сам с собой.', 'Without it the code is only compared to itself.'))
        return 2

    print(t('1. сканирую прототипы', '1. scanning prototypes'))
    run('scan.py')
    if cfg.get('sources'):
        print(t('   сканирую продовые источники', '   scanning production sources'))
        run('scan_src.py')
    print(t('2. сверяю с дизайн-системой', '2. comparing against the design system'))
    passthru = []
    for flag in ('--fail-on', '--sarif'):
        if flag in args:
            passthru += [flag, args[args.index(flag) + 1]]
    diff_code = run('diff.py', passthru)
    print(t('3. ревью изменений в ДС', '3. reviewing DS changes'))
    run('review.py')

    if '--fix' in args:
        print(t('4. чекпоинт', '4. checkpoint'))
        cp = checkpoint(cfg, 'fix')
        if not cp:
            print(t('   чекпоинт не записан — правок не вношу, остаётся только отчёт',
                    '   checkpoint failed — no edits, report only'))
            return 1
        print('   %s' % os.path.relpath(cp, HERE))
        print(t('5. правки', '5. fixes'))
        n = apply_fixes(cfg)
        if n:
            print(t('   внесено правок: %d — пересобираю отчёт', '   fixes applied: %d — rebuilding the report') % n)
            run('scan.py'); run('diff.py')
        print(t('   откат: скопировать файлы из %s обратно', '   rollback: copy the files back from %s') % os.path.relpath(cp, HERE))

    print()
    print(t('reports/REPORT.md      — расхождения ДС и прототипов', 'reports/REPORT.md      — DS vs code findings'))
    print(t('reports/DS-REVIEW.md   — что изменилось в самой ДС', 'reports/DS-REVIEW.md   — what changed in the DS itself'))
    print(t('reports/changelog-card.json — карточка для change-log', 'reports/changelog-card.json — change-log card payload'))
    return diff_code


if __name__ == '__main__':
    sys.exit(main())
