// Реле Figma → GitHub.
//
// Figma шлёт POST на голый URL и не умеет ставить произвольные заголовки,
// а repository_dispatch у GitHub требует Authorization. Поэтому между ними
// нужен посредник. Это он: тридцать строк, разворачивается за минуту.
//
// Переменные окружения воркера:
//   NW_WEBHOOK_PASSCODE  — тот же секрет, что отдан Figma при создании вебхука
//   GITHUB_TOKEN         — токен с правом repo (для repository_dispatch)
//   GITHUB_REPO          — например Vladislavkotyreb/DS-helper
//
// Секреты держать в Worker Secrets, не в коде.

export default {
  async fetch(request, env) {
    if (request.method !== 'POST') {
      return new Response('only POST', { status: 405 });
    }

    let payload;
    try {
      payload = await request.json();
    } catch {
      return new Response('bad json', { status: 400 });
    }

    // Общий секрет: без него любой желающий мог бы будить наш пайплайн.
    if (!env.NW_WEBHOOK_PASSCODE || payload.passcode !== env.NW_WEBHOOK_PASSCODE) {
      return new Response('forbidden', { status: 403 });
    }

    // PING приходит сразу после создания вебхука — просто подтверждаем.
    if (payload.event_type === 'PING') {
      return new Response('pong', { status: 200 });
    }

    if (payload.event_type !== 'LIBRARY_PUBLISH') {
      return new Response('ignored', { status: 200 });
    }

    // Секрет дальше не передаём: GitHub он не нужен и в логах ему не место.
    const { passcode, ...clean } = payload;

    const res = await fetch(`https://api.github.com/repos/${env.GITHUB_REPO}/dispatches`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${env.GITHUB_TOKEN}`,
        Accept: 'application/vnd.github+json',
        'User-Agent': 'night-watch-relay',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        event_type: 'figma-library-publish',
        client_payload: clean,
      }),
    });

    if (!res.ok) {
      // Figma повторит доставку, если ответить не-2xx.
      return new Response(`github ${res.status}`, { status: 502 });
    }
    return new Response('dispatched', { status: 200 });
  },
};
