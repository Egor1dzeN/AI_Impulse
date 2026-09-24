import hljs from 'highlight.js/lib/core';
import javascript from 'highlight.js/lib/languages/javascript';
import typescript from 'highlight.js/lib/languages/typescript';
import python from 'highlight.js/lib/languages/python';
import bash from 'highlight.js/lib/languages/bash';
import json from 'highlight.js/lib/languages/json';
import xml from 'highlight.js/lib/languages/xml';
import css from 'highlight.js/lib/languages/css';
import sql from 'highlight.js/lib/languages/sql';
import yaml from 'highlight.js/lib/languages/yaml';
import cpp from 'highlight.js/lib/languages/cpp';
import csharp from 'highlight.js/lib/languages/csharp';
import java from 'highlight.js/lib/languages/java';
import go from 'highlight.js/lib/languages/go';
import rust from 'highlight.js/lib/languages/rust';
import php from 'highlight.js/lib/languages/php';
import { translate } from './i18n.jsx';

hljs.registerLanguage('javascript', javascript);
hljs.registerLanguage('typescript', typescript);
hljs.registerLanguage('python', python);
hljs.registerLanguage('bash', bash);
hljs.registerLanguage('json', json);
hljs.registerLanguage('xml', xml);
hljs.registerLanguage('css', css);
hljs.registerLanguage('sql', sql);
hljs.registerLanguage('yaml', yaml);
hljs.registerLanguage('cpp', cpp);
hljs.registerLanguage('csharp', csharp);
hljs.registerLanguage('java', java);
hljs.registerLanguage('go', go);
hljs.registerLanguage('rust', rust);
hljs.registerLanguage('php', php);

// Common fence-language aliases models actually write, normalized to the
// canonical names registered above (also the keys LANG_LABELS uses).
const LANG_ALIASES = {
  js: 'javascript', jsx: 'javascript', mjs: 'javascript', cjs: 'javascript',
  ts: 'typescript', tsx: 'typescript',
  py: 'python', py3: 'python',
  sh: 'bash', shell: 'bash', zsh: 'bash', console: 'bash',
  html: 'xml', htm: 'xml',
  yml: 'yaml',
  'c++': 'cpp', cc: 'cpp', cxx: 'cpp', c: 'cpp',
  'c#': 'csharp', cs: 'csharp',
};

const LANG_LABELS = {
  javascript: 'JavaScript',
  typescript: 'TypeScript',
  python: 'Python',
  bash: 'Bash',
  json: 'JSON',
  xml: 'HTML',
  css: 'CSS',
  sql: 'SQL',
  yaml: 'YAML',
  cpp: 'C++',
  csharp: 'C#',
  java: 'Java',
  go: 'Go',
  rust: 'Rust',
  php: 'PHP',
};

const esc = (s) =>
  s.replace(/[&<>"]/g, (c) => (c === '&' ? '&amp;' : c === '<' ? '&lt;' : c === '>' ? '&gt;' : '&quot;'));

// Highlights a fenced code block. `lang` is whatever followed the opening
// ``` (may be empty, unknown, or an alias like "js"/"py" — hljs resolves
// those via each language's own `aliases` list). Falls back to
// auto-detection, then to plain escaped text if even that fails.
function highlight(code, lang) {
  if (lang && hljs.getLanguage(lang)) {
    return { html: hljs.highlight(code, { language: lang, ignoreIllegals: true }).value, lang };
  }
  try {
    const auto = hljs.highlightAuto(code);
    if (auto.language) return { html: auto.value, lang: auto.language };
  } catch {
    // fall through to plain text
  }
  return { html: esc(code), lang: '' };
}

// Bare http(s) URLs in already-escaped HTML become links; existing anchors
// and inline code are left alone. Trailing punctuation stays outside the link.
function autolink(html) {
  return html
    .split(/(<a [^>]*>.*?<\/a>|<code>.*?<\/code>)/g)
    .map((part, i) =>
      i % 2
        ? part
        : part.replace(/https?:\/\/[^\s<]+/g, (raw) => {
            const [, url, tail] = raw.match(/^(.*?)((?:[.,;:!?)»]|&quot;)*)$/);
            return `<a href="${url}" target="_blank" rel="noopener noreferrer">${url}</a>${tail}`;
          })
    )
    .join('');
}

export function md(src, lang = 'ru') {
  const inline = (s) =>
    autolink(
      esc(s)
        .replace(/`([^`]+)`/g, '<code>$1</code>')
        .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
        // [label](https://url) -> a real link. Restricted to http(s) so a
        // model can't smuggle a javascript: URL into an anchor href.
        .replace(
          /\[([^\]]+)\]\((https?:\/\/[^\s")]+)\)/g,
          '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>'
        )
    );

  const blocks = [];
  src = src.replace(/```([a-zA-Z0-9_+-]*)\n?([\s\S]*?)(?:```|$)/g, (m, rawLang, code) => {
    const normalized = rawLang.toLowerCase();
    const { html, lang: fenceLang } = highlight(code.replace(/\n$/, ''), LANG_ALIASES[normalized] || normalized);
    const label = LANG_LABELS[fenceLang] || translate(lang, 'chat.code');
    blocks.push(
      `<div class="codeblock">` +
        `<div class="codeblock__bar">` +
          `<span class="codeblock__lang">${esc(label)}</span>` +
          `<button type="button" class="codeblock__copy">${esc(translate(lang, 'chat.copy'))}</button>` +
        `</div>` +
        `<pre class="code"><code class="hljs language-${fenceLang || 'plaintext'}">${html}</code></pre>` +
      `</div>`
    );
    return `@@code${blocks.length - 1}@@`;
  });

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
