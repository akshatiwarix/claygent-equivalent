// Page logic: load saved examples, run live briefs (streamed), render a brief.
// All text goes in through textContent, never innerHTML, because brief text comes from a model.
(() => {
  const $ = (id) => document.getElementById(id);
  const form = $('run-form'), runBtn = $('run-btn'), statusBox = $('status'), briefBox = $('brief'), chipsBox = $('examples');

  // ---- tiny DOM helper -------------------------------------------------------------------
  function h(tag, attrs, ...kids) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === 'class') node.className = v;
      else if (k === 'text') node.textContent = v;
      else node.setAttribute(k, v === true ? '' : v);
    }
    for (const kid of kids.flat()) if (kid != null) node.append(kid);
    return node;
  }
  const safeHref = (u) => (/^https?:\/\//i.test(u) ? u : '#');
  const words = (s) => s.trim().split(/\s+/).length;
  const fmtDate = (iso) => new Date(iso + 'T00:00:00Z').toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' });
  const fmtLong = (iso) => new Date(iso + 'T00:00:00Z').toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric', timeZone: 'UTC' });
  const typeLabel = (t) => t.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());
  const tierClass = { 'first-party': 't-first', 'second-party': 't-second', 'third-party': 't-third' };
  const checkIcon = () => {
    const ns = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('width', '16'); svg.setAttribute('height', '16'); svg.setAttribute('viewBox', '0 0 16 16'); svg.setAttribute('aria-hidden', 'true');
    const p = document.createElementNS(ns, 'path');
    p.setAttribute('d', 'M3 8.5l3.2 3.2L13 4.8'); p.setAttribute('fill', 'none'); p.setAttribute('stroke', 'currentColor');
    p.setAttribute('stroke-width', '2'); p.setAttribute('stroke-linecap', 'round'); p.setAttribute('stroke-linejoin', 'round');
    svg.append(p);
    return svg;
  };
  const tierChip = (tier) => h('span', { class: 'chipt ' + (tierClass[tier] || 't-third'), text: tier });

  // ---- brief sections --------------------------------------------------------------------
  function expandable(items, shown, noun, build) {
    const box = h('div');
    const rest = items.length - shown;
    items.forEach((it, i) => { const n = build(it, i); if (i >= shown) n.hidden = true; box.append(n); });
    if (rest > 0) {
      const btn = h('button', { class: 'more', type: 'button', text: `Show ${rest} more ${noun}${rest > 1 ? 's' : ''}` });
      btn.addEventListener('click', () => { [...box.children].forEach((c) => (c.hidden = false)); btn.remove(); });
      box.append(btn);
    }
    return box;
  }

  function triggerRow(t) {
    return h('article', { class: 'trigger' },
      h('div', { class: 'date', text: fmtDate(t.date) }),
      h('div', { class: 'body' },
        h('div', { class: 'meta' }, tierChip(t.tier), h('span', { text: typeLabel(t.type) })),
        h('p', { class: 'sum', text: t.summary }),
        h('p', { class: 'quote' }, h('mark', { text: t.evidence_quote })),
        h('p', { class: 'src' },
          h('span', { class: 'ok' }, checkIcon(), 'Quote found on the page'),
          h('a', { href: safeHref(t.source_url), target: '_blank', rel: 'noopener noreferrer', text: t.source_url.replace(/^https?:\/\/(www\.)?/, '') }))));
  }

  function notesList(notes) {
    if (!notes.length) return h('p', { class: 'empty', text: 'Nothing flagged.' });
    const ul = h('ul', { class: 'notes' });
    notes.forEach((n, i) => { const li = h('li', { text: n }); if (i >= 3) li.hidden = true; ul.append(li); });
    const rest = notes.length - 3;
    if (rest <= 0) return ul;
    const btn = h('button', { class: 'more', type: 'button', text: `Show ${rest} more note${rest > 1 ? 's' : ''}` });
    btn.addEventListener('click', () => { [...ul.children].forEach((c) => (c.hidden = false)); btn.remove(); });
    return h('div', {}, ul, btn);
  }

  function openerRow(tr) {
    const btn = h('button', { class: 'copy', type: 'button', text: 'Copy' });
    btn.addEventListener('click', async () => {
      try { await navigator.clipboard.writeText(tr.opener); btn.textContent = 'Copied'; } catch { btn.textContent = 'Press Ctrl+C'; }
      setTimeout(() => (btn.textContent = 'Copy'), 1500);
    });
    return h('div', { class: 'opener' }, h('p', { text: tr.opener }), h('span', { class: 'wc', text: `${words(tr.opener)} words` }), btn);
  }

  // Same email, two ways: a stock email next to the first opener, with the evidence behind it.
  function comparison(b) {
    const tr = b.talk_tracks[0];
    if (!tr) return null;
    const pain = b.pains[tr.pain_index];
    const trig = pain && pain.trigger_index >= 0 ? b.triggers[pain.trigger_index] : null;
    // Highlight the factual part: the first sentence, or for a one-sentence opener the clause before the first comma, semicolon or colon.
    const m = tr.opener.match(/^(.*?[.!?])\s+(.*)$/s) || tr.opener.match(/^(.*?[,;:])\s+(.*)$/s);
    const fact = m ? m[1] : tr.opener, ask = m ? m[2] : '';
    const without = h('div', { class: 'mail without' },
      h('h4', { text: 'Without the brief' }),
      h('div', { class: 'txt' },
        h('p', { text: 'Hi [First name],' }),
        h('p', {}, h('u', { text: 'I hope this finds you well.' }), ' I wanted to reach out because ', h('u', { text: 'teams like yours often struggle with pipeline generation and rep productivity.' }), ' Our platform can help.'),
        h('p', { class: 'ph', text: '[Your one-line pitch]' })),
      h('ul', {}, h('li', { text: 'A stock opener every inbox has seen.' }), h('li', { text: 'Could be sent to any company on earth.' }), h('li', { text: 'Nothing the reader can check or care about.' })));
    const proof = trig
      ? h('div', { class: 'proof' },
          h('h5', { text: 'Why the highlighted line is safe to send' }),
          h('div', { class: 'meta' }, tierChip(trig.tier), h('span', { text: `${typeLabel(trig.type)}, ${fmtLong(trig.date)}` })),
          h('p', { class: 'q', text: `“${trig.evidence_quote}”` }),
          h('p', { class: 'q' }, h('a', { href: safeHref(trig.source_url), target: '_blank', rel: 'noopener noreferrer', text: trig.source_url.replace(/^https?:\/\/(www\.)?/, '') })))
      : h('div', { class: 'proof' }, h('p', { text: 'No verified event sits behind this opener, so check it before sending.' }));
    const withBrief = h('div', { class: 'mail with' },
      h('h4', { text: 'With the brief' }),
      h('div', { class: 'txt' },
        h('p', { text: 'Hi [First name],' }),
        h('p', {}, trig ? h('mark', { text: fact }) : fact, ask ? ' ' + ask : ''),
        h('p', { class: 'ph', text: '[Your one-line pitch]' })),
      proof);
    return h('div', { class: 'compare' }, without, withBrief);
  }

  function legend() {
    return h('aside', { class: 'legend' },
      h('h3', { text: 'How to read this brief' }),
      h('div', { class: 'row' }, tierChip('first-party'), h('p', { text: "The company's own site. Strongest source." })),
      h('div', { class: 'row' }, tierChip('second-party'), h('p', { text: 'Published by the company on a partner platform, like its job board or a press wire.' })),
      h('div', { class: 'row' }, tierChip('third-party'), h('p', { text: 'Anyone else: press, aggregators, data sites. Treat as a lead, not a fact.' })),
      h('div', { class: 'row rule' }, h('mark', { text: 'Highlighted text' }), h('p', { text: 'An exact quote. The tool checks that it appears in what the search returned before keeping the trigger.' })),
      h('div', { class: 'row rule' }, h('p', { text: 'Triggers with a missing quote, a stale date, or a dead link are dropped and listed under what we could not verify.' })));
  }

  function renderBrief(result, kind) {
    const b = result.brief, s = b.snapshot, st = result.stats;
    const angle = [result.persona, result.offer].filter(Boolean).join(' · ');
    const main = h('div', { class: 'main' },
      h('h3', { text: 'Snapshot' }),
      h('dl', { class: 'snap' },
        h('dt', { text: 'Sells' }), h('dd', { text: s.what_they_sell }),
        h('dt', { text: 'Sells to' }), h('dd', { text: s.who_they_sell_to }),
        h('dt', { text: 'Stage' }), h('dd', { text: s.size_and_stage })),
      h('h3', { text: 'Recent triggers' }),
      h('p', { class: 'note', text: 'Last 90 days. Best-sourced first.' }),
      b.triggers.length ? expandable(b.triggers, 4, 'trigger', triggerRow) : h('p', { class: 'empty', text: 'No verified triggers found. Treat this brief with caution.' }),
      h('h3', { text: 'Likely pains' }),
      h('ol', { class: 'pains' }, b.pains.map((p) => h('li', {}, h('strong', { text: p.pain.replace(/\.$/, '') + '. ' }), h('span', { class: 'why', text: p.reasoning })))),
      h('h3', { text: 'Talk tracks' }),
      h('p', { class: 'note', text: 'One opener per pain. Each is under 25 words.' }),
      h('div', { class: 'openers' }, b.talk_tracks.map(openerRow)),
      h('h3', { text: 'Same email, two ways' }),
      h('p', { class: 'note', text: 'The first opener in a full email, next to the email most reps send without research.' }),
      comparison(b),
      h('h3', { text: 'What we could not verify' }),
      h('p', { class: 'note', text: "So you don't repeat a shaky claim to the prospect." }),
      notesList(b.confidence_notes));
    briefBox.replaceChildren(
      h('div', { class: 'brief-head' },
        h('h2', {}, s.company_name + ' ', h('small', { text: `${result.domain}, ${fmtLong(result.generated)}` })),
        h('p', { class: 'stats', text: `${kind === 'example' ? 'Saved example. ' : 'Live run. '}${st.searches} searches, ${st.tokens.toLocaleString('en-US')} tokens, $${st.cost_usd.toFixed(3)}, ${st.seconds}s` })),
      angle ? h('p', { class: 'angle', text: `Angled for ${angle}` }) : null,
      h('div', { class: 'cols' }, main, legend()));
    briefBox.hidden = false;
  }

  // ---- status ----------------------------------------------------------------------------
  const STAGES = [['research', 'Research'], ['write', 'Write'], ['check', 'Check']];
  let timer = null;
  function showProgress(stage, info, startedAt) {
    const idx = STAGES.findIndex(([k]) => k === stage);
    const detail = {
      research: 'Searching the web for what changed in the last 90 days.',
      write: `Found ${info.sources ?? 0} sources in ${info.searches ?? 0} searches. Writing the brief.`,
      check: 'Checking every quote, date and link.',
    }[stage];
    const secs = Math.round((Date.now() - startedAt) / 1000);
    statusBox.className = 'status';
    statusBox.replaceChildren(
      h('ol', { class: 'stepper' }, STAGES.map(([, label], i) => h('li', { class: i < idx ? 'done' : i === idx ? 'now' : '', text: label }))),
      h('p', { class: 'msg', text: detail }),
      h('p', { class: 'sub', text: `${secs}s so far. Usually about a minute.` }));
    statusBox.hidden = false;
  }
  function showError(message) {
    statusBox.className = 'status error';
    statusBox.replaceChildren(h('p', { class: 'msg', text: message }));
    statusBox.hidden = false;
  }
  const clearStatus = () => { statusBox.hidden = true; statusBox.replaceChildren(); };

  // ---- examples --------------------------------------------------------------------------
  async function openExample(domain) {
    clearStatus();
    [...chipsBox.children].forEach((c) => c.setAttribute('aria-pressed', String(c.dataset.domain === domain)));
    try {
      const res = await fetch('/api/examples/' + encodeURIComponent(domain));
      if (!res.ok) throw new Error();
      renderBrief(await res.json(), 'example');
    } catch { showError('Could not load that example. Try another one.'); }
  }

  async function loadExamples() {
    try {
      const rows = await (await fetch('/api/examples')).json();
      chipsBox.replaceChildren(...rows.map((r) => {
        const chip = h('button', { class: 'chip', type: 'button', 'aria-pressed': 'false', 'data-domain': r.domain, text: r.domain });
        chip.addEventListener('click', () => openExample(r.domain));
        return chip;
      }));
      if (rows.length) openExample(rows.find((r) => r.domain === 'notion.so')?.domain || rows[0].domain);
    } catch { chipsBox.replaceChildren(h('span', { class: 'sub', text: 'Saved examples are unavailable right now.' })); }
  }

  // ---- live run --------------------------------------------------------------------------
  async function runLive(domain, persona, offer) {
    runBtn.disabled = true;
    [...chipsBox.children].forEach((c) => c.setAttribute('aria-pressed', 'false'));
    const startedAt = Date.now();
    let stage = 'research', info = {};
    showProgress(stage, info, startedAt);
    timer = setInterval(() => showProgress(stage, info, startedAt), 1000);
    try {
      const res = await fetch('/api/brief', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ domain, persona, offer }) });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.error || (typeof data.detail === 'string' ? data.detail : 'Enter a company domain like notion.so'));
      }
      const reader = res.body.getReader(), dec = new TextDecoder();
      let buf = '', finished = false;
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let nl;
        while ((nl = buf.indexOf('\n')) >= 0) {
          const line = buf.slice(0, nl).trim(); buf = buf.slice(nl + 1);
          if (!line) continue;
          const ev = JSON.parse(line);
          if (ev.type === 'stage') { stage = ev.stage; info = ev; showProgress(stage, info, startedAt); }
          else if (ev.type === 'error') throw new Error(ev.message);
          else if (ev.type === 'result') { finished = true; clearStatus(); renderBrief(ev, 'live'); briefBox.scrollIntoView({ block: 'start' }); }
        }
      }
      if (!finished) throw new Error('The run ended early. Try again, or open a saved example.');
    } catch (e) {
      showError(e.message || 'Something went wrong. Try a saved example.');
    } finally {
      clearInterval(timer); runBtn.disabled = false;
    }
  }

  form.addEventListener('submit', (e) => {
    e.preventDefault();
    const domain = $('domain').value.trim();
    if (!domain) { showError('Enter a company domain like notion.so'); $('domain').focus(); return; }
    runLive(domain, $('persona').value.trim() || null, $('offer').value.trim() || null);
  });

  loadExamples();
})();
