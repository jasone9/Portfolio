const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// Execute the actual inline form handler, with only browser APIs replaced.
const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const script = html.match(/const contactForm=[\s\S]*?(?=<\/script>)/)[0];
const KEY = '11111111-2222-4333-8444-555555555555';

function harness({ key = KEY, botcheck = null, valid = true, fetchImpl } = {}) {
  const values = { access_key: key, name: 'José Example', email: 'visitor@example.com',
    message: 'Hello Jason!\nLet’s talk.', subject: 'New message from Jason Escalante\'s portfolio', botcheck };
  const elements = new Map();
  function element() {
    return { textContent: '', dataset: {}, attributes: {}, disabled: false,
      classList: { add() {}, remove() {} },
      setAttribute(k, v) { this.attributes[k] = v; },
      removeAttribute(k) { delete this.attributes[k]; },
      focus() { this.focused = true; } };
  }
  for (const id of ['contactForm', 'contactSubmit', 'contactStatus', 'contact-name',
    'contact-email', 'contact-message', 'nameError', 'emailError', 'messageError']) elements.set(id, element());
  const form = elements.get('contactForm');
  let handler, resetCount = 0, timer;
  form.addEventListener = (name, fn) => { assert.equal(name, 'submit'); handler = fn; };
  form.reportValidity = () => valid;
  form.reset = () => { resetCount++; values.name = ''; values.email = ''; values.message = ''; };
  const calls = [];
  vm.runInNewContext(script, {
    document: { getElementById: id => elements.get(id) },
    FormData: class { get(name) { return values[name] ?? null; } },
    AbortController,
    setTimeout(fn, delay) { assert.equal(delay, 15000); timer = fn; return 1; },
    clearTimeout() { timer = null; },
    fetch: async (url, options) => {
      calls.push({ url, options });
      return fetchImpl ? fetchImpl(url, options) : { ok: true, json: async () => ({ success: true }) };
    },
  });
  return { elements, values, calls, form, submit: () => handler({ preventDefault() {} }),
    status: elements.get('contactStatus'), button: elements.get('contactSubmit'),
    get resetCount() { return resetCount; }, expire: () => timer() };
}

test('posts visitor fields and public key directly to Web3Forms, then resets on confirmed success', async () => {
  const h = harness();
  await h.submit();
  assert.equal(h.calls.length, 1);
  const { url, options } = h.calls[0];
  assert.equal(url, 'https://api.web3forms.com/submit');
  assert.equal(options.credentials, 'omit');
  assert.equal(options.mode, 'cors');
  assert.deepEqual(JSON.parse(options.body), { access_key: KEY, name: 'José Example',
    email: 'visitor@example.com', message: 'Hello Jason!\nLet’s talk.',
    subject: 'New message from Jason Escalante\'s portfolio', botcheck: false });
  assert.equal(h.resetCount, 1);
  assert.equal(h.status.dataset.state, 'success');
  assert.equal(h.button.disabled, false);
  assert.equal(h.form.attributes['aria-busy'], undefined);
});

test('blank, placeholder, and malformed keys never make a network request', async () => {
  for (const key of ['', 'YOUR_ACCESS_KEY_HERE', 'not-a-key']) {
    const h = harness({ key });
    await h.submit();
    assert.equal(h.calls.length, 0);
    assert.equal(h.resetCount, 0);
    assert.match(h.status.textContent, /isn’t configured/);
  }
});

test('invalid form and honeypot submissions do not send', async () => {
  for (const options of [{ valid: false }, { botcheck: 'on' }]) {
    const h = harness(options);
    await h.submit();
    assert.equal(h.calls.length, 0);
    assert.equal(h.resetCount, 0);
  }
});

test('whitespace-only name and message are rejected before contacting Web3Forms', async () => {
  for (const name of ['name', 'message']) {
    const h = harness();
    h.values[name] = '   ';
    await h.submit();
    assert.equal(h.calls.length, 0);
    assert.equal(h.elements.get('contact-' + name).attributes['aria-invalid'], 'true');
    assert.equal(h.elements.get('contact-' + name).focused, true);
  }
});

test('HTTP errors, provider failures, and non-boolean success preserve the message', async () => {
  for (const [ok, result] of [
    [false, { success: false, message: 'Rate limit reached' }],
    [false, { success: true }],
    [true, { success: false, body: { message: 'Invalid access key' } }],
    [true, { success: 'true' }], [true, { ok: true }], [true, null],
  ]) {
    const h = harness({ fetchImpl: async () => ({ ok, json: async () => result }) });
    await h.submit();
    assert.equal(h.status.dataset.state, 'error');
    assert.equal(h.resetCount, 0);
    assert.equal(h.values.message, 'Hello Jason!\nLet’s talk.');
    assert.equal(h.button.disabled, false);
  }
});

test('provider errors remain text and can focus the invalid field', async () => {
  const h = harness({ fetchImpl: async () => ({ ok: false, json: async () => ({ success: false,
    message: '<img src=x onerror=alert(1)>', errors: { email: 'Check your email' } }) }) });
  await h.submit();
  assert.equal(h.status.textContent, '<img src=x onerror=alert(1)>');
  assert.equal(h.elements.get('contact-email').attributes['aria-invalid'], 'true');
  assert.equal(h.elements.get('contact-email').focused, true);
});

test('network and invalid-JSON errors preserve fields and restore the button', async () => {
  for (const fetchImpl of [
    async () => { throw new Error('offline'); },
    async () => ({ ok: true, json: async () => { throw new Error('not JSON'); } }),
  ]) {
    const h = harness({ fetchImpl });
    await h.submit();
    assert.equal(h.resetCount, 0);
    assert.equal(h.status.dataset.state, 'error');
    assert.match(h.status.textContent, /Unable to confirm/);
    assert.equal(h.button.disabled, false);
  }
});

test('duplicate clicks cannot submit while a request is pending', async () => {
  let finish;
  const h = harness({ fetchImpl: () => new Promise(resolve => { finish = resolve; }) });
  const pending = h.submit();
  assert.equal(h.button.disabled, true);
  assert.equal(h.form.attributes['aria-busy'], 'true');
  await h.submit();
  assert.equal(h.calls.length, 1);
  finish({ ok: true, json: async () => ({ success: true }) });
  await pending;
  assert.equal(h.resetCount, 1);
});

test('timeout cancels the request and restores the form without claiming delivery', async () => {
  const h = harness({ fetchImpl: (url, options) => new Promise((resolve, reject) => {
    options.signal.addEventListener('abort', () => reject(new Error('timeout')));
  }) });
  const pending = h.submit();
  h.expire();
  await pending;
  assert.equal(h.status.dataset.state, 'error');
  assert.equal(h.resetCount, 0);
  assert.equal(h.button.disabled, false);
});
