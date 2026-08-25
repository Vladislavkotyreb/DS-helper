// Figma → GitHub relay.
//
// Figma POSTs to a bare URL and cannot set custom headers, while GitHub's
// repository_dispatch demands Authorization. Hence a middleman: thirty lines,
// deployed in a minute.
//
// Worker environment:
//   NW_WEBHOOK_PASSCODE  — the same secret given to Figma when creating the webhook
//   GITHUB_TOKEN         — a token with repo scope (for repository_dispatch)
//   GITHUB_REPO          — e.g. Vladislavkotyreb/DS-helper
//
// Keep secrets in Worker Secrets, never in code.

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

    // Shared secret: without it anyone could wake our pipeline.
    if (!env.NW_WEBHOOK_PASSCODE || payload.passcode !== env.NW_WEBHOOK_PASSCODE) {
      return new Response('forbidden', { status: 403 });
    }

    // A PING arrives right after webhook creation — just acknowledge it.
    if (payload.event_type === 'PING') {
      return new Response('pong', { status: 200 });
    }

    if (payload.event_type !== 'LIBRARY_PUBLISH') {
      return new Response('ignored', { status: 200 });
    }

    // The secret goes no further: GitHub does not need it and logs must not see it.
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
      // Figma retries delivery on a non-2xx response.
      return new Response(`github ${res.status}`, { status: 502 });
    }
    return new Response('dispatched', { status: 200 });
  },
};
