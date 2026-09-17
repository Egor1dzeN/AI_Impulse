/* Собирает однофайловую версию страницы для публикации как Artifact:
   без doctype/html/head/body — эту обёртку добавляет сам хостинг.
   Запуск: node build.js  →  dist/impulse.html                        */

const fs = require('fs');
const path = require('path');

const root = __dirname;
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const css = fs.readFileSync(path.join(root, 'styles.css'), 'utf8');
const js = fs.readFileSync(path.join(root, 'app.js'), 'utf8');

const body = html.match(/<body>([\s\S]*?)<\/body>/)[1]
  .replace(/\s*<script src="app\.js"><\/script>/, '')
  .trim();

const fonts = html.match(/<link href="https:\/\/fonts\.googleapis[^>]*>/)[0];
const three = html.match(/<script src="https:\/\/cdnjs[^>]*><\/script>/)[0];

const out = [
  '<title>Impulse</title>',
  '<link rel="preconnect" href="https://fonts.googleapis.com">',
  '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>',
  fonts,
  '<style>',
  css.trim(),
  '</style>',
  '',
  body,
  '',
  three,
  '<script>',
  js.trim(),
  '</script>',
  ''
].join('\n');

fs.mkdirSync(path.join(root, 'dist'), { recursive: true });
fs.writeFileSync(path.join(root, 'dist', 'impulse.html'), out);
console.log('dist/impulse.html — ' + out.length + ' байт');
