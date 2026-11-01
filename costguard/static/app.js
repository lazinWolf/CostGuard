// Shared helpers for CostGuard pages. Nothing here knows about a specific page.

const escapeHtml = text => String(text ?? '').replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));

// Call the CostGuard API; failures throw an Error carrying the server's message.
async function api(path, body, method) {
  const response = await fetch('/api/v1/' + path, {
    method: method || (body === undefined ? 'GET' : 'POST'),
    headers: {'Content-Type': 'application/json'},
    body: body === undefined ? undefined : JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) throw Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail));
  return data;
}

// Prefill price fields with the OpenRouter reference rate for a gateway model ID and say where they came from.
// A model without a listed rate keeps what the person typed, but not a reference rate left from another model.
async function fillReferenceRate(model, fields, note) {
  let rate = null;
  try { rate = await api('reference-prices?model=' + encodeURIComponent(model.trim())); } catch (error) {}  // null when unlisted
  const filled = [fields.input, fields.output, fields.version].filter(Boolean);
  if (rate) {
    fields.input.value = rate.input_per_million_usd;
    fields.output.value = rate.output_per_million_usd;
    if (fields.version) fields.version.value = rate.version;
    filled.forEach(field => field.dataset.reference = '1');
  } else if (fields.input.dataset.reference) {
    // Another model's reference rate must not stay behind under this model's name.
    filled.forEach(field => { field.value = ''; delete field.dataset.reference; });
  }
  note.textContent = rate
    ? `OpenRouter rate for ${rate.model}: $${rate.input_per_million_usd} in / $${rate.output_per_million_usd} out per million tokens.`
    : 'No reference price is listed for this model: enter the provider\'s price.';
  return rate;
}

// Disable every button while `work` runs; show its failure in `#message` if there is one.
async function busy(work) {
  const buttons = document.querySelectorAll('button');
  const message = document.getElementById('message');
  buttons.forEach(button => button.disabled = true);
  try { await work(); }
  catch (error) { if (message) { message.hidden = false; message.textContent = error.message; } }
  buttons.forEach(button => button.disabled = false);
}

// Reload when `read()` reports something other than `seen`; used to follow queued work.
function reloadOnChange(read, seen, interval = 1500) {
  const tick = async () => {
    try { if (JSON.stringify(await read()) !== JSON.stringify(seen)) return location.reload(); } catch (error) {}
    setTimeout(tick, interval);
  };
  setTimeout(tick, interval);
}

// Word-level diff of two prompts: removed words in `left`, added words in `right`.
function diffWords(leftEl, rightEl) {
  const a = leftEl.textContent.split(/(\s+)/), b = rightEl.textContent.split(/(\s+)/);
  const n = a.length, m = b.length, dp = Array.from({length: n + 1}, () => new Uint16Array(m + 1));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--)
    dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  let i = 0, j = 0, left = '', right = '';
  while (i < n && j < m) {
    if (a[i] === b[j]) { left += escapeHtml(a[i]); right += escapeHtml(b[j]); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) { left += /^\s+$/.test(a[i]) ? escapeHtml(a[i]) : '<del>' + escapeHtml(a[i]) + '</del>'; i++; }
    else { right += /^\s+$/.test(b[j]) ? escapeHtml(b[j]) : '<ins>' + escapeHtml(b[j]) + '</ins>'; j++; }
  }
  while (i < n) left += '<del>' + escapeHtml(a[i++]) + '</del>';
  while (j < m) right += '<ins>' + escapeHtml(b[j++]) + '</ins>';
  leftEl.innerHTML = left; rightEl.innerHTML = right;
}
document.querySelectorAll('[data-diff-left]').forEach(el => {
  const other = document.getElementById(el.dataset.diffLeft);
  if (other) diffWords(el, other);
});
