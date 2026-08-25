#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Night Watch R4S Librarian — прогон.

    python3 bin/nw.py            снять код, сверить с ДС, собрать ревью и отчёт
    python3 bin/nw.py --fix      то же + механические правки в CSS (только после чекпоинта)
    python3 bin/nw.py --promote  принять текущий слепок ДС как базовый для следующего ревью

Принцип, унаследованный от оригинала: перед любой правкой пишется чекпоинт.
Не удалось записать чекпоинт — правок не вносится, остаётся только отчёт.
"""
import json, os, shutil, subprocess, sys, datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def proto_root(cfg):
    """Где лежат прототипы. По умолчанию — папка на уровень выше night-watch."""
    rel = cfg.get('prototypesRoot')
    return os.path.abspath(os.path.join(HERE, rel)) if rel else os.path.dirname(HERE)


ROOT = os.path.dirname(HERE)


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


def run(script):
    r = subprocess.run([sys.executable, os.path.join(HERE, 'bin', script)],
                       cwd=HERE, capture_output=True, text=True)
    sys.stdout.write(r.stdout)
    if r.returncode not in (0, 1):
        sys.stderr.write(r.stderr)
        raise SystemExit('%s упал с кодом %d' % (script, r.returncode))
    return r.returncode


def checkpoint(cfg, label):
    """Копия всех редактируемых файлов + слепков. Возвращает путь или None."""
    root = proto_root(cfg)
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    dest = os.path.join(HERE, 'snapshots', 'runs', '%s-%s' % (stamp, label))
    try:
        os.makedirs(dest, exist_ok=False)
        for proto in cfg['prototypes']:
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
        sys.stderr.write('чекпоинт не записан: %s\n' % e)
        return None


def apply_fixes(cfg):
    """Только механическое: значение токена, разошедшееся с Figma. Ничего сложнее."""
    findings = json.load(open(os.path.join(HERE, 'snapshots', 'findings.json'),
                              encoding='utf-8'))['findings']
    todo = [f for f in findings if f['cat'] == 'TOKEN_VALUE_DRIFT' and f.get('fix')]
    if not todo:
        print('механических правок нет')
        return 0
    by_file = {}
    for f in todo:
        proto = next(p for p in cfg['prototypes'] if p['id'] == f['proto'])
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

    if '--promote' in args:
        src = os.path.join(HERE, 'snapshots', 'ds-latest.json')
        dst = os.path.join(HERE, 'snapshots', 'ds-previous.json')
        shutil.copy2(src, dst)
        print('слепок ДС принят за базовый — следующее ревью будет считать дельту от него')
        return 0

    ds = os.path.join(HERE, 'snapshots', 'ds-latest.json')
    if not os.path.exists(ds):
        print('нет snapshots/ds-latest.json.')
        print('Слепок ДС снимает агент через Figma MCP — см. skills/night-watch-r4s.')
        print('Без него сверяется только код сам с собой.')
        return 2

    print('1. сканирую прототипы')
    run('scan.py')
    print('2. сверяю с дизайн-системой')
    run('diff.py')
    print('3. ревью изменений в ДС')
    run('review.py')

    if '--fix' in args:
        print('4. чекпоинт')
        cp = checkpoint(cfg, 'fix')
        if not cp:
            print('   чекпоинт не записан — правок не вношу, остаётся только отчёт')
            return 1
        print('   %s' % os.path.relpath(cp, HERE))
        print('5. правки')
        n = apply_fixes(cfg)
        if n:
            print('   внесено правок: %d — пересобираю отчёт' % n)
            run('scan.py'); run('diff.py')
        print('   откат: скопировать файлы из %s обратно' % os.path.relpath(cp, HERE))

    print()
    print('reports/REPORT.md      — расхождения ДС и прототипов')
    print('reports/DS-REVIEW.md   — что изменилось в самой ДС')
    print('reports/changelog-card.json — карточка для change-log (узел 12929:4)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
