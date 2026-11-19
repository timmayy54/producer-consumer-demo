const element = id => document.getElementById(id);
let displayedResults = 0;
let requestingStart = false;

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
  element('start').disabled = running || requestingStart;
  element('start').textContent = running ? 'Demonstration running…' : state.status === 'ready' ? 'Start demonstration' : 'Run again';
  element('status').textContent = running && state.producer_done ? 'Draining queue' : {ready:'Ready', running:'Running', finished:'Complete', failed:'Failed'}[state.status];
  element('elapsed').textContent = `${state.elapsed.toFixed(1)}s elapsed`;
  for (const field of ['produced', 'completed']) element(field).textContent = state[field];
  element('depth').textContent = state.pending.length;
  element('peak').textContent = `Peak: ${state.peak}`;
  element('link-count').textContent = state.links;
  element('error-count').textContent = state.errors;
  for (const worker of ['producer', 'consumer']) {
    element(`${worker}-activity`).textContent = state[worker];
    element(`${worker}-card`).classList.toggle('working', running && !state[`${worker}_done`]);
    element(`${worker}-progress`).value = worker === 'producer' ? state.produced : state.completed;
    renderLog(worker, state[`${worker}_log`]);
  }
  const slots = element('queue-slots');
  while (slots.children.length < 100) {
    const slot = document.createElement('span');
    slot.className = 'slot';
    slots.append(slot);
  }
  [...slots.children].forEach((slot, index) => {
    slot.classList.toggle('filled', index < state.pending.length);
    slot.title = state.pending[index] || 'Empty slot';
  });
  slots.setAttribute('aria-label', `${state.pending.length} pages waiting, capacity 100`);
  if (state.results.length < displayedResults) {
    element('results').replaceChildren();
    displayedResults = 0;
  }
  for (const result of state.results.slice(displayedResults)) {
    const card = document.createElement('article');
    card.className = `result${result.error ? ' error' : ''}`;
    for (const [className, text] of [
      ['result-title', result.title],
      ['result-meta', `${result.filename} · ${result.error ? 'Failed' : result.links.length + ' links extracted'}`],
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
}

element('start').addEventListener('click', async () => {
  requestingStart = true;
  element('start').disabled = true;
  try {
    const response = await fetch('/api/start', {method:'POST'});
    if (!response.ok) throw new Error((await response.json()).error || 'Could not start');
    element('results').replaceChildren();
    displayedResults = 0;
  } catch (error) {
    element('status').textContent = error.message;
  } finally {
    requestingStart = false;
  }
});

async function poll() {
  try {
    const response = await fetch('/api/state', {cache:'no-store'});
    if (!response.ok) throw new Error('State request failed');
    render(await response.json());
  } catch (error) {
    element('status').textContent = 'Connection lost — check the Flask terminal';
    element('start').disabled = true;
  }
  setTimeout(poll, 200); // Schedule after completion: no overlapping requests.
}
poll();
