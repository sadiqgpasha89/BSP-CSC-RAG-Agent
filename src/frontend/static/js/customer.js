/* Customer chat client. Sessions are bound to the signed token returned by /auth/login; all text is rendered with textContent. */
const API = "/api/v1";
const $ = (id) => document.getElementById(id);
const st = { sub: null, token: null, level: null, session: null, pending: null, lastHuman: 0, poll: null };

function add(text, cls, sources) {
  const d = document.createElement("div");
  d.className = "m " + cls;
  d.textContent = text;
  (sources || []).slice(0, 3).forEach((s) => {
    const e = document.createElement("span");
    e.className = "src";
    e.textContent = "Source: " + (s.title || s.doc_id || s.id || "knowledge base");
    d.appendChild(e);
  });
  $("log").appendChild(d);
  $("log").scrollTop = $("log").scrollHeight;
}

async function post(path, body) {
  const r = await fetch(API + path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || "Request failed (" + r.status + ")");
  return j;
}

function render(res) {
  st.session = res.session_id;
  add(res.final_response, "bot", res.sources);
  st.pending = res.pending_action;
  $("confirm").hidden = !st.pending;
  if (st.pending) $("confirm-summary").textContent = st.pending.summary;
  $("stepup").hidden = !res.requires_step_up;
  if (res.escalated_to_human && res.handover_id) startPolling();
  if (res.escalated_to_human || res.steps_executed > 0) $("csat").hidden = false;
}

async function send(text) {
  if (!text.trim()) return;
  add(text, "me");
  try {
    render(await post("/chat/message", { subscriber_id: st.sub, message: text, session_id: st.session, session_token: st.token, channel: "web", language_hint: $("lang").value || null }));
  } catch (e) { add("Sorry, I couldn't process that: " + e.message, "bot"); }
}

async function control(word) {
  $("confirm").hidden = true;
  try { render(await post("/chat/" + word, { subscriber_id: st.sub, session_id: st.session, session_token: st.token })); }
  catch (e) { add(e.message, "bot"); }
}

function startPolling() {
  if (st.poll) return;
  st.poll = setInterval(async () => {
    try {
      const q = new URLSearchParams({ subscriber_id: st.sub, after: st.lastHuman, session_token: st.token || "" });
      const r = await fetch(API + "/chat/" + encodeURIComponent(st.session) + "/messages?" + q);
      if (!r.ok) return;
      const j = await r.json();
      j.messages.forEach((m) => { add(m.text, "human"); st.lastHuman = Math.max(st.lastHuman, m.ts || m.timestamp || 0); });
    } catch (_) { /* transient network error: try again on next tick */ }
  }, 3000);
}

$("login-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  $("login-error").textContent = "";
  try {
    const r = await post("/auth/login", { identifier: $("identifier").value.trim(), pin: $("pin").value });
    Object.assign(st, { sub: r.subscriber_id, token: r.session_token, level: r.auth_level });
    $("login").hidden = true; $("chat").hidden = false;
    $("who").textContent = st.sub;
    $("assurance").textContent = st.level === "authenticated_mfa" ? "Fully verified" : "Basic sign-in (extra code needed for sensitive changes)";
    add("Hello! I can explain your bill, check balance and usage, recharge, change your plan, help with SIM/eSIM, report problems and more. What do you need?", "bot");
  } catch (e) { $("login-error").textContent = e.message; }
});
$("send-form").addEventListener("submit", (ev) => { ev.preventDefault(); const t = $("msg").value; $("msg").value = ""; send(t); });
$("confirm-yes").addEventListener("click", () => control("confirm"));
$("confirm-no").addEventListener("click", () => control("cancel"));
document.querySelectorAll(".chip").forEach((c) => c.addEventListener("click", () => send(c.textContent)));
document.querySelectorAll("#csat button").forEach((b) => b.addEventListener("click", async () => {
  try { await post("/chat/csat", { subscriber_id: st.sub, session_id: st.session, session_token: st.token, rating: Number(b.dataset.score) }); $("csat").textContent = "Thanks for the feedback."; } catch (_) { $("csat").textContent = "Couldn't record feedback."; }
}));
$("signout").addEventListener("click", () => location.reload());
