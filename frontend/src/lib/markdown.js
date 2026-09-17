export function md(src) {
  const esc = (s) =>
    s.replace(/[&<>]/g, (c) => (c === '&' ? '&amp;' : c === '<' ? '&lt;' : '&gt;'));

  const blocks = [];
  src = src.replace(/```[a-z]*\n?([\s\S]*?)(?:```|$)/g, (m, code) => {
    blocks.push(`<pre class="code"><code>${esc(code.replace(/\n$/, ''))}</code></pre>`);
    return `@@code${blocks.length - 1}@@`;
  });

  const inline = (s) =>
    esc(s)
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');

  let out = '';
  let list = false;
  const lines = src.split('\n');

  for (const rawLine of lines) {
    const line = rawLine.trim();
    const ph = line.match(/^@@code(\d+)@@$/);
    if (ph) {
      if (list) { out += '</ul>'; list = false; }
      out += blocks[+ph[1]];
      continue;
    }
    if (!line) {
      if (list) { out += '</ul>'; list = false; }
      continue;
    }
    const li = line.match(/^[-•]\s+(.*)$/);
    if (li) {
      if (!list) { out += '<ul>'; list = true; }
      out += `<li>${inline(li[1])}</li>`;
      continue;
    }
    if (list) { out += '</ul>'; list = false; }
    out += `<p>${inline(line)}</p>`;
  }
  if (list) out += '</ul>';
  return out;
}
