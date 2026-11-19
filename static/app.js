const element = id => document.getElementById(id);
let displayedResults = 0;
let requestingStart = false;
let displayedDrops = 0;

function renderWorkers(id, workers) {
  const target = element(id);
  target.replaceChildren();
  for (const [name, activity] of Object.entries(workers)) {
    const row = document.createElement('div');
    row.textContent = `${name}: ${activity}`;
    target.append(row);
  }
}

function renderLog(worker, entries) {
  const log = element(`${worker}-log`);
  log.replaceChildren();
  for (const entry of entries) {
    const line = document.createElement('div');
    const time = document.createElement('time');
    time.textContent = `${entry.time.toFixed(1)}s`;
    line.append(time, document.createTextNode(entry.message));
    log.append(line);
  }
  log.scrollTop = log.scrollHeight;
}

function render(state) {
  const running = state.status === 'running';
  for (const id of ['start', 'advanced']) element(id).disabled = running || requestingStart;
  const advanced = state.mode === 'advanced';
  element('pace').textContent = advanced ? 'Advanced: 4 fetchers × 2.5/s · each consumer 2/s' : 'Basic: 5 fetched/s · 4 parsed/s';
  element('producer-rate').textContent = advanced ? '4 × 2.5 / sec' : '5 / sec';
  element('consumer-rate').textContent = advanced ? `${state.consumer_count} × 2 / sec` : '4 / sec';
  element('capacity').textContent = `${state.capacity} capacity`;
  element('policy').textContent = advanced ? 'Drop oldest · no backpressure' : 'Backpressure enabled';
  element('queue-note').textContent = advanced ? 'At capacity, each arrival replaces the oldest waiting page. After five drops, a simulated DevOps intervention starts Consumer 2. Trimming stays active.' : 'The producer is faster, so the queue grows. After fetching finishes, the consumer drains it.';
  renderWorkers('fetch-workers', state.fetch_workers);
  renderWorkers('consumer-workers', state.consumers);
  element('intervention').hidden = !advanced;
  element('intervention').textContent = state.intervention ? `${state.intervention.time.toFixed(1)}s — ${state.intervention.message}` : 'Advanced scenario: Consumer 2 starts automatically after the fifth oldest-page drop.';
  element('drop-count').textContent = state.dropped.length;
  element('drop-summary').hidden = !advanced;
  element('drop-summary').textContent = `${state.dropped.length} oldest pages dropped · ${state.consumer_count} active/started consumers`;
  element('accounting').textContent = `Accounted for: ${state.completed} processed + ${state.dropped.length} dropped = ${state.completed + state.dropped.length} / ${state.total} inputs`;
  element('status').textContent = running && state.producer_done ? 'Draining queue' : {ready:'Ready', running:'Running', finished:'Complete', failed:'Failed'}[state.status];
  element('elapsed').textContent = `${state.elapsed.toFixed(1)}s elapsed`;
  for (const field of ['produced', 'completed']) element(field).textContent = state[field];
  element('depth').textContent = state.pending.length;
  element('peak').textContent = `Peak: ${state.peak}`;
  element('link-count').textContent = state.links;
  element('error-count').textContent = state.errors;
  for (const worker of ['producer', 'consumer']) {
    element(`${worker}-activity`).textContent = advanced && running ? (worker === 'producer' ? (state.producer_done ? state.producer : 'Four concurrent fetch workers') : `${state.consumer_count} consumers sharing one queue`) : state[worker];
    element(`${worker}-card`).classList.toggle('working', running && !state[`${worker}_done`]);
    element(`${worker}-progress`).value = worker === 'producer' ? state.produced : state.completed;
    renderLog(worker, state[`${worker}_log`]);
  }
  const slots = element('queue-slots');
  if (slots.children.length !== state.capacity) slots.replaceChildren();
  while (slots.children.length < state.capacity) {
    const slot = document.createElement('span');
    slot.className = 'slot';
    slots.append(slot);
  }
  [...slots.children].forEach((slot, index) => {
    slot.classList.toggle('filled', index < state.pending.length);
    slot.title = state.pending[index] || 'Empty slot';
  });
  slots.setAttribute('aria-label', `${state.pending.length} pages waiting, capacity ${state.capacity}`);
  if (state.results.length < displayedResults) {
    element('results').replaceChildren();
    displayedResults = 0;
  }
  for (const result of state.results.slice(displayedResults)) {
    const card = document.createElement('article');
    card.className = `result${result.error ? ' error' : ''}`;
    for (const [className, text] of [
      ['result-title', result.title],
      ['result-meta', `${result.filename} · ${result.consumer ? result.consumer + ' · ' : ''}${result.error ? 'Failed' : result.links.length + ' links extracted'}`],
      ['result-links', result.error || result.links.join(' · ')]
    ]) {
      const line = document.createElement('div');
      line.className = className;
      line.textContent = text;
      card.append(line);
    }
    element('results').prepend(card);
  }
  displayedResults = state.results.length;
  element('empty').hidden = displayedResults > 0;
  element('drop-panel').hidden = !advanced;
  if (state.dropped.length < displayedDrops) { element('dropped-pages').replaceChildren(); displayedDrops = 0; }
  for (const drop of state.dropped.slice(displayedDrops)) {
    const row = document.createElement('div');
    row.textContent = `${drop.time.toFixed(1)}s · ${drop.filename} · ${drop.reason} (${drop.consumers} consumer${drop.consumers === 1 ? '' : 's'})`;
    element('dropped-pages').prepend(row);
  }
  displayedDrops = state.dropped.length;
}

async function startDemo(mode) {
  requestingStart = true;
  element('start').disabled = true;
  try {
    const response = await fetch('/api/start', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({mode})});
    if (!response.ok) throw new Error((await response.json()).error || 'Could not start');
    element('results').replaceChildren();
    displayedResults = 0;
    element('dropped-pages').replaceChildren();
    displayedDrops = 0;
  } catch (error) {
    element('status').textContent = error.message;
  } finally {
    requestingStart = false;
  }
}
element('start').addEventListener('click', () => startDemo('basic'));
element('advanced').addEventListener('click', () => startDemo('advanced'));

async function poll() {
  try {
    const response = await fetch('/api/state', {cache:'no-store'});
    if (!response.ok) throw new Error('State request failed');
    render(await response.json());
  } catch (error) {
    element('status').textContent = 'Connection lost — check the Flask terminal';
    element('start').disabled = true;
    element('advanced').disabled = true;
  }
  setTimeout(poll, 200); // Schedule after completion: no overlapping requests.
}
poll();
