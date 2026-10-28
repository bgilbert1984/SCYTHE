/* app.js — Player Hub UI. Runs in the browser; engine and case come from the bundle (window.__game). */
(function () {
  'use strict';
  const { createGame, replay, createStore, caseData, COSTS } = window.__game;

  let storage = null;
  try { storage = window.localStorage; } catch (e) { storage = null; }
  const store = createStore(storage);

  const root = document.getElementById('app');
  const state = {
    screen: 'home', game: null, seed: 0, selected: null, message: null, focus: null, commitOpen: false,
    notice: null,
  };

  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
      else if (k === 'disabled' || k === 'checked' || k === 'selected') el[k] = Boolean(v);
      else el.setAttribute(k, v === true ? '' : String(v));
    }
    for (const kid of kids.flat()) {
      if (kid === null || kid === undefined || kid === false) continue;
      el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
    }
    return el;
  }

  const pct = (x) => `${Math.round(x * 100)}%`;
  const label = (list, id, field) => (list.find((x) => x.id === id) || {})[field] || id;

  function persist() {
    store.saveInProgress({ case_id: caseData.case_id, seed: state.seed, actions: state.game.actionLog });
  }

  function act(action, focusKey) {
    const r = state.game.dispatch(action);
    state.focus = focusKey || null;
    if (!r.ok) {
      state.message = { kind: 'err', text: r.error.message };
    } else {
      state.message = { kind: 'ok', text: describe(action, r.result) };
      if (state.game.finished) finish(r.result);
      else persist();
    }
    render();
    return r;
  }

  function describe(action, result) {
    switch (action.type) {
      case 'inspect': return `Inspected ${result.id}.`;
      case 'compare': return `${result.a} and ${result.b}: ${result.agreement === 'agree' ? 'they can agree' : 'they conflict'}${result.shared_upstream ? '; they share an upstream source, so they are not independent' : '; independent sources'}.`;
      case 'corroborate': return corroborationText(result.kind, result.note);
      case 'reevaluate': return result.note;
      case 'commit': return 'Finding committed.';
      default: return 'Done.';
    }
  }

  function corroborationText(kind, note) {
    if (kind === 'result') return `Corroboration: ${note}`;
    if (kind === 'no_match') return `Nothing found: ${note}`;
    return `Could not tell: ${note} This says nothing either way.`;
  }

  function finish(outcome) {
    store.recordResult({
      case_id: caseData.case_id,
      seed: state.seed,
      hypothesis: outcome.committed_hypothesis,
      correct: outcome.is_correct,
      outcome: outcome.score.outcome_text,
      score: outcome.score.total,
      stated_confidence: outcome.stated_confidence,
      reference_confidence: outcome.reference_posterior[outcome.committed_hypothesis],
      finished_at: new Date().toISOString(),
    });
    store.clearInProgress();
    state.screen = 'debrief';
  }

  function newInvestigation() {
    if (store.hasInProgress() && !window.confirm('Starting a new investigation discards your saved one. Continue?')) return;
    state.seed = Math.floor(Math.random() * 2147483647);
    state.game = createGame(caseData, { seed: state.seed });
    state.selected = null; state.message = null; state.commitOpen = false; state.notice = null;
    persist();
    state.screen = 'play';
    state.focus = 'briefing';
    render();
  }

  function continueInvestigation() {
    const loaded = store.loadInProgress(caseData.case_id);
    if (loaded.error || !loaded.save) {
      state.notice = { text: `${loaded.error || 'Nothing to continue.'} It cannot be restored.`, discard: true };
      return render();
    }
    const { game, error } = replay(caseData, loaded.save.seed, loaded.save.actions);
    if (error) {
      state.notice = { text: `The saved investigation could not be restored (${error.code}).`, discard: true };
      return render();
    }
    state.seed = loaded.save.seed; state.game = game;
    state.selected = null; state.message = { kind: 'ok', text: 'Investigation restored.' };
    state.commitOpen = false; state.notice = null;
    state.screen = 'play'; state.focus = 'briefing';
    render();
  }

  function go(screen) { state.screen = screen; state.notice = null; state.focus = null; render(); }

  /* ---------- screens ---------- */

  function home() {
    const resumable = store.hasInProgress();
    return h('main', { class: 'home' },
      h('h1', null, caseData.title),
      h('p', { class: 'muted' }, 'An intelligence-analysis game. Weigh the evidence, test your ideas, and commit to a finding.'),
      !store.persistent && h('div', { class: 'banner', role: 'status' }, 'Progress cannot be saved in this browser session. Continue will not be available after you close the page.'),
      state.notice && h('div', { class: 'banner', role: 'alert' }, state.notice.text, ' ',
        state.notice.discard && h('button', { onclick: () => { store.clearInProgress(); state.notice = null; render(); } }, 'Discard saved investigation')),
      h('nav', { class: 'menu', 'aria-label': 'Main menu' },
        h('button', { class: 'primary', 'data-key': 'new', onclick: newInvestigation }, 'New Investigation'),
        h('button', { 'data-key': 'continue', disabled: !resumable, onclick: continueInvestigation }, 'Continue Investigation'),
        h('button', { 'data-key': 'career', onclick: () => go('career') }, 'Career')),
      h('p', { class: 'muted' }, 'Everything in this game is fictional. It runs entirely on this device.'));
  }

  function careerScreen() {
    const rows = store.career().slice().reverse();
    return h('main', null,
      h('div', { class: 'topbar' }, h('h1', null, 'Career'), h('button', { 'data-key': 'back', onclick: () => go('home') }, 'Back')),
      h('div', { class: 'panel' },
        rows.length === 0
          ? h('p', { class: 'muted' }, 'No completed investigations yet.')
          : h('table', null,
            h('thead', null, h('tr', null, ['Finished', 'Committed', 'Outcome', 'Score', 'Your confidence', 'Reference'].map((t) => h('th', { scope: 'col' }, t)))),
            h('tbody', null, rows.map((r) => h('tr', null,
              h('td', null, r.finished_at.slice(0, 10)),
              h('td', null, r.hypothesis),
              h('td', null, r.outcome || (r.correct ? 'Correct' : 'Incorrect')),
              h('td', null, r.score === undefined ? '-' : r.score),
              h('td', null, pct(r.stated_confidence)),
              h('td', null, pct(r.reference_confidence))))))));
  }

  function budgetMeter(v) {
    const pips = [];
    for (let i = 0; i < v.budget; i++) pips.push(h('span', { class: `pip${i < v.budget_remaining ? ' on' : ''}`, 'aria-hidden': 'true' }));
    return h('div', { class: 'meter', role: 'img', 'aria-label': `${v.budget_remaining} of ${v.budget} action points left` }, pips,
      h('span', { class: 'muted', style: 'margin-left:8px' }, `${v.budget_remaining}/${v.budget} action points`));
  }

  function hypothesisPanel(v) {
    return h('section', { class: 'panel', 'aria-labelledby': 'h-hyp' },
      h('h2', { id: 'h-hyp' }, 'Hypotheses'),
      h('ul', { class: 'plain' }, v.hypotheses.map((hy) => h('li', { class: `hyp${hy.rejected ? ' rejected' : ''}`, style: 'margin-bottom:12px' },
        h('p', null, h('strong', null, hy.id), hy.rejected ? ' (rejected). ' : '. ', hy.claim),
        !hy.rejected && h('div', { class: 'row' },
          h('label', { for: `conf-${hy.id}` }, 'Confidence ', h('span', { id: `confv-${hy.id}` }, hy.confidence === null ? 'not set' : pct(hy.confidence))),
          h('input', {
            id: `conf-${hy.id}`, type: 'range', min: 0, max: 100, step: 5, value: hy.confidence === null ? 0 : Math.round(hy.confidence * 100),
            oninput: (e) => { document.getElementById(`confv-${hy.id}`).textContent = `${e.target.value}%`; },
            onchange: (e) => act({ type: 'set_confidence', hypothesis: hy.id, value: Number(e.target.value) / 100 }, `conf-${hy.id}`),
          })),
        h('div', { class: 'row' },
          hy.rejected
            ? h('button', { 'data-key': `restore-${hy.id}`, onclick: () => act({ type: 'restore', hypothesis: hy.id }, `restore-${hy.id}`) }, `Restore (${COSTS.restore})`)
            : h('button', { 'data-key': `reject-${hy.id}`, onclick: () => act({ type: 'reject', hypothesis: hy.id }, `reject-${hy.id}`) }, 'Reject'))))),
      h('p', { class: 'muted' }, 'Conditions known: ', v.conditions_known.map((c) => c.label).join('; ')));
  }

  function evidenceList(v) {
    const types = [['sdr', 'Radio observations'], ['log', 'Logs'], ['device', 'Device records'], ['witness', 'Witness reports']];
    return h('section', { class: 'panel', 'aria-labelledby': 'h-ev' },
      h('h2', { id: 'h-ev' }, 'Evidence'),
      types.map(([t, name]) => {
        const items = v.evidence.filter((e) => e.type === t);
        return items.length && h('div', null, h('h3', null, name),
          h('ul', { class: 'plain' }, items.map((e) => h('li', null,
            h('button', {
              class: `ev${state.selected === e.id ? ' sel' : ''}`, 'data-key': `ev-${e.id}`, 'aria-pressed': String(state.selected === e.id),
              onclick: () => { state.selected = e.id; state.focus = `ev-${e.id}`; render(); },
            }, h('span', { class: 'tag' }, e.id), e.title, e.inspected && h('span', { class: 'tag' }, 'read'))))));
      }));
  }

  function detail(v) {
    const e = v.evidence.find((x) => x.id === state.selected);
    if (!e) return h('section', { class: 'panel' }, h('h2', null, 'Case file'), h('p', { class: 'muted' }, 'Select an item to see it here.'));
    if (!e.inspected) {
      return h('section', { class: 'panel' },
        h('h2', null, `${e.id}: ${e.title}`),
        h('p', { class: 'muted' }, 'Not yet read.'),
        h('button', { class: 'primary', 'data-key': 'inspect', onclick: () => act({ type: 'inspect', evidence: e.id }, 'inspect') }, `Inspect (${COSTS.inspect})`));
    }
    const others = v.evidence.filter((x) => x.inspected && x.id !== e.id);
    const live = v.hypotheses.filter((x) => !x.rejected);
    const links = v.links[e.id] || {};
    let compareWith = others[0] && others[0].id;
    let variant = v.conditions_known[0].variant_id;
    let linkHyp = live[0] && live[0].id;
    let linkRel = 'supports';
    return h('section', { class: 'panel', 'aria-labelledby': 'h-det' },
      h('h2', { id: 'h-det' }, `${e.id}: ${e.title}`),
      h('p', null, e.reading),
      h('p', { class: 'muted' }, `Limits: ${e.limits}`),
      h('p', { class: 'muted' }, `Source: ${e.source.label} (${e.source.reliability}). ${e.source.limits}`),
      h('p', { class: 'muted' }, `Conditions: ${e.conditions}. ${e.time}.`),
      e.notes.map((n) => h('div', { class: 'note' },
        n.kind === 'compare' ? `Compared with ${n.with}: ${n.agreement}${n.shared_upstream ? ', shares an upstream source' : ''}.`
          : n.kind === 'reevaluation' ? `Re-evaluated under ${n.variant}: ${n.note}`
            : corroborationText(n.result_kind, n.note))),
      h('div', { class: 'row' },
        h('label', { for: 'cmp' }, 'Compare with'),
        h('select', { id: 'cmp', disabled: others.length === 0, onchange: (x) => { compareWith = x.target.value; } },
          others.map((o) => h('option', { value: o.id }, `${o.id}: ${o.title}`))),
        h('button', { 'data-key': 'compare', disabled: others.length === 0, onclick: () => act({ type: 'compare', a: e.id, b: compareWith }, 'compare') }, `Compare (${COSTS.compare})`)),
      h('div', { class: 'row' },
        h('button', { 'data-key': 'corroborate', onclick: () => act({ type: 'corroborate', evidence: e.id }, 'corroborate') }, `Ask for corroboration (${COSTS.corroborate})`)),
      h('div', { class: 'row' },
        h('label', { for: 'var' }, 'Re-evaluate under'),
        h('select', { id: 'var', onchange: (x) => { variant = x.target.value; } },
          v.conditions_known.map((c) => h('option', { value: c.variant_id }, c.label))),
        h('button', { 'data-key': 'reeval', onclick: () => act({ type: 'reevaluate', evidence: e.id, variant }, 'reeval') }, `Re-evaluate (${COSTS.reevaluate})`)),
      h('div', { class: 'row' },
        h('label', { for: 'lh' }, 'This item'),
        h('select', { id: 'lr', 'aria-label': 'relation', onchange: (x) => { linkRel = x.target.value; } },
          ['supports', 'contradicts', 'neutral'].map((r) => h('option', { value: r }, r))),
        h('select', { id: 'lh', onchange: (x) => { linkHyp = x.target.value; } }, live.map((x) => h('option', { value: x.id }, x.id))),
        h('button', { 'data-key': 'link', onclick: () => act({ type: 'link', evidence: e.id, hypothesis: linkHyp, relation: linkRel }, 'link') }, 'Link')),
      Object.keys(links).length > 0 && h('ul', { class: 'plain' }, Object.entries(links).map(([hy, rel]) => h('li', { class: 'row' },
        `${rel} ${hy}`, h('button', { 'data-key': `unlink-${hy}`, onclick: () => act({ type: 'unlink', evidence: e.id, hypothesis: hy }, `unlink-${hy}`) }, 'Unlink')))));
  }

  function commitPanel(v) {
    const live = v.hypotheses.filter((x) => !x.rejected);
    const read = v.evidence.filter((x) => x.inspected);
    let hyp = live[0].id;
    let conf = 50;
    const cited = new Set();
    return h('section', { class: 'panel', 'aria-labelledby': 'h-com' },
      h('h2', { id: 'h-com' }, 'Commit a finding'),
      !state.commitOpen
        ? h('button', { 'data-key': 'commit-open', onclick: () => { state.commitOpen = true; state.focus = 'commit-hyp'; render(); } }, 'Prepare finding')
        : h('div', null,
          h('p', { class: 'muted' }, 'This ends the investigation. You cannot change it afterwards.'),
          h('div', { class: 'row' }, h('label', { for: 'commit-hyp' }, 'Hypothesis'),
            h('select', { id: 'commit-hyp', 'data-key': 'commit-hyp', onchange: (x) => { hyp = x.target.value; } }, live.map((x) => h('option', { value: x.id }, `${x.id}: ${x.claim}`)))),
          h('div', { class: 'row' }, h('label', { for: 'commit-conf' }, 'Confidence ', h('span', { id: 'commit-confv' }, '50%')),
            h('input', { id: 'commit-conf', type: 'range', min: 0, max: 100, step: 5, value: 50, oninput: (x) => { conf = Number(x.target.value); document.getElementById('commit-confv').textContent = `${conf}%`; } })),
          h('fieldset', null, h('legend', null, 'Cite the evidence behind this finding'),
            read.length === 0 ? h('p', { class: 'muted' }, 'Read some evidence first.')
              : read.map((x) => h('label', { style: 'display:block' },
                h('input', { type: 'checkbox', value: x.id, onchange: (c) => { c.target.checked ? cited.add(x.id) : cited.delete(x.id); } }), ` ${x.id}: ${x.title}`))),
          h('div', { class: 'row' },
            h('button', { class: 'primary', 'data-key': 'commit', onclick: () => act({ type: 'commit', hypothesis: hyp, confidence: conf / 100, citations: [...cited] }, 'commit') }, 'Commit finding'),
            h('button', { onclick: () => { state.commitOpen = false; state.focus = 'commit-open'; render(); } }, 'Cancel'))));
  }

  function play() {
    const v = state.game.view();
    return h('main', null,
      h('div', { class: 'topbar' }, h('h1', null, v.title), budgetMeter(v),
        h('button', { 'data-key': 'menu', onclick: () => go('home') }, 'Save and exit to menu')),
      h('p', { id: 'briefing', tabindex: '-1', 'data-key': 'briefing' }, v.briefing),
      h('div', { class: `msg ${state.message ? state.message.kind : ''}`, role: 'status', 'aria-live': 'polite' }, state.message ? state.message.text : ''),
      h('div', { class: 'grid' }, hypothesisPanel(v), evidenceList(v), h('div', null, detail(v), commitPanel(v))));
  }

  const CITATION_TEXT = {
    supports: 'supports your finding',
    against: 'argues against your finding',
    not_diagnostic: 'does not help tell the explanations apart',
    double_counted: 'repeats evidence you already cited',
  };

  function debrief() {
    const o = state.game.view().outcome;
    const sc = o.score;
    const c = caseData;
    const byId = Object.fromEntries(c.evidence.map((e) => [e.id, e]));
    const ref = o.reference_posterior;
    const COMPONENTS = [['correctness', 'Correct conclusion'], ['calibration', 'Calibration'], ['citation', 'Quality of citations'], ['efficiency', 'Efficiency']];
    return h('main', null,
      h('div', { class: 'topbar' }, h('h1', null, 'Debrief'), h('button', { 'data-key': 'home', onclick: () => go('home') }, 'Back to menu')),
      h('section', { class: 'panel', 'aria-labelledby': 'h-out' },
        h('h2', { id: 'h-out', tabindex: '-1', 'data-key': 'debrief' }, sc.outcome_text),
        h('p', null, `You committed ${o.committed_hypothesis} at ${pct(o.stated_confidence)}. ${sc.calibration_text}: the evidence you gathered supported it at about ${pct(ref[o.committed_hypothesis])}.`),
        h('p', null, h('strong', null, `Score ${sc.total} / 100`)),
        h('details', null, h('summary', null, 'Score breakdown'),
          h('table', null,
            h('thead', null, h('tr', null, ['Part', 'Points', 'Weight'].map((t) => h('th', { scope: 'col' }, t)))),
            h('tbody', null, COMPONENTS.map(([k, name]) => h('tr', null, h('td', null, name), h('td', null, `${sc.components[k]} / 100`), h('td', null, pct(sc.weights[k])))))),
          h('p', { class: 'muted' }, `Brier score for your stated confidence: ${sc.brier.toFixed(2)} (lower is better).`))),
      h('section', { class: 'panel' }, h('h2', null, 'What the evidence supported'),
        c.hypotheses.map((hy) => {
          const mine = o.player_confidence[hy.id];
          return h('div', { style: 'margin-bottom:8px' },
            h('p', { class: 'muted' }, `${hy.id}: evidence supported ${pct(ref[hy.id])}${mine === null || mine === undefined ? '' : `; you rated it ${pct(mine)}`}`),
            h('div', { class: 'bar' }, h('span', { style: `width:${Math.round(ref[hy.id] * 100)}%` })));
        })),
      h('section', { class: 'panel' }, h('h2', null, 'Your citations'),
        h('ul', null, sc.citations.map((x) => h('li', null, `${x.id} (${byId[x.id].title}) ${CITATION_TEXT[x.status]}.`)))),
      h('section', { class: 'panel' }, h('h2', null, 'What actually happened'), h('p', null, o.debrief.truth)),
      h('section', { class: 'panel' }, h('h2', null, 'Misleading clues'),
        h('ul', null, o.debrief.misleading_clues.map((m) => h('li', null, `${m.evidence.join(', ')}: ${m.why}`)))),
      h('section', { class: 'panel' }, h('h2', null, 'The key moment'),
        h('p', null, o.debrief.pivotal_explanation),
        h('p', { class: 'muted' }, `Key evidence: ${o.debrief.key_evidence.map((id) => `${id} (${byId[id].title})`).join('; ')}`)));
  }

  function render() {
    const screens = { home, career: careerScreen, play, debrief };
    root.replaceChildren(screens[state.screen]());
    if (state.focus) {
      const el = root.querySelector(`[data-key="${state.focus}"]`);
      if (el) el.focus();
    }
    window.scrollTo && state.focus === 'briefing' && window.scrollTo(0, 0);
  }

  window.__hub = { state, render };
  render();
})();
