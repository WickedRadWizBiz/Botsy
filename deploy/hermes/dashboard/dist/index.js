/*
 * Botsy tab for the Hermes v0.21.7 dashboard (docs/OVERSEER.md "Botsy dashboard tab").
 * A plain IIFE: React and the UI parts come from window.__HERMES_PLUGIN_SDK__; nothing is bundled.
 * VIEW everything plus two actions, Pause and Lower cap. The only backend calls are the four
 * routes in ROUTES (plugin_api.py); there is no approve, resume, raise or settings call.
 * All text is rendered as React text (never as HTML).
 */
(function () {
  "use strict";

  var SDK = window.__HERMES_PLUGIN_SDK__;
  var REG = window.__HERMES_PLUGINS__;
  if (!SDK || !REG) return;
  var React = SDK.React;
  var h = React.createElement;
  var useState = SDK.hooks.useState;
  var useEffect = SDK.hooks.useEffect;
  var useCallback = SDK.hooks.useCallback;
  var C = SDK.components || {};
  var Card = C.Card || "div";
  var CardHeader = C.CardHeader || "div";
  var CardTitle = C.CardTitle || "h3";
  var CardContent = C.CardContent || "div";
  var Button = C.Button || "button";
  var Badge = C.Badge || "span";

  var BASE = "/api/plugins/botsy";
  var ROUTES = {
    overview: BASE + "/overview",
    thumb: BASE + "/thumb",
    pause: BASE + "/pause",
    lower_cap: BASE + "/lower_cap"
  };
  var REFRESH_MS = 60000;

  function get(url) {
    return SDK.fetchJSON(url);
  }

  function post(url, body) {
    return SDK.fetchJSON(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Botsy-Tab": "1" },
      body: JSON.stringify(body || {})
    });
  }

  function money(v) {
    return typeof v === "number" ? "$" + v.toFixed(2) : "-";
  }

  function when(v) {
    if (!v) return "-";
    try {
      return new Date(v).toLocaleString();
    } catch (e) {
      return String(v);
    }
  }

  function text(v) {
    return v === null || v === undefined || v === "" ? "-" : String(v);
  }

  function section(title, body, extra) {
    return h(Card, { className: "botsy-card" },
      h(CardHeader, null, h(CardTitle, null, title), extra || null),
      h(CardContent, null, body));
  }

  function refused(s) {
    var why = (s && (s.explain && s.explain.length ? s.explain.join(" ") : (s.reasons || []).join(", "))) || "";
    return h("p", { className: "botsy-muted" }, "Not available" + (why ? ": " + why : "."));
  }

  function table(head, rows) {
    return h("table", { className: "botsy-table" },
      h("thead", null, h("tr", null, head.map(function (x, i) { return h("th", { key: i }, x); }))),
      h("tbody", null, rows.length ? rows : h("tr", null, h("td", { colSpan: head.length, className: "botsy-muted" }, "Nothing here."))));
  }

  // ------------------------------------------------------------------ sections

  function StatusCard(props) {
    var s = props.s;
    if (!s.ok) return section("Status", refused(s));
    var label = s.state === "budget_stop" ? "Budget stop" : s.state === "paused" ? "Paused" : "Running";
    var lines = [];
    if (s.paused) {
      lines.push(h("li", { key: "p" }, "Paused by " + text(s.paused.by) + " since " + when(s.paused.since) +
        " (" + text(s.paused.reason_code) + ")" + (s.paused.by === "user" ? ". Only you can lift it." : ".")));
    }
    if (s.budget_stop) {
      lines.push(h("li", { key: "b" }, "Budget stop for " + text(s.budget_stop.month) + " since " + when(s.budget_stop.since)));
    }
    lines.push(h("li", { key: "l" }, "Last check: " + when(s.last_checked) +
      (s.last_check ? " (items " + text(s.last_check.items) + ", drafts " + text(s.last_check.drafts) +
        ", errors " + text(s.last_check.errors) + ")" : "")));
    lines.push(h("li", { key: "m" }, s.live ? "Live: drafts go to Etsy." : "Practice mode (dry run)."));
    // level3_last_24h counts every approval Hermes makes and only those of yours that lead to
    // posting on Etsy or KDP (user decision D1, 2026-10-21); it is not "Hermes's" count.
    lines.push(h("li", { key: "a" }, "Approvals counted in 24 h (all of Hermes's, yours that post to Etsy or KDP): " +
      text(s.approvals_today.used) + " of " + text(s.approvals_today.limit) + "; Hermes fixes in 24 h: " + text(s.fixes_today)));
    if (!s.limits_file_ok) lines.push(h("li", { key: "f", className: "botsy-warn" }, "The limits file is not OK: Hermes's orders are refused."));
    return section("Status", h("ul", { className: "botsy-list" }, lines),
      h(Badge, { className: "botsy-state botsy-state-" + s.state }, label));
  }

  function MoneyCard(props) {
    var m = props.m;
    if (!m.ok) return section("Money", refused(m));
    var cap = m.cap || 0;
    var pct = cap > 0 ? Math.min(100, Math.round(100 * (m.spent || 0) / cap)) : 0;
    var warnPct = cap > 0 && typeof m.warn_line === "number" ? Math.min(100, Math.round(100 * m.warn_line / cap)) : null;
    return section("Money (" + text(m.month) + ")", h("div", null,
      h("div", { className: "botsy-bar" },
        h("div", { className: "botsy-bar-fill" + (m.warn_reached ? " botsy-bar-warn" : ""), style: { width: pct + "%" } }),
        warnPct === null ? null : h("div", { className: "botsy-bar-line", style: { left: warnPct + "%" }, title: "warn line" })),
      h("p", null, "Spent " + money(m.spent) + " of " + money(m.cap) + " (warn at " + money(m.warn_line) + "); left " +
        money(m.money_left) + ". Cap from " + text(m.cap_source) + "."),
      m.cap_reached ? h("p", { className: "botsy-warn" }, "The cap is reached.") : null));
  }

  function PlanCard(props) {
    var p = props.p;
    if (!p.ok) return section("Weekly plan", refused(p));
    var rows = p.weeks.map(function (w) {
      var held = w.held ? "held by " + text(w.held_by) : "";
      return h("tr", { key: w.plan_week },
        h("td", null, text(w.plan_week) + (w.plan_week === p.this_week ? " (this week)" : "")),
        h("td", null, w.has_plan ? text(w.status) : "no plan"),
        h("td", null, w.has_plan ? w.lines_done + " / " + w.lines_total : "-"),
        h("td", null, w.has_plan ? text(w.packs) : "-"),
        h("td", null, money(w.cost_usd)),
        h("td", null, held || (w.needs_approval ? "waits for you (" + text(w.approval_reason) + ")" : "-")),
        h("td", null, text(w.start_at_local)));
    });
    return section("Weekly plan", h("div", null,
      table(["Week", "Status", "Lines made", "Packs", "Cost", "Hold / approval", "Starts (New York)"], rows),
      h("p", { className: "botsy-muted" }, "Mode: " + text(p.mode) + "; plans approved so far: " + text(p.approved_count) +
        ". Approve, hold or start plans in Discord or on the server, not here.")));
  }

  function Thumb(props) {
    var st = useState(null);
    var img = st[0], setImg = st[1];
    var show = useCallback(function () {
      setImg({ loading: true });
      get(ROUTES.thumb + "?item_id=" + encodeURIComponent(props.id)).then(function (r) {
        setImg(r && r.ok && typeof r.data_url === "string" && r.data_url.indexOf("data:image/jpeg;base64,") === 0
          ? { url: r.data_url } : { none: true });
      }).catch(function () { setImg({ none: true }); });
    }, [props.id]);
    if (!img) return h(Button, { size: "sm", variant: "outline", onClick: show }, "Show");
    if (img.loading) return h("span", { className: "botsy-muted" }, "...");
    if (img.none) return h("span", { className: "botsy-muted" }, "no image");
    return h("img", { className: "botsy-thumb", src: img.url, alt: "our own product image" });
  }

  function ReviewCard(props) {
    var r = props.r;
    if (!r.ok) return section("Review queue", refused(r));
    var rows = r.items.map(function (it) {
      return h("tr", { key: it.item_id },
        h("td", null, h(Thumb, { id: it.item_id })),
        h("td", { className: "botsy-mono" }, it.item_id),
        h("td", null, text(it.niche)),
        h("td", null, text(it.theme)),
        h("td", null, it.reasons.join(", ") || "-"),
        h("td", null, text(it.queued)),
        h("td", null, it.approvable ? "yes" : "no"));
    });
    return section("Review queue (" + r.count + (r.more ? ", " + r.more + " more" : "") + ")", h("div", null,
      table(["Image", "Item", "Niche", "Theme", "Reasons", "Queued", "Approvable"], rows),
      h("p", { className: "botsy-muted" }, "Approve or reject in Discord or with python -m qa review on the server.")));
  }

  function orderRows(list) {
    return list.map(function (o) {
      return h("tr", { key: o.order_id || Math.random() },
        h("td", { className: "botsy-mono" }, text(o.order_id)),
        h("td", null, text(o.kind)),
        h("td", null, text(o.summary)),
        h("td", null, text(o.created_by)),
        h("td", null, text(o.status) + (o.needs_approval ? " (waits for you)" : "")),
        h("td", null, when(o.created_at)));
    });
  }

  function OrdersCard(props) {
    var o = props.o;
    if (!o.ok) return section("Recent orders and fixes", refused(o));
    var head = ["Order", "Kind", "What", "Who", "Status", "When"];
    return section("Recent orders and fixes (14 days)", h("div", null,
      h("h4", { className: "botsy-sub" }, "Orders"), table(head, orderRows(o.orders)),
      h("h4", { className: "botsy-sub" }, "Fixes"), table(head, orderRows(o.fixes))));
  }

  function KdpCard(props) {
    var k = props.k;
    if (!k.ok) return section("KDP slots", refused(k));
    var kits = Object.keys(k.kits_planned || {}).map(function (w) { return w + ": " + k.kits_planned[w]; }).join(", ");
    return section("KDP slots", h("ul", { className: "botsy-list" },
      h("li", null, "Slots left: " + text(k.slots_left)),
      h("li", null, "KDP kits in the plans: " + (kits || "-")),
      h("li", null, "Room for more work: " + (k.room ? "yes" : "no" + (k.room_reasons.length ? " (" + k.room_reasons.join(", ") + ")" : ""))),
      h("li", null, "Review queue " + text(k.review_queue) + ", drafts waiting " + text(k.waiting_drafts))));
  }

  function LogCard(props) {
    var l = props.l;
    if (!l.ok) return section("Daily log", refused(l));
    return section("Daily log (" + text(l.date) + ")", h("div", null,
      h("pre", { className: "botsy-log" }, l.text),
      l.cut ? h("p", { className: "botsy-muted" }, "Cut short.") : null));
  }

  // ------------------------------------------------------------------ the two actions

  function Actions(props) {
    var capState = useState("");
    var cap = capState[0], setCap = capState[1];
    var msgState = useState(null);
    var msg = msgState[0], setMsg = msgState[1];
    var busyState = useState(false);
    var busy = busyState[0], setBusy = busyState[1];

    function report(r) {
      if (r && r.ok) {
        setMsg({ ok: true, text: "Done: order " + text(r.order_id) + " (" + text(r.status) + "), recorded as from Hermes." });
      } else {
        var why = r ? (r.explain && r.explain.length ? (Array.isArray(r.explain) ? r.explain.join(" ") : r.explain) : (r.reasons || [r.error]).join(", ")) : "";
        setMsg({ ok: false, text: "Refused: " + why });
      }
      setBusy(false);
      props.reload();
    }

    function fail(e) {
      setMsg({ ok: false, text: "Failed: " + (e && e.message ? e.message : "no answer") });
      setBusy(false);
    }

    function pause() {
      if (!window.confirm("Pause Botsy's paid work? It is recorded as a Hermes pause, so you or Hermes can lift it.")) return;
      setBusy(true);
      post(ROUTES.pause, { reason_code: "user_request" }).then(report).catch(fail);
    }

    function lower() {
      var v = Number(cap);
      if (!isFinite(v) || v <= 0) { setMsg({ ok: false, text: "Enter the new, lower cap in US dollars." }); return; }
      if (typeof props.cap === "number" && v >= props.cap) { setMsg({ ok: false, text: "The new cap must be lower than " + money(props.cap) + "." }); return; }
      if (!window.confirm("Lower this month's cap to " + money(v) + "?")) return;
      setBusy(true);
      post(ROUTES.lower_cap, { cap_usd: Math.round(v * 100) / 100 }).then(report).catch(fail);
    }

    return section("Actions", h("div", null,
      h("div", { className: "botsy-actions" },
        h(Button, { onClick: pause, disabled: busy || props.state === "paused" }, "Pause"),
        h("label", { className: "botsy-cap" }, "New cap $ ",
          h("input", { type: "number", min: 1, step: "0.01", value: cap, className: "botsy-input",
            onChange: function (e) { setCap(e.target.value); } })),
        h(Button, { variant: "outline", onClick: lower, disabled: busy }, "Lower cap")),
      msg ? h("p", { className: msg.ok ? "botsy-ok" : "botsy-warn" }, msg.text) : null,
      h("p", { className: "botsy-muted" }, "This tab can only pause and lower the cap. Resume, approvals, plans and raising the cap " +
        "stay in Discord or on the server. A Pause here is recorded as from Hermes (the dashboard has no login of its own); " +
        "for a pause only you can lift, use botsy service control pause on the server.")));
  }

  // ------------------------------------------------------------------ the page

  function BotsyPage() {
    var dataState = useState(null);
    var data = dataState[0], setData = dataState[1];
    var errState = useState(null);
    var err = errState[0], setErr = errState[1];

    var reload = useCallback(function () {
      get(ROUTES.overview).then(function (d) { setData(d); setErr(null); })
        .catch(function (e) { setErr(e && e.message ? e.message : "no answer"); });
    }, []);

    useEffect(function () {
      reload();
      var t = window.setInterval(reload, REFRESH_MS);
      return function () { window.clearInterval(t); };
    }, [reload]);

    if (err) return h("div", { className: "botsy-page" }, h("p", { className: "botsy-warn" }, "The Botsy tab cannot load: " + err));
    if (!data) return h("div", { className: "botsy-page" }, h("p", { className: "botsy-muted" }, "Loading..."));
    if (!data.ok) return h("div", { className: "botsy-page" }, h("p", { className: "botsy-warn" }, text(data.explain || data.error)));

    return h("div", { className: "botsy-page" },
      h("div", { className: "botsy-top" },
        h("h2", null, "Botsy"),
        h(Button, { variant: "outline", size: "sm", onClick: reload }, "Refresh")),
      h("div", { className: "botsy-grid" },
        h(StatusCard, { s: data.status }),
        h(MoneyCard, { m: data.money }),
        h(Actions, { reload: reload, cap: data.money && data.money.cap, state: data.status && data.status.state }),
        h(KdpCard, { k: data.kdp })),
      h(PlanCard, { p: data.plan }),
      h(ReviewCard, { r: data.review }),
      h(OrdersCard, { o: data.orders }),
      h(LogCard, { l: data.daily_log }));
  }

  REG.register("botsy", BotsyPage);
})();
