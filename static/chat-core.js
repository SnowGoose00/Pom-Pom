/* Browser-side conversation state, kept free of DOM so it can be unit tested
 * (tests/frontend_core.test.js). app.js wires it to the actual page. */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  } else {
    root.PomPomCore = api;
  }
})(typeof self !== "undefined" ? self : this, function () {
  const MAX_HISTORY = 40; // 20 turns (user + assistant), matches HISTORY_TURNS
  const MAX_MESSAGES = 40; // what we keep in the browser across reloads

  /**
   * Turn /api/health into the line under the title.
   * `status` is "degraded" whenever the server cannot really answer: knowledge
   * base missing/empty, or the embedding model failed to load.
   */
  function statusText(data) {
    if (!data || !data.db_ready || !data.db_chunks) {
      return { text: "智库尚未就绪，请运行数据导入", ok: false };
    }
    if (data.status !== "ok" || !data.embedder_ready) {
      return { text: "列车智库在线，但检索模型没加载好，请查看服务日志", ok: false };
    }
    return {
      text:
        `列车智库在线 · ${data.db_chunks} 条记录` +
        (data.model_configured ? "" : " · 未配置通行证"),
      ok: Boolean(data.model_configured),
    };
  }

  /**
   * Where the access token comes from: a `#token=…` link (share this with the
   * people you want to let in) or the value we remembered last time.
   */
  function resolveToken(hash, stored) {
    const match = /(?:^|[#&])token=([^&]+)/.exec(String(hash || ""));
    if (match) {
      try {
        return decodeURIComponent(match[1]);
      } catch (err) {
        return match[1];
      }
    }
    return stored || "";
  }

  function createConversation(initial) {
    let transcript = Array.isArray(initial) ? initial.slice() : [];
    let generation = 0;

    function history() {
      return transcript
        .map((item) => ({
          role: item.role === "user" ? "user" : "assistant",
          content: item.text,
        }))
        .slice(-MAX_HISTORY);
    }

    return {
      get transcript() {
        return transcript.slice();
      },
      get history() {
        return history();
      },
      /** Bumped on every clear, so replies from before the clear can be dropped. */
      get generation() {
        return generation;
      },
      add(role, text) {
        transcript.push({ role, text });
        if (transcript.length > MAX_MESSAGES) {
          transcript = transcript.slice(-MAX_MESSAGES);
        }
      },
      clear() {
        transcript = [];
        generation += 1;
      },
      /**
       * History to send with `message`: the current question travels as
       * `message` itself, so keeping it in history would send it twice.
       */
      outgoing(message) {
        const items = history();
        const last = items[items.length - 1];
        if (last && last.role === "user" && last.content === message) {
          items.pop();
        }
        return items;
      },
      /** True when `token` was captured before a clear() that has since happened. */
      isStale(token) {
        return token !== generation;
      },
    };
  }

  return { MAX_HISTORY, MAX_MESSAGES, createConversation, statusText, resolveToken };
});
