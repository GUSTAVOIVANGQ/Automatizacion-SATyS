const $ = (id) => document.getElementById(id);

const els = {
  title: $('page-title'), eyebrow: $('eyebrow'), refresh: $('refresh-btn'),
  statusPill: $('status-pill'), stage: $('stage'), startedAt: $('started-at'), updatedAt: $('updated-at'),
  mSatys: $('m-satys'), mExcel: $('m-excel'), mNuevos: $('m-nuevos'), mProcesados: $('m-procesados'), mFallidos: $('m-fallidos'),
  timerHora: $('timer-hora'), saveTimer: $('save-timer-btn'), timerNote: $('timer-note'),
  svcActive: $('svc-active'), timerActive: $('timer-active'), timerEnabled: $('timer-enabled'), nextTimer: $('next-timer'),
  startDaily: $('start-daily-btn'), dailySummary: $('daily-summary'), lastRefresh: $('last-refresh'),
  dailyLogSource: $('daily-log-source'), dailyLogBox: $('daily-log-box'), dailyAutoscroll: $('daily-autoscroll'),
  manualForm: $('manual-form'), tipoTxt: $('tipo-txt'), manualFile: $('manual-file'), manualWorkers: $('manual-workers'), manualHeadless: $('manual-headless'),
  manualCommand: $('manual-command'), manualPill: $('manual-pill'), manualStatusText: $('manual-status-text'), manualPid: $('manual-pid'), manualStarted: $('manual-started'), manualRc: $('manual-rc'),
  manualLogSource: $('manual-log-source'), manualLogBox: $('manual-log-box'), manualAutoscroll: $('manual-autoscroll'),
  dailyHistory: $('daily-history'), manualHistory: $('manual-history'),
  infoExcel: $('info-excel'), infoConsolidado: $('info-consolidado'), infoOutput: $('info-output'), infoDescargas: $('info-descargas'),
  registroInput: $('registro-download-input'), registroTipo: $('registro-download-tipo'), registroBuscarBtn: $('registro-buscar-btn'), registroDownloadBtn: $('registro-download-btn'), registroResult: $('registro-download-result'),
  toast: $('toast'),
};

const viewTitles = {
  automatizacion: ['SATyS — Monitor diario', 'Automatización diaria'],
  procesar: ['SATyS — Descarga, Excel y Organización', 'Procesamiento SATyS'],
  historial: ['SATyS — Historial', 'Historial'],
  salidas: ['SATyS — Archivos generados', 'Salidas'],
};

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
}
function setText(el, value) { if (el) el.textContent = value ?? '—'; }
function first(data, keys, fallback = '—') {
  for (const key of keys) {
    const value = data?.[key];
    if (value !== undefined && value !== null && value !== '') return value;
  }
  return fallback;
}
function numberOrDash(value) {
  return value === undefined || value === null || value === '' ? '—' : value;
}
function showToast(msg, type = '') {
  els.toast.textContent = msg;
  els.toast.className = `toast ${type}`.trim();
  window.setTimeout(() => els.toast.classList.add('hidden'), 6500);
}
function sizeHuman(bytes) {
  if (bytes === undefined || bytes === null) return '—';
  const units = ['B', 'KB', 'MB', 'GB'];
  let n = Number(bytes); let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i ? 1 : 0)} ${units[i]}`;
}

async function fetchJson(url, options = {}) {
  const r = await fetch(url, { cache: 'no-store', ...options });
  if (!r.ok) {
    let detail = await r.text();
    try { detail = JSON.parse(detail).detail || detail; } catch (_) {}
    throw new Error(`${r.status}: ${detail}`);
  }
  return r.json();
}

function navTo(view) {
  document.querySelectorAll('.nav-item').forEach(btn => btn.classList.toggle('active', btn.dataset.view === view));
  document.querySelectorAll('.view').forEach(section => section.classList.remove('active-view'));
  $(`view-${view}`).classList.add('active-view');
  const [eyebrow, title] = viewTitles[view] || ['', view];
  els.eyebrow.textContent = eyebrow;
  els.title.textContent = title;
  if (view === 'historial') refreshHistory();
  if (view === 'salidas') refreshFiles();
}

document.querySelectorAll('.nav-item').forEach(btn => btn.addEventListener('click', () => navTo(btn.dataset.view)));

function setPill(el, state, text) {
  el.className = `pill ${state}`;
  el.textContent = text;
}

function renderEstado(data) {
  const running = data.running === true;
  const ok = data.ok === true || data.resultado === 'ok';
  const error = data.ok === false && data.stage !== 'sin_estado';
  if (running) setPill(els.statusPill, 'running', 'En ejecución');
  else if (error) setPill(els.statusPill, 'error', 'Con error');
  else if (ok) setPill(els.statusPill, 'ok', 'Finalizado');
  else setPill(els.statusPill, 'neutral', 'Sin ejecución');

  setText(els.stage, first(data, ['stage', 'etapa', 'mensaje'], 'Sin estado'));
  setText(els.startedAt, first(data, ['started_at', 'fecha_ejecucion', 'inicio'], '—'));
  setText(els.updatedAt, first(data, ['updated_at', 'ultimo_latido', 'finished_at', 'fecha_fin'], '—'));
  setText(els.mSatys, numberOrDash(first(data, ['satys_detectados', 'total_satys', 'total_registros_satys', 'registros_satys'], null)));
  setText(els.mExcel, numberOrDash(first(data, ['existentes_excel', 'total_excel', 'registros_excel'], null)));
  setText(els.mNuevos, numberOrDash(first(data, ['nuevos', 'total_nuevos', 'registros_nuevos'], null)));
  setText(els.mProcesados, numberOrDash(first(data, ['procesados', 'total_procesados', 'registros_procesados'], null)));
  setText(els.mFallidos, numberOrDash(first(data, ['fallidos', 'total_fallidos', 'total_fallidos_controlados'], null)));
}

function renderSystemd(data) {
  setText(els.svcActive, data.service_active || '—');
  setText(els.timerActive, data.timer_active || '—');
  setText(els.timerEnabled, data.timer_enabled || '—');
  setText(els.nextTimer, data.next_timer_raw || 'Sin información de timer');
}

function flattenSummary(data) {
  const pairs = [
    ['Resultado', ['mensaje']],
    ['SATyS detectados', ['total_registros_satys', 'satys_detectados', 'total_satys']],
    ['Excel existentes', ['total_excel', 'existentes_excel']],
    ['Nuevos', ['total_nuevos', 'nuevos', 'registros_nuevos']],
    ['Fallidos controlados', ['total_fallidos_controlados', 'fallidos']],
    ['Código main', ['return_code_main']],
    ['Inicio', ['fecha_ejecucion']],
    ['Fin', ['fecha_fin', 'finished_at']],
  ];
  const out = [];
  for (const [label, keys] of pairs) {
    const value = first(data, keys, null);
    if (value !== null && value !== undefined && value !== '') out.push([label, value]);
  }
  return out;
}
function renderSummary(data) {
  const items = flattenSummary(data);
  if (!items.length) {
    els.dailySummary.className = 'summary-grid empty';
    els.dailySummary.textContent = 'Aún no hay resumen disponible.';
    return;
  }
  els.dailySummary.className = 'summary-grid';
  els.dailySummary.innerHTML = items.map(([label, value]) => `<div class="summary-item"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join('');
}

function renderManual(data) {
  if (data.running) setPill(els.manualPill, 'running', 'En ejecución');
  else if (data.ok === true) setPill(els.manualPill, 'ok', 'Finalizado');
  else if (data.ok === false) setPill(els.manualPill, 'error', 'Con error');
  else setPill(els.manualPill, 'neutral', 'Listo');
  setText(els.manualStatusText, data.mensaje || (data.running ? 'En ejecución' : 'Listo'));
  setText(els.manualPid, data.pid || '—');
  setText(els.manualStarted, data.started_at || '—');
  setText(els.manualRc, data.return_code ?? '—');
}

function renderFiles(data) {
  const excel = data.excel_control || {};
  const cons = data.excel_consolidado || {};
  const output = data.output || {};
  const desc = data.descargas || {};
  els.infoExcel.textContent = excel.exists ? `${sizeHuman(excel.size)} · ${excel.modified_at}` : 'No existe todavía';
  els.infoConsolidado.textContent = cons.exists ? `${sizeHuman(cons.size)} · ${cons.modified_at}` : 'No existe todavía';
  els.infoOutput.textContent = output.exists ? `${output.files || 0} archivos · ${sizeHuman(output.size)}` : 'No existe todavía';
  els.infoDescargas.textContent = desc.exists ? `${desc.files || 0} archivos · ${sizeHuman(desc.size)}` : 'No existe todavía';
}

function renderHistoryRow(item) {
  const ok = item.ok === true || item.return_code === 0;
  const code = item.return_code_main ?? item.return_code ?? '—';
  const title = item.fecha || item.started_at || item.run_id || item.archivo || '—';
  const msg = item.mensaje || item.input_file || item.log_path || item.archivo || '';
  return `<div class="history-row">
    <div class="history-icon ${ok ? '' : 'err'}">${ok ? '✓' : '!'}</div>
    <div><strong>${escapeHtml(title)}</strong><span>${escapeHtml(msg)}</span></div>
    <div class="history-code">Código ${escapeHtml(code)}</div>
  </div>`;
}
function renderHistory(data) {
  const daily = data.daily || [];
  const manual = data.manual || [];
  els.dailyHistory.className = daily.length ? 'history-list' : 'history-list empty';
  els.dailyHistory.innerHTML = daily.length ? daily.map(renderHistoryRow).join('') : 'Sin historial diario.';
  els.manualHistory.className = manual.length ? 'history-list' : 'history-list empty';
  els.manualHistory.innerHTML = manual.length ? manual.map(renderHistoryRow).join('') : 'Sin historial manual.';
}

async function refreshAll(silent = false) {
  try {
    const [cfg, estado, systemd, manual, archivos] = await Promise.all([
      fetchJson('/api/config').catch(() => ({})),
      fetchJson('/api/estado').catch(() => ({})),
      fetchJson('/api/systemd').catch(() => null),
      fetchJson('/api/manual/estado').catch(() => ({})),
      fetchJson('/api/archivos').catch(() => null),
    ]);
    if (cfg.timer_hora) els.timerHora.value = cfg.timer_hora;
    renderEstado(estado);
    if (systemd) renderSystemd(systemd);
    renderManual(manual);
    if (archivos) renderFiles(archivos);

    fetchJson('/api/resumen/ultimo')
      .then(renderSummary)
      .catch(() => {
        els.dailySummary.className = 'summary-grid empty';
        els.dailySummary.textContent = 'Aún no hay resumen disponible.';
      });
    els.lastRefresh.textContent = `Actualizado ${new Date().toLocaleTimeString('es-MX')}`;
  } catch (err) {
    if (!silent) showToast(`No se pudo actualizar: ${err.message}`, 'error');
  }
}
async function refreshFiles() {
  try { renderFiles(await fetchJson('/api/archivos')); }
  catch (err) { showToast(`No se pudieron consultar salidas: ${err.message}`, 'error'); }
}
async function refreshHistory() {
  try { renderHistory(await fetchJson('/api/historial')); }
  catch (err) { showToast(`No se pudo cargar historial: ${err.message}`, 'error'); }
}

function registroValue() {
  return (els.registroInput?.value || '').trim().toUpperCase();
}
function renderRegistroResult(data) {
  if (!els.registroResult) return;
  if (!data.total) {
    els.registroResult.className = 'registro-result empty';
    els.registroResult.textContent = `No encontré carpetas o metadata para ${data.registro}.`;
    return;
  }
  els.registroResult.className = 'registro-result';
  const rows = (data.items || []).map(item => `<li><strong>${escapeHtml(item.raiz)}</strong> · ${escapeHtml(item.relpath)}</li>`).join('');
  els.registroResult.innerHTML = `<div><strong>${escapeHtml(data.registro)}</strong>: ${data.total} coincidencia(s).</div><ul>${rows}</ul>`;
}
async function buscarRegistro() {
  const registro = registroValue();
  if (!registro) { showToast('Escribe un número de registro.', 'warn'); return; }
  const tipo = els.registroTipo?.value || 'auto';
  els.registroBuscarBtn.disabled = true;
  try {
    const data = await fetchJson(`/api/registros/${encodeURIComponent(registro)}/buscar?tipo=${encodeURIComponent(tipo)}`);
    renderRegistroResult(data);
    showToast(data.total ? `Encontré ${data.total} coincidencia(s).` : 'No encontré coincidencias.', data.total ? 'ok' : 'warn');
  } catch (err) {
    if (els.registroResult) {
      els.registroResult.className = 'registro-result empty';
      els.registroResult.textContent = `Error: ${err.message}`;
    }
    showToast(`No se pudo buscar: ${err.message}`, 'error');
  } finally {
    els.registroBuscarBtn.disabled = false;
  }
}
function descargarRegistro() {
  const registro = registroValue();
  if (!registro) { showToast('Escribe un número de registro.', 'warn'); return; }
  const tipo = els.registroTipo?.value || 'auto';
  window.location.href = `/api/registros/${encodeURIComponent(registro)}/download?tipo=${encodeURIComponent(tipo)}`;
}

function updateManualCommand() {
  const kind = els.tipoTxt.value;
  const workers = els.manualWorkers.value || 6;
  const flag = kind === 'registros' ? '--archivo-registro entrada.txt' : '--archivo-folios entrada.txt';
  els.manualCommand.textContent = `main_procesar.py ${flag} --workers ${workers}${els.manualHeadless.checked ? ' --headless' : ''}`;
}
[els.tipoTxt, els.manualWorkers, els.manualHeadless].forEach(el => el.addEventListener('change', updateManualCommand));
updateManualCommand();

async function saveTimer() {
  const hora = els.timerHora.value || '10:00';
  els.saveTimer.disabled = true;
  try {
    const result = await fetchJson('/api/timer/hora', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({hora}),
    });
    const msg = result.install?.message || `Hora guardada: ${hora}`;
    const commands = result.install?.manual_commands ? `\n\nComandos manuales:\n${result.install.manual_commands.join('\n')}` : '';
    els.timerNote.textContent = msg + commands;
    showToast(`Hora guardada: ${hora}`, result.install?.installed ? 'ok' : 'warn');
    await refreshAll(true);
  } catch (err) {
    showToast(`No se pudo guardar hora: ${err.message}`, 'error');
  } finally {
    els.saveTimer.disabled = false;
  }
}

async function startDaily() {
  if (!confirm('¿Ejecutar ahora la tarea diaria satys-diario.service?')) return;
  els.startDaily.disabled = true;
  try {
    await fetchJson('/api/proceso/iniciar', { method: 'POST' });
    showToast('Tarea diaria iniciada.', 'ok');
    await refreshAll(true);
  } catch (err) {
    showToast(`No se pudo ejecutar: ${err.message}`, 'error');
  } finally {
    els.startDaily.disabled = false;
  }
}

async function startManual(event) {
  event.preventDefault();
  if (!els.manualFile.files.length) {
    showToast('Selecciona un TXT primero.', 'warn');
    return;
  }
  if (!confirm('¿Iniciar procesamiento manual con el TXT seleccionado?')) return;
  const data = new FormData();
  data.append('archivo', els.manualFile.files[0]);
  data.append('tipo_txt', els.tipoTxt.value);
  data.append('workers', els.manualWorkers.value || '6');
  data.append('headless', els.manualHeadless.checked ? 'true' : 'false');
  const btn = els.manualForm.querySelector('button[type="submit"]');
  btn.disabled = true;
  try {
    const result = await fetchJson('/api/manual/procesar', { method: 'POST', body: data });
    renderManual(result);
    showToast('Corrida manual iniciada.', 'ok');
    navTo('procesar');
  } catch (err) {
    showToast(`No se pudo iniciar corrida manual: ${err.message}`, 'error');
  } finally {
    btn.disabled = false;
  }
}

function appendLog(box, autoscroll, line) {
  const maxChars = 250000;
  box.textContent += line + '\n';
  if (box.textContent.length > maxChars) box.textContent = box.textContent.slice(-maxChars);
  if (autoscroll.checked) box.scrollTop = box.scrollHeight;
}
function startLogStream(tipo, box, sourceLabel, autoscroll) {
  if (!window.EventSource) {
    appendLog(box, autoscroll, 'Tu navegador no soporta EventSource.');
    return;
  }
  const src = new EventSource(`/api/log/stream?tipo=${tipo}`);
  src.onmessage = (event) => appendLog(box, autoscroll, event.data);
  src.addEventListener('source', (event) => {
    sourceLabel.textContent = `Fuente: ${event.data}`;
    appendLog(box, autoscroll, `── Log: ${event.data} ──`);
  });
  src.addEventListener('status', (event) => { sourceLabel.textContent = event.data; });
  src.onerror = () => { sourceLabel.textContent = 'Conexión de log interrumpida; reintentando…'; };
}

els.refresh.addEventListener('click', () => refreshAll(false));
els.saveTimer.addEventListener('click', saveTimer);
els.startDaily.addEventListener('click', startDaily);
els.manualForm.addEventListener('submit', startManual);
if (els.registroBuscarBtn) els.registroBuscarBtn.addEventListener('click', buscarRegistro);
if (els.registroDownloadBtn) els.registroDownloadBtn.addEventListener('click', descargarRegistro);
if (els.registroInput) els.registroInput.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') buscarRegistro(); });

refreshAll(true);
window.setInterval(() => refreshAll(true), 5000);
startLogStream('diario', els.dailyLogBox, els.dailyLogSource, els.dailyAutoscroll);
startLogStream('manual', els.manualLogBox, els.manualLogSource, els.manualAutoscroll);

// Theme Toggle Logic
const themeToggleBtn = document.getElementById('theme-toggle');
if (themeToggleBtn) {
  const currentTheme = localStorage.getItem('theme') || 'light';
  if (currentTheme === 'dark') document.documentElement.setAttribute('data-theme', 'dark');
  
  const updateIcon = () => {
    const icon = themeToggleBtn.querySelector('i');
    const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
    icon.className = isDark ? 'ph ph-sun' : 'ph ph-moon';
  };
  updateIcon();

  themeToggleBtn.addEventListener('click', () => {
    const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
    if (isDark) {
      document.documentElement.removeAttribute('data-theme');
      localStorage.setItem('theme', 'light');
    } else {
      document.documentElement.setAttribute('data-theme', 'dark');
      localStorage.setItem('theme', 'dark');
    }
    updateIcon();
  });
}

