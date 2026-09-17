/* ── Impulse ──────────────────────────────────────────────────────────
   1. Core   — облако точек на сфере, смещённое шумом в вершинном
              шейдере. Курсор поворачивает ядро и выдавливает бугор;
              пока идёт ответ — амплитуда и скорость растут.
   2. Chat   — интерфейс переписки. Ответы демонстрационные: смотри
              replyFor(), туда же подставляется реальный запрос к API.
   ─────────────────────────────────────────────────────────────────── */

(function () {
  'use strict';

  var reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var $ = function (s) { return document.querySelector(s); };
  var sleep = function (ms) { return new Promise(function (r) { setTimeout(r, ms); }); };

  /* ── 1. Ядро ─────────────────────────────────────────────────────── */

  var NOISE = [
    'vec3 mod289(vec3 x){return x-floor(x*(1.0/289.0))*289.0;}',
    'vec4 mod289(vec4 x){return x-floor(x*(1.0/289.0))*289.0;}',
    'vec4 permute(vec4 x){return mod289(((x*34.0)+1.0)*x);}',
    'vec4 taylorInvSqrt(vec4 r){return 1.79284291400159-0.85373472095314*r;}',
    'float snoise(vec3 v){',
    '  const vec2 C=vec2(1.0/6.0,1.0/3.0); const vec4 D=vec4(0.0,0.5,1.0,2.0);',
    '  vec3 i=floor(v+dot(v,C.yyy)); vec3 x0=v-i+dot(i,C.xxx);',
    '  vec3 g=step(x0.yzx,x0.xyz); vec3 l=1.0-g;',
    '  vec3 i1=min(g.xyz,l.zxy); vec3 i2=max(g.xyz,l.zxy);',
    '  vec3 x1=x0-i1+C.xxx; vec3 x2=x0-i2+C.yyy; vec3 x3=x0-D.yyy;',
    '  i=mod289(i);',
    '  vec4 p=permute(permute(permute(i.z+vec4(0.0,i1.z,i2.z,1.0))',
    '        +i.y+vec4(0.0,i1.y,i2.y,1.0))+i.x+vec4(0.0,i1.x,i2.x,1.0));',
    '  float n_=0.142857142857; vec3 ns=n_*D.wyz-D.xzx;',
    '  vec4 j=p-49.0*floor(p*ns.z*ns.z);',
    '  vec4 x_=floor(j*ns.z); vec4 y_=floor(j-7.0*x_);',
    '  vec4 x=x_*ns.x+ns.yyyy; vec4 y=y_*ns.x+ns.yyyy; vec4 h=1.0-abs(x)-abs(y);',
    '  vec4 b0=vec4(x.xy,y.xy); vec4 b1=vec4(x.zw,y.zw);',
    '  vec4 s0=floor(b0)*2.0+1.0; vec4 s1=floor(b1)*2.0+1.0; vec4 sh=-step(h,vec4(0.0));',
    '  vec4 a0=b0.xzyw+s0.xzyw*sh.xxyy; vec4 a1=b1.xzyw+s1.xzyw*sh.zzww;',
    '  vec3 p0=vec3(a0.xy,h.x); vec3 p1=vec3(a0.zw,h.y);',
    '  vec3 p2=vec3(a1.xy,h.z); vec3 p3=vec3(a1.zw,h.w);',
    '  vec4 norm=taylorInvSqrt(vec4(dot(p0,p0),dot(p1,p1),dot(p2,p2),dot(p3,p3)));',
    '  p0*=norm.x; p1*=norm.y; p2*=norm.z; p3*=norm.w;',
    '  vec4 m=max(0.6-vec4(dot(x0,x0),dot(x1,x1),dot(x2,x2),dot(x3,x3)),0.0); m=m*m;',
    '  return 42.0*dot(m*m,vec4(dot(p0,x0),dot(p1,x1),dot(p2,x2),dot(p3,x3)));',
    '}'
  ].join('\n');

  var HEAD = [
    'uniform float uTime; uniform float uAmp; uniform float uSize;',
    'uniform float uScale; uniform float uDpr; uniform vec3 uPointer;',
    'uniform float uReach;',
    'varying float vD;',
    'varying float vFront;',
    NOISE,
    'vec3 displace(vec3 pos){',
    '  vec3 n = normalize(pos);',
    '  float d = snoise(pos * 1.25 + vec3(0.0, uTime * 0.20, 0.0)) * 0.66',
    '          + snoise(pos * 3.00 - vec3(uTime * 0.15, 0.0, uTime * 0.10)) * 0.26;',
    '  float bulge = smoothstep(0.95, 0.0, distance(n, uPointer)) * uReach;',
    '  vD = clamp(d * 0.62 + 0.5, 0.0, 1.0);',
    '  return pos + n * (d * uAmp + bulge * 0.17);',
    '}'
  ].join('\n');

  var POINT_VERT = HEAD + [
    'void main(){',
    '  vec4 mv = modelViewMatrix * vec4(displace(position), 1.0);',
    '  vFront = clamp((mv.z + 3.9) / 2.0, 0.0, 1.0);',      // 1 — ближняя сторона, 0 — дальняя
    '  float grow = 0.70 + vFront * 0.45 + vD * 0.25;',
    '  gl_PointSize = uSize * uDpr * uScale * grow * (2.2 / -mv.z);',
    '  gl_Position = projectionMatrix * mv;',
    '}'
  ].join('\n');

  var POINT_FRAG = [
    'varying float vD;',
    'varying float vFront;',
    'void main(){',
    '  vec2 c = gl_PointCoord - 0.5;',
    '  float r = dot(c, c);',
    '  if (r > 0.25) discard;',
    '  float a = smoothstep(0.25, 0.06, r);',
    '  vec3 col = mix(vec3(0.760, 0.235, 0.0), vec3(1.0, 0.706, 0.451), vD);',
    '  col = mix(vec3(1.0, 0.855, 0.780), col, 0.35 + vFront * 0.65);',
    '  gl_FragColor = vec4(col, a * (0.26 + vFront * 0.68));',
    '}'
  ].join('\n');

  /* Решётка Фибоначчи: точки ложатся на сферу равномерно, без швов
     триангуляции. Лёгкий разброс сбивает спиральный муар.          */
  function shell(count) {
    var pos = new Float32Array(count * 3);
    var step = Math.PI * (3 - Math.sqrt(5));
    for (var i = 0; i < count; i++) {
      var y = 1 - (i / (count - 1)) * 2;
      var r = Math.sqrt(Math.max(0, 1 - y * y));
      var a = step * i;
      pos[i * 3]     = Math.cos(a) * r + (Math.random() - 0.5) * 0.013;
      pos[i * 3 + 1] = y            + (Math.random() - 0.5) * 0.013;
      pos[i * 3 + 2] = Math.sin(a) * r + (Math.random() - 0.5) * 0.013;
    }
    var geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    return geo;
  }

  var Core = (function () {
    var stage = $('#stage');
    var canvas = $('#core');
    var renderer, scene, camera, tilt, spin, uni;
    var live = false;
    var energy = 0, target = 0;
    var px = 0, py = 0, tpx = 0, tpy = 0, reach = 0, treach = 0;
    var dir = null, quat = null, clock = null;
    var ampScale = 1;

    function flat() {
      stage.classList.add('is-flat');
      if (canvas) canvas.remove();
      return false;
    }

    function init() {
      if (!window.THREE) return flat();
      try {
        renderer = new THREE.WebGLRenderer({ canvas: canvas, alpha: true, antialias: true });
      } catch (e) { return flat(); }

      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
      renderer.setClearAlpha(0);

      scene = new THREE.Scene();
      camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100);
      camera.position.z = 3.05;

      tilt = new THREE.Group();
      spin = new THREE.Group();
      tilt.add(spin);
      scene.add(tilt);

      uni = {
        uTime:    { value: 0 },
        uAmp:     { value: 0.21 },
        uSize:    { value: 3.5 },
        uScale:   { value: 1 },
        uDpr:     { value: renderer.getPixelRatio() },
        uPointer: { value: new THREE.Vector3(0, 0, 1) },
        uReach:   { value: 0 }
      };

      spin.add(new THREE.Points(shell(32000), new THREE.ShaderMaterial({
        uniforms: uni, vertexShader: POINT_VERT, fragmentShader: POINT_FRAG,
        transparent: true, depthWrite: false
      })));

      dir = new THREE.Vector3();
      quat = new THREE.Quaternion();
      clock = new THREE.Clock();
      live = true;

      resize();
      if (window.ResizeObserver) new ResizeObserver(resize).observe(stage);
      else window.addEventListener('resize', resize);

      if (reduce) {
        uni.uTime.value = 1.4;
        renderer.render(scene, camera);
      } else {
        window.addEventListener('pointermove', onMove, { passive: true });
        window.addEventListener('pointerleave', function () { treach = 0; });
        requestAnimationFrame(loop);
      }
      return true;
    }

    function onMove(e) {
      tpx = (e.clientX / window.innerWidth) * 2 - 1;
      tpy = (e.clientY / window.innerHeight) * 2 - 1;
      treach = 1;
    }

    function resize() {
      if (!live) return;
      var w = stage.clientWidth || 1, h = stage.clientHeight || 1;
      renderer.setSize(w, h, false);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      uni.uScale.value = Math.pow(Math.max(h, 8) / 400, 0.35);
      // в маленьком аватаре шум почти выключен — форма должна читаться как круг
      ampScale = Math.max(0.30, Math.min(1, h / 220));
      if (reduce) renderer.render(scene, camera);
    }

    function loop() {
      requestAnimationFrame(loop);
      var dt = Math.min(clock.getDelta(), 0.05);

      energy += (target - energy) * Math.min(dt * 3.4, 1);
      px += (tpx - px) * Math.min(dt * 3.6, 1);
      py += (tpy - py) * Math.min(dt * 3.6, 1);
      reach += (treach - reach) * Math.min(dt * 4.5, 1);

      uni.uTime.value += dt * (1 + energy * 2.6);
      uni.uAmp.value = (0.20 + energy * 0.17) * ampScale;
      uni.uReach.value = reach;

      spin.rotation.y += dt * (0.16 + energy * 0.55);
      tilt.rotation.x = -py * 0.42;
      tilt.rotation.y = px * 0.55;
      camera.position.x = px * 0.16;
      camera.position.y = -py * 0.16;
      camera.lookAt(0, 0, 0);

      // курсор в систему координат объекта — бугор остаётся под мышью
      dir.set(px, -py, 1.35).normalize();
      spin.getWorldQuaternion(quat);
      dir.applyQuaternion(quat.invert());
      uni.uPointer.value.copy(dir);

      renderer.render(scene, camera);
    }

    return {
      init: init,
      setEnergy: function (v) { target = v; },
      resize: resize
    };
  })();

  /* ── 2. Разметка ответа ──────────────────────────────────────────── */

  function md(src) {
    var esc = function (s) {
      return s.replace(/[&<>]/g, function (c) {
        return c === '&' ? '&amp;' : c === '<' ? '&lt;' : '&gt;';
      });
    };
    var blocks = [];
    src = src.replace(/```[a-z]*\n?([\s\S]*?)(?:```|$)/g, function (m, code) {
      blocks.push('<pre class="code"><code>' + esc(code.replace(/\n$/, '')) + '</code></pre>');
      return '@@code' + (blocks.length - 1) + '@@';
    });
    var inline = function (s) {
      return esc(s)
        .replace(/`([^`]+)`/g, '<code>$1</code>')
        .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    };
    var out = '', list = false, lines = src.split('\n');
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i].trim();
      var ph = line.match(/^@@code(\d+)@@$/);
      if (ph) { if (list) { out += '</ul>'; list = false; } out += blocks[+ph[1]]; continue; }
      if (!line) { if (list) { out += '</ul>'; list = false; } continue; }
      var li = line.match(/^[-•]\s+(.*)$/);
      if (li) {
        if (!list) { out += '<ul>'; list = true; }
        out += '<li>' + inline(li[1]) + '</li>';
        continue;
      }
      if (list) { out += '</ul>'; list = false; }
      out += '<p>' + inline(line) + '</p>';
    }
    if (list) out += '</ul>';
    return out;
  }

  /* ── 3. Ответы ───────────────────────────────────────────────────── */
  /* Демонстрационные заготовки. Чтобы подключить настоящую модель,
     замените тело replyFor() на запрос к вашему бэкенду.            */

  var CANNED = [
    {
      re: /запутанн|квант|физик/i,
      text: 'Запутанность — это когда две частицы описываются **одним** состоянием на двоих. Отдельного состояния у каждой уже нет.\n\nЧто из этого следует:\n- Измерили спин первой частицы — состояние второй определилось мгновенно, на любом расстоянии.\n- Передать сообщение так нельзя: результат измерения случаен, а без классического канала связи второй наблюдатель видит просто шум.\n- Корреляции сильнее любых, которые можно объяснить заранее припасённой «шпаргалкой» внутри частиц. Это и проверяют неравенства Белла.\n\nБлижайшая честная аналогия — не пара перчаток в коробках, а монета, которая падает решкой только тогда, когда её двойник за океаном падает орлом.'
    },
    {
      re: /sql|запрос|база данных|select|join/i,
      text: 'Пришлите запрос — разберу построчно. Пока три вещи, которые чаще всего замедляют выборку:\n\n- Функция поверх колонки в `WHERE` отключает индекс.\n- `SELECT *` тянет всё, включая тяжёлые текстовые поля.\n- `OFFSET` на больших страницах заставляет базу пересчитывать всё, что перед ним.\n\nПервый пункт лечится переносом функции на другую сторону сравнения:\n```sql\n-- было: индекс по created_at не работает\nWHERE date_trunc(\'day\', created_at) = \'2026-09-09\'\n\n-- стало: диапазон по индексу\nWHERE created_at >= \'2026-09-09\'\n  AND created_at <  \'2026-09-10\'\n```\nНачните с `EXPLAIN ANALYZE` — он покажет, где именно уходит время.'
    },
    {
      re: /кофейн|назван|нейминг|бренд/i,
      text: 'Восемь вариантов, разбиты по интонации.\n\nКоротко и предметно:\n- **Кипяток** — прямо, тепло, легко произносится.\n- **Дно** — про гущу на дне чашки, хорошо смотрится на вывеске.\n- **Пар** — одно слово, читается издалека.\n\nПро ритуал:\n- **Первый глоток**\n- **Пока горячий**\n- **Ещё один**\n\nС характером места:\n- **Обжарка на углу**\n- **Смена** — если открываетесь рано и работаете для тех, кто идёт на работу.\n\nПроверьте выбранное вслух: название должно выживать в фразе «встретимся в …».'
    },
    {
      re: /типографик|шрифт|вёрстк|верстк/i,
      text: 'Четыре книги, в порядке возрастания сложности:\n\n- **Ян Чихольд, «Облик книги»** — короткие эссе о том, почему поля именно такие. Начинать лучше отсюда.\n- **Роберт Брингхерст, «Основы стиля в типографике»** — главный справочник. Читается не подряд, а по разделам.\n- **Эмиль Рудер, «Типографика»** — про ритм и контраст, много упражнений глазом.\n- **Эрик Шпикерманн, «О шрифте»** — как шрифт звучит и почему один текст кажется громче другого.\n\nЕсли времени мало: у Брингхерста прочитайте главы о ритме и о выборе кегля — они окупаются сразу.'
    },
    {
      re: /привет|здравств|хай|hello|hi\b/i,
      text: 'Привет. Спрашивайте — разберу вопрос, напишу текст или почитаю ваш код.'
    },
    {
      re: /код|javascript|python|функц|ошибк|баг/i,
      text: 'Покажите код и опишите, что происходит вместо ожидаемого — так разбор будет быстрее.\n\nЕсли есть сообщение об ошибке, пришлите его целиком, вместе со стеком: последняя строка обычно говорит, что сломалось, а верхние — где именно.\n```js\ntry {\n  await run();\n} catch (err) {\n  console.error(err.stack);\n}\n```'
    }
  ];

  var FALLBACK = [
    'Понял вопрос. Это демонстрационная сборка интерфейса: ядро, анимации и переписка работают, но ответы берутся из локального набора заготовок — обращения к модели здесь нет.\n\nЧтобы подключить настоящую модель, замените тело `replyFor()` в `app.js` на запрос к вашему бэкенду. Всё остальное — потоковый вывод, разметка, состояния кнопок — уже готово.',
    'Отвечаю из заготовок: сетевого запроса в этой сборке нет.\n\nПопробуйте подсказки на стартовом экране — под них написаны развёрнутые ответы, на которых видно, как работает потоковый вывод и разметка: списки, **выделение** и блоки кода.'
  ];

  var fallbackTurn = 0;

  function replyFor(text) {
    for (var i = 0; i < CANNED.length; i++) {
      if (CANNED[i].re.test(text)) return CANNED[i].text;
    }
    return FALLBACK[fallbackTurn++ % FALLBACK.length];
  }

  /* ── 4. Чат ──────────────────────────────────────────────────────── */

  var main = $('#main');
  var feed = $('#feed');
  var form = $('#composer');
  var input = $('#input');
  var sendBtn = $('#send');
  var stopBtn = $('#stop');
  var resetBtn = $('#reset');
  var chips = $('#chips');
  var attach = $('#attach');

  var busy = false, aborted = false, pinned = true;

  main.addEventListener('scroll', function () {
    pinned = main.scrollHeight - main.scrollTop - main.clientHeight < 60;
  });

  function scroll() {
    if (pinned) main.scrollTop = main.scrollHeight;
  }

  // Пока переписка не началась, поле ввода стоит под заставкой,
  // а не у нижнего края. Считаем, насколько его поднять.
  var dock = document.querySelector('.dock');
  var hero = $('#hero');

  var root = document.documentElement;

  function lift(instant) {
    if (document.body.classList.contains('is-chat')) return;
    if (instant) dock.style.transition = 'none';

    root.style.setProperty('--idle-top', '0px');
    dock.style.setProperty('--lift', '0px');

    // dock уже вынесен из main, поэтому его высоту вычитать не нужно
    var slack = main.clientHeight - hero.offsetHeight - 20;
    var shift = Math.max(0, slack) / 2;
    root.style.setProperty('--idle-top', Math.round(shift) + 'px');

    var gap = main.clientHeight - hero.offsetHeight - shift - 20;
    dock.style.setProperty('--lift', Math.max(0, Math.round(gap)) + 'px');

    if (instant) requestAnimationFrame(function () { dock.style.transition = ''; });
  }

  function grow() {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 200) + 'px';
    lift(true);
  }

  window.addEventListener('resize', function () { lift(true); });
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(function () { lift(true); });

  function bubble(role) {
    var msg = document.createElement('div');
    msg.className = 'msg msg--' + role;
    if (role === 'ai') {
      var orb = document.createElement('span');
      orb.className = 'msg__orb';
      msg.appendChild(orb);
    }
    var body = document.createElement('div');
    body.className = 'msg__body';
    msg.appendChild(body);
    feed.appendChild(msg);
    return body;
  }

  function caretInto(el) {
    if (reduce) return;
    var node = el;
    while (node.lastElementChild) node = node.lastElementChild;
    var c = document.createElement('span');
    c.className = 'caret';
    node.appendChild(c);
  }

  function copyButton(msg, text) {
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'copy';
    btn.textContent = 'Скопировать';
    btn.addEventListener('click', function () {
      navigator.clipboard.writeText(text).then(function () {
        btn.textContent = 'Скопировано';
        setTimeout(function () { btn.textContent = 'Скопировать'; }, 1600);
      });
    });
    msg.appendChild(btn);
  }

  function setBusy(state) {
    busy = state;
    sendBtn.hidden = state;
    stopBtn.hidden = !state;
    Core.setEnergy(state ? 1 : 0);
  }

  async function send(text) {
    text = text.trim();
    if (!text || busy) return;

    if (!document.body.classList.contains('is-chat')) {
      document.body.classList.add('is-chat');
      resetBtn.hidden = false;
    }

    bubble('you').textContent = text;
    input.value = '';
    grow();
    pinned = true;
    scroll();

    setBusy(true);
    aborted = false;

    var body = bubble('ai');
    body.innerHTML = '<div class="dots"><i></i><i></i><i></i></div>';
    scroll();

    await sleep(reduce ? 120 : 420 + Math.random() * 500);
    if (aborted) { body.parentNode.remove(); setBusy(false); return; }

    var full = replyFor(text);
    var tokens = full.match(/\S+\s*|\s+/g) || [full];
    var acc = '';

    for (var i = 0; i < tokens.length; i++) {
      if (aborted) break;
      acc += tokens[i];
      body.innerHTML = md(acc);
      caretInto(body);
      scroll();
      if (!reduce) await sleep(12 + Math.random() * 24);
    }

    body.innerHTML = md(acc);
    copyButton(body.parentNode, acc);
    scroll();
    setBusy(false);
  }

  form.addEventListener('submit', function (e) {
    e.preventDefault();
    send(input.value);
  });

  stopBtn.addEventListener('click', function () { aborted = true; });

  input.addEventListener('input', grow);

  input.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send(input.value);
    }
  });

  chips.addEventListener('click', function (e) {
    var chip = e.target.closest('.chip');
    if (chip) send(chip.textContent);
  });

  resetBtn.addEventListener('click', function () {
    aborted = true;
    feed.innerHTML = '';
    document.body.classList.remove('is-chat');
    resetBtn.hidden = true;
    setBusy(false);
    lift(false);
    input.focus();
  });

  attach.addEventListener('click', function () {
    var picker = document.createElement('input');
    picker.type = 'file';
    picker.addEventListener('change', function () {
      if (!picker.files.length) return;
      input.value = (input.value ? input.value.trim() + ' ' : '') + picker.files[0].name;
      grow();
      input.focus();
    });
    picker.click();
  });

  Core.init();
  grow();
  input.focus();
})();
