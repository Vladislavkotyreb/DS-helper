#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Night Watch в телеграме: прогонять сверку сообщением.

Работает длинным опросом (getUpdates), поэтому публичный адрес не нужен —
запускается на ноутбуке и живёт за NAT.

    TG_BOT_TOKEN=... TG_ALLOWED_CHATS=123456 python3 bin/tgbot.py

Токен берётся из окружения и никуда не пишется и не логируется.
Отвечает только тем, чьи chat id перечислены в TG_ALLOWED_CHATS: иначе любой,
кто найдёт бота, сможет запускать правки в чужих файлах.

Чего бот не умеет: обновить слепок дизайн-системы. Значения переменных Figma
отдаёт только через MCP-сессию агента (Variables REST API — Enterprise).
На вопрос «обнови ДС» он честно скажет, что это делается через агента.
"""
import json, os, re, subprocess, sys, time, urllib.request, urllib.error, mimetypes

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = 'https://api.telegram.org/bot%s/%s'

HELP = """Night Watch — сверка вёрстки с дизайн-системой R4S.

/run — прогнать сверку, прислать сводку
/report — полный отчёт файлом
/ds — что изменилось в дизайн-системе
/status — состояние: слепок, находки, базовая линия
/fix — механические правки (спросит подтверждение)
/accept — принять текущее за базовую линию
/help — эта справка

Слепок ДС обновляется не отсюда, а прогоном агента с Figma MCP.
Но если задан FIGMA_TOKEN, бот заметит, что ДС уехала, и предупредит."""


# ---------- транспорт ----------

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
    """sendDocument — multipart руками, чтобы не тащить зависимости."""
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
    # У телеграма предел 4096 символов на сообщение — режем по строкам,
    # чтобы не рвать посередине слова.
    text = text.strip() or '(пусто)'
    while text:
        if len(text) <= 3900:
            chunk, text = text, ''
        else:
            cut = text.rfind('\n', 0, 3900)
            cut = cut if cut > 2000 else 3900
            chunk, text = text[:cut], text[cut:]
        tg(token, 'sendMessage', {'chat_id': chat_id, 'text': chunk,
                                  'disable_web_page_preview': True})


# ---------- команды ----------

def run_nw(*extra):
    r = subprocess.run([sys.executable, os.path.join(HERE, 'bin', 'nw.py')] + list(extra),
                       cwd=HERE, capture_output=True, text=True, timeout=600)
    return r.returncode, (r.stdout or '') + (r.stderr or '')


def load(name, default=None):
    p = os.path.join(HERE, 'snapshots', name)
    return json.load(open(p, encoding='utf-8')) if os.path.exists(p) else default


def summary():
    """Короткая сводка находок — то, что влезает в одно сообщение."""
    data = load('findings.json')
    if not data:
        return 'Прогонов ещё не было. /run'
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

    lines = ['Расхождений: %d, важных %d' % (len(fresh), len(high))]
    if based:
        lines.append('В базовой линии: %d (не считаются)' % based)
    if data.get('fixedSinceBaseline'):
        lines.append('Исправлено с прошлого раза: %d' % len(data['fixedSinceBaseline']))
    lines.append('')
    for c, n in cat.most_common(8):
        lines.append('  %-38s %d' % (CAT_RU.get(c, c)[:38], n))
    if high:
        lines.append('')
        lines.append('Важное:')
        for x in high[:6]:
            loc = ' (%s:%s)' % (x['file'], x['line']) if x.get('file') else ''
            lines.append('• %s — %s%s' % (str(x['subject'])[:44], x['msg'][:70], loc))
        if len(high) > 6:
            lines.append('… и ещё %d' % (len(high) - 6))
    return '\n'.join(lines)


def staleness():
    """
    Не устарел ли слепок ДС. Значения переменных отсюда не снять, но узнать,
    что библиотеку публиковали после слепка, можно обычным Files API — он на pro
    доступен. Молчаливая сверка со старым слепком хуже, чем отсутствие сверки.
    """
    if not os.environ.get('FIGMA_TOKEN'):
        return None
    try:
        r = subprocess.run([sys.executable, os.path.join(HERE, 'bin', 'watch.py')],
                           cwd=HERE, capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            return 'Свежесть ДС проверить не вышло: %s' % (r.stderr or r.stdout)[:200]
    except Exception as e:
        return 'Свежесть ДС проверить не вышло: %s' % e
    t = load('trigger.json')
    if not t or not t.get('changed'):
        return None
    d = t.get('delta') or {}
    bits = []
    for verb, word in (('created', 'добавлено'), ('modified', 'изменено'), ('deleted', 'удалено')):
        names = d.get(verb) or []
        if names:
            bits.append('%s %d (%s%s)' % (word, len(names), ', '.join(names[:3]),
                                          '…' if len(names) > 3 else ''))
    msg = ['ВНИМАНИЕ: дизайн-система менялась после того, как снят слепок.']
    if bits:
        msg.append('  ' + '; '.join(bits))
    if t.get('needsVariableRefresh'):
        msg.append('  Значения переменных могли поехать — сверка ниже может врать.')
    msg.append('  Обновить: попросите агента «сними слепок ДС и прогони night-watch».')
    return '\n'.join(msg)


def status():
    ds = load('ds-latest.json')
    code = load('code-latest.json')
    bl = load('baseline.json')
    fnd = load('findings.json')
    L = []
    if ds:
        L.append('Слепок ДС: %s' % ds.get('generatedAt', '?')[:16].replace('T', ' '))
        L.append('  переменных %d, компонентов %d, полный: %s'
                 % (len(ds.get('variables', {})), len(ds.get('components', [])),
                    'да' if ds.get('variablesComplete') else 'нет'))
    else:
        L.append('Слепка ДС нет — нужен прогон агента с Figma MCP')
    if code:
        L.append('Скан кода: %s' % code.get('generatedAt', '?')[:16].replace('T', ' '))
        for p in code.get('prototypes', []):
            if p.get('exists'):
                L.append('  %-26s токенов %3d' % (p['id'][:26], len(p.get('tokens', {}))))
    L.append('Базовая линия: %s' % ('%d расхождений от %s'
             % (bl['count'], bl['acceptedAt'][:10]) if bl else 'нет'))
    if fnd:
        L.append('Последний прогон: %s' % fnd.get('generatedAt', '?')[:16].replace('T', ' '))
    return '\n'.join(L)


def ds_review():
    p = os.path.join(HERE, 'reports', 'DS-REVIEW.md')
    if not os.path.exists(p):
        return 'Ревью ещё не собиралось. /run'
    return open(p, encoding='utf-8').read()


PENDING_FIX = {}


def handle(token, chat_id, text):
    cmd = (text or '').strip().split()
    head = cmd[0].lower().lstrip('/').split('@')[0] if cmd else ''
    arg = cmd[1].lower() if len(cmd) > 1 else ''

    if head in ('start', 'help', ''):
        return say(token, chat_id, HELP)

    if head == 'status':
        stale = staleness()
        return say(token, chat_id, status() + (('\n\n' + stale) if stale else ''))

    if head == 'run':
        say(token, chat_id, 'Прогоняю…')
        stale = staleness()
        if stale:
            say(token, chat_id, stale)
        code, out = run_nw('--fail-on', 'never')
        say(token, chat_id, summary())
        rp = os.path.join(HERE, 'reports', 'REPORT.md')
        if os.path.exists(rp):
            tg_document(token, chat_id, rp, 'Полный отчёт')
        return

    if head == 'report':
        rp = os.path.join(HERE, 'reports', 'REPORT.md')
        if not os.path.exists(rp):
            return say(token, chat_id, 'Отчёта ещё нет. /run')
        return tg_document(token, chat_id, rp, 'Отчёт по сверке')

    if head == 'ds':
        return say(token, chat_id, ds_review())

    if head == 'fix':
        # Правка файлов по сообщению из телефона — то, что стоит переспросить.
        if arg == 'confirm' and PENDING_FIX.get(chat_id, 0) > time.time() - 300:
            PENDING_FIX.pop(chat_id, None)
            say(token, chat_id, 'Пишу чекпоинт и правлю…')
            code, out = run_nw('--fix', '--fail-on', 'never')
            return say(token, chat_id, out[-3500:] or 'готово')
        PENDING_FIX[chat_id] = time.time()
        return say(token, chat_id,
                   'Это изменит файлы прототипов. Перед правкой будет записан чекпоинт, '
                   'без него правок не будет.\n\nПодтвердите: /fix confirm\n'
                   'Подтверждение действует 5 минут.')

    if head == 'accept':
        code, out = run_nw('--accept')
        return say(token, chat_id, out or 'готово')

    if re.search(r'обнов\w*\s+(дс|дизайн|слеп)', (text or '').lower()):
        return say(token, chat_id,
                   'Слепок ДС отсюда не обновить: значения переменных Figma отдаёт только '
                   'через MCP-сессию агента, обычным токеном их не взять на тарифе pro.\n'
                   'Попросите агента: «сними слепок ДС и прогони night-watch».')

    say(token, chat_id, 'Не понял. /help')


ENV_FILE = os.path.expanduser('~/.night-watch.env')


def load_env_file():
    """
    Секреты из ~/.night-watch.env, если их нет в окружении.
    launchd умеет держать переменные прямо в plist, но там они лежат открытым
    текстом в файле, который попадает в бэкапы. Отдельный файл с правами 600 лучше.
    """
    if not os.path.exists(ENV_FILE):
        return
    mode = os.stat(ENV_FILE).st_mode & 0o777
    if mode & 0o077:
        sys.stderr.write('ВНИМАНИЕ: %s читаем не только вам (права %o). '
                         'Поправьте: chmod 600 %s\n' % (ENV_FILE, mode, ENV_FILE))
    for line in open(ENV_FILE, encoding='utf-8'):
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, _, v = line.partition('=')
        os.environ.setdefault(k.strip(), v.strip().strip('"\''))


def check():
    """Проверить настройку, ничего не запуская и никуда не ходя."""
    load_env_file()
    ok = True
    tok = os.environ.get('TG_BOT_TOKEN', '')
    chats = os.environ.get('TG_ALLOWED_CHATS', '')
    print('файл секретов: %s' % (ENV_FILE if os.path.exists(ENV_FILE) else 'нет'))
    if not tok or 'ТОКЕН' in tok:
        print('  TG_BOT_TOKEN — не задан'); ok = False
    elif not re.match(r'^\d{6,}:[A-Za-z0-9_-]{30,}$', tok):
        print('  TG_BOT_TOKEN — не похож на токен телеграма'); ok = False
    else:
        print('  TG_BOT_TOKEN — на месте (%s…)' % tok.split(':')[0])
    if not chats or 'CHAT' in chats.upper():
        print('  TG_ALLOWED_CHATS — не задан'); ok = False
    else:
        print('  TG_ALLOWED_CHATS — %s' % chats)
    print('  FIGMA_TOKEN — %s' % ('задан, свежесть ДС будет проверяться'
                                  if os.environ.get('FIGMA_TOKEN') else
                                  'не задан, проверки свежести ДС не будет'))
    for f in ('config.json', 'snapshots/ds-latest.json'):
        print('  %-28s %s' % (f, 'есть' if os.path.exists(os.path.join(HERE, f)) else 'НЕТ'))
    print('готов к запуску' if ok else 'не готов — заполните файл секретов')
    return 0 if ok else 1


def main():
    if '--check' in sys.argv:
        return check()
    load_env_file()
    token = os.environ.get('TG_BOT_TOKEN')
    if not token:
        sys.stderr.write(
            'Нет TG_BOT_TOKEN.\n'
            'Токен выдаёт @BotFather. Положите его в переменную окружения или\n'
            'в %s строкой TG_BOT_TOKEN=...\n'
            'Бот его только читает, никуда не пишет и не логирует.\n' % ENV_FILE)
        return 2
    allowed = {c.strip() for c in (os.environ.get('TG_ALLOWED_CHATS') or '').split(',') if c.strip()}
    if not allowed:
        sys.stderr.write(
            'Нет TG_ALLOWED_CHATS.\n'
            'Через запятую — chat id, которым бот отвечает. Без этого списка любой,\n'
            'кто найдёт бота, сможет запускать правки в ваших файлах.\n'
            'Свой id узнать: напишите боту что угодно и запустите с TG_ALLOWED_CHATS=whoami\n')
        return 2

    me = tg(token, 'getMe', {})
    if not me.get('ok'):
        sys.stderr.write('Телеграм не принял токен: %s\n' % me.get('error', me))
        return 2
    print('бот @%s на связи, отвечает чатам: %s'
          % (me['result'].get('username'), ', '.join(sorted(allowed))))

    offset = None
    while True:
        upd = tg(token, 'getUpdates', {'offset': offset, 'timeout': 30})
        if not upd.get('ok'):
            print('опрос не удался: %s' % upd.get('error')); time.sleep(5); continue
        for u in upd.get('result', []):
            offset = u['update_id'] + 1
            msg = u.get('message') or u.get('edited_message')
            if not msg:
                continue
            chat_id = str(msg['chat']['id'])
            text = msg.get('text', '')
            if 'whoami' in allowed:
                print('chat id: %s (%s)' % (chat_id, msg['chat'].get('first_name', '')))
                say(token, chat_id, 'Ваш chat id: %s' % chat_id)
                continue
            if chat_id not in allowed:
                print('чужой чат %s — игнорирую' % chat_id)
                continue
            try:
                handle(token, chat_id, text)
            except Exception as e:
                say(token, chat_id, 'Сломалось: %s' % e)
                print('ошибка обработки: %s' % e)


if __name__ == '__main__':
    sys.exit(main())
