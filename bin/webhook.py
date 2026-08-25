#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Registers a LIBRARY_PUBLISH webhook on the design-system file.

    python3 bin/webhook.py list
    python3 bin/webhook.py create https://your-relay/figma
    python3 bin/webhook.py delete <id>

A file-level webhook takes only "Can edit" — no organisation required.
The token comes from FIGMA_TOKEN, the webhook secret from NW_WEBHOOK_PASSCODE.
Neither is ever printed.

Figma POSTs to a bare URL and cannot set custom headers, so it cannot reach
GitHub directly: repository_dispatch demands Authorization. Hence the relay —
see relay/cloudflare-worker.js.
"""
import json, os, sys, urllib.request, urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from i18n import t

API = 'https://api.figma.com/v2'


def call(method, path, token, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method,
                                 headers={'X-Figma-Token': token,
                                          'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode('utf-8')
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raise SystemExit('Figma API %s %s → %s: %s'
                         % (method, path, e.code, e.read().decode('utf-8', 'replace')[:300]))


def main():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = json.load(open(os.path.join(here, 'config.json'), encoding='utf-8'))
    key = cfg['figma']['designSystemFileKey']
    token = os.environ.get('FIGMA_TOKEN')
    if not token:
        sys.stderr.write(t('Нет FIGMA_TOKEN\n', 'FIGMA_TOKEN is not set\n')); return 2

    cmd = sys.argv[1] if len(sys.argv) > 1 else 'list'

    if cmd == 'list':
        r = call('GET', '/webhooks?context=file&context_id=%s' % key, token)
        hooks = r.get('webhooks', r if isinstance(r, list) else [])
        if not hooks:
            print(t('вебхуков на файле нет', 'no webhooks on this file'))
        for h in hooks:
            print('%s  %-16s %-8s %s' % (h.get('id'), h.get('event_type'),
                                         h.get('status'), h.get('endpoint')))
        return 0

    if cmd == 'create':
        if len(sys.argv) < 3:
            sys.stderr.write(t('нужен URL реле\n', 'relay URL required\n')); return 2
        passcode = os.environ.get('NW_WEBHOOK_PASSCODE')
        if not passcode:
            sys.stderr.write(t(
                'Нет NW_WEBHOOK_PASSCODE.\nЭто общий секрет: реле по нему отличает запросы Figma от посторонних.\nПридумайте строку, положите её и сюда, и в переменные реле.\n',
                'NW_WEBHOOK_PASSCODE is not set.\nIt is a shared secret: the relay uses it to tell Figma apart from strangers.\nInvent a string and put it both here and into the relay env.\n'))
            return 2
        r = call('POST', '/webhooks', token, {
            'event_type': 'LIBRARY_PUBLISH', 'context': 'file', 'context_id': key,
            'endpoint': sys.argv[2], 'passcode': passcode,
            'description': 'Night Watch DS-helper'})
        print(t('вебхук создан: %s', 'webhook created: %s') % r.get('id'))
        print(t('Figma пришлёт PING сразу — реле должно ответить 200', 'Figma sends a PING immediately — the relay must answer 200'))
        return 0

    if cmd == 'delete':
        if len(sys.argv) < 3:
            sys.stderr.write(t('нужен id вебхука\n', 'webhook id required\n')); return 2
        call('DELETE', '/webhooks/%s' % sys.argv[2], token)
        print(t('вебхук удалён', 'webhook deleted'))
        return 0

    sys.stderr.write(t('команды: list | create <url> | delete <id>\n', 'commands: list | create <url> | delete <id>\n'))
    return 2


if __name__ == '__main__':
    sys.exit(main())
