/*
 * Lightsaber visualisers — the "visual" fill type (see lightsaber/visuals.py).
 *
 * Shared by index.html (editor) and output.html (projector window). This is a
 * deliberate break from those two pages' convention of hand-duplicating every
 * draw function: shaders are long, and two copies would drift. Each page
 * maps the shape's corners into its own coordinates and hands them over; this
 * file returns an image of the shape ALREADY WARPED, with where to put it,
 * and the page places it with one drawImage under its usual clips, opacity
 * and filter — so masks, feather, key, FX, the kaleidoscope and the
 * post-distort mirror fold all apply as they do to any other fill.
 *
 * Why not warp through drawMediaWarped like video: that mesh overlaps each
 * of its 72 triangles by ~1.5px to hide seams, which is invisible for opaque
 * pixels and visible as a grid for semi-transparent ones — and a visualiser
 * is semi-transparent almost everywhere (light on nothing). Here the
 * fragment shader inverts the shape's corner-pin homography per output
 * pixel instead: one draw, no seams, a true projective warp rather than a
 * piecewise-affine approximation. (ARCHITECTURE.md's GPU-cost item 3 names
 * exactly this — "one textured quad with a projective transform" — as the
 * real fix for the mesh; a visualiser gets it for free because it is
 * already being drawn on the GPU.)
 *
 * Why every preset is a pure function of (time, phase, audio, params): each
 * output window renders its own copy, so a shape spanning two projectors only
 * lines up if they compute the same picture. No frame-to-frame state lives in
 * the shaders. Audio-driven speed is integrated on the server (visual_phase);
 * see visuals.py's docstring.
 *
 * Plain GLSL ES 3.0 fragment shaders on purpose: the render host (topology B,
 * ARCHITECTURE.md §5) is a GL compositor, and these carry straight across.
 *
 * Fails soft: with no WebGL2 (or a lost context) frame() returns null and the
 * shape simply draws nothing, logged once — the rest of the frame is
 * unaffected.
 */
(function (root) {
  "use strict";

  // Cap on the long side of the render, in device px. A shape smaller than
  // this renders at its true on-screen size; a bigger one renders smaller
  // and is scaled up by the drawImage. Visualisers are soft, so 1280 on a
  // 1920-wide output is not a visible loss, and both the shader and the
  // GL -> 2D handoff (the part that measured expensive in ARCHITECTURE.md
  // §12's WebGL warp prototype) scale with pixel count.
  const MAX_LONG_SIDE = 1280;
  const EVICT_AFTER_MS = 3000;     // same as the media element cache

  const VS = `#version 300 es
in vec2 aPos;
out vec2 vUv;
void main(){ vUv = aPos * 0.5 + 0.5; gl_Position = vec4(aPos, 0.0, 1.0); }`;

  // Shared helpers. hash12/hash22 are Dave Hoskins' "Hash without Sine"
  // (MIT): sin()-based hashes lose precision on some GPUs and band visibly.
  const COMMON = `#version 300 es
precision highp float;
in vec2 vUv;
out vec4 outColor;
uniform vec4 uBox;        // rendered region in the page's coords: x, y, w, h
uniform mat3 uInvH;       // page coords -> the shape's own unit square
uniform vec4 uMirror;     // mirror x, mirror y (0/1), fold point x, y
uniform float uAspect;    // the shape's own width/height on screen
uniform float uTime;      // canvas clock, s (frozen by Stop/Freeze)
uniform float uPhase;     // server-integrated, audio-modulated motion
uniform vec4 uAudio;      // low, mid, high, master — smoothed 0..1
float hash12(vec2 p){
  vec3 p3 = fract(vec3(p.xyx) * 0.1031);
  p3 += dot(p3, p3.yzx + 33.33);
  return fract((p3.x + p3.y) * p3.z);
}
vec2 hash22(vec2 p){
  vec3 p3 = fract(vec3(p.xyx) * vec3(0.1031, 0.1030, 0.0973));
  p3 += dot(p3, p3.yzx + 33.33);
  return fract((p3.xx + p3.yz) * p3.zy);
}
float vnoise(vec2 p){
  vec2 i = floor(p), f = fract(p);
  vec2 u = f * f * f * (f * (f * 6.0 - 15.0) + 10.0);   // quintic: no grid creases
  float a = hash12(i), b = hash12(i + vec2(1, 0));
  float c = hash12(i + vec2(0, 1)), d = hash12(i + vec2(1, 1));
  return mix(mix(a, b, u.x), mix(c, d, u.x), u.y);
}
// The rotation between octaves stops the grid's axes lining up into visible
// horizontal/vertical streaks.
float fbm(vec2 p){
  float s = 0.0, a = 0.5;
  const mat2 m = mat2(1.6, 1.2, -1.2, 1.6);
  for (int i = 0; i < 5; i++){ s += a * vnoise(p); p = m * p; a *= 0.5; }
  return s;
}
vec3 hsv2rgb(vec3 c){
  vec3 p = abs(fract(c.xxx + vec3(0.0, 2.0/3.0, 1.0/3.0)) * 6.0 - 3.0);
  return c.z * mix(vec3(1.0), clamp(p - 1.0, 0.0, 1.0), c.y);
}
// Light on nothing: emitted colour becomes premultiplied RGBA with alpha =
// the brightest channel, so a visualiser composites OVER the shapes behind
// it instead of painting them black. On a projector, black is "no light".
vec4 emit(vec3 e){ e = clamp(e, 0.0, 1.0); return vec4(e, max(e.r, max(e.g, e.b))); }
// The same mirror + fold the media path applies to its source sampling
// (foldFraction in both pages): keep [0, point], mirror it over the rest.
float fold(float u, float point){ return point >= 0.999 ? u : (u <= point ? u : max(0.0, 2.0 * point - u)); }
vec4 shade(vec2 uv, float aspect);   // each preset's: uv 0..1, y up -> premultiplied RGBA
void main(){
  // This fragment's position in the page's coordinates (y down)...
  vec2 px = uBox.xy + vec2(vUv.x, 1.0 - vUv.y) * uBox.zw;
  // ...back through the corner-pin to the shape's own square (v down).
  vec3 h = uInvH * vec3(px, 1.0);
  vec2 uv = h.xy / h.z;
  // Anti-aliased edge, one output pixel wide whatever the warp.
  vec2 fw = max(fwidth(uv), vec2(1e-6));
  vec2 e = min(uv, 1.0 - uv) / fw;
  float cov = clamp(min(e.x, e.y) + 0.5, 0.0, 1.0);
  if (cov <= 0.0 || h.z <= 0.0){ outColor = vec4(0.0); return; }
  uv.x = fold(uMirror.x > 0.5 ? 1.0 - uv.x : uv.x, uMirror.z);
  uv.y = fold(uMirror.y > 0.5 ? 1.0 - uv.y : uv.y, uMirror.w);
  outColor = shade(vec2(uv.x, 1.0 - uv.y), uAspect) * cov;
}
`;

  const PRESET_FS = {
    // Layers of stars at different depths, each sliding from far (dense,
    // tiny) to near (sparse, big) and wrapping round — which reads as flying
    // forward. A layer re-seeds every time it wraps, behind a fade, so the
    // same pattern never visibly comes round again.
    starfield: `
uniform float u_speed, u_density, u_size, u_twinkle, u_hue, u_colour, u_brightness,
              u_react_bass, u_react_high;
const int LAYERS = 6;
vec4 shade(vec2 uv, float aspect){
  vec2 p = (uv - 0.5) * vec2(aspect, 1.0);
  float sparkle = 1.0 + u_react_high * uAudio.z * 1.5;
  float present = clamp(0.32 * u_density, 0.02, 0.95);
  vec3 e = vec3(0.0);
  for (int i = 0; i < LAYERS; i++){
    float fi = float(i);
    float travel = fi / float(LAYERS) + uPhase * 0.25;
    float z = fract(travel);                       // 0 far .. 1 near
    float cycle = floor(travel);
    float zoom = mix(16.0, 0.7, z);
    float fade = smoothstep(0.0, 0.3, z) * smoothstep(1.0, 0.82, z);
    vec2 q = p * zoom + hash22(vec2(fi * 7.31, cycle * 3.17)) * 97.0;
    vec2 id = floor(q), gv = fract(q) - 0.5;
    for (int y = -1; y <= 1; y++){
      for (int x = -1; x <= 1; x++){
        vec2 o = vec2(x, y);
        vec2 r = hash22(id + o + fi * 13.7);
        if (r.x > present) continue;
        vec2 pos = o + (hash22(id + o + 41.3) - 0.5) * 0.8;
        float d = length(gv - pos);
        float rad = 0.035 * u_size * (0.4 + r.y);
        float core = smoothstep(rad, 0.0, d);
        // Exponential, not 1/d: a 1/d glow never reaches zero, so it ran past
        // the 3x3 cells searched here and was cut off at every cell edge —
        // a visible grid of grey squares across the whole sky.
        float halo = 0.6 * exp(-d / (rad * 2.2));
        float tw = 1.0 - u_twinkle * 0.5 * (1.0 + sin(uTime * (2.0 + 6.0 * r.y) + r.x * 40.0));
        vec3 col = hsv2rgb(vec3(u_hue + (r.y - 0.5) * 0.18, u_colour * r.x / max(present, 1e-3), 1.0));
        e += col * (core + halo) * fade * tw * sparkle;
      }
    }
  }
  return emit(e * u_brightness);
}`,

    // Domain-warped noise ("noise of noise of noise"): each layer's sample
    // point is pushed around by the layer before, which is what turns plain
    // fbm into slow, folding, cloud-like shapes. Drift rides on uPhase (the
    // server-integrated speed); the slow internal churn rides on uTime.
    clouds: `
uniform float u_speed, u_scale, u_coverage, u_softness, u_warp, u_hue, u_hue2,
              u_brightness, u_react_level, u_react_bass;
vec4 shade(vec2 uv, float aspect){
  vec2 p = (uv - 0.5) * vec2(aspect, 1.0) * 2.0 * u_scale;
  float level = uAudio.w, bass = uAudio.x;
  vec2 drift = vec2(uPhase * 0.35, uPhase * 0.12);
  float slow = uTime * 0.03;
  float w = u_warp * (1.0 + u_react_bass * bass * 0.8);
  vec2 q = vec2(fbm(p + drift + vec2(0.0, slow)),
                fbm(p + drift + vec2(5.2, 1.3) - slow));
  vec2 r = vec2(fbm(p + w * 2.0 * q + vec2(1.7, 9.2) + 0.15 * slow),
                fbm(p + w * 2.0 * q + vec2(8.3, 2.8) - 0.12 * slow));
  float f = fbm(p + drift * 0.5 + w * 2.0 * r);
  // Calibrated against the measured distribution of f (2026-11, numpy
  // replica of this shader): median 0.50, interquartile 0.41-0.60, 5-95%
  // 0.28-0.72. So coverage c leaves ~c of the shape solid cloud, and the
  // softness band must stay narrow next to that spread — at +-0.15 nearly
  // every pixel sat inside the ramp and the whole shape went faintly cloudy.
  float cov = clamp(u_coverage + u_react_level * level * 0.35, 0.0, 1.0);
  float thr = mix(0.78, 0.22, cov);
  float soft = 0.01 + u_softness * 0.12;
  float dens = smoothstep(thr - soft, thr + soft, f);
  vec3 a = hsv2rgb(vec3(u_hue, 0.55, 0.75));
  vec3 b = hsv2rgb(vec3(u_hue2, 0.45, 0.9));
  vec3 col = mix(a, b, clamp(length(q) * 0.9, 0.0, 1.0));
  // Thin edges dim, thick cores glow, with a soft "lit" side from the warp
  // field. Kept under 1.0 at default brightness: it used to peak ~1.45 and
  // clip the whole cloud to flat white.
  float lit = clamp(dot(r, vec2(0.6, 0.4)), 0.0, 1.0);
  col *= 0.35 + 0.45 * dens + 0.2 * lit;
  float bright = u_brightness * (0.85 + 0.4 * level * u_react_level);
  // Alpha follows density with a gentler curve than colour, so the edges
  // read as thin mist over whatever is behind rather than a cut-out.
  float alpha = clamp(dens * dens * (3.0 - 2.0 * dens), 0.0, 1.0) * 0.92;
  return vec4(clamp(col * bright, 0.0, 1.0) * alpha, alpha);
}`,

    // Marbled flow: the sample point is pushed round in a few rounds of
    // sine-and-noise displacement (each round feeding the next — that
    // feedback is what makes it read as turbulence rather than ripples), and
    // a sine of the displaced coordinate draws the streaks. Mids add churn
    // live; bass speeds the flow through the server-integrated phase.
    turbulence: `
uniform float u_speed, u_scale, u_churn, u_detail, u_softness, u_hue, u_hue2,
              u_brightness, u_react_bass, u_react_mid;
vec4 shade(vec2 uv, float aspect){
  vec2 p = (uv - 0.5) * vec2(aspect, 1.0) * u_scale * 3.0;
  float t = uPhase;
  float churn = u_churn * (1.0 + u_react_mid * uAudio.y * 1.2);
  vec2 q = p;
  for (int i = 0; i < 4; i++){
    float fi = float(i);
    q += churn * 0.32 * vec2(sin(q.y * 1.7 + t * 0.6 + fi * 1.3 + fbm(q + fi * 3.1) * 3.0),
                             cos(q.x * 1.5 - t * 0.5 + fi * 2.1 + fbm(q.yx - fi * 1.7) * 3.0));
  }
  float f = fbm(q * 0.8 + vec2(t * 0.1, -t * 0.07));
  float streak = 0.5 + 0.5 * sin(q.x * u_detail * 2.0 + f * 6.0);
  streak = pow(streak, mix(6.0, 1.2, u_softness));
  vec3 col = mix(hsv2rgb(vec3(u_hue, 0.75, 1.0)), hsv2rgb(vec3(u_hue2, 0.65, 1.0)),
                 smoothstep(0.3, 0.7, f));
  vec3 e = col * (0.08 + streak) * smoothstep(0.25, 0.75, f) * 1.1;
  return emit(e * u_brightness);
}`,

    // Thin-film interference — what makes oil on water rainbow. A film of
    // thickness h reflects each wavelength in and out of phase as h changes,
    // so a smoothly varying thickness field (warped noise) becomes bands of
    // colour. The cosine below is that interference term per channel, at
    // roughly red/green/blue wavelengths (um) for a film of index ~1.4.
    oil: `
uniform float u_speed, u_scale, u_thickness, u_warp, u_coverage, u_saturation,
              u_brightness, u_react_bass, u_react_level;
vec4 shade(vec2 uv, float aspect){
  vec2 p = (uv - 0.5) * vec2(aspect, 1.0) * u_scale * 2.0;
  float t = uPhase;
  vec2 q = vec2(fbm(p + vec2(t * 0.20, 0.0)), fbm(p + vec2(3.7, 1.9) - t * 0.15));
  // The thickness field is deliberately SMOOTH (two octaves, not fbm's
  // five): fine detail here turns into tight, noisy rainbow contours, which
  // read as a rave poster rather than oil.
  vec2 pw = p + u_warp * 2.2 * q + vec2(0.0, t * 0.05);
  float h = 0.65 * vnoise(pw * 0.7) + 0.35 * vnoise(pw * 1.4 + 7.3);
  // Shimmer: the level nudges thickness, which slides every band at once.
  float shimmer = u_react_level * uAudio.w * 0.12;
  // ~0.1-1um: the range real slicks show, giving the familiar sequence of
  // "Newton's colours" (gold, magenta, blue, green...). The colour is the
  // film's reflectance summed over seven wavelengths across the visible
  // range — three single-wavelength cosines (the first version) gave flat
  // neon primaries rather than those mixed, natural hues.
  float thick = 0.1 + (h * u_thickness + shimmer) * 0.8;          // um
  vec3 film = vec3(0.0), norm = vec3(0.0);
  for (int k = 0; k < 7; k++){
    float lam = 0.40 + 0.05 * float(k);                            // 0.40-0.70um
    float refl = 0.5 + 0.5 * cos(6.2831853 * 2.0 * 1.4 * thick / lam);
    // rough wavelength -> RGB response
    vec3 w = vec3(exp(-pow((lam - 0.61) / 0.07, 2.0)) + 0.25 * exp(-pow((lam - 0.42) / 0.03, 2.0)),
                  exp(-pow((lam - 0.54) / 0.06, 2.0)),
                  exp(-pow((lam - 0.45) / 0.05, 2.0)));
    film += refl * w; norm += w;
  }
  film /= norm;
  // contrast up (summed reflectance is muted), then the colour control
  film = clamp((film - 0.5) * 1.8 + 0.5, 0.0, 1.0);
  film = mix(vec3(dot(film, vec3(0.333))), film, u_saturation);
  // Where the film is thinnest it reads as dark water, not colour.
  float slick = smoothstep(mix(0.85, 0.25, u_coverage), mix(0.95, 0.4, u_coverage), h);
  vec3 e = film * slick * 0.85;
  return emit(e * u_brightness);
}`,

    // Faked, not simulated: each drop is a timed event (seeded by its index
    // and which cycle it's on), so the picture stays a pure function of the
    // phase and every output agrees. A drop lands, its cloud swells with
    // age, ridged noise inside it draws the tendrils, and it fades out as the
    // next one blooms elsewhere. Bass speeds the cycle via the phase.
    ink: `
uniform float u_speed, u_drops, u_size, u_swirl, u_tendrils, u_hue, u_spread,
              u_brightness, u_react_bass;
vec4 shade(vec2 uv, float aspect){
  vec2 p = (uv - 0.5) * vec2(aspect, 1.0);
  vec3 e = vec3(0.0);
  for (int i = 0; i < 8; i++){
    float fi = float(i);
    if (fi >= u_drops) break;
    float life = uPhase * 0.12 + fi / max(u_drops, 1.0);
    float age = fract(life), cycle = floor(life);
    vec2 c = (hash22(vec2(fi * 3.1, cycle * 7.7)) - 0.5) * vec2(aspect * 0.8, 0.8);
    vec2 d = p - c - vec2(0.0, -age * 0.12);                 // sinks a little
    float r = (0.06 + age * 0.42) * u_size;
    vec2 w = d / r;
    float n = fbm(w * 1.5 + vec2(0.0, age * 2.0) + fi * 11.0 + uTime * 0.04);
    float n2 = fbm(w * 3.0 + n * 2.0 * u_swirl - fi * 5.0);
    float ridge = 1.0 - abs(2.0 * n2 - 1.0);                 // filaments
    float body = smoothstep(1.15, 0.15, length(w) + (n - 0.5) * 1.4 * u_swirl);
    float dens = body * mix(0.7, ridge * ridge * 1.8, u_tendrils);
    dens *= smoothstep(0.0, 0.06, age) * (1.0 - smoothstep(0.5, 1.0, age));
    vec3 col = hsv2rgb(vec3(u_hue + (hash12(vec2(fi, cycle)) - 0.5) * u_spread, 0.8, 1.0));
    e += col * dens;
  }
  return emit(e * 0.9 * u_brightness);
}`,

    // Curtains: each is a wavy horizontal band (its baseline a slow noise
    // function of x), lit bright at its foot and fading upwards into the
    // second hue, broken into vertical rays by high-frequency noise along x.
    aurora: `
uniform float u_speed, u_curtains, u_height, u_ripple, u_hue, u_hue2,
              u_brightness, u_react_level, u_react_high, u_react_bass;
vec4 shade(vec2 uv, float aspect){
  float x = (uv.x - 0.5) * aspect;
  float t = uPhase;
  vec3 e = vec3(0.0);
  float shimmer = 1.0 + u_react_high * uAudio.z * 1.5;
  for (int i = 0; i < 5; i++){
    float fi = float(i);
    if (fi >= u_curtains) break;
    // A curtain hangs in folds: a slow noise drift plus a travelling sway,
    // so its foot snakes across the sky rather than lying like a horizon.
    float base = 0.2 + 0.45 * (fi + 0.5) / max(u_curtains, 1.0)
               + (fbm(vec2(x * 0.8 + t * 0.15 + fi * 7.0, fi)) - 0.5) * 0.7 * u_ripple
               + sin(x * (1.6 + fi * 0.7) + t * (0.5 + fi * 0.13) + fi * 2.0) * 0.08 * u_ripple;
    float above = uv.y - base;
    float foot = smoothstep(-0.035, 0.0, above);                       // soft lower edge
    float rise = exp(-max(above, 0.0) / (0.2 * u_height + 0.03));     // tall rays, fading up
    float rays = 0.55 + 0.45 * fbm(vec2(x * 18.0 + fi * 3.0, t * 0.6 * shimmer));
    float glow = foot * rise * rays;
    vec3 col = mix(hsv2rgb(vec3(u_hue + fi * 0.02, 0.8, 1.0)), hsv2rgb(vec3(u_hue2, 0.7, 1.0)),
                   clamp(above / (0.25 * u_height + 0.02), 0.0, 1.0));
    e += col * glow * 0.75;
  }
  e *= 1.0 + u_react_level * uAudio.w * 0.8;
  return emit(e * u_brightness);
}`,
  };

  // ---- GL state ------------------------------------------------------------
  let gl = null, glc = null, vbo = null, failed = false, warned = false;
  const programs = new Map();      // preset -> {prog, uni: {name: loc}}

  function warnOnce(msg, err){
    if (warned) return;
    warned = true;
    console.warn(`[lightsaber] visualisers: ${msg}`, err || "");
  }
  function init(){
    if (gl || failed) return !!gl;
    try {
      glc = document.createElement("canvas");
      glc.width = 256; glc.height = 256;           // grows on demand in frame()
      gl = glc.getContext("webgl2", {premultipliedAlpha: true, alpha: true, antialias: false,
                                     preserveDrawingBuffer: false});
      if (!gl){ failed = true; warnOnce("WebGL2 unavailable — visual shapes will draw nothing"); return false; }
      // A lost context (GPU reset, driver hiccup, too many contexts) must
      // not be the end: drop everything and rebuild on restore.
      glc.addEventListener("webglcontextlost", e => { e.preventDefault(); programs.clear(); vbo = null; });
      glc.addEventListener("webglcontextrestored", () => { programs.clear(); vbo = null; });
      return true;
    } catch (e){ failed = true; warnOnce("WebGL2 init failed", e); return false; }
  }
  function compile(type, src){
    const sh = gl.createShader(type);
    gl.shaderSource(sh, src);
    gl.compileShader(sh);
    if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(sh));
    return sh;
  }
  function programFor(preset){
    let p = programs.get(preset);
    if (p) return p;
    const fs = PRESET_FS[preset];
    if (!fs) return null;
    const prog = gl.createProgram();
    gl.attachShader(prog, compile(gl.VERTEX_SHADER, VS));
    gl.attachShader(prog, compile(gl.FRAGMENT_SHADER, COMMON + fs));
    gl.bindAttribLocation(prog, 0, "aPos");
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(prog));
    const uni = {};
    const n = gl.getProgramParameter(prog, gl.ACTIVE_UNIFORMS);
    for (let i = 0; i < n; i++){
      const info = gl.getActiveUniform(prog, i);
      uni[info.name] = gl.getUniformLocation(prog, info.name);
    }
    p = {prog, uni};
    programs.set(preset, p);
    return p;
  }
  function ensureGeometry(){
    if (vbo) return;
    // One oversized triangle covers the viewport — no diagonal seam.
    vbo = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, vbo);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
  }

  // ---- clock + audio ---------------------------------------------------------
  // Messages land at the server's tick rate with network jitter; pages paint
  // faster than that (output windows repaint every period while anything is
  // live). Extrapolating each message by its own arrival time would make
  // time step backwards whenever one arrives late — visible as a stutter in
  // a fast starfield — so each layer keeps a local clock that runs at the
  // published rate and is eased toward the extrapolated server value, and
  // snaps only on a real jump (a canvas change, a reset).
  let _msgAt = performance.now(), _running = false;
  let _audioTarget = {low: 0, mid: 0, high: 0, master: 0};
  const _audio = {low: 0, mid: 0, high: 0, master: 0};
  let _audioAt = performance.now();
  const _clocks = new Map();        // layer id -> {t, phase, at}
  const _targets = new Map();       // layer id -> {canvas, ctx, used}

  function onState(m){
    _msgAt = performance.now();
    const st = (m && m.state) || {};
    _running = !!st.active && !st.frozen;
    const b = st.audio_bands || {};
    const low = +b.low || 0, mid = +b.mid || 0, high = +b.high || 0;
    _audioTarget = {low, mid, high, master: b.master != null ? +b.master : (low + mid + high) / 3};
  }
  function smoothedAudio(){
    // One-pole toward the latest levels (tau ~70ms): the levels arrive in
    // steps at the tick rate, and stepped brightness reads as flicker.
    const now = performance.now();
    const k = 1 - Math.exp(-Math.max(0, now - _audioAt) / 70);
    _audioAt = now;
    for (const key in _audio) _audio[key] += (_audioTarget[key] - _audio[key]) * k;
    return _audio;
  }
  function clockFor(layer){
    const now = performance.now();
    const rate = _running ? (+layer.visual_rate || 0) : 0;
    const ahead = _running ? Math.max(0, (now - _msgAt) / 1000) : 0;
    const tT = (+layer.visual_t || 0) + ahead;
    const pT = (+layer.visual_phase || 0) + rate * ahead;
    let c = _clocks.get(layer.id);
    if (!c || Math.abs(c.t - tT) > 0.5){
      c = {t: tT, phase: pT, at: now};
      _clocks.set(layer.id, c);
      return c;
    }
    const dt = Math.max(0, (now - c.at) / 1000);
    c.at = now;
    if (_running){ c.t += dt; c.phase += rate * dt; }
    c.t += (tT - c.t) * 0.15;
    c.phase += (pT - c.phase) * 0.15;
    return c;
  }

  // ---- render ----------------------------------------------------------------
  // Unit square -> quad, then inverted. Same convention as the pages'
  // homography(): (0,0) is corner 0 (top-left), u runs to corner 1, v to
  // corner 3. Returns the inverse as a column-major mat3 for GLSL, or null
  // for a degenerate quad.
  function invHomography(q){
    const [[x0, y0], [x1, y1], [x2, y2], [x3, y3]] = q;
    const dx1 = x1 - x2, dy1 = y1 - y2, dx2 = x3 - x2, dy2 = y3 - y2;
    const sx = x0 - x1 + x2 - x3, sy = y0 - y1 + y2 - y3;
    const den = dx1 * dy2 - dx2 * dy1;
    const g = den ? (sx * dy2 - dx2 * sy) / den : 0;
    const h = den ? (dx1 * sy - sx * dy1) / den : 0;
    const a = x1 - x0 + g * x1, b = x3 - x0 + h * x3, c = x0;
    const d = y1 - y0 + g * y1, e = y3 - y0 + h * y3, f = y0;
    // inverse of [[a b c][d e f][g h 1]] by adjugate
    const A = e - f * h, B = c * h - b, C = b * f - c * e;
    const D = f * g - d, E = a - c * g, F = c * d - a * f;
    const G = d * h - e * g, H = b * g - a * h, I = a * e - b * d;
    const det = a * A + b * D + c * G;
    if (!det || !isFinite(det)) return null;
    // row-major [[A B C][D E F][G H I]] / det, emitted column-major
    return new Float32Array([A / det, D / det, G / det, B / det, E / det, H / det, C / det, F / det, I / det]);
  }

  // Renders `layer` already warped into `quad` — its four corners (TL, TR,
  // BR, BL) in the CALLER's drawing coordinates — and returns
  // {canvas, x, y, w, h}: draw canvas into that rect, in those coordinates,
  // and it lands exactly on the shape. Or null (off-screen, degenerate, no
  // WebGL).
  //   opts.pxScale  device px per caller unit (the editor's zoom; 1 on output)
  //   opts.clip     {x,y,w,h} in caller coords — only this part is rendered
  //   opts.mirror   [mirrorX, mirrorY, foldPointX, foldPointY]
  //
  // Copying out of the one shared GL canvas into a per-shape 2D canvas is
  // what lets several visual shapes share one GL context.
  function frame(layer, quad, opts){
    if (!init() || gl.isContextLost()) return null;
    if (!quad || quad.length !== 4 || quad.some(p => !isFinite(p[0]) || !isFinite(p[1]))) return null;
    opts = opts || {};
    const preset = layer.visual_preset;
    let prog;
    try { prog = programFor(preset); }
    catch (e){ warnOnce(`shader for "${preset}" failed to build`, e); return null; }
    if (!prog) return null;
    const inv = invHomography(quad);
    if (!inv) return null;

    let x0 = Math.min(...quad.map(p => p[0])), x1 = Math.max(...quad.map(p => p[0]));
    let y0 = Math.min(...quad.map(p => p[1])), y1 = Math.max(...quad.map(p => p[1]));
    if (opts.clip){                 // never shade pixels nobody will see
      x0 = Math.max(x0, opts.clip.x); y0 = Math.max(y0, opts.clip.y);
      x1 = Math.min(x1, opts.clip.x + opts.clip.w); y1 = Math.min(y1, opts.clip.y + opts.clip.h);
    }
    const bw = x1 - x0, bh = y1 - y0;
    if (!(bw > 0.5 && bh > 0.5)) return null;
    const scale = Math.max(1e-3, opts.pxScale || 1);
    const fit = Math.min(1, MAX_LONG_SIDE / Math.max(bw * scale, bh * scale));
    const w = Math.max(2, Math.round(bw * scale * fit)), h = Math.max(2, Math.round(bh * scale * fit));
    if (glc.width < w || glc.height < h){                // grow only: resizing reallocates
      glc.width = Math.max(glc.width, w); glc.height = Math.max(glc.height, h);
    }
    // The shape's own aspect, from its edge lengths rather than its bounding
    // box, so a star stays round on a rotated or keystoned shape.
    const len = (p, q) => Math.hypot(q[0] - p[0], q[1] - p[1]);
    const qw = (len(quad[0], quad[1]) + len(quad[3], quad[2])) / 2;
    const qh = (len(quad[0], quad[3]) + len(quad[1], quad[2])) / 2;
    const aspect = qh > 0 ? Math.max(0.05, Math.min(20, qw / qh)) : 1;

    const clock = clockFor(layer), audio = smoothedAudio();
    ensureGeometry();
    gl.viewport(0, 0, w, h);
    gl.disable(gl.BLEND);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.useProgram(prog.prog);
    gl.bindBuffer(gl.ARRAY_BUFFER, vbo);
    gl.enableVertexAttribArray(0);
    gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
    const u = prog.uni;
    if (u.uBox) gl.uniform4f(u.uBox, x0, y0, bw, bh);
    if (u.uInvH) gl.uniformMatrix3fv(u.uInvH, false, inv);
    const m = opts.mirror || [0, 0, 1, 1];
    if (u.uMirror) gl.uniform4f(u.uMirror, m[0] ? 1 : 0, m[1] ? 1 : 0, m[2] ?? 1, m[3] ?? 1);
    if (u.uAspect) gl.uniform1f(u.uAspect, aspect);
    // Wrapped to keep float32 precision over a long show; every use of uTime
    // in the shaders is a slow churn or a twinkle, where a wrap every ~17
    // minutes is invisible.
    if (u.uTime) gl.uniform1f(u.uTime, clock.t % 1000);
    if (u.uPhase) gl.uniform1f(u.uPhase, clock.phase);
    if (u.uAudio) gl.uniform4f(u.uAudio, audio.low, audio.mid, audio.high, audio.master);
    const params = layer.visual_params || {};
    for (const k in params){
      const loc = u["u_" + k];
      if (loc) gl.uniform1f(loc, +params[k] || 0);
    }
    gl.drawArrays(gl.TRIANGLES, 0, 3);

    let tgt = _targets.get(layer.id);
    if (!tgt){
      const c = document.createElement("canvas");
      tgt = {canvas: c, ctx: c.getContext("2d"), used: 0};
      _targets.set(layer.id, tgt);
    }
    if (tgt.canvas.width !== w || tgt.canvas.height !== h){ tgt.canvas.width = w; tgt.canvas.height = h; }
    else tgt.ctx.clearRect(0, 0, w, h);
    // The drawn region is the BOTTOM-left of the GL canvas (GL's origin),
    // which is rows [height-h, height) of its image.
    tgt.ctx.drawImage(glc, 0, glc.height - h, w, h, 0, 0, w, h);
    tgt.used = performance.now();
    evict(tgt.used);
    return {canvas: tgt.canvas, x: x0, y: y0, w: bw, h: bh};
  }
  function evict(now){
    for (const [id, t] of _targets){
      if (now - t.used > EVICT_AFTER_MS){ _targets.delete(id); _clocks.delete(id); }
    }
  }
  // True when the layer should keep repainting between messages.
  function isLive(layer){ return layer && layer.source_type === "visual"; }

  root.LSVisuals = {onState, frame, isLive, presets: () => Object.keys(PRESET_FS)};
})(window);
