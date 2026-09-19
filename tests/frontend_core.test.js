// Browser-side conversation state, exercised without a DOM.
// Run via pytest (tests/test_frontend_core.py) or directly: node tests/frontend_core.test.js
const assert = require("node:assert/strict");
const {
  createConversation,
  statusText,
  resolveToken,
  MAX_HISTORY,
} = require("../static/chat-core.js");

function check(name, fn) {
  try {
    fn();
    console.log("ok   " + name);
  } catch (err) {
    console.error("FAIL " + name);
    throw err;
  }
}

check("the current question is not also sent as history", () => {
  const conv = createConversation();
  conv.add("user", "帕姆是谁？");
  assert.deepEqual(conv.outgoing("帕姆是谁？"), []);
});

check("previous turns are sent with the new question", () => {
  const conv = createConversation();
  conv.add("user", "问题一");
  conv.add("pom", "回答一");
  conv.add("user", "问题二");
  assert.deepEqual(conv.outgoing("问题二"), [
    { role: "user", content: "问题一" },
    { role: "assistant", content: "回答一" },
  ]);
});

check("clearing empties the transcript and invalidates in-flight replies", () => {
  const conv = createConversation();
  conv.add("user", "问题一");
  const token = conv.generation;
  conv.clear();
  assert.equal(conv.isStale(token), true, "旧请求必须被判定为过期");
  assert.deepEqual(conv.history, []);
  assert.equal(conv.isStale(conv.generation), false, "新请求不能算过期");
});

check("history is capped at MAX_HISTORY messages", () => {
  const conv = createConversation();
  for (let i = 0; i < MAX_HISTORY + 10; i += 1) {
    conv.add(i % 2 === 0 ? "user" : "pom", `消息${i}`);
  }
  assert.equal(conv.history.length, MAX_HISTORY);
  assert.equal(conv.history[conv.history.length - 1].content, `消息${MAX_HISTORY + 9}`);
});

check("status text tells the truth about a degraded server", () => {
  const healthy = statusText({
    status: "ok",
    db_ready: true,
    db_chunks: 102243,
    embedder_ready: true,
    model_configured: true,
  });
  assert.equal(healthy.ok, true);
  assert.match(healthy.text, /102243/);

  const emptyKb = statusText({
    status: "degraded",
    db_ready: true,
    db_chunks: 0,
    embedder_ready: true,
    model_configured: true,
  });
  assert.equal(emptyKb.ok, false);
  assert.match(emptyKb.text, /尚未就绪/);

  const noEmbedder = statusText({
    status: "degraded",
    db_ready: true,
    db_chunks: 120,
    embedder_ready: false,
    model_configured: true,
  });
  assert.equal(noEmbedder.ok, false);
  assert.match(noEmbedder.text, /检索模型/);

  const noKey = statusText({
    status: "ok",
    db_ready: true,
    db_chunks: 120,
    embedder_ready: true,
    model_configured: false,
  });
  assert.equal(noKey.ok, false, "没配 Key 不能显示成一切正常");
  assert.match(noKey.text, /通行证/);
});

check("access token comes from the link and is remembered", () => {
  assert.equal(resolveToken("#token=abc123", ""), "abc123");
  assert.equal(resolveToken("", "remembered"), "remembered");
  assert.equal(resolveToken("#other=1", ""), "");
  assert.equal(resolveToken("#token=a%20b", "old"), "a b");
  assert.equal(resolveToken("#token=", "kept"), "kept");
});

console.log("frontend core: all checks passed");
