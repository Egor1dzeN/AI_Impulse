import { forwardRef, useEffect, useImperativeHandle, useRef } from 'react';
import * as THREE from 'three';

const reduce =
  typeof window !== 'undefined' &&
  window.matchMedia('(prefers-reduced-motion: reduce)').matches;

const NOISE = [
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
  '}',
].join('\n');

const HEAD = [
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
  '}',
].join('\n');

const POINT_VERT =
  HEAD +
  [
    'void main(){',
    '  vec4 mv = modelViewMatrix * vec4(displace(position), 1.0);',
    '  vFront = clamp((mv.z + 3.9) / 2.0, 0.0, 1.0);',
    '  float grow = 0.70 + vFront * 0.45 + vD * 0.25;',
    '  gl_PointSize = uSize * uDpr * uScale * grow * (2.2 / -mv.z);',
    '  gl_Position = projectionMatrix * mv;',
    '}',
  ].join('\n');

const POINT_FRAG = [
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
  '}',
].join('\n');

function shell(count) {
  const pos = new Float32Array(count * 3);
  const step = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < count; i++) {
    const y = 1 - (i / (count - 1)) * 2;
    const r = Math.sqrt(Math.max(0, 1 - y * y));
    const a = step * i;
    pos[i * 3] = Math.cos(a) * r + (Math.random() - 0.5) * 0.013;
    pos[i * 3 + 1] = y + (Math.random() - 0.5) * 0.013;
    pos[i * 3 + 2] = Math.sin(a) * r + (Math.random() - 0.5) * 0.013;
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  return geo;
}

const OrbCanvas = forwardRef(function OrbCanvas(_props, ref) {
  const stageRef = useRef(null);
  const canvasRef = useRef(null);
  const targetEnergy = useRef(0);

  useImperativeHandle(ref, () => ({
    setEnergy(v) {
      targetEnergy.current = v;
    },
  }));

  useEffect(() => {
    const stage = stageRef.current;
    const canvas = canvasRef.current;
    if (!stage || !canvas || !window.WebGLRenderingContext) {
      stage?.classList.add('is-flat');
      return;
    }

    let renderer;
    try {
      renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true });
    } catch {
      stage.classList.add('is-flat');
      return;
    }

    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setClearAlpha(0);

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100);
    camera.position.z = 3.05;

    const tilt = new THREE.Group();
    const spin = new THREE.Group();
    tilt.add(spin);
    scene.add(tilt);

    const uni = {
      uTime: { value: 0 },
      uAmp: { value: 0.21 },
      uSize: { value: 3.5 },
      uScale: { value: 1 },
      uDpr: { value: renderer.getPixelRatio() },
      uPointer: { value: new THREE.Vector3(0, 0, 1) },
      uReach: { value: 0 },
    };

    spin.add(
      new THREE.Points(
        shell(32000),
        new THREE.ShaderMaterial({
          uniforms: uni,
          vertexShader: POINT_VERT,
          fragmentShader: POINT_FRAG,
          transparent: true,
          depthWrite: false,
        })
      )
    );

    const dir = new THREE.Vector3();
    const quat = new THREE.Quaternion();
    const clock = new THREE.Clock();

    let energy = 0;
    let px = 0, py = 0, tpx = 0, tpy = 0, reach = 0, treach = 0;
    let ampScale = 1;
    let rafId = 0;

    function resize() {
      const w = stage.clientWidth || 1;
      const h = stage.clientHeight || 1;
      renderer.setSize(w, h, false);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      uni.uScale.value = Math.pow(Math.max(h, 8) / 400, 0.35);
      ampScale = Math.max(0.3, Math.min(1, h / 220));
      if (reduce) renderer.render(scene, camera);
    }

    function onMove(e) {
      tpx = (e.clientX / window.innerWidth) * 2 - 1;
      tpy = (e.clientY / window.innerHeight) * 2 - 1;
      treach = 1;
    }

    function onLeave() {
      treach = 0;
    }

    function loop() {
      rafId = requestAnimationFrame(loop);
      const dt = Math.min(clock.getDelta(), 0.05);

      energy += (targetEnergy.current - energy) * Math.min(dt * 3.4, 1);
      px += (tpx - px) * Math.min(dt * 3.6, 1);
      py += (tpy - py) * Math.min(dt * 3.6, 1);
      reach += (treach - reach) * Math.min(dt * 4.5, 1);

      uni.uTime.value += dt * (1 + energy * 2.6);
      uni.uAmp.value = (0.2 + energy * 0.17) * ampScale;
      uni.uReach.value = reach;

      spin.rotation.y += dt * (0.16 + energy * 0.55);
      tilt.rotation.x = -py * 0.42;
      tilt.rotation.y = px * 0.55;
      camera.position.x = px * 0.16;
      camera.position.y = -py * 0.16;
      camera.lookAt(0, 0, 0);

      dir.set(px, -py, 1.35).normalize();
      spin.getWorldQuaternion(quat);
      dir.applyQuaternion(quat.invert());
      uni.uPointer.value.copy(dir);

      renderer.render(scene, camera);
    }

    resize();

    let ro;
    if (window.ResizeObserver) {
      ro = new ResizeObserver(resize);
      ro.observe(stage);
    } else {
      window.addEventListener('resize', resize);
    }

    if (reduce) {
      uni.uTime.value = 1.4;
      renderer.render(scene, camera);
    } else {
      window.addEventListener('pointermove', onMove, { passive: true });
      window.addEventListener('pointerleave', onLeave);
      rafId = requestAnimationFrame(loop);
    }

    return () => {
      cancelAnimationFrame(rafId);
      ro?.disconnect();
      window.removeEventListener('resize', resize);
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerleave', onLeave);
      renderer.dispose();
    };
  }, []);

  return (
    <div className="stage" id="stage" ref={stageRef} aria-hidden="true">
      <canvas id="core" ref={canvasRef} />
    </div>
  );
});

export default OrbCanvas;
