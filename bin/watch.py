#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Сторож: замечает, что дизайн-система изменилась, и решает, надо ли будить бота.

Два способа узнать об изменении, оба поддерживаются:

  1. Событие LIBRARY_PUBLISH — приходит вебхуком при публикации библиотеки
     и уже содержит дельту: created/modified/deleted для компонентов, стилей
     и переменных. Точный и дешёвый путь.
     Крупная публикация приходит несколькими событиями по типам ассетов,
     поэтому события складываются в очередь и склеиваются окном.

  2. Опрос по расписанию — на случай пропущенного события и как основной режим,
     пока вебхук не поднят. Сравнивает опубликованные компоненты и стили
     с прошлым состоянием.

Токен читается из переменной окружения FIGMA_TOKEN и никуда не пишется.
Значения переменных ДС через REST недоступны (Variables API — только Enterprise),
поэтому сторож умеет лишь сказать «переменные могли поехать, нужен прогон агента».

    python3 bin/watch.py                     опросить и записать trigger.json
    python3 bin/watch.py --event payload.json  положить событие вебхука в очередь
    python3 bin/watch.py --drain             склеить очередь событий в trigger.json
"""
import json, os, sys, time, datetime, urllib.request, urllib.error

API = 'https://api.figma.com/v1'
COALESCE_SECONDS = 600   # окно склейки событий одной публикации


class ApiUnavailable(Exception):
    """Figma не ответила. transient=True — лимит или сбой, не ошибка конфигурации."""
    def __init__(self, msg, transient=False):
        super().__init__(msg)
        self.transient = transient


def load_config(here):
    p = os.path.join(here, 'config.json')
    if not os.path.exists(p):
        sys.stderr.write('Нет config.json — скопируйте config.example.json\n')
        raise SystemExit(2)
    return json.load(open(p, encoding='utf-8'))


def api_get(path, token):
    req = urllib.request.Request(API + path, headers={'X-Figma-Token': token})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', 'replace')[:300]
        raise ApiUnavailable('Figma API %s → %s: %s' % (path, e.code, body),
                             transient=e.code in (429, 500, 502, 503))
    except urllib.error.URLError as e:
        raise ApiUnavailable('Figma API недоступен: %s' % e.reason, transient=True)


def index_assets(payload):
    """Ответ /components или /component_sets → {key: {name, updated_at, description}}"""
    out = {}
    for m in (payload.get('meta') or {}).get('component_sets', []) + \
             (payload.get('meta') or {}).get('components', []) + \
             (payload.get('meta') or {}).get('styles', []):
        out[m.get('key')] = {
            'name': m.get('name'),
            'updated_at': m.get('updated_at'),
            'description': (m.get('description') or '')[:400],
        }
    return out


def poll(cfg, token):
    key = cfg['figma']['designSystemFileKey']
    state = {'fetchedAt': datetime.datetime.now().isoformat(timespec='seconds'),
             'componentSets': {}, 'components': {}, 'styles': {}}
    meta = api_get('/files/%s?depth=1' % key, token)
    state['fileVersion'] = meta.get('version')
    state['lastModified'] = meta.get('lastModified')
    state['componentSets'] = index_assets(api_get('/files/%s/component_sets' % key, token))
    state['components'] = index_assets(api_get('/files/%s/components' % key, token))
    state['styles'] = index_assets(api_get('/files/%s/styles' % key, token))
    return state


def diff_state(prev, cur):
    """Что изменилось между двумя опросами."""
    d = {'created': [], 'modified': [], 'deleted': []}
    for bucket in ('componentSets', 'components', 'styles'):
        p, c = (prev or {}).get(bucket, {}), cur.get(bucket, {})
        for k in c.keys() - p.keys():
            d['created'].append(c[k]['name'])
        for k in p.keys() - c.keys():
            d['deleted'].append(p[k]['name'])
        for k in p.keys() & c.keys():
            if p[k].get('updated_at') != c[k].get('updated_at'):
                d['modified'].append(c[k]['name'])
    for v in d.values():
        v.sort()
    return d


def queue_path(here):
    return os.path.join(here, 'snapshots', 'events')


def put_event(here, payload):
    q = queue_path(here)
    os.makedirs(q, exist_ok=True)
    name = '%d-%s.json' % (int(time.time() * 1000), payload.get('event_type', 'unknown'))
    json.dump(payload, open(os.path.join(q, name), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=2)
    return name


def drain_events(here):
    """Склеить события одной публикации в общую дельту."""
    q = queue_path(here)
    if not os.path.isdir(q):
        return None, []
    files = sorted(f for f in os.listdir(q) if f.endswith('.json'))
    if not files:
        return None, []
    newest = int(files[-1].split('-')[0]) / 1000.0
    if time.time() - newest < COALESCE_SECONDS:
        # публикация ещё может досылать события — не будим бота на полпути
        return 'ждём', files
    merged = {'created': [], 'modified': [], 'deleted': [], 'variablesTouched': False}
    for f in files:
        p = json.load(open(os.path.join(q, f), encoding='utf-8'))
        for verb in ('created', 'modified', 'deleted'):
            for kind in ('components', 'styles', 'variables'):
                for item in p.get('%s_%s' % (verb, kind)) or []:
                    merged[verb].append(item.get('name') or item.get('key') or '?')
                    if kind == 'variables':
                        merged['variablesTouched'] = True
    for verb in ('created', 'modified', 'deleted'):
        merged[verb] = sorted(set(merged[verb]))
    for f in files:
        os.remove(os.path.join(q, f))
    return merged, files


def write_trigger(here, changed, reasons, delta, needs_vars, source):
    t = {'generatedAt': datetime.datetime.now().isoformat(timespec='seconds'),
         'source': source, 'changed': bool(changed), 'reasons': reasons,
         'delta': delta, 'needsVariableRefresh': bool(needs_vars),
         'variablesNote': ('Значения переменных через REST недоступны — Variables API '
                           'только для Enterprise. Нужен прогон агента с Figma MCP.')}
    json.dump(t, open(os.path.join(here, 'snapshots', 'trigger.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=2)
    return t


def main():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = load_config(here)
    args = sys.argv[1:]

    if '--event' in args:
        path = args[args.index('--event') + 1]
        name = put_event(here, json.load(open(path, encoding='utf-8')))
        print('событие принято: %s' % name)
        return 0

    if '--drain' in args:
        merged, files = drain_events(here)
        if merged is None:
            print('очередь пуста — будить бота не из-за чего')
            write_trigger(here, False, ['событий не было'], None, False, 'webhook')
            return 0
        if merged == 'ждём':
            print('в очереди %d событий, последнее свежее %d с — жду, публикация может досылать'
                  % (len(files), COALESCE_SECONDS))
            return 0
        n = sum(len(merged[v]) for v in ('created', 'modified', 'deleted'))
        reasons = ['публикация библиотеки: %d ассетов' % n]
        if merged['variablesTouched']:
            reasons.append('в публикации есть переменные')
        write_trigger(here, n > 0, reasons, merged, merged['variablesTouched'], 'webhook')
        print('склеено %d событий → изменений: %d%s'
              % (len(files), n, ', затронуты переменные' if merged['variablesTouched'] else ''))
        return 0

    token = os.environ.get('FIGMA_TOKEN')
    if not token:
        sys.stderr.write(
            'Нет FIGMA_TOKEN.\n'
            'Личный токен Figma кладётся в переменную окружения (локально) или\n'
            'в секреты репозитория (в CI). Бот его только читает и никуда не пишет.\n')
        return 2

    sp = os.path.join(here, 'snapshots', 'ds-remote-state.json')
    prev = json.load(open(sp, encoding='utf-8')) if os.path.exists(sp) else None
    try:
        cur = poll(cfg, token)
    except ApiUnavailable as e:
        # Временный отказ (лимит 429 и подобное) не должен ронять запланированный
        # прогон: сверка со старым слепком всё равно полезна. Но и молчать нельзя.
        write_trigger(here, False, ['опрос не удался: %s' % e], None, False, 'poll')
        sys.stderr.write('%s\n' % e)
        if e.transient:
            print('опрос пропущен (временный отказ), прошлое состояние не тронуто')
            return 0
        return 2
    delta = diff_state(prev, cur)
    n = sum(len(delta[v]) for v in ('created', 'modified', 'deleted'))

    reasons = []
    if prev is None:
        reasons.append('первый опрос — базовое состояние записано')
    else:
        if prev.get('fileVersion') != cur.get('fileVersion'):
            reasons.append('версия файла ДС изменилась')
        if n:
            reasons.append('опубликованных ассетов изменилось: %d' % n)
    changed = bool(prev is not None and (n or prev.get('fileVersion') != cur.get('fileVersion')))
    needs_vars = changed

    json.dump(cur, open(sp, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    write_trigger(here, changed, reasons or ['изменений нет'], delta, needs_vars, 'poll')

    print('ДС: версия %s, компонент-сетов %d, стилей %d'
          % (cur.get('fileVersion'), len(cur['componentSets']), len(cur['styles'])))
    print('изменений: %d — %s' % (n, '; '.join(reasons) if reasons else 'нет'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
