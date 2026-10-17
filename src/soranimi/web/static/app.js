import { element, icon, renderMarkdown } from './markdown.js';

const $ = id => document.getElementById(id);
const labels = { 'accept-edits': '自动接受编辑', manual: '手动审批', plan: '只读规划', auto: '自动执行' };
const state = { boot: null, sessions: [], current: null, view: null, defaults: {}, busy: false, job: null, files: null, live: null, preview: null, tab: 'activity' };
let toastTimer;

function toast(text, error = false) {
  clearTimeout(toastTimer);
  $('toast').textContent = text;
  $('toast').classList.toggle('error', error);
  $('toast').hidden = false;
  toastTimer = setTimeout(() => { $('toast').hidden = true; }, error ? 7000 : 3500);
}

async function api(path, options = {}) {
  const response = await fetch(`/api${path}`, { ...options, headers: { 'Content-Type': 'application/json', 'X-SoraNimi-Request': '1', ...options.headers } });
  if (!response.ok) {
    let message = `请求失败 (${response.status})`;
    try { const body = await response.json(); message = typeof body.detail === 'string' ? body.detail : message; } catch { /* use HTTP status */ }
    throw new Error(message);
  }
  return response.json();
}

function post(path, body = {}) { return api(path, { method: 'POST', body: JSON.stringify(body) }); }
function shortModel(model = '') { return model.includes(':') ? model.slice(model.indexOf(':') + 1) : model; }
function hasPending() { return Boolean(state.view?.pending?.length); }
function scrollBottom(force = false) {
  const box = $('conversation-scroll');
  if (force || box.scrollHeight - box.scrollTop - box.clientHeight < 200) box.scrollTop = box.scrollHeight;
}

function setBusy(value, job = null) {
  state.busy = value;
  state.job = job;
  $('stop').hidden = !value;
  $('running-status').hidden = !value;
  $('stop').disabled = false;
  $('send').hidden = value;
  $('prompt').disabled = value || hasPending() || Boolean(state.view?.resumable);
  ['new-session', 'model-picker', 'permission-picker', 'settings'].forEach(id => { $(id).disabled = value; });
  $('undo').disabled = value || !state.view?.changes?.length || Boolean(state.view?.resumable);
  updateSend();
}

function updateSend() { $('send').disabled = state.busy || hasPending() || Boolean(state.view?.resumable) || !$('prompt').value.trim() || !state.boot; }
function resizePrompt() { $('prompt').style.height = 'auto'; $('prompt').style.height = `${Math.min($('prompt').scrollHeight, 190)}px`; updateSend(); }

function renderSessions() {
  const list = $('session-list');
  list.replaceChildren();
  $('session-count').textContent = state.sessions.length;
  if (!state.sessions.length) { list.append(element('p', 'sidebar-empty', '新的想法，从一次对话开始。')); return; }
  let previousGroup = '';
  for (const item of state.sessions) {
    const today = new Date().toLocaleDateString('zh-CN');
    const date = new Date(item.updated).toLocaleDateString('zh-CN');
    const group = today === date ? '今天' : date;
    if (group !== previousGroup) { list.append(element('div', 'session-group-label', group)); previousGroup = group; }
    const button = element('button', `session-item${item.id === state.current ? ' active' : ''}`);
    button.type = 'button';
    button.title = item.title || `会话 ${item.id.slice(0, 8)}`;
    button.append(icon('chat'), element('span', '', button.title));
    if (item.id === state.current) button.setAttribute('aria-current', 'page');
    button.addEventListener('click', () => selectSession(item.id).catch(error => toast(error.message, true)));
    list.append(button);
  }
}

async function refreshSessions() { state.sessions = await api('/sessions'); renderSessions(); }

function assistantMessage(text = '') {
  const article = element('article', 'message message-assistant');
  const heading = element('div', 'assistant-heading');
  const portrait = element('span', 'assistant-portrait');
  const mark = document.createElement('img'); mark.src = '/assets/nimi-character.png'; mark.alt = '新海天';
  portrait.append(mark);
  heading.append(portrait, element('span', '', '新海天'), element('span', '', state.boot?.demo ? '离线演示' : shortModel(state.view?.session.model || state.defaults.model)));
  const body = element('div', 'message-body');
  if (text) renderMarkdown(body, text);
  else { const typing = element('div', 'typing-indicator'); typing.append(element('span'), element('span'), element('span')); body.append(typing); }
  article.append(heading, body);
  $('messages').append(article);
  return { article, body, text };
}

function userMessage(text) {
  const article = element('article', 'message message-user');
  article.append(element('div', 'user-bubble', text));
  $('messages').append(article);
}

function toolResult(message, call) {
  const wrapper = element('div', 'tool-message');
  const details = element('details', 'tool-inline');
  const summary = element('summary');
  const status = element('span', `tool-status${message.status === 'error' ? ' error' : ''}`, message.status === 'error' ? '未完成' : '已完成');
  summary.append(icon(message.name === 'bash' ? 'terminal' : 'code'), element('span', '', message.name || '工具调用'), status, icon('down'));
  const detail = [call ? `参数\n${JSON.stringify(call.args, null, 2)}\n\n` : '', `结果\n${message.text}`].join('');
  details.append(summary, element('pre', '', detail));
  wrapper.append(details); $('messages').append(wrapper);
}

function renderMessages(messages) {
  $('messages').replaceChildren();
  state.live = null;
  const calls = new Map();
  for (const message of messages) {
    if (message.role === 'human') userMessage(message.text);
    else if (message.role === 'ai') {
      if (message.text) assistantMessage(message.text);
      (message.calls || []).forEach(call => calls.set(call.id, call));
    } else if (message.role === 'tool') toolResult(message, calls.get(message.call_id));
  }
  $('welcome').hidden = messages.length > 0;
}

function activityItem(name, status, detail = '', id = '') {
  const item = element('div', `activity-item ${status === 'running' ? 'running' : status === 'error' ? 'error' : ''}`);
  item.dataset.callId = id;
  item.append(element('span', 'activity-dot'), element('strong', '', name), element('p', '', detail), element('span', 'activity-label', status === 'running' ? '执行中' : status === 'error' ? '执行未完成' : '执行完成'));
  return item;
}

function renderActivity(messages) {
  const list = $('activity-list'); list.replaceChildren();
  const calls = new Map();
  let count = 0;
  for (const message of messages) {
    if (message.role === 'ai') (message.calls || []).forEach(call => calls.set(call.id, call));
    if (message.role === 'tool') {
      const call = calls.get(message.call_id);
      const detail = call ? Object.values(call.args).filter(value => typeof value === 'string').join(' · ') : message.text.slice(0, 100);
      list.append(activityItem(message.name || '工具', message.status, detail, message.call_id));
      count++;
    }
  }
  $('activity-count').textContent = count;
  $('activity-empty').hidden = count > 0;
}

function renderPlan(steps = []) {
  $('plan').replaceChildren(); $('plan').hidden = !steps.length;
  if (!steps.length) return;
  $('plan').append(element('div', 'plan-title', '任务计划'));
  steps.forEach(step => {
    const row = element('div', `plan-step ${step.status}`);
    row.append(step.status === 'completed' ? icon('check') : element('span', 'step-circle'), element('span', '', step.step));
    $('plan').append(row);
  });
}

function renderChanges(changes = []) {
  $('change-count').textContent = changes.length;
  $('changes-list').replaceChildren();
  $('undo').disabled = state.busy || !changes.length || Boolean(state.view?.resumable);
  if (!changes.length) { $('changes-list').append(element('p', 'panel-empty', '还没有文件修改。\nAgent 产生的改动会显示在这里。')); return; }
  changes.forEach(change => {
    const button = element('button', 'change-file');
    button.type = 'button'; button.title = change.path;
    button.append(icon('file'), element('span', '', change.path), element('span', 'added-count', `+${change.added}`), element('span', 'removed-count', `−${change.removed}`));
    button.addEventListener('click', () => openPreview(change.path, change.diff, true));
    $('changes-list').append(button);
  });
}

function renderApproval() {
  const panel = $('approval'); panel.replaceChildren(); panel.hidden = !hasPending();
  $('resume-bar').hidden = !state.view?.resumable || hasPending() || state.busy;
  if (!hasPending()) return;
  const heading = element('div', 'approval-heading'); heading.append(icon('shield'), document.createTextNode('这一步，需要你确认'));
  panel.append(heading, element('p', '', '查看即将执行的操作。允许后继续执行，拒绝后 Agent 会收到取消结果。'));
  for (const pending of state.view.pending) panel.append(element('pre', '', `${pending.tool} · ${pending.risk}\n${JSON.stringify(pending.args, null, 2)}`));
  const actions = element('div', 'approval-actions');
  for (const [text, allowed, className] of [['拒绝操作', false, 'deny-button'], ['允许本次操作', true, 'approve-button']]) {
    const button = element('button', className, text); button.type = 'button'; button.disabled = state.busy;
    button.addEventListener('click', () => {
      const approvals = Object.fromEntries(state.view.pending.map(p => [p.id, allowed]));
      runJob({ approvals }).catch(error => toast(error.message, true));
    }); actions.append(button);
  }
  panel.append(actions);
}

function applyView(view) {
  state.view = view; state.current = view.session.id;
  localStorage.setItem('soranimi-session', state.current);
  $('conversation-title').textContent = view.session.title || `会话 ${state.current.slice(0, 8)}`;
  $('model-name').textContent = shortModel(view.session.model);
  $('permission-label').textContent = labels[view.session.permission || state.defaults.permission];
  $('token-count').textContent = `${view.tokens.toLocaleString()} tokens`;
  $('token-meter').style.width = `${Math.min(100, view.tokens / 500)}%`;
  renderMessages(view.messages); renderActivity(view.messages); renderPlan(view.plan); renderChanges(view.changes);
  setBusy(view.running, view.job_id); renderApproval(); renderSessions(); scrollBottom(true);
}

async function selectSession(id) {
  if (state.busy) { toast('请先等待当前任务完成，或暂停任务。'); return; }
  const view = await api(`/sessions/${id}`);
  applyView(view); document.body.classList.remove('nav-open');
  if (view.running && view.job_id) listen(view.job_id, view.event_cursor || 0).catch(error => toast(error.message, true));
}

async function newSession(options = state.defaults) {
  if (state.busy) { toast('请先等待当前任务完成，或暂停任务。'); return; }
  const view = await post('/sessions', options);
  applyView(view); await refreshSessions();
  $('prompt').value = ''; resizePrompt(); $('prompt').focus();
  document.body.classList.remove('nav-open');
}

function streamEvent(type, data) {
  if (type === 'token') {
    if (!state.live) state.live = assistantMessage();
    state.live.text += data.text;
    renderMarkdown(state.live.body, state.live.text); scrollBottom();
  } else if (type === 'assistant') {
    if (data.text) {
      if (state.live) renderMarkdown(state.live.body, data.text);
      else assistantMessage(data.text);
    } else if (state.live) state.live.article.remove();
    state.live = null; scrollBottom();
  } else if (type === 'tool') {
    const list = $('activity-list');
    const existing = [...list.children].findLast(item => item.dataset.callId === data.id && item.classList.contains('running'));
    if (data.event === 'tool_start') {
      if (!existing) list.append(activityItem(data.name, 'running', '', data.id));
      $('activity-empty').hidden = true;
      $('activity-count').textContent = list.children.length;
    } else if (existing) {
      existing.classList.remove('running'); existing.classList.toggle('error', data.status === 'error');
      existing.querySelector('.activity-label').textContent = data.status === 'error' ? '执行未完成' : '执行完成';
    }
  } else if (type === 'result') { toolResult(data); scrollBottom(); }
  else if (type === 'plan') renderPlan(data.steps);
  else if (type === 'complete') {
    applyView(data.state);
    if (data.stopped) toast('任务已暂停，可以从检查点继续。');
    if (data.state.pending.length) toast('有一项操作等待你的确认。');
  } else if (type === 'error') { state.jobError = data.message; }
}

async function listen(jobId, cursor = 0) {
  state.jobError = null;
  setBusy(true, jobId);
  let failure;
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const response = await fetch(`/api/jobs/${jobId}/events?after=${cursor}`, { headers: { 'X-SoraNimi-Request': '1' } });
      if (!response.ok) throw new Error('连接已失效，请刷新会话。');
      const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = '';
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let boundary;
        while ((boundary = buffer.indexOf('\n\n')) !== -1) {
          const packet = buffer.slice(0, boundary); buffer = buffer.slice(boundary + 2);
          if (packet.startsWith(':')) continue;
          let type, data, sequence;
          for (const line of packet.split('\n')) {
            if (line.startsWith('event: ')) type = line.slice(7);
            else if (line.startsWith('data: ')) data = line.slice(6);
            else if (line.startsWith('id: ')) sequence = Number(line.slice(4));
          }
          if (type && data) { cursor = sequence || cursor; streamEvent(type, JSON.parse(data)); }
        }
      }
      failure = null; break;
    } catch (error) { failure = error; await new Promise(resolve => setTimeout(resolve, 600 * (attempt + 1))); }
  }
  setBusy(false); state.live = null;
  if (state.current) applyView(await api(`/sessions/${state.current}`));
  await refreshSessions();
  if (failure) throw failure;
  if (state.jobError) throw new Error(state.jobError);
  if (!hasPending() && !state.view?.resumable) $('prompt').focus();
}

async function runJob(body) {
  if (state.busy) return;
  const previousInput = $('prompt').value;
  if (!state.current) await newSession();
  const id = state.current;
  setBusy(true);
  try {
    const result = await post(`/sessions/${id}/run`, body);
    if (body.message) {
      $('welcome').hidden = true; userMessage(body.message);
      $('prompt').value = ''; resizePrompt(); scrollBottom(true);
    }
    $('approval').hidden = true; $('resume-bar').hidden = true;
    await listen(result.job_id);
  } catch (error) {
    setBusy(false); renderApproval();
    if (!$('prompt').value && body.message) $('prompt').value = previousInput;
    resizePrompt(); throw error;
  }
}

function settingsDialog() {
  $('setting-model').value = state.view?.session.model || state.defaults.model;
  $('setting-permission').value = state.view?.session.permission || state.defaults.permission;
  $('setting-sandbox').value = state.view?.session.sandbox || state.defaults.sandbox;
  $('settings-note').textContent = state.boot?.demo ? '当前为离线演示模式，不调用真实模型。真实编程任务请去掉启动参数 --demo。' : 'Docker 需要已启动的 daemon 和构建好的镜像。已有会话保留各自的设置。';
  $('settings-dialog').showModal();
}

function renderSearch() {
  const query = $('search-input').value.toLowerCase(); $('search-results').replaceChildren();
  const matches = state.sessions.filter(item => `${item.title || item.id} ${item.model}`.toLowerCase().includes(query));
  if (!matches.length) $('search-results').append(element('p', 'panel-empty', '没有找到匹配的会话。'));
  matches.forEach(item => {
    const button = element('button', 'search-result'); button.append(element('strong', '', item.title || `会话 ${item.id.slice(0, 8)}`), element('span', '', `${shortModel(item.model)} · ${new Date(item.updated).toLocaleDateString('zh-CN')}`));
    button.addEventListener('click', () => { $('search-dialog').close(); selectSession(item.id).catch(error => toast(error.message, true)); });
    $('search-results').append(button);
  });
}

async function showTab(tab) {
  state.tab = tab;
  document.querySelectorAll('[data-tab]').forEach(button => { const active = button.dataset.tab === tab; button.classList.toggle('active', active); button.setAttribute('aria-selected', String(active)); });
  ['activity', 'changes', 'files'].forEach(name => { $(`${name}-panel`).hidden = name !== tab; });
  if (tab === 'files') {
    if (!state.files) { $('files-list').replaceChildren(element('p', 'panel-empty', '正在读取工作区…')); const result = await api('/files'); state.files = result.files; if (result.truncated) toast('只展示前 1500 个文件，可在任务中使用搜索工具。'); }
    renderFiles();
  }
}

function renderFiles() {
  const query = $('file-filter').value.toLowerCase(); $('files-list').replaceChildren();
  const files = (state.files || []).filter(file => file.path.toLowerCase().includes(query));
  if (!files.length) $('files-list').append(element('p', 'panel-empty', '没有找到匹配的文件。'));
  files.forEach(file => {
    const button = element('button', 'file-entry'); button.title = file.path;
    button.append(icon('file'), element('span', '', file.path));
    button.addEventListener('click', async () => { try { const data = await api(`/file?path=${encodeURIComponent(file.path)}`); openPreview(data.path, data.content); } catch (error) { toast(error.message, true); } });
    $('files-list').append(button);
  });
}

function openPreview(path, content, diff = false) {
  state.preview = { path, content, diff };
  $('file-title').textContent = path; $('file-content').replaceChildren();
  const code = element('pre', 'file-code');
  if (diff) {
    content.split('\n').forEach(line => code.append(element('span', `diff-line ${line.startsWith('@@') ? 'diff-header' : line.startsWith('+') && !line.startsWith('+++') ? 'diff-add' : line.startsWith('-') && !line.startsWith('---') ? 'diff-remove' : ''}`, line)));
  } else code.textContent = content;
  $('file-content').append(code); $('file-dialog').showModal();
}

function theme(value) {
  document.documentElement.dataset.theme = value;
  localStorage.setItem('soranimi-nimi-theme', value);
  $('theme').replaceChildren(icon(value === 'dark' ? 'sun' : 'moon'));
  $('theme').setAttribute('aria-label', value === 'dark' ? '切换浅色主题' : '切换深色主题');
}

$('prompt').addEventListener('input', resizePrompt);
$('prompt').addEventListener('keydown', event => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); if (!$('send').disabled) $('composer').requestSubmit(); } });
$('composer').addEventListener('submit', event => { event.preventDefault(); const message = $('prompt').value.trim(); if (message && !state.busy) runJob({ message }).catch(error => toast(error.message, true)); });
$('new-session').addEventListener('click', () => newSession().catch(error => toast(error.message, true)));
document.querySelectorAll('.suggestion').forEach(button => button.addEventListener('click', () => { $('prompt').value = button.dataset.prompt; resizePrompt(); $('prompt').focus(); }));
['settings', 'model-picker', 'permission-picker'].forEach(id => $(id).addEventListener('click', settingsDialog));
$('settings-form').addEventListener('submit', async event => { event.preventDefault(); const options = { model: $('setting-model').value.trim(), permission: $('setting-permission').value, sandbox: $('setting-sandbox').value }; if (!options.model) return; try { await newSession(options); state.defaults = options; $('settings-dialog').close(); toast('新会话已准备就绪。'); } catch (error) { toast(error.message, true); } });
document.querySelectorAll('[data-close]').forEach(button => button.addEventListener('click', () => $(button.dataset.close).close()));
document.querySelectorAll('dialog').forEach(dialog => dialog.addEventListener('click', event => { if (event.target === dialog) { const bounds = dialog.getBoundingClientRect(); if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) dialog.close(); } }));
$('search-session').addEventListener('click', () => { $('search-input').value = ''; renderSearch(); $('search-dialog').showModal(); $('search-input').focus(); });
$('search-input').addEventListener('input', renderSearch);
$('theme').addEventListener('click', () => theme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'));
$('toggle-nav').addEventListener('click', () => document.body.classList.toggle('nav-open'));
$('toggle-inspector').addEventListener('click', () => { if (window.innerWidth <= 1050) document.body.classList.toggle('inspector-mobile-open'); else document.body.classList.toggle('inspector-hidden'); });
document.querySelectorAll('[data-tab]').forEach(button => button.addEventListener('click', () => showTab(button.dataset.tab).catch(error => toast(error.message, true))));
$('file-filter').addEventListener('input', renderFiles);
$('mention-file').addEventListener('click', async () => { document.body.classList.remove('inspector-hidden'); if (window.innerWidth <= 1050) document.body.classList.add('inspector-mobile-open'); try { await showTab('files'); $('file-filter').focus(); toast('选择文件预览，再点击「引用此文件」。'); } catch (error) { toast(error.message, true); } });
$('copy-file').addEventListener('click', async () => { try { await navigator.clipboard.writeText(state.preview.content); toast('内容已复制。'); } catch { toast('浏览器未允许访问剪贴板。', true); } });
$('reference-file').addEventListener('click', () => { if ($('prompt').disabled) { toast('请等待当前任务完成后再引用文件。'); return; } $('prompt').value += `${$('prompt').value ? ' ' : ''}@${state.preview.path} `; $('file-dialog').close(); resizePrompt(); $('prompt').focus(); });
$('stop').addEventListener('click', async () => { if (!state.job) return; try { await post(`/jobs/${state.job}/stop`); $('stop').disabled = true; toast('将在当前步骤完成后暂停任务。'); } catch (error) { toast(error.message, true); } });
$('resume-task').addEventListener('click', () => runJob({}).catch(error => toast(error.message, true)));
$('undo').addEventListener('click', async () => { if (!state.current) return; $('undo').disabled = true; try { const data = await post(`/sessions/${state.current}/undo`); applyView(data.state); toast(data.message.startsWith('Undid') ? `已撤销 ${data.message.slice(6)}` : '没有可以撤销的文件修改。'); state.files = null; } catch (error) { toast(error.message, true); $('undo').disabled = false; } });
document.addEventListener('keydown', event => { if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); $('search-session').click(); } else if (event.altKey && event.key.toLowerCase() === 'n' && !state.busy) { event.preventDefault(); $('new-session').click(); } });

async function boot() {
  theme(localStorage.getItem('soranimi-nimi-theme') === 'dark' ? 'dark' : 'light');
  const data = await api('/bootstrap'); state.boot = data; state.sessions = data.sessions; state.defaults = data.defaults;
  $('workspace-name').textContent = data.workspace.name; $('workspace-path').textContent = data.workspace.path; $('workspace-path').title = data.workspace.path;
  $('project-crumb').textContent = data.workspace.name; $('mode-label').textContent = data.demo ? '离线演示' : '本地运行'; $('model-name').textContent = shortModel(data.defaults.model); $('permission-label').textContent = labels[data.defaults.permission];
  renderSessions(); updateSend();
  const selected = data.active?.session_id || localStorage.getItem('soranimi-session');
  if (selected && data.sessions.some(item => item.id === selected)) await selectSession(selected);
}

boot().catch(error => { toast(`无法连接工作台：${error.message}。请刷新页面重试。`, true); $('workspace-path').textContent = '连接不可用'; });
