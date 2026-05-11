/* ─────────────────────────────────────────────────────
   INCIDENT_OPS // shared.js  —  API + UI helpers
───────────────────────────────────────────────────── */

function getApiBase() {
  const saved = localStorage.getItem('ops_api_base');
  if (saved) return saved;
  if (location.protocol === 'http:' || location.protocol === 'https:') return '/api';
  return 'http://localhost:8000';
}
let API_BASE = getApiBase();

/* ── API LAYER ── */
class ApiError extends Error {
  constructor(status, msg) { super(msg); this.status = status; }
}

async function apiFetch(path, opts = {}) {
  try {
    const res = await fetch(API_BASE + path, {
      headers: { 'Content-Type': 'application/json', ...opts.headers }, ...opts,
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new ApiError(res.status, body.detail || res.statusText);
    }
    return await res.json();
  } catch (e) {
    if (e instanceof ApiError) throw e;
    throw new ApiError(0, `Cannot reach backend · ${e.message}`);
  }
}

function apiStream(path, onEvent, onError) {
  const es = new EventSource(API_BASE + path);
  const handle = ev => { try { onEvent(JSON.parse(ev.data)); } catch(err) { onError?.(err); } };
  es.onmessage = handle;
  es.addEventListener('ticket', handle);
  es.onerror = () => onError?.(new ApiError(0, 'Stream disconnected'));
  return es;
}

/* ── LOADING / ERROR ── */
function showError(id, message, status = '') {
  const el = document.getElementById(id);
  if (!el) return;
  el.innerHTML = `<div class="api-error" style="display:flex;align-items:flex-start;gap:12px;padding:16px 20px;
    border:1px solid #fca5a5;background:#fff5f5;border-radius:8px;margin-bottom:12px">
    <span style="color:var(--critical);font-size:18px;flex-shrink:0">⚠</span>
    <div style="font-size:12px">
      <div style="font-weight:700;color:var(--critical);font-family:var(--mono);letter-spacing:.08em">BACKEND ERROR${status?' '+status:''}</div>
      <div style="color:var(--text2);margin-top:4px">${message}</div>
      <div style="color:var(--text3);font-family:var(--mono);font-size:10px;margin-top:6px">Base URL: ${API_BASE}</div>
    </div>
  </div>`;
}

function showSkeleton(id, rows = 6) {
  const el = document.getElementById(id);
  if (!el) return;
  el.innerHTML = Array.from({length:rows}, (_,i) => `
    <div style="padding:10px 16px;border-bottom:1px solid var(--border);display:flex;gap:14px;align-items:center">
      ${[110,300,90,90,70].map(w=>`<div style="height:8px;width:${w}px;background:var(--bg3);border-radius:4px;
        animation:skeleton-pulse 1.4s ${(i*.08).toFixed(2)}s infinite"></div>`).join('')}
    </div>`).join('');
}

function clearContainer(id) { const el=document.getElementById(id); if(el) el.innerHTML=''; }

/* ── BACKEND SHAPE NORMALIZATION ── */
function html(v) {
  return String(v ?? '').replace(/[&<>"']/g, c => ({
    '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'
  }[c]));
}

function firstDefined(...values) {
  return values.find(v => v !== undefined && v !== null && v !== '');
}

function firstUsefulGroup(...values) {
  const groups = values.filter(v => v !== undefined && v !== null && v !== '');
  return groups.find(v => String(v).toLowerCase() !== 'pending review') || groups[0];
}

function asArray(v) {
  if (Array.isArray(v)) return v;
  if (v === undefined || v === null || v === '') return [];
  return [v];
}

function asNumber(v) {
  if (v === undefined || v === null || v === '') return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function renderResolution(value) {
  const items = asArray(value).filter(Boolean);
  if (!items.length) return '';
  if (items.length === 1) return html(items[0]);
  return `<ol style="margin:0;padding-left:18px">${items.map(v => `<li>${html(v)}</li>`).join('')}</ol>`;
}

function plainResolutionItems(value) {
  return asArray(value)
    .flatMap(item => String(item || '').split(/\n+/))
    .map(item => item.replace(/^\s*\d+[\).\s-]*/, '').trim())
    .filter(Boolean);
}

function renderRouteExplanation(value) {
  if (!value) return '';
  if (!Array.isArray(value)) return html(value);
  return value.map(item => `
    <div style="display:grid;grid-template-columns:120px 1fr 90px;gap:10px;padding:6px 0;border-bottom:1px solid var(--border)">
      <span style="font-family:var(--mono);font-size:10px;color:var(--text3)">${html(item.label || 'signal')}</span>
      <span style="font-size:11px;color:var(--text2)">${html(item.value || '—')}</span>
      <span style="font-family:var(--mono);font-size:10px;color:${item.impact === 'negative' ? 'var(--critical)' : item.impact === 'positive' ? 'var(--ok)' : 'var(--text3)'}">${html(item.impact || 'neutral')}</span>
    </div>`).join('');
}

function normalizeTicketPayload(payload = {}) {
  const p = payload || {};
  if (p.__normalized) return p;
  const ticket = p.ticket || {};
  const routing = p.routing || {};
  const matched = p.matched_ticket || routing.matched_ticket || {};
  const cc = p.confidence_components || routing.confidence_components || {};
  const slaObj = p.sla_risk || routing.sla_risk || {};
  const kgRaw = firstDefined(p.knowledge_gap, routing.knowledge_gap);
  const kgReason = firstDefined(p.knowledge_gap_reason, routing.knowledge_gap_reason);
  const kg = kgRaw && typeof kgRaw !== 'object' && kgReason
    ? { is_gap: Boolean(kgRaw), reason: kgReason }
    : kgRaw;
  const resolver = firstDefined(
    p.resolver_recommendation,
    routing.resolver_recommendation,
    firstDefined(p.resolver_group, routing.resolver_group) ? {
      group: firstDefined(p.resolver_group, routing.resolver_group),
      confidence: firstDefined(p.resolver_confidence, routing.resolver_confidence),
    } : undefined
  );
  const routeExplanation = firstDefined(p.route_explanation, routing.route_explanation);
  const storedResolution = firstDefined(p.resolution, ticket.resolution, ticket.stored_resolution, p.stored_resolution);
  const suggested = firstDefined(p.suggested_resolution, routing.suggested_resolution, storedResolution);
  const suggestedItems = asArray(suggested).filter(Boolean);

  const classification = asNumber(firstDefined(
    cc.classification, cc.classification_confidence,
    p.classification_confidence, routing.classification_confidence
  ));
  const retrieval = asNumber(firstDefined(
    p.retrieval_similarity, routing.retrieval_similarity,
    cc.retrieval_similarity, matched.retrieval_similarity
  ));
  const verifier = asNumber(firstDefined(
    p.verifier_score, routing.verifier_score, cc.verifier_score
  ));
  const privacyRisk = asNumber(firstDefined(
    p.privacy_risk, routing.privacy_risk, cc.privacy_risk
  ));
  const slaRisk = asNumber(firstDefined(
    typeof slaObj === 'object' ? slaObj.score : slaObj,
    p.sla_risk_score, routing.sla_risk_score
  ));

  const resolverText = resolver && typeof resolver === 'object'
    ? `${resolver.group || '—'}${resolver.confidence != null ? ` (${Math.round(resolver.confidence * 100)}%)` : ''}`
    : resolver;
  const gapText = kg && typeof kg === 'object'
    ? (kg.is_gap === false ? 'none' : firstDefined(kg.reason, kg.severity, 'gap detected'))
    : kg;

  return {
    ...ticket,
    ...routing,
    ...p,
    __normalized: true,
    ticket_id: firstDefined(p.ticket_id, ticket.ticket_id, routing.ticket_id, p.id, ticket.id),
    short_description: firstDefined(p.short_description, ticket.short_description, routing.short_description),
    description: firstDefined(p.description, ticket.description, ticket.sanitized_text, p.sanitized_text),
    resolution: storedResolution,
    category: firstDefined(p.category, ticket.category, routing.assigned_category, p.assigned_category),
    assigned_category: firstDefined(p.assigned_category, routing.assigned_category, ticket.category, p.category),
    assignment_group: firstUsefulGroup(p.assignment_group, ticket.assignment_group, routing.assignment_group, p.resolver_group, routing.resolver_group, resolver?.group, matched.assignment_group),
    confidence_score: asNumber(firstDefined(p.confidence_score, routing.confidence_score)),
    confidence_components: {
      classification,
      classification_confidence: classification,
      retrieval_similarity: retrieval,
      verifier_score: verifier,
      privacy_risk: privacyRisk,
    },
    retrieval_similarity: retrieval,
    verifier_score: verifier,
    privacy_risk: privacyRisk,
    redacted_entities_count: firstDefined(p.redacted_entities_count, routing.redacted_entities_count, p.privacy_audit?.length),
    retrieved_ticket_ids: asArray(firstDefined(p.retrieved_ticket_ids, routing.retrieved_ticket_ids)),
    route_path: firstDefined(p.route_path, routing.route_path),
    semantic_cache_hit: Boolean(firstDefined(p.semantic_cache_hit, routing.semantic_cache_hit)),
    matched_ticket_id: firstDefined(p.matched_ticket_id, routing.matched_ticket_id, matched.ticket_id),
    matched_ticket: matched,
    sla_risk: slaRisk,
    sla_risk_level: firstDefined(slaObj?.level, p.sla_risk_level, routing.sla_risk_level),
    knowledge_gap: gapText,
    knowledge_gap_obj: kg,
    resolver_recommendation: resolverText,
    resolver_group: resolver?.group,
    resolver_confidence: resolver?.confidence,
    suggested_resolution: renderResolution(suggested),
    suggested_resolution_items: suggestedItems,
    suggested_resolution_preview: suggestedItems[0] || '',
    matched_resolution: matched.resolution,
    route_explanation: renderRouteExplanation(routeExplanation),
    route_explanation_items: Array.isArray(routeExplanation) ? routeExplanation : [],
    status: firstDefined(p.status, ticket.status, routing.status, p.escalation_required ? 'human_review_required' : undefined),
    created_at: firstDefined(p.created_at, ticket.created_at, routing.created_at),
  };
}

/* ── FORMATTERS ── */
function fmtNum(v, d=2)  { if(v==null) return '—'; return typeof v==='number'?v.toFixed(d):v; }
function fmtDate(val)    {
  if(!val) return '—';
  try { const d=new Date(val); return d.toLocaleDateString('en-GB',{day:'2-digit',month:'short'})+' '+d.toLocaleTimeString('en-GB',{hour:'2-digit',minute:'2-digit'}); }
  catch { return String(val); }
}
function countUp(id, target, suffix='', dur=700) {
  const el=document.getElementById(id); if(!el) return;
  const end=parseFloat(target)||0, t0=performance.now();
  (function frame(now){
    const p=Math.min((now-t0)/dur,1), ep=1-Math.pow(1-p,3), v=end*ep;
    el.textContent=(Number.isInteger(end)?Math.round(v):v.toFixed(2))+suffix;
    if(p<1) requestAnimationFrame(frame);
  })(performance.now());
}

function routePathBadge(path) {
  if(!path) return '<span style="color:var(--text3);font-size:10px">—</span>';
  const C={semantic_cache:'var(--ok)',generative_rag:'var(--accent)',out_of_distribution:'var(--warn)',human_review_required:'var(--critical)',manual_review_requested:'var(--critical)'};
  const B={semantic_cache:'#f0fdf4',generative_rag:'#eff6ff',out_of_distribution:'#fffbeb',human_review_required:'#fff5f5',manual_review_requested:'#fff5f5'};
  const c=C[path]||'var(--text3)';
  const label=String(path).replace(/_/g,' ');
  return `<span class="route-path-badge" title="${html(label)}" style="font-family:var(--mono);font-size:10px;font-weight:600;letter-spacing:.06em;color:${c};border:1px solid ${c};border-radius:3px;padding:2px 7px;background:${B[path]||'var(--bg2)'}">${html(label)}</span>`;
}

function ticketDetailUrl(ticketId) {
  return `ticket.html?id=${encodeURIComponent(ticketId || '')}`;
}

function openTicketDetail(ticketId) {
  if (!ticketId) return;
  window.location.href = ticketDetailUrl(ticketId);
}

const MANUAL_REVIEW_KEY = 'incidentops_manual_reviews';

function getManualReviewQueue() {
  try {
    return JSON.parse(localStorage.getItem(MANUAL_REVIEW_KEY) || '[]');
  } catch {
    return [];
  }
}

function saveManualReviewQueue(items) {
  localStorage.setItem(MANUAL_REVIEW_KEY, JSON.stringify(items || []));
}

function addManualReview(ticket, reason = 'Operator requested human review from ticket detail page.') {
  const t = normalizeTicketPayload(ticket || {});
  const id = t.ticket_id || t.id;
  if (!id) return null;
  const queue = getManualReviewQueue().filter(item => (item.ticket_id || item.id) !== id);
  const item = {
    ...t,
    ticket_id: id,
    status: 'human_review_required',
    route_path: 'manual_review_requested',
    escalation_required: true,
    manual_review_requested: true,
    manual_review_reason: reason,
    created_at: new Date().toISOString(),
  };
  queue.unshift(item);
  saveManualReviewQueue(queue.slice(0, 50));
  return item;
}

function removeManualReview(ticketId) {
  saveManualReviewQueue(getManualReviewQueue().filter(item => (item.ticket_id || item.id) !== ticketId));
}

function statusBadge(s) {
  if(!s) return '<span class="badge badge-pending">—</span>';
  const m={routed:'routed',resolved:'resolved',semantic_cache_resolved:'resolved',human_review_required:'escalated',unrouted:'pending'};
  return `<span class="badge badge-${m[s]||'pending'}">${s.replace(/_/g,' ').toUpperCase()}</span>`;
}

function slaRiskBadge(risk) {
  if(risk==null) return '—';
  const r=parseFloat(risk), c=r>.7?'var(--critical)':r>.4?'var(--warn)':'var(--ok)';
  return `<span style="font-family:var(--mono);font-size:10px;font-weight:600;color:${c}">${Math.round(r*100)}%</span>`;
}

function confBar(conf) {
  if(conf==null) return '<span style="color:var(--text3)">—</span>';
  const pct=Math.round(conf*100), c=conf<.65?'var(--critical)':conf<.85?'var(--warn)':'var(--ok)';
  return `<div class="conf-bar"><div class="conf-bar-track"><div class="conf-bar-fill" style="width:${pct}%;background:${c}"></div></div><span class="conf-val" style="color:${c}">${pct}%</span></div>`;
}

function startAutoRefresh(callback, defaultSeconds = 30) {
  const saved = Number(localStorage.getItem('ops_refresh') || defaultSeconds);
  if (!Number.isFinite(saved) || saved <= 0) return null;
  return setInterval(callback, saved * 1000);
}

/* ══════════════════════════════════════════════════════
   DECISION TRACE  —  vertical LangGraph timeline
══════════════════════════════════════════════════════ */
function buildDecisionTrace(t) {
  t = normalizeTicketPayload(t);
  const pass = 'var(--ok)', gen = 'var(--accent)', weak = 'var(--warn)', esc = 'var(--critical)';
  let stepIndex = 0;

  function step(num, label, color, items, isLast) {
    const delay = (stepIndex++ * 0.06).toFixed(2);
    const pulseClass = color === esc ? 'gate-fail' : color === weak ? 'gate-warn' : '';
    const rows = items.filter(([,v])=>v!=null&&v!=='').map(([k,v])=>
      `<div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid var(--border);font-size:11px">
        <span style="color:var(--text3)">${k}</span>
        <span style="font-family:var(--mono);font-weight:600;color:var(--text)">${v}</span>
      </div>`).join('');
    return `
      <div class="trace-step ${pulseClass}" style="display:flex;gap:12px;opacity:1;animation-delay:${delay}s">
        <div style="display:flex;flex-direction:column;align-items:center;flex-shrink:0">
          <div style="width:28px;height:28px;border-radius:50%;background:${color}20;border:2px solid ${color};
            display:flex;align-items:center;justify-content:center;font-family:var(--mono);font-size:11px;font-weight:700;color:${color}">${num}</div>
          ${!isLast?`<div style="width:2px;flex:1;min-height:20px;background:${color}30;margin:4px 0"></div>`:''}
        </div>
        <div style="flex:1;padding-bottom:${isLast?'0':'16px'}">
          <div style="font-size:12px;font-weight:700;color:var(--text);margin-bottom:8px;padding-bottom:4px;border-bottom:1.5px solid ${color}30">${label}</div>
          ${rows||'<div style="font-size:11px;color:var(--text3);font-style:italic">No data</div>'}
        </div>
      </div>`;
  }

  const privColor   = t.privacy_risk>0.15 ? esc : pass;
  const retColor    = !t.retrieval_similarity ? weak : t.retrieval_similarity>=.7 ? gen : esc;
  const assColor    = t.route_path==='human_review_required' ? esc : t.route_path==='out_of_distribution' ? weak : pass;
  const genColor    = t.semantic_cache_hit ? pass : t.route_path==='generative_rag' ? gen : weak;
  const verColor    = !t.verifier_score ? weak : t.verifier_score>=.7 ? pass : esc;

  return `
    <div style="margin-top:16px">
      <div style="font-size:11px;font-weight:700;color:var(--text);letter-spacing:.08em;text-transform:uppercase;margin-bottom:14px;padding-bottom:8px;border-bottom:1.5px solid var(--border)">Decision Trace</div>
      ${step(1,'Privacy Node',privColor,[
        ['Entities redacted', t.redacted_entities_count??'—'],
        ['Privacy risk',      fmtNum(t.privacy_risk,3)],
        ['Gate',             (t.privacy_risk||0)<=0.15?'✓ Passed':'✕ Escalated'],
      ],false)}
      ${step(2,'Embedding + Retrieval',retColor,[
        ['Matched ticket',    t.matched_ticket_id||'—'],
        ['Retrieved IDs',    (t.retrieved_ticket_ids||[]).length ? t.retrieved_ticket_ids.slice(0,3).join(', ') : '—'],
        ['Retrieval similarity', fmtNum(t.retrieval_similarity,3)],
        ['Branch signal',   !t.retrieval_similarity?'—':t.retrieval_similarity>=.95?'→ Cache':t.retrieval_similarity>=.70?'→ RAG':'→ OOD'],
      ],false)}
      ${step(3,'Assessment',assColor,[
        ['Route path',       t.route_path?.replace(/_/g,' ')||'—'],
        ['Knowledge gap',    t.knowledge_gap||'none'],
        ['Resolver rec.',    t.resolver_recommendation||'—'],
        ['SLA risk',         t.sla_risk!=null?Math.round(t.sla_risk*100)+'%':'—'],
      ],false)}
      ${step(4,'Generation / Cache / Escalation',genColor,[
        ['Cache hit',        t.semantic_cache_hit?'✓ Yes':'No'],
        ['Resolution',       t.suggested_resolution_preview ? t.suggested_resolution_preview.slice(0,80)+'…' : '—'],
        ['Escalation flag',  t.escalation_required?'⚠ Yes':'No'],
      ],false)}
      ${step(5,'Verifier',verColor,[
        ['Verifier score',   fmtNum(t.verifier_score,3)],
        ['Confidence score', t.confidence_score!=null?Math.round(t.confidence_score*100)+'%':'—'],
        ['Gate',            (t.verifier_score||0)>=.7?'✓ Passed':'✕ Below threshold'],
        ['Final status',     t.escalation_required?'Escalated to Human':'Auto-completed'],
      ],true)}
    </div>`;
}

/* ══════════════════════════════════════════════════════
   CONFIDENCE BREAKDOWN  —  4 bars + formula + gates
══════════════════════════════════════════════════════ */
function buildConfidenceBreakdown(t) {
  t = normalizeTicketPayload(t);
  const cc = t.confidence_components || {};
  const clf  = cc.classification ?? cc.classification_confidence ?? t.confidence_score ?? null;
  const ret  = cc.retrieval_similarity ?? t.retrieval_similarity ?? null;
  const ver  = cc.verifier_score ?? t.verifier_score ?? null;
  const priv = t.privacy_risk != null ? (1 - t.privacy_risk) : null;
  const finalConfidence = t.confidence_score;

  function bar(label, val, gate, gateLabel, weight) {
    if(val==null) return '';
    const pct   = Math.round(val*100);
    const fail  = gate != null && val < gate;
    const color = fail ? 'var(--critical)' : val < .8 ? 'var(--warn)' : 'var(--ok)';
    return `
      <div style="margin-bottom:12px">
        <div style="display:flex;justify-content:space-between;margin-bottom:4px">
          <span style="font-size:11px;color:var(--text2)">${label} <span style="color:var(--text3);font-size:10px">×${weight}</span></span>
          <span style="font-family:var(--mono);font-size:12px;font-weight:700;color:${color}">${pct}%</span>
        </div>
        <div style="height:6px;background:var(--bg3);border-radius:999px;overflow:hidden">
          <div style="height:100%;width:${pct}%;background:${color};border-radius:999px;transition:width .4s"></div>
        </div>
        ${fail?`<div style="font-size:10px;color:var(--critical);margin-top:3px">✕ Below gate ${gateLabel}</div>`:''}
      </div>`;
  }

  const weightedSignal = [clf,ret,ver,priv].every(v=>v==null) ? null :
    ((clf||0)*.35 + (ret||0)*.25 + (ver||0)*.30 + (priv||0)*.10);
  const finalColor = finalConfidence == null ? 'var(--text3)' : finalConfidence<.65?'var(--critical)':finalConfidence<.85?'var(--warn)':'var(--ok)';

  return `
    <div style="margin-top:16px">
      <div style="font-size:11px;font-weight:700;color:var(--text);letter-spacing:.08em;text-transform:uppercase;margin-bottom:12px;padding-bottom:8px;border-bottom:1.5px solid var(--border)">Confidence Breakdown</div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px">
        <div style="background:var(--bg2);border:1px solid var(--border);border-radius:6px;padding:10px 12px">
          <div style="font-size:10px;font-weight:600;color:var(--text3);letter-spacing:.08em;text-transform:uppercase;margin-bottom:5px">Final Backend Confidence</div>
          <div style="font-family:var(--mono);font-size:22px;font-weight:800;color:${finalColor}">${finalConfidence!=null?Math.round(finalConfidence*100)+'%':'—'}</div>
        </div>
        <div style="background:var(--bg2);border:1px solid var(--border);border-radius:6px;padding:10px 12px">
          <div style="font-size:10px;font-weight:600;color:var(--text3);letter-spacing:.08em;text-transform:uppercase;margin-bottom:5px">Weighted Signal</div>
          <div style="font-family:var(--mono);font-size:22px;font-weight:800;color:${weightedSignal==null?'var(--text3)':weightedSignal<.65?'var(--critical)':weightedSignal<.85?'var(--warn)':'var(--ok)'}">${weightedSignal!=null?Math.round(weightedSignal*100)+'%':'—'}</div>
        </div>
      </div>
      ${bar('Classification Confidence', clf,  .65, '0.65', '0.35')}
      ${bar('Retrieval Similarity',      ret,  .70, '0.70', '0.25')}
      ${bar('Verifier Score',            ver,  .70, '0.70', '0.30')}
      ${bar('Privacy Safety',            priv, .85, '0.85 (risk≤0.15)', '0.10')}
      <div style="background:var(--bg2);border:1px solid var(--border);border-radius:6px;padding:10px 12px;font-size:11px;margin-top:4px">
        <span style="color:var(--text3)">Formula: </span>
        <span style="font-family:var(--mono);font-size:10px;color:var(--text2)">0.35·clf + 0.25·ret + 0.30·ver + 0.10·privacy</span>
        ${weightedSignal!=null?`<span style="float:right;font-weight:700;font-family:var(--mono);color:${weightedSignal<.65?'var(--critical)':weightedSignal<.85?'var(--warn)':'var(--ok)'}">${Math.round(weightedSignal*100)}%</span>`:''}
      </div>
      <div style="font-size:10px;color:var(--text3);line-height:1.6;margin-top:6px">Final backend confidence is authoritative. The weighted signal is shown for auditability; route gates such as OOD, weak retrieval, verifier failure, or policy escalation can lower the final score.</div>
    </div>`;
}

function detailMetric(label, value) {
  return `
    <div style="background:var(--bg2);border:1px solid var(--border);border-radius:6px;padding:10px 12px;min-width:0">
      <div style="font-size:9px;font-weight:700;color:var(--text3);letter-spacing:.08em;text-transform:uppercase;margin-bottom:6px">${label}</div>
      <div style="font-size:12px;font-weight:700;color:var(--text);min-width:0;overflow:hidden;text-overflow:ellipsis">${value || '—'}</div>
    </div>`;
}

function buildOutcomeSummary(t) {
  t = normalizeTicketPayload(t);
  const confidence = t.confidence_score != null
    ? `<span style="font-family:var(--mono);font-size:14px;color:${t.confidence_score < .65 ? 'var(--critical)' : t.confidence_score < .85 ? 'var(--warn)' : 'var(--ok)'}">${Math.round(t.confidence_score * 100)}%</span>`
    : '—';
  const privacy = t.privacy_risk != null
    ? `<span style="font-family:var(--mono);font-size:13px;color:${t.privacy_risk > .15 ? 'var(--critical)' : 'var(--ok)'}">${fmtNum(t.privacy_risk, 3)}</span>`
    : '—';
  return `
    <div style="margin:14px 0 16px">
      <div style="font-size:11px;font-weight:700;color:var(--text);letter-spacing:.08em;text-transform:uppercase;margin-bottom:10px;padding-bottom:8px;border-bottom:1.5px solid var(--border)">Outcome</div>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px">
        ${detailMetric('Category', html(t.assigned_category || t.category || '—'))}
        ${detailMetric('Owner Group', html(t.assignment_group || t.resolver_group || '—'))}
        ${detailMetric('Route Path', routePathBadge(t.route_path))}
        ${detailMetric('Final Confidence', confidence)}
        ${detailMetric('SLA Risk', slaRiskBadge(t.sla_risk))}
        ${detailMetric('Privacy Risk', privacy)}
      </div>
    </div>`;
}

function buildAuditDetails(label, content, open = false) {
  if (!content) return '';
  return `
    <details ${open ? 'open' : ''} style="margin-top:12px;border:1px solid var(--border);border-radius:8px;background:var(--bg1);overflow:hidden">
      <summary style="cursor:pointer;list-style:none;padding:12px 14px;background:var(--bg2);font-size:11px;font-weight:700;color:var(--text);letter-spacing:.08em;text-transform:uppercase">${label}</summary>
      <div style="padding:0 14px 14px">${content}</div>
    </details>`;
}

function buildResolutionPlan(t) {
  t = normalizeTicketPayload(t);
  const source = t.suggested_resolution_items?.length
    ? t.suggested_resolution_items
    : firstDefined(t.resolution, t.matched_resolution, t.suggested_resolution_preview);
  const items = plainResolutionItems(source);
  const evidence = t.semantic_cache_hit
    ? 'Resolution reused from an approved high-similarity historical incident.'
    : t.route_path === 'generative_rag'
      ? 'Resolution generated from retrieved historical context and verifier checks.'
      : t.escalation_required
        ? 'Human validation is required before applying a fix.'
        : 'Resolution is based on the backend routing decision.';

  return `
    <div style="margin-top:16px">
      <div style="font-size:11px;font-weight:700;color:var(--text);letter-spacing:.08em;text-transform:uppercase;margin-bottom:10px;padding-bottom:8px;border-bottom:1.5px solid var(--border)">Resolution Plan</div>
      <div style="background:#f8fafc;border:1px solid var(--border);border-radius:6px;padding:12px 14px">
        <div style="font-size:10px;font-weight:700;color:var(--text3);letter-spacing:.08em;text-transform:uppercase;margin-bottom:8px">How To Resolve</div>
        ${items.length
          ? `<ol style="margin:0;padding-left:18px;color:var(--text2);font-size:12px;line-height:1.75">
              ${items.map(item => `<li>${html(item)}</li>`).join('')}
            </ol>`
          : '<div class="empty-state" style="padding:10px 0;text-align:left">No backend resolution plan returned for this ticket.</div>'}
        <div style="font-size:10px;color:var(--text3);line-height:1.5;margin-top:10px">${html(evidence)}</div>
      </div>
    </div>`;
}

/* ══════════════════════════════════════════════════════
   MATCHED EVIDENCE  —  RAG / cache evidence panel
══════════════════════════════════════════════════════ */
function buildMatchedEvidence(t) {
  t = normalizeTicketPayload(t);
  const ids = t.retrieved_ticket_ids || [];
  if (!t.matched_ticket_id && !ids.length) return '';

  let label, labelColor;
  if (t.semantic_cache_hit) {
    label = '✓ Approved historical resolution reused'; labelColor = 'var(--ok)';
  } else if (t.route_path === 'generative_rag') {
    label = '◉ Retrieved context used for generation'; labelColor = 'var(--accent)';
  } else if (!t.retrieval_similarity || t.retrieval_similarity < .70) {
    label = '⚠ No sufficient historical match — out of distribution'; labelColor = 'var(--warn)';
  } else {
    label = '○ Partial match'; labelColor = 'var(--text3)';
  }

  return `
    <div style="margin-top:16px">
      <div style="font-size:11px;font-weight:700;color:var(--text);letter-spacing:.08em;text-transform:uppercase;margin-bottom:10px;padding-bottom:8px;border-bottom:1.5px solid var(--border)">Matched Evidence</div>
      <div style="font-size:11px;font-weight:600;color:${labelColor};margin-bottom:10px">${label}</div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:10px">
        <div><div style="font-size:10px;font-weight:600;color:var(--text3);text-transform:uppercase;letter-spacing:.08em;margin-bottom:4px">Matched Ticket</div>
          <div style="font-family:var(--mono);font-size:12px;font-weight:700;color:var(--accent)">${t.matched_ticket_id||'—'}</div></div>
        <div><div style="font-size:10px;font-weight:600;color:var(--text3);text-transform:uppercase;letter-spacing:.08em;margin-bottom:4px">Similarity Score</div>
          <div style="font-family:var(--mono);font-size:16px;font-weight:700;color:var(--text)">${fmtNum(t.retrieval_similarity,3)}</div></div>
        <div><div style="font-size:10px;font-weight:600;color:var(--text3);text-transform:uppercase;letter-spacing:.08em;margin-bottom:4px">Matched Category</div>
          <div style="font-size:12px;font-weight:600;color:var(--text)">${t.assigned_category||t.category||'—'}</div></div>
        <div><div style="font-size:10px;font-weight:600;color:var(--text3);text-transform:uppercase;letter-spacing:.08em;margin-bottom:4px">Matched Group</div>
          <div style="font-size:12px;color:var(--text2)">${t.assignment_group||'—'}</div></div>
      </div>
      ${ids.length ? `<div style="font-size:10px;font-weight:600;color:var(--text3);text-transform:uppercase;letter-spacing:.08em;margin-bottom:5px">Retrieved Context IDs</div>
        <div style="font-family:var(--mono);font-size:10px;color:var(--text3);background:var(--bg2);padding:8px 10px;border-radius:5px;line-height:1.8">${ids.join(' · ')}</div>` : ''}
      ${t.matched_resolution ? `
        <div style="font-size:10px;font-weight:600;color:var(--text3);text-transform:uppercase;letter-spacing:.08em;margin:12px 0 5px">Historical Resolution</div>
        <div style="background:var(--bg2);padding:12px;border-left:3px solid ${labelColor};border-radius:0 6px 6px 0;font-size:12px;color:var(--text2);line-height:1.7">${html(t.matched_resolution)}</div>` : ''}
    </div>`;
}

/* ══════════════════════════════════════════════════════
   FULL DETAIL CONTENT  (used in drawer / modal)
══════════════════════════════════════════════════════ */
function buildDetailContent(t) {
  t = normalizeTicketPayload(t);
  const confidenceEvidence = `${buildConfidenceBreakdown(t)}${buildMatchedEvidence(t)}`;
  const decisionAudit = `${buildDecisionTrace(t)}${t.route_explanation ? `<div style="margin-top:14px;padding:12px;background:#eff6ff;border-left:3px solid var(--accent);border-radius:0 6px 6px 0;font-size:12px;color:var(--text2);line-height:1.7"><strong style="display:block;font-size:10px;color:var(--accent);letter-spacing:.08em;margin-bottom:4px">ROUTE EXPLANATION</strong>${t.route_explanation}</div>` : ''}`;
  return `
    ${(t.short_description || t.description) ? `
    <div style="margin-bottom:16px;padding-bottom:14px;border-bottom:1px solid var(--border)">
      ${t.short_description ? `<div style="font-size:13px;font-weight:700;color:var(--text);margin-bottom:6px">${html(t.short_description)}</div>` : ''}
      ${t.description ? `<div style="font-size:12px;color:var(--text2);line-height:1.65">${html(t.description)}</div>` : ''}
    </div>` : ''}
    ${buildOutcomeSummary(t)}
    ${buildResolutionPlan(t)}
    ${buildAuditDetails('Confidence And Evidence', confidenceEvidence)}
    ${buildAuditDetails('Decision Trace', decisionAudit)}`;
}

const buildDrawerContent = buildDetailContent;

/* ══════════════════════════════════════════════════════
   MODAL
══════════════════════════════════════════════════════ */
async function openTicketModal(ticketId) {
  openTicketDetail(ticketId);
  return;
  const modal=document.getElementById('ticket-modal');
  const titleEl=document.getElementById('modal-ticket-id');
  const contentEl=document.getElementById('modal-content');
  if(!modal) return;
  modal.classList.add('open');
  if(titleEl) titleEl.textContent=ticketId;
  if(contentEl) contentEl.innerHTML=`<div style="padding:30px;text-align:center;font-size:12px;color:var(--text3);animation:skeleton-pulse 1.2s infinite">Loading…</div>`;
  try {
    const t=await apiFetch(`/v1/tickets/detail/${ticketId}`);
    if(contentEl) contentEl.innerHTML=buildDetailContent(t);
  } catch(e) {
    if(contentEl) contentEl.innerHTML=`<div style="color:var(--critical);font-size:12px;padding:16px">⚠ ${e.message}</div>`;
  }
}

function closeModal() { document.getElementById('ticket-modal')?.classList.remove('open'); }

/* ══════════════════════════════════════════════════════
   BACKEND HEALTH CHECK
══════════════════════════════════════════════════════ */
async function renderHealthPanel(containerId) {
  const el = document.getElementById(containerId);
  if (!el) return;
  try {
    const h = await apiFetch('/v1/health');
    const ok = s => s ? '✓' : '✕';
    const c  = s => s ? 'var(--ok)' : 'var(--critical)';
    el.innerHTML = `
      <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:10px">
        ${[
          ['API',              h.status==='ok'||h.status==='degraded', h.status||'—'],
          ['ClickHouse',       !!h.clickhouse,                         h.clickhouse?'connected':'unreachable'],
          ['NVIDIA LLM',       !!h.nvidia_configured,                  h.nvidia_configured?'configured':'not configured'],
          ['Model',            !!h.model,                              h.model||'—'],
          ['Uptime',           h.uptime_seconds!=null,                 h.uptime_seconds!=null?Math.round(h.uptime_seconds)+'s':'—'],
        ].map(([label,status,note])=>`
          <div style="background:${status?'#f0fdf4':'#fff5f5'};border:1px solid ${status?'#bbf7d0':'#fca5a5'};border-radius:8px;padding:14px">
            <div style="font-size:10px;font-weight:600;color:var(--text3);letter-spacing:.08em;text-transform:uppercase;margin-bottom:6px">${label}</div>
            <div style="font-size:22px;font-weight:700;color:${c(status)}">${ok(status)}</div>
            <div style="font-size:10px;color:${c(status)};margin-top:4px">${note}</div>
          </div>`).join('')}
      </div>`;
  } catch(e) {
    el.innerHTML = `
      <div style="background:#fff5f5;border:1px solid #fca5a5;border-radius:8px;padding:16px">
        <div style="font-size:13px;font-weight:700;color:var(--critical);margin-bottom:12px">Backend Unavailable</div>
        <div style="font-size:12px;color:var(--text2);margin-bottom:14px">${e.message}</div>
        <div style="font-size:11px;font-weight:600;color:var(--text3);text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px">Setup Checklist</div>
        ${['Start the Docker Compose stack','Ensure ClickHouse is healthy','Set NVIDIA_API_KEY for LLM if model calls are required','Start Privacy Shield service','Use same-origin /api proxy or a reachable backend URL'].map(s=>
          `<div style="display:flex;gap:8px;font-size:11px;color:var(--text2);padding:5px 0;border-bottom:1px solid var(--border)">
            <span style="color:var(--warn)">›</span><span>${s}</span></div>`).join('')}
        <div style="font-family:var(--mono);font-size:10px;color:var(--text3);margin-top:10px">Base URL: ${API_BASE}</div>
      </div>`;
  }
}

async function refreshStatusPill() {
  const pill = document.getElementById('status-pill');
  if (!pill) return;
  const label = pill.querySelector('span');
  const set = (text, cls) => {
    pill.classList.remove('ok', 'warn', 'fail');
    pill.classList.add(cls);
    if (label) label.textContent = text;
  };
  try {
    const h = await apiFetch('/v1/health');
    if (h.status === 'ok') set('STATUS_OK', 'ok');
    else set('DEGRADED', 'warn');
  } catch {
    set('DISCONNECTED', 'fail');
  }
}

function initNavigationToggle() {
  const sidebar = document.getElementById('sidebar');
  document.body.classList.remove('nav-open');
  document.getElementById('nav-toggle')?.remove();
  document.getElementById('nav-backdrop')?.remove();
  document.querySelectorAll('.sidebar-toggle, .menu-toggle, [aria-label="Open page menu"], [aria-label="Close page menu"]').forEach(el => el.remove());
  if (!sidebar) return;
  sidebar.setAttribute('aria-label', 'Primary navigation');
}

function initSharedNavigation() {
  const brand = document.querySelector('#topbar .brand');
  if (brand) {
    brand.innerHTML = '<strong>IncidentOps</strong><span>Routing Console</span>';
  }

  const sidebar = document.getElementById('sidebar');
  if (!sidebar) return;
  const page = location.pathname.split('/').pop() || 'dashboard.html';
  const sections = [
    ['OPERATIONS', [
      ['dashboard.html', 'Dashboard'],
      ['stream.html', 'Ticket Stream'],
      ['routing.html', 'Routing Desk'],
      ['search.html', 'Search'],
      ['ticket.html', 'Ticket Detail'],
    ]],
    ['ANALYTICS', [
      ['intelligence.html', 'Intelligence'],
    ]],
    ['DATA', [
      ['knowledge.html', 'Knowledge Base'],
      ['privacy.html', 'Privacy Audit'],
      ['escalations.html', 'Human Review'],
    ]],
  ];

  if (!sidebar.children.length) {
    sidebar.innerHTML = sections.map(([title, links]) => `
      <div class="nav-section">${title}</div>
      ${links.map(([href, label]) => `
        <a class="nav-item" href="${href}"><span>${label}</span></a>`).join('')}
    `).join('');
  }

  sidebar.querySelectorAll('.nav-item').forEach(link => {
    const href = link.getAttribute('href');
    link.classList.toggle('active', href === page || (page === '' && href === 'dashboard.html'));
  });
}

/* ══════════════════════════════════════════════════════
   PAGE INIT
══════════════════════════════════════════════════════ */
function initPage() {
  API_BASE = getApiBase();
  applySettings();
  initSharedNavigation();
  initNavigationToggle();
  injectSettingsDrawer();

  function tick() {
    const el=document.getElementById('utc-clock');
    if(el) el.textContent=new Date().toUTCString().slice(17,25)+' UTC';
  }
  tick(); setInterval(tick,1000);
  refreshStatusPill();

  document.getElementById('ticket-modal')?.addEventListener('click',e=>{
    if(e.target.id==='ticket-modal') closeModal();
  });
}

/* ══════════════════════════════════════════════
   SETTINGS — apply saved prefs to <html>
══════════════════════════════════════════════ */
function applySettings() {
  const r = document.documentElement;
  const theme   = localStorage.getItem('ops_theme')   || 'light';
  const accent  = localStorage.getItem('ops_accent')  || 'blue';
  const density = localStorage.getItem('ops_density') || 'comfortable';
  const fs      = localStorage.getItem('ops_fontsize')|| 'default';
  const rm      = localStorage.getItem('ops_reduced_motion') || '0';
  const hlSla   = localStorage.getItem('ops_hl_sla')  || '0';
  const hlHuman = localStorage.getItem('ops_hl_human')|| '0';
  r.dataset.theme         = theme;
  r.dataset.accent        = accent;
  r.dataset.density       = density;
  r.dataset.fontsize      = fs;
  r.dataset.reducedMotion = rm;
  r.dataset.highlightSla  = hlSla;
  r.dataset.highlightHuman= hlHuman;
}

/* ══════════════════════════════════════════════
   SETTINGS DRAWER — injected once per page
══════════════════════════════════════════════ */
function injectSettingsDrawer() {
  if (document.getElementById('settings-drawer')) return;

  /* Button in topbar */
  const right = document.querySelector('#topbar .right');
  if (right) {
    const btn = document.createElement('button');
    btn.id = 'settings-btn'; btn.title = 'Settings';
    btn.innerHTML = '&#9881;';
    btn.onclick = openSettings;
    right.prepend(btn);
  }

  /* Overlay + Drawer HTML */
  const overlay = document.createElement('div');
  overlay.id = 'settings-overlay';
  overlay.onclick = e => { if(e.target===overlay) closeSettings(); };

  overlay.innerHTML = `
  <div id="settings-drawer" role="dialog" aria-label="Settings">
    <header>
      <h2>Settings</h2>
      <button id="sd-close" onclick="closeSettings()" aria-label="Close">&#10005;</button>
    </header>
    <div id="settings-body">

      <!-- 1. APPEARANCE -->
      <div class="sd-section">
        <div class="sd-section-title">Appearance</div>
        <div class="sd-row">
          <span class="sd-label">Theme</span>
          <div class="sd-control" id="sd-theme">
            <button class="sd-chip" data-val="light"   onclick="sdSet('ops_theme','light',this,'sd-theme')">Light</button>
            <button class="sd-chip" data-val="dark"    onclick="sdSet('ops_theme','dark',this,'sd-theme')">Dark</button>
            <button class="sd-chip" data-val="high-contrast" onclick="sdSet('ops_theme','high-contrast',this,'sd-theme')">High Contrast</button>
          </div>
        </div>
        <div class="sd-row">
          <span class="sd-label">Accent color</span>
          <div class="sd-control" id="sd-accent">
            <button class="sd-chip" data-val="blue"  onclick="sdSet('ops_accent','blue',this,'sd-accent')">Blue</button>
            <button class="sd-chip" data-val="green" onclick="sdSet('ops_accent','green',this,'sd-accent')">Green</button>
            <button class="sd-chip" data-val="amber" onclick="sdSet('ops_accent','amber',this,'sd-accent')">Amber</button>
          </div>
        </div>
        <div class="sd-row">
          <span class="sd-label">Font size</span>
          <div class="sd-control" id="sd-fontsize">
            <button class="sd-chip" data-val="small"   onclick="sdSet('ops_fontsize','small',this,'sd-fontsize')">Small</button>
            <button class="sd-chip" data-val="default" onclick="sdSet('ops_fontsize','default',this,'sd-fontsize')">Default</button>
            <button class="sd-chip" data-val="large"   onclick="sdSet('ops_fontsize','large',this,'sd-fontsize')">Large</button>
          </div>
        </div>
        <div class="sd-row">
          <span class="sd-label">UI density</span>
          <div class="sd-control" id="sd-density">
            <button class="sd-chip" data-val="compact"     onclick="sdSet('ops_density','compact',this,'sd-density')">Compact</button>
            <button class="sd-chip" data-val="comfortable" onclick="sdSet('ops_density','comfortable',this,'sd-density')">Comfortable</button>
          </div>
        </div>
        <div class="sd-row">
          <span class="sd-label">Reduced motion</span>
          <label class="sd-toggle"><input type="checkbox" id="sd-rm" onchange="sdToggle('ops_reduced_motion',this)"><span class="sd-slider"></span></label>
        </div>
      </div>

      <!-- 2. API CONNECTION -->
      <div class="sd-section">
        <div class="sd-section-title">API Connection</div>
        <div style="margin-bottom:8px">
          <div class="sd-label" style="margin-bottom:5px">Backend Base URL</div>
          <input id="sd-api-url" class="sd-input" type="text" placeholder="/api"
            onblur="sdSaveUrl()" onkeydown="if(event.key==='Enter')sdSaveUrl()">
        </div>
        <button class="btn btn-ghost" style="font-size:11px;padding:5px 12px;width:100%" onclick="sdTestConnection()">Test Connection</button>
        <div id="sd-conn-status"></div>
      </div>

      <!-- 3. REVIEWER PROFILE -->
      <div class="sd-section">
        <div class="sd-section-title">Reviewer Profile</div>
        <div class="sd-label" style="margin-bottom:5px">Reviewer name</div>
        <input id="sd-rev-name" class="sd-input" style="margin-bottom:8px" placeholder="Your name"
          onblur="localStorage.setItem('ops_reviewer_name',this.value)">
        <div class="sd-label" style="margin-bottom:5px">Role / team</div>
        <input id="sd-rev-role" class="sd-input" style="margin-bottom:8px" placeholder="e.g. NOC Level 2"
          onblur="localStorage.setItem('ops_reviewer_role',this.value)">
        <div class="sd-label" style="margin-bottom:5px">Default assignment group</div>
        <input id="sd-rev-group" class="sd-input" placeholder="e.g. Network_Ops"
          onblur="localStorage.setItem('ops_reviewer_group',this.value)">
      </div>

      <!-- 4. PRIVACY & GOVERNANCE -->
      <div class="sd-section">
        <div class="sd-section-title">Privacy &amp; Governance</div>
        <div class="sd-row"><span class="sd-label">Policy version</span><span id="sd-policy-ver" style="font-family:var(--mono);font-size:11px;color:var(--text3)">—</span></div>
        <div class="sd-row"><span class="sd-label">Detector version</span><span id="sd-detector-ver" style="font-family:var(--mono);font-size:11px;color:var(--text3)">—</span></div>
        <div class="sd-row">
          <span class="sd-label">Min. privacy confidence</span>
          <div style="display:flex;align-items:center;gap:6px">
            <input type="range" id="sd-min-priv-conf" min="0" max="1" step="0.05" value="0"
              style="width:80px;accent-color:var(--accent)"
              oninput="document.getElementById('sd-mpc-val').textContent=parseFloat(this.value).toFixed(2);localStorage.setItem('ops_min_priv_conf',this.value)">
            <span id="sd-mpc-val" style="font-family:var(--mono);font-size:11px">0.00</span>
          </div>
        </div>
        <div class="sd-row">
          <span class="sd-label">Highlight high-risk entities</span>
          <label class="sd-toggle"><input type="checkbox" id="sd-hl-risk" onchange="sdToggle('ops_hl_risk',this)"><span class="sd-slider"></span></label>
        </div>
        <div style="font-size:10px;color:var(--text3);margin-top:6px">PII redaction cannot be disabled.</div>
      </div>

      <!-- 5. OPERATIONS PREFERENCES -->
      <div class="sd-section">
        <div class="sd-section-title">Operations Preferences</div>
        <div class="sd-row">
          <span class="sd-label">Auto-refresh interval</span>
          <div class="sd-control" id="sd-refresh">
            <button class="sd-chip" data-val="0"   onclick="sdSet('ops_refresh','0',this,'sd-refresh')">Off</button>
            <button class="sd-chip" data-val="30"  onclick="sdSet('ops_refresh','30',this,'sd-refresh')">30s</button>
            <button class="sd-chip" data-val="60"  onclick="sdSet('ops_refresh','60',this,'sd-refresh')">1m</button>
            <button class="sd-chip" data-val="300" onclick="sdSet('ops_refresh','300',this,'sd-refresh')">5m</button>
          </div>
        </div>
        <div class="sd-row">
          <span class="sd-label">Highlight critical SLA tickets</span>
          <label class="sd-toggle"><input type="checkbox" id="sd-hl-sla" onchange="sdToggle('ops_hl_sla',this);applySettings()"><span class="sd-slider"></span></label>
        </div>
        <div class="sd-row">
          <span class="sd-label">Highlight human review tickets</span>
          <label class="sd-toggle"><input type="checkbox" id="sd-hl-human" onchange="sdToggle('ops_hl_human',this);applySettings()"><span class="sd-slider"></span></label>
        </div>
        <div class="sd-row">
          <span class="sd-label">Sound alerts</span>
          <label class="sd-toggle"><input type="checkbox" id="sd-sound" onchange="sdToggle('ops_sound',this)"><span class="sd-slider"></span></label>
        </div>
      </div>
    </div><!-- /settings-body -->

    <div class="sd-footer">
      <button class="btn btn-ghost" style="flex:1;font-size:11px" onclick="sdReset()">Reset defaults</button>
    </div>
  </div>`;

  document.body.appendChild(overlay);

  /* Keyboard close */
  document.addEventListener('keydown', e => { if(e.key==='Escape') closeSettings(); });

  /* Populate from localStorage */
  sdSyncUI();
}

function openSettings()  { document.getElementById('settings-overlay')?.classList.add('open'); document.getElementById('settings-drawer')?.classList.add('open'); }
function closeSettings() { document.getElementById('settings-overlay')?.classList.remove('open'); document.getElementById('settings-drawer')?.classList.remove('open'); }

function sdSet(key, val, btn, groupId) {
  localStorage.setItem(key, val);
  document.querySelectorAll('#'+groupId+' .sd-chip').forEach(c=>c.classList.remove('active'));
  btn.classList.add('active');
  applySettings();
}
function sdToggle(key, input) { localStorage.setItem(key, input.checked?'1':'0'); applySettings(); }

function sdSaveUrl() {
  const v = document.getElementById('sd-api-url')?.value.trim();
  if (v) localStorage.setItem('ops_api_base', v);
  else localStorage.removeItem('ops_api_base');
  API_BASE = getApiBase();
}

async function sdTestConnection() {
  const statusEl = document.getElementById('sd-conn-status');
  statusEl.innerHTML = '<div class="sd-status">Testing…</div>';
  sdSaveUrl();
  try {
    const h = await apiFetch('/v1/health');
    const row = (label, ok, note) => `<div><span class="${ok?'ok':'fail'}">${ ok?'✓':'✕'} ${label}</span> <span style="color:var(--text3)">${note}</span></div>`;
    statusEl.innerHTML = `<div class="sd-status">
      ${row('API', h.status==='ok'||h.status==='degraded', h.status||'')}
      ${row('ClickHouse', !!h.clickhouse, h.clickhouse?'connected':'unreachable')}
      ${row('NVIDIA LLM', !!h.nvidia_configured, h.nvidia_configured?'configured':'not configured')}
      ${row('Model', !!h.model, h.model||'—')}
    </div>`;
  } catch(e) {
    statusEl.innerHTML = `<div class="sd-status"><span class="fail">✕ ${e.message}</span></div>`;
  }
}

function sdSyncUI() {
  const get = k => localStorage.getItem(k);
  /* chip groups */
  [['sd-theme','ops_theme'],['sd-accent','ops_accent'],['sd-fontsize','ops_fontsize'],
   ['sd-density','ops_density'],['sd-refresh','ops_refresh']].forEach(([gId,key]) => {
    const val = get(key);
    if (!val) return;
    document.querySelectorAll('#'+gId+' .sd-chip').forEach(c => c.classList.toggle('active', c.dataset.val===val));
  });
  /* toggles */
  [['sd-rm','ops_reduced_motion'],['sd-hl-risk','ops_hl_risk'],
   ['sd-hl-sla','ops_hl_sla'],['sd-hl-human','ops_hl_human'],['sd-sound','ops_sound']].forEach(([id,key]) => {
    const el = document.getElementById(id); if(el) el.checked = get(key)==='1';
  });
  /* text inputs */
  const url = document.getElementById('sd-api-url'); if(url) url.value = API_BASE;
  const n=document.getElementById('sd-rev-name'); if(n) n.value=get('ops_reviewer_name')||'';
  const ro=document.getElementById('sd-rev-role'); if(ro) ro.value=get('ops_reviewer_role')||'';
  const rg=document.getElementById('sd-rev-group'); if(rg) rg.value=get('ops_reviewer_group')||'';
  /* privacy conf slider */
  const sl=document.getElementById('sd-min-priv-conf'),mv=document.getElementById('sd-mpc-val');
  if(sl&&mv){const v=parseFloat(get('ops_min_priv_conf')||0);sl.value=v;mv.textContent=v.toFixed(2);}
  /* try fetch policy/detector version from last audit data */
  try {
    apiFetch('/v1/privacy/audit/recent').then(d=>{
      const arr=Array.isArray(d)?d:(d.findings||[]);
      if(arr[0]?.policy_version)  document.getElementById('sd-policy-ver').textContent=arr[0].policy_version;
      if(arr[0]?.detector_version)document.getElementById('sd-detector-ver').textContent=arr[0].detector_version;
    }).catch(()=>{});
  } catch{}
}

function sdReset() {
  if(!confirm('Reset all settings to defaults?')) return;
  ['ops_theme','ops_accent','ops_fontsize','ops_density','ops_reduced_motion',
   'ops_hl_sla','ops_hl_human','ops_sound','ops_refresh','ops_min_priv_conf'].forEach(k=>localStorage.removeItem(k));
  applySettings();
  sdSyncUI();
}

/* Helper: get saved reviewer name for Human Review submissions */
function getReviewerName() { return localStorage.getItem('ops_reviewer_name') || 'operator'; }
