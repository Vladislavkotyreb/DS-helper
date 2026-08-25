#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Регистрация вебхука LIBRARY_PUBLISH на файле дизайн-системы.

    python3 bin/webhook.py list
    python3 bin/webhook.py create https://ваше-реле/figma
    python3 bin/webhook.py delete <id>

Вебхук на уровне файла заводится правом «Can edit» — организация не нужна.
Токен читается из FIGMA_TOKEN, секрет вебхука — из NW_WEBHOOK_PASSCODE.
Ни то, ни другое не печатается.

Figma шлёт POST на голый URL и не умеет ставить произвольные заголовки, поэтому
напрямую в GitHub она достучаться не может: repository_dispatch требует Authorization.
Отсюда реле — см. relay/cloudflare-worker.js.
"""
import json, os, sys, urllib.request, urllib.error

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
        sys.stderr.write('Нет FIGMA_TOKEN\n'); return 2

    cmd = sys.argv[1] if len(sys.argv) > 1 else 'list'

    if cmd == 'list':
        r = call('GET', '/webhooks?context=file&context_id=%s' % key, token)
        hooks = r.get('webhooks', r if isinstance(r, list) else [])
        if not hooks:
            print('вебхуков на файле нет')
        for h in hooks:
            print('%s  %-16s %-8s %s' % (h.get('id'), h.get('event_type'),
                                         h.get('status'), h.get('endpoint')))
        return 0

    if cmd == 'create':
        if len(sys.argv) < 3:
            sys.stderr.write('нужен URL реле\n'); return 2
        passcode = os.environ.get('NW_WEBHOOK_PASSCODE')
        if not passcode:
            sys.stderr.write(
                'Нет NW_WEBHOOK_PASSCODE.\n'
                'Это общий секрет: реле по нему отличает запросы Figma от посторонних.\n'
                'Придумайте строку, положите её и сюда, и в переменные реле.\n')
            return 2
        r = call('POST', '/webhooks', token, {
            'event_type': 'LIBRARY_PUBLISH', 'context': 'file', 'context_id': key,
            'endpoint': sys.argv[2], 'passcode': passcode,
            'description': 'Night Watch DS-helper'})
        print('вебхук создан: %s' % r.get('id'))
        print('Figma пришлёт PING сразу — реле должно ответить 200')
        return 0

    if cmd == 'delete':
        if len(sys.argv) < 3:
            sys.stderr.write('нужен id вебхука\n'); return 2
        call('DELETE', '/webhooks/%s' % sys.argv[2], token)
        print('вебхук удалён')
        return 0

    sys.stderr.write('команды: list | create <url> | delete <id>\n')
    return 2


if __name__ == '__main__':
    sys.exit(main())
