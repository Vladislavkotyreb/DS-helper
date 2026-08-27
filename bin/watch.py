#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
The watcher: notices the design system changed and decides whether to wake the bot.

Two ways to learn about a change, both supported:

  1. The LIBRARY_PUBLISH event — arrives by webhook on library publish and
     already carries the delta: created/modified/deleted for components,
     styles and variables. Precise and cheap.
     A large publish arrives as several events split by asset type, so events
     are queued and coalesced within a window.

  2. Scheduled polling — covers missed events and serves as the default mode
     until the webhook is up. Compares published components and styles
     against the previous state.

The token is read from the FIGMA_TOKEN environment variable, never stored.
DS variable values are unavailable over REST (the Variables API is
Enterprise-only), so the watcher can only say "variables may have moved,
an agent run is needed".

    python3 bin/watch.py                       poll and write trigger.json
    python3 bin/watch.py --event payload.json  queue a webhook event
    python3 bin/watch.py --drain               coalesce the queue into trigger.json
"""
import json, os, sys, time, datetime, urllib.request, urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from i18n import t

API = 'https://api.figma.com/v1'
COALESCE_SECONDS = 600   # coalescing window for one publish's events


class ApiUnavailable(Exception):
    """Figma did not answer. transient=True — a limit or outage, not a config error."""
    def __init__(self, msg, transient=False):
        super().__init__(msg)
        self.transient = transient


def load_config(here):
    p = os.path.join(here, 'config.json')
    if not os.path.exists(p):
        sys.stderr.write(t('Нет config.json — скопируйте config.example.json\n',
                           'No config.json — copy config.example.json\n'))
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
        raise ApiUnavailable(t('Figma API недоступен: %s', 'Figma API unreachable: %s') % e.reason, transient=True)


def index_assets(payload):
    """/components or /component_sets response → {key: {name, updated_at, description}}"""
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
    """What changed between two polls."""
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
    """Coalesce one publish's events into a single delta."""
    q = queue_path(here)
    if not os.path.isdir(q):
        return None, []
    files = sorted(f for f in os.listdir(q) if f.endswith('.json'))
    if not files:
        return None, []
    newest = int(files[-1].split('-')[0]) / 1000.0
    if time.time() - newest < COALESCE_SECONDS:
        # the publish may still be sending events — do not wake the bot halfway
        return 'wait', files
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
    # NB: not named `t` — that would shadow the i18n helper and blow up
    trig = {'generatedAt': datetime.datetime.now().isoformat(timespec='seconds'),
            'source': source, 'changed': bool(changed), 'reasons': reasons,
            'delta': delta, 'needsVariableRefresh': bool(needs_vars),
            'variablesNote': t('Значения переменных через REST недоступны — Variables API только для Enterprise. Нужен прогон агента с Figma MCP.',
                               'Variable values are unavailable over REST — the Variables API is Enterprise-only. An agent run with Figma MCP is required.')}
    json.dump(trig, open(os.path.join(here, 'snapshots', 'trigger.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=2)
    return trig


def main():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = load_config(here)
    args = sys.argv[1:]

    if '--event' in args:
        path = args[args.index('--event') + 1]
        name = put_event(here, json.load(open(path, encoding='utf-8')))
        print(t('событие принято: %s', 'event queued: %s') % name)
        return 0

    if '--drain' in args:
        merged, files = drain_events(here)
        if merged is None:
            print(t('очередь пуста — будить бота не из-за чего', 'queue empty — nothing to wake the bot for'))
            write_trigger(here, False, [t('событий не было', 'no events')], None, False, 'webhook')
            return 0
        if merged == 'wait':
            print(t('в очереди %d событий, последнее свежее %d с — жду, публикация может досылать',
                    '%d events queued, newest is fresher than %d s — waiting, the publish may still be sending')
                  % (len(files), COALESCE_SECONDS))
            return 0
        n = sum(len(merged[v]) for v in ('created', 'modified', 'deleted'))
        reasons = [t('публикация библиотеки: %d ассетов', 'library publish: %d assets') % n]
        if merged['variablesTouched']:
            reasons.append(t('в публикации есть переменные', 'the publish touches variables'))
        write_trigger(here, n > 0, reasons, merged, merged['variablesTouched'], 'webhook')
        print(t('склеено %d событий → изменений: %d%s', 'merged %d events → changes: %d%s')
              % (len(files), n, t(', затронуты переменные', ', variables touched') if merged['variablesTouched'] else ''))
        return 0

    token = os.environ.get('FIGMA_TOKEN')
    if not token:
        sys.stderr.write(t(
            'Нет FIGMA_TOKEN.\nЛичный токен Figma кладётся в переменную окружения (локально) или\nв секреты репозитория (в CI). Бот его только читает и никуда не пишет.\n',
            'FIGMA_TOKEN is not set.\nPut your personal Figma token into the environment (locally) or\ninto repository secrets (in CI). The bot only reads it, never stores it.\n'))
        return 2

    sp = os.path.join(here, 'snapshots', 'ds-remote-state.json')
    prev = json.load(open(sp, encoding='utf-8')) if os.path.exists(sp) else None
    try:
        cur = poll(cfg, token)
    except ApiUnavailable as e:
        # A transient failure (429 and friends) must not fail a scheduled run:
        # comparing against yesterday's snapshot is still useful. Silence is not.
        write_trigger(here, False, [t('опрос не удался: %s', 'poll failed: %s') % e], None, False, 'poll')
        sys.stderr.write('%s\n' % e)
        if e.transient:
            print(t('опрос пропущен (временный отказ), прошлое состояние не тронуто',
                    'poll skipped (transient failure), previous state untouched'))
            return 0
        return 2
    delta = diff_state(prev, cur)
    n = sum(len(delta[v]) for v in ('created', 'modified', 'deleted'))

    reasons = []
    if prev is None:
        reasons.append(t('первый опрос — базовое состояние записано', 'first poll — base state recorded'))
    else:
        if prev.get('fileVersion') != cur.get('fileVersion'):
            reasons.append(t('версия файла ДС изменилась', 'DS file version changed'))
        if n:
            reasons.append(t('опубликованных ассетов изменилось: %d', 'published assets changed: %d') % n)
    changed = bool(prev is not None and (n or prev.get('fileVersion') != cur.get('fileVersion')))
    needs_vars = changed

    json.dump(cur, open(sp, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    write_trigger(here, changed, reasons or [t('изменений нет', 'no changes')], delta, needs_vars, 'poll')

    print(t('ДС: версия %s, компонент-сетов %d, стилей %d', 'DS: version %s, component sets %d, styles %d')
          % (cur.get('fileVersion'), len(cur['componentSets']), len(cur['styles'])))
    print(t('изменений: %d — %s', 'changes: %d — %s') % (n, '; '.join(reasons) if reasons else t('нет', 'none')))
    return 0


if __name__ == '__main__':
    sys.exit(main())
