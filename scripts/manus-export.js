/**
 * Manus Conversation Export Script
 *
 * Usage:
 *   1. Open https://manus.im/app in your browser
 *   2. Log in to your account
 *   3. Open Developer Tools (F12 or Cmd+Option+I)
 *   4. Go to the Console tab
 *   5. Paste this entire script and press Enter
 *   6. Wait for the export to complete — it will download a .jsonl file
 *
 * What it does:
 *   - Reads session IDs from the sidebar
 *   - Fetches full conversation data for each session via the internal API
 *   - Downloads everything as a JSONL file ready for ABLE distillation
 *
 * After download, place the file at:
 *   ~/.able/external_sessions/manus_export.jsonl
 *
 * Then run:
 *   python -m able.core.distillation.import_history --platform manus
 */

(async function exportManusConversations() {
  const LOG_PREFIX = '[ABLE Manus Export]';
  console.log(`${LOG_PREFIX} Starting export...`);

  // Get auth token from localStorage
  const token = localStorage.getItem('token') || localStorage.getItem('session_id');
  if (!token) {
    console.error(`${LOG_PREFIX} No auth token found. Are you logged in?`);
    return;
  }

  // Step 1: Collect session IDs from the Redux/Zustand store or DOM
  let sessionIds = [];

  // Try to get from the app's state store
  try {
    // The Manus app uses Zustand — try to access the store
    const storeKey = Object.keys(window.__NEXT_DATA__?.props?.pageProps || {}).find(k => k.includes('session'));
    if (storeKey) {
      console.log(`${LOG_PREFIX} Found session data in Next.js props`);
    }
  } catch (e) {}

  // Fallback: scrape session IDs from the sidebar DOM
  if (sessionIds.length === 0) {
    console.log(`${LOG_PREFIX} Scanning sidebar for sessions...`);

    // Manus sidebar items have session IDs in their links or data attributes
    const sidebarLinks = document.querySelectorAll('a[href*="/app/"], [data-session-id], [class*="session"]');
    const hrefPattern = /\/app\/([a-zA-Z0-9_-]+)/;

    for (const el of sidebarLinks) {
      const href = el.getAttribute('href') || '';
      const match = href.match(hrefPattern);
      if (match && match[1] && match[1] !== 'app' && match[1].length > 5) {
        sessionIds.push(match[1]);
      }
      const dataId = el.getAttribute('data-session-id');
      if (dataId) sessionIds.push(dataId);
    }

    // Also try the Next.js router state
    try {
      const routerState = JSON.parse(sessionStorage.getItem('__next_router_state') || '{}');
      if (routerState.sessions) {
        sessionIds.push(...Object.keys(routerState.sessions));
      }
    } catch (e) {}
  }

  // Deduplicate
  sessionIds = [...new Set(sessionIds)];

  if (sessionIds.length === 0) {
    console.log(`${LOG_PREFIX} No sessions found in sidebar. Trying API approach...`);

    // Try the getSessionV2 API with a probe
    // If we can't enumerate, ask user to scroll sidebar to load all sessions
    console.log(`${LOG_PREFIX} Please scroll through your sidebar to load all sessions, then run this script again.`);
    console.log(`${LOG_PREFIX} Or try the API key approach: go to your Manus Settings → API to get a key.`);

    // Alternative: try to intercept the WebSocket connection
    // Look for existing socket connections
    const perfEntries = performance.getEntriesByType('resource');
    const wsEntries = perfEntries.filter(e => e.name.includes('api.manus.im'));
    console.log(`${LOG_PREFIX} Found ${wsEntries.length} connections to api.manus.im`);

    // Try to access the app's internal store via React DevTools fiber
    try {
      const appRoot = document.getElementById('__next');
      if (appRoot && appRoot._reactRootContainer) {
        console.log(`${LOG_PREFIX} Found React root — attempting state extraction...`);
      }

      // Try zustand devtools
      if (window.__ZUSTAND_DEVTOOLS_GLOBAL_STORE__) {
        const stores = window.__ZUSTAND_DEVTOOLS_GLOBAL_STORE__;
        for (const [name, store] of Object.entries(stores)) {
          const state = store.getState();
          if (state.sessions) {
            const ids = Object.keys(state.sessions.entities || state.sessions);
            sessionIds.push(...ids);
            console.log(`${LOG_PREFIX} Found ${ids.length} sessions in ${name} store`);
          }
        }
      }
    } catch (e) {
      console.log(`${LOG_PREFIX} State extraction failed:`, e.message);
    }
  }

  // If we still have no session IDs, try the network intercept approach
  if (sessionIds.length === 0) {
    console.log(`${LOG_PREFIX} Attempting to intercept session data from network...`);

    // Monkey-patch fetch to capture session-related responses
    const originalFetch = window.fetch;
    const capturedSessions = [];

    window.fetch = async function(...args) {
      const response = await originalFetch.apply(this, args);
      const url = typeof args[0] === 'string' ? args[0] : args[0]?.url || '';
      if (url.includes('session') || url.includes('task')) {
        try {
          const clone = response.clone();
          const data = await clone.json();
          capturedSessions.push({ url, data });
        } catch (e) {}
      }
      return response;
    };

    console.log(`${LOG_PREFIX} Network intercept active. Navigate through your conversations in the sidebar.`);
    console.log(`${LOG_PREFIX} After browsing, run: window.__ABLE_EXPORT_CAPTURED()`);

    window.__ABLE_EXPORT_CAPTURED = async function() {
      window.fetch = originalFetch; // Restore
      console.log(`${LOG_PREFIX} Captured ${capturedSessions.length} session-related responses`);

      const conversations = [];
      for (const { url, data } of capturedSessions) {
        if (data?.data?.events || data?.data?.messages) {
          const session = data.data;
          conversations.push({
            source: 'manus',
            session_id: session.sessionId || session.id || url.split('sessionId=')[1],
            title: session.title || 'Manus Session',
            messages: extractMessages(session),
            exported_at: new Date().toISOString(),
          });
        }
      }

      downloadJsonl(conversations);
    };

    return;
  }

  console.log(`${LOG_PREFIX} Found ${sessionIds.length} sessions. Fetching details...`);

  // Step 2: Fetch full conversation data for each session
  const conversations = [];
  let fetched = 0;
  let failed = 0;

  for (const sessionId of sessionIds) {
    try {
      const resp = await fetch(`/api/chat/getSessionV2?sessionId=${sessionId}`, {
        headers: { 'Authorization': `Bearer ${token}` },
      });

      if (!resp.ok) {
        // Try the other endpoint
        const resp2 = await fetch(`/api/chat/getSession?sessionId=${sessionId}`, {
          headers: { 'Authorization': `Bearer ${token}` },
        });
        if (!resp2.ok) {
          failed++;
          continue;
        }
        var data = await resp2.json();
      } else {
        var data = await resp.json();
      }

      if (data?.success || data?.data) {
        const session = data.data || data;
        conversations.push({
          source: 'manus',
          session_id: sessionId,
          title: session.title || session.name || 'Manus Session',
          messages: extractMessages(session),
          created_at: session.createdAt || session.created_at,
          exported_at: new Date().toISOString(),
        });
        fetched++;
        console.log(`${LOG_PREFIX} [${fetched}/${sessionIds.length}] ${session.title || sessionId}`);
      }

      // Rate limit: 200ms between requests
      await new Promise(r => setTimeout(r, 200));
    } catch (e) {
      failed++;
      console.warn(`${LOG_PREFIX} Failed to fetch ${sessionId}:`, e.message);
    }
  }

  console.log(`${LOG_PREFIX} Fetched ${fetched} conversations (${failed} failed)`);

  if (conversations.length === 0) {
    console.log(`${LOG_PREFIX} No conversations exported. Try the network intercept approach above.`);
    return;
  }

  // Step 3: Download as JSONL
  downloadJsonl(conversations);

  function extractMessages(session) {
    const messages = [];
    const events = session.events || session.messages || session.timeline || [];

    for (const event of (Array.isArray(events) ? events : [])) {
      // Handle different event formats
      if (event.role && event.content) {
        messages.push({
          role: event.role,
          content: typeof event.content === 'string' ? event.content : JSON.stringify(event.content),
        });
      } else if (event.type === 'user_message' || event.type === 'userMessage') {
        messages.push({
          role: 'user',
          content: event.content || event.text || event.message || '',
        });
      } else if (event.type === 'agent_message' || event.type === 'agentMessage' || event.type === 'reply') {
        messages.push({
          role: 'assistant',
          content: event.content || event.text || event.message || '',
        });
      } else if (event.type === 'tool_call' || event.type === 'toolCall') {
        messages.push({
          role: 'assistant',
          content: `[Tool: ${event.name || event.toolName || 'unknown'}] ${event.input || event.args || ''}`,
          tool_call: true,
        });
      } else if (event.type === 'thought' || event.type === 'thinking') {
        messages.push({
          role: 'assistant',
          content: `<think>${event.content || event.text || ''}</think>`,
          thinking: true,
        });
      }
    }

    return messages;
  }

  function downloadJsonl(conversations) {
    const lines = conversations.map(c => JSON.stringify(c)).join('\n');
    const blob = new Blob([lines], { type: 'application/jsonl' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `manus_export_${new Date().toISOString().slice(0, 10)}.jsonl`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);

    console.log(`${LOG_PREFIX} Downloaded ${conversations.length} conversations as ${a.download}`);
    console.log(`${LOG_PREFIX} Move to: ~/.able/external_sessions/${a.download}`);
    console.log(`${LOG_PREFIX} Then run: python -m able.core.distillation.import_history --platform manus`);
  }
})();
