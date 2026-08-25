#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Night Watch in Telegram: run the check from a message.

Long polling (getUpdates), so no public address is needed — runs on a laptop
behind NAT just fine.

    TG_BOT_TOKEN=... TG_ALLOWED_CHATS=123456 python3 bin/tgbot.py

The token comes from the environment and is never stored or logged.
Only chats listed in TG_ALLOWED_CHATS get answers: otherwise anyone who
finds the bot could trigger edits in your files.

What it cannot do: refresh the DS snapshot. Figma serves variable values
only through an agent MCP session (the Variables REST API is Enterprise-only).
Asked to "update the DS" it honestly points to the agent.
"""
import json, os, re, subprocess, sys, time, urllib.request, urllib.error, mimetypes

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from i18n import t

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = 'https://api.telegram.org/bot%s/%s'

def project_name():
    try:
        cfg = json.load(open(os.path.join(HERE, 'config.json'), encoding='utf-8'))
        return cfg.get('project') or 'вашей ДС'
    except Exception:
        return t('вашей ДС', 'your DS')


HELP_RU = """Night Watch — сверка вёрстки с дизайн-системой {project}.

/run — прогнать сверку, прислать сводку
/report — полный отчёт файлом
/ds — что изменилось в дизайн-системе
/status — состояние: слепок, находки, базовая линия
/fix — механические правки (спросит подтверждение)
/accept — принять текущее за базовую линию
/help — эта справка

Слепок ДС обновляется не отсюда, а прогоном агента с Figma MCP.
Но если задан FIGMA_TOKEN, бот заметит, что ДС уехала, и предупредит."""

HELP_EN = """Night Watch — checks your markup against the {project} design system.

/run — run the check, get a summary
/report — full report as a file
/ds — what changed in the design system
/status — snapshot, findings, baseline
/fix — mechanical fixes (asks for confirmation)
/accept — accept current findings as the baseline
/help — this help

The DS snapshot is refreshed by an agent with Figma MCP, not from here.
With FIGMA_TOKEN set, the bot notices the DS moved on and warns you."""


def help_text():
    return t(HELP_RU, HELP_EN).replace('{project}', project_name())


# ---------- transport ----------

def tg(token, method, payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(API % (token, method), data=data,
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return {'ok': False, 'error': '%s %s' % (e.code, e.read().decode('utf-8', 'replace')[:200])}
    except urllib.error.URLError as e:
        return {'ok': False, 'error': str(e.reason)}


def tg_document(token, chat_id, path, caption=''):
    """sendDocument — multipart by hand, keeping the zero-dependency promise."""
    boundary = '----nightwatch%d' % int(time.time() * 1000)
    fname = os.path.basename(path)
    ctype = mimetypes.guess_type(fname)[0] or 'application/octet-stream'
    body = b''
    for k, v in (('chat_id', str(chat_id)), ('caption', caption[:1000])):
        body += ('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n'
                 % (boundary, k, v)).encode()
    body += ('--%s\r\nContent-Disposition: form-data; name="document"; filename="%s"\r\n'
             'Content-Type: %s\r\n\r\n' % (boundary, fname, ctype)).encode()
    body += open(path, 'rb').read() + ('\r\n--%s--\r\n' % boundary).encode()
    req = urllib.request.Request(API % (token, 'sendDocument'), data=body,
                                 headers={'Content-Type': 'multipart/form-data; boundary=' + boundary})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return {'ok': False, 'error': '%s' % e.code}


def say(token, chat_id, text):
    # Telegram caps a message at 4096 chars — split on line breaks,
    # never mid-word.
    text = text.strip() or '(empty)'
    while text:
        if len(text) <= 3900:
            chunk, text = text, ''
        else:
            cut = text.rfind('\n', 0, 3900)
            cut = cut if cut > 2000 else 3900
            chunk, text = text[:cut], text[cut:]
        tg(token, 'sendMessage', {'chat_id': chat_id, 'text': chunk,
                                  'disable_web_page_preview': True})


# ---------- commands ----------

def run_nw(*extra):
    r = subprocess.run([sys.executable, os.path.join(HERE, 'bin', 'nw.py')] + list(extra),
                       cwd=HERE, capture_output=True, text=True, timeout=600)
    return r.returncode, (r.stdout or '') + (r.stderr or '')


def load(name, default=None):
    p = os.path.join(HERE, 'snapshots', name)
    return json.load(open(p, encoding='utf-8')) if os.path.exists(p) else default


def summary():
    """Short findings summary — what fits into a single message."""
    data = load('findings.json')
    if not data:
        return t('Прогонов ещё не было. /run', 'No runs yet. /run')
    f = data['findings']
    fresh = [x for x in f if x.get('state') != 'baselined']
    based = len(f) - len(fresh)
    high = [x for x in fresh if x['sev'] == 'high']

    import collections
    cat = collections.Counter(x['cat'] for x in fresh)
    try:
        sys.path.insert(0, os.path.join(HERE, 'bin'))
        from diff import CAT_RU
    except Exception:
        CAT_RU = {}

    lines = [t('Расхождений: %d, важных %d', 'Findings: %d, high: %d') % (len(fresh), len(high))]
    if based:
        lines.append(t('В базовой линии: %d (не считаются)', 'Baselined: %d (not counted)') % based)
    if data.get('fixedSinceBaseline'):
        lines.append(t('Исправлено с прошлого раза: %d', 'Fixed since last time: %d') % len(data['fixedSinceBaseline']))
    lines.append('')
    for c, n in cat.most_common(8):
        lines.append('  %-38s %d' % (CAT_RU.get(c, c)[:38], n))
    if high:
        lines.append('')
        lines.append(t('Важное:', 'High severity:'))
        for x in high[:6]:
            loc = ' (%s:%s)' % (x['file'], x['line']) if x.get('file') else ''
            lines.append('• %s — %s%s' % (str(x['subject'])[:44], x['msg'][:70], loc))
        if len(high) > 6:
            lines.append(t('… и ещё %d', '… and %d more') % (len(high) - 6))
    return '\n'.join(lines)


def staleness():
    """
    Is the DS snapshot stale? Variable values cannot be fetched from here, but
    the plain Files API — available on pro — tells whether the library was
    published after the snapshot. A silent check against stale data is worse
    than no check at all.
    """
    if not os.environ.get('FIGMA_TOKEN'):
        return None
    try:
        r = subprocess.run([sys.executable, os.path.join(HERE, 'bin', 'watch.py')],
                           cwd=HERE, capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            return t('Свежесть ДС проверить не вышло: %s', 'Could not verify DS freshness: %s') % (r.stderr or r.stdout)[:200]
    except Exception as e:
        return t('Свежесть ДС проверить не вышло: %s', 'Could not verify DS freshness: %s') % e
    t = load('trigger.json')
    if not t or not t.get('changed'):
        return None
    d = t.get('delta') or {}
    bits = []
    for verb, word in (('created', t('добавлено', 'added')), ('modified', t('изменено', 'modified')), ('deleted', t('удалено', 'deleted'))):
        names = d.get(verb) or []
        if names:
            bits.append('%s %d (%s%s)' % (word, len(names), ', '.join(names[:3]),
                                          '…' if len(names) > 3 else ''))
    msg = [t('ВНИМАНИЕ: дизайн-система менялась после того, как снят слепок.',
             'WARNING: the design system changed after the snapshot was taken.')]
    if bits:
        msg.append('  ' + '; '.join(bits))
    if t.get('needsVariableRefresh'):
        msg.append(t('  Значения переменных могли поехать — сверка ниже может врать.',
                     '  Variable values may have drifted — the check below may be wrong.'))
    msg.append(t('  Обновить: попросите агента «сними слепок ДС и прогони night-watch».',
                 '  To refresh: ask your agent to "take a DS snapshot and run night-watch".'))
    return '\n'.join(msg)


def status():
    ds = load('ds-latest.json')
    code = load('code-latest.json')
    bl = load('baseline.json')
    fnd = load('findings.json')
    L = []
    if ds:
        L.append(t('Слепок ДС: %s', 'DS snapshot: %s') % ds.get('generatedAt', '?')[:16].replace('T', ' '))
        L.append(t('  переменных %d, компонентов %d, полный: %s', '  variables %d, components %d, complete: %s')
                 % (len(ds.get('variables', {})), len(ds.get('components', [])),
                    t('да', 'yes') if ds.get('variablesComplete') else t('нет', 'no')))
    else:
        L.append(t('Слепка ДС нет — нужен прогон агента с Figma MCP', 'No DS snapshot — an agent run with Figma MCP is needed'))
    if code:
        L.append(t('Скан кода: %s', 'Code scan: %s') % code.get('generatedAt', '?')[:16].replace('T', ' '))
        for p in code.get('prototypes', []):
            if p.get('exists'):
                L.append(t('  %-26s токенов %3d', '  %-26s tokens %3d') % (p['id'][:26], len(p.get('tokens', {}))))
    L.append(t('Базовая линия: %s', 'Baseline: %s') % (t('%d расхождений от %s', '%d findings from %s')
             % (bl['count'], bl['acceptedAt'][:10]) if bl else t('нет', 'none')))
    if fnd:
        L.append(t('Последний прогон: %s', 'Last run: %s') % fnd.get('generatedAt', '?')[:16].replace('T', ' '))
    return '\n'.join(L)


def ds_review():
    p = os.path.join(HERE, 'reports', 'DS-REVIEW.md')
    if not os.path.exists(p):
        return t('Ревью ещё не собиралось. /run', 'No review yet. /run')
    return open(p, encoding='utf-8').read()


PENDING_FIX = {}


def handle(token, chat_id, text):
    cmd = (text or '').strip().split()
    head = cmd[0].lower().lstrip('/').split('@')[0] if cmd else ''
    arg = cmd[1].lower() if len(cmd) > 1 else ''

    if head in ('start', 'help', ''):
        return say(token, chat_id, help_text())

    if head == 'status':
        stale = staleness()
        return say(token, chat_id, status() + (('\n\n' + stale) if stale else ''))

    if head == 'run':
        say(token, chat_id, t('Прогоняю…', 'Running…'))
        stale = staleness()
        if stale:
            say(token, chat_id, stale)
        code, out = run_nw('--fail-on', 'never')
        say(token, chat_id, summary())
        rp = os.path.join(HERE, 'reports', 'REPORT.md')
        if os.path.exists(rp):
            tg_document(token, chat_id, rp, t('Полный отчёт', 'Full report'))
        return

    if head == 'report':
        rp = os.path.join(HERE, 'reports', 'REPORT.md')
        if not os.path.exists(rp):
            return say(token, chat_id, t('Отчёта ещё нет. /run', 'No report yet. /run'))
        return tg_document(token, chat_id, rp, t('Отчёт по сверке', 'Check report'))

    if head == 'ds':
        return say(token, chat_id, ds_review())

    if head == 'fix':
        # Editing files from a phone message is exactly what deserves a confirm step.
        if arg == 'confirm' and PENDING_FIX.get(chat_id, 0) > time.time() - 300:
            PENDING_FIX.pop(chat_id, None)
            say(token, chat_id, t('Пишу чекпоинт и правлю…', 'Writing a checkpoint and fixing…'))
            code, out = run_nw('--fix', '--fail-on', 'never')
            return say(token, chat_id, out[-3500:] or t('готово', 'done'))
        PENDING_FIX[chat_id] = time.time()
        return say(token, chat_id, t(
                   'Это изменит файлы прототипов. Перед правкой будет записан чекпоинт, без него правок не будет.\n\nПодтвердите: /fix confirm\nПодтверждение действует 5 минут.',
                   'This will modify prototype files. A checkpoint is written first; no checkpoint — no edits.\n\nConfirm: /fix confirm\nThe confirmation lasts 5 minutes.'))

    if head == 'accept':
        code, out = run_nw('--accept')
        return say(token, chat_id, out or t('готово', 'done'))

    if re.search(r'обнов\w*\s+(дс|дизайн|слеп)|(update|refresh)\w*\s+(the\s+)?(ds|design|snapshot)', (text or '').lower()):
        return say(token, chat_id, t(
                   'Слепок ДС отсюда не обновить: значения переменных Figma отдаёт только через MCP-сессию агента, обычным токеном их не взять на тарифе pro.\nПопросите агента: «сними слепок ДС и прогони night-watch».',
                   'The DS snapshot cannot be refreshed from here: Figma only serves variable values through an agent MCP session; a plain token will not do below Enterprise.\nAsk your agent to "take a DS snapshot and run night-watch".'))

    say(token, chat_id, t('Не понял. /help', 'Did not understand. /help'))


ENV_FILE = os.path.expanduser('~/.night-watch.env')


def load_env_file():
    """
    Secrets from ~/.night-watch.env when absent from the environment.
    launchd can hold vars right in the plist, but that is plaintext in a file
    that ends up in backups. A separate mode-600 file is better.
    """
    if not os.path.exists(ENV_FILE):
        return
    mode = os.stat(ENV_FILE).st_mode & 0o777
    if mode & 0o077:
        sys.stderr.write(t('ВНИМАНИЕ: %s читаем не только вам (права %o). Поправьте: chmod 600 %s\n',
                           'WARNING: %s is readable by others (mode %o). Fix: chmod 600 %s\n') % (ENV_FILE, mode, ENV_FILE))
    for line in open(ENV_FILE, encoding='utf-8'):
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, _, v = line.partition('=')
        os.environ.setdefault(k.strip(), v.strip().strip('"\''))


def check():
    """Verify the setup without starting anything or going anywhere."""
    load_env_file()
    ok = True
    tok = os.environ.get('TG_BOT_TOKEN', '')
    chats = os.environ.get('TG_ALLOWED_CHATS', '')
    print(t('файл секретов: %s', 'secrets file: %s') % (ENV_FILE if os.path.exists(ENV_FILE) else t('нет', 'missing')))
    if not tok or 'ТОКЕН' in tok or 'TOKEN_FROM' in tok:
        print(t('  TG_BOT_TOKEN — не задан', '  TG_BOT_TOKEN — not set')); ok = False
    elif not re.match(r'^\d{6,}:[A-Za-z0-9_-]{30,}$', tok):
        print(t('  TG_BOT_TOKEN — не похож на токен телеграма', '  TG_BOT_TOKEN — does not look like a Telegram token')); ok = False
    else:
        print(t('  TG_BOT_TOKEN — на месте (%s…)', '  TG_BOT_TOKEN — present (%s…)') % tok.split(':')[0])
    if not chats or 'CHAT' in chats.upper():
        print(t('  TG_ALLOWED_CHATS — не задан', '  TG_ALLOWED_CHATS — not set')); ok = False
    else:
        print('  TG_ALLOWED_CHATS — %s' % chats)
    print('  FIGMA_TOKEN — %s' % (t('задан, свежесть ДС будет проверяться', 'set — DS freshness will be checked')
                                  if os.environ.get('FIGMA_TOKEN') else
                                  t('не задан, проверки свежести ДС не будет', 'not set — no DS freshness checks')))
    for f in ('config.json', 'snapshots/ds-latest.json'):
        print('  %-28s %s' % (f, t('есть', 'present') if os.path.exists(os.path.join(HERE, f)) else t('НЕТ', 'MISSING')))
    print(t('готов к запуску', 'ready to start') if ok else t('не готов — заполните файл секретов', 'not ready — fill in the secrets file'))
    return 0 if ok else 1


def main():
    if '--check' in sys.argv:
        return check()
    load_env_file()
    token = os.environ.get('TG_BOT_TOKEN')
    if not token:
        sys.stderr.write(t(
            'Нет TG_BOT_TOKEN.\nТокен выдаёт @BotFather. Положите его в переменную окружения или\nв %s строкой TG_BOT_TOKEN=...\nБот его только читает, никуда не пишет и не логирует.\n',
            'TG_BOT_TOKEN is not set.\nGet a token from @BotFather. Put it into the environment or\ninto %s as TG_BOT_TOKEN=...\nThe bot only reads it, never stores or logs it.\n') % ENV_FILE)
        return 2
    allowed = {c.strip() for c in (os.environ.get('TG_ALLOWED_CHATS') or '').split(',') if c.strip()}
    if not allowed:
        sys.stderr.write(t(
            'Нет TG_ALLOWED_CHATS.\nЧерез запятую — chat id, которым бот отвечает. Без этого списка любой,\nкто найдёт бота, сможет запускать правки в ваших файлах.\nСвой id узнать: напишите боту что угодно и запустите с TG_ALLOWED_CHATS=whoami\n',
            'TG_ALLOWED_CHATS is not set.\nComma-separated chat ids the bot answers to. Without this list anyone\nwho finds the bot could trigger edits in your files.\nTo learn your id: message the bot and start with TG_ALLOWED_CHATS=whoami\n'))
        return 2

    me = tg(token, 'getMe', {})
    if not me.get('ok'):
        sys.stderr.write(t('Телеграм не принял токен: %s\n', 'Telegram rejected the token: %s\n') % me.get('error', me))
        return 2
    print(t('бот @%s на связи, отвечает чатам: %s', 'bot @%s online, answering chats: %s')
          % (me['result'].get('username'), ', '.join(sorted(allowed))))

    offset = None
    while True:
        upd = tg(token, 'getUpdates', {'offset': offset, 'timeout': 30})
        if not upd.get('ok'):
            print(t('опрос не удался: %s', 'poll failed: %s') % upd.get('error')); time.sleep(5); continue
        for u in upd.get('result', []):
            offset = u['update_id'] + 1
            msg = u.get('message') or u.get('edited_message')
            if not msg:
                continue
            chat_id = str(msg['chat']['id'])
            text = msg.get('text', '')
            if 'whoami' in allowed:
                print('chat id: %s (%s)' % (chat_id, msg['chat'].get('first_name', '')))
                say(token, chat_id, t('Ваш chat id: %s', 'Your chat id: %s') % chat_id)
                continue
            if chat_id not in allowed:
                print(t('чужой чат %s — игнорирую', 'foreign chat %s — ignoring') % chat_id)
                continue
            try:
                handle(token, chat_id, text)
            except Exception as e:
                say(token, chat_id, t('Сломалось: %s', 'Broke: %s') % e)
                print(t('ошибка обработки: %s', 'handler error: %s') % e)


if __name__ == '__main__':
    sys.exit(main())
