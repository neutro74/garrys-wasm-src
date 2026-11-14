// GPU backends for GameBox: WebGPU (preferred) with a WebGL2 fallback.
//
// This is the layer the lifted D3D9 device will target. It owns the swap chain,
// render passes, pipelines and readback; the D3D9 shim only translates state
// and draw calls into this API.

const TRIANGLE_WGSL = `
struct VOut { @builtin(position) pos: vec4f, @location(0) color: vec4f };
@vertex fn vs(@builtin(vertex_index) i: u32) -> VOut {
  var p = array<vec2f, 3>(vec2f(-0.6, -0.5), vec2f(0.6, -0.5), vec2f(0.0, 0.6));
  var c = array<vec4f, 3>(vec4f(1,0,0,1), vec4f(0,1,0,1), vec4f(0,0,1,1));
  var o: VOut;
  o.pos = vec4f(p[i], 0.0, 1.0);
  o.color = c[i];
  return o;
}
@fragment fn fs(in: VOut) -> @location(0) vec4f { return in.color; }
`;

const TRIANGLE_GLSL_VS = `#version 300 es
in vec2 a_pos; in vec3 a_color; out vec3 v_color;
void main() { v_color = a_color; gl_Position = vec4(a_pos, 0.0, 1.0); }`;
const TRIANGLE_GLSL_FS = `#version 300 es
precision mediump float; in vec3 v_color; out vec4 o;
void main() { o = vec4(v_color, 1.0); }`;

function hexToRgba(color) {
  const [r, g, b, a = 255] = color;
  return { r: r / 255, g: g / 255, b: b / 255, a: a / 255, bytes: [r, g, b, a] };
}

class WebGpuBackend {
  constructor(canvas, device, context, format) {
    this.name = 'webgpu'; this.canvas = canvas; this.device = device;
    this.context = context; this.format = format;
    this.cleared = [0, 0, 0, 255];
    this.pipeline = null;
  }

  static async create(canvas) {
    if (!navigator.gpu) throw new Error('WebGPU is not available');
    const adapter = await navigator.gpu.requestAdapter();
    if (!adapter) throw new Error('no WebGPU adapter');
    const device = await adapter.requestDevice();
    const context = canvas.getContext('webgpu');
    if (!context) throw new Error('canvas has no webgpu context');
    const format = navigator.gpu.getPreferredCanvasFormat();
    context.configure({ device, format, alphaMode: 'opaque',
      usage: GPUTextureUsage.RENDER_ATTACHMENT | GPUTextureUsage.COPY_SRC });
    const backend = new WebGpuBackend(canvas, device, context, format);
    backend.adapterInfo = adapter.info ? { ...adapter.info } : {};
    return backend;
  }

  clear(color) {
    const c = hexToRgba(color);
    this.cleared = c.bytes;
    this.frameTexture = this.context.getCurrentTexture();
    const encoder = this.device.createCommandEncoder();
    const pass = encoder.beginRenderPass({
      colorAttachments: [{ view: this.frameTexture.createView(),
        clearValue: { r: c.r, g: c.g, b: c.b, a: c.a }, loadOp: 'clear', storeOp: 'store' }],
    });
    pass.end();
    this.device.queue.submit([encoder.finish()]);
  }

  ensurePipeline() {
    if (this.pipeline) return this.pipeline;
    const module = this.device.createShaderModule({ code: TRIANGLE_WGSL });
    this.pipeline = this.device.createRenderPipeline({
      layout: 'auto',
      vertex: { module, entryPoint: 'vs' },
      fragment: { module, entryPoint: 'fs',
        targets: [{ format: this.format, blend: {
          color: { srcFactor: 'src-alpha', dstFactor: 'one-minus-src-alpha', operation: 'add' },
          alpha: { srcFactor: 'one', dstFactor: 'one-minus-src-alpha', operation: 'add' } } }] },
      primitive: { topology: 'triangle-list' },
    });
    return this.pipeline;
  }

  drawTriangle() {
    this.frameTexture = this.context.getCurrentTexture();
    const encoder = this.device.createCommandEncoder();
    const pass = encoder.beginRenderPass({
      colorAttachments: [{ view: this.frameTexture.createView(),
        clearValue: { r: 0, g: 0, b: 0, a: 1 }, loadOp: 'clear', storeOp: 'store' }],
    });
    pass.setPipeline(this.ensurePipeline());
    pass.draw(3);
    pass.end();
    this.device.queue.submit([encoder.finish()]);
  }

  async readPixels(x = 0, y = 0) {
    // Copy the texture that was actually rendered, not the next swap-chain frame.
    const texture = this.frameTexture || this.context.getCurrentTexture();
    const bytesPerRow = 256; // copyTextureToBuffer requires 256-byte alignment
    const buffer = this.device.createBuffer({ size: bytesPerRow, usage: GPUBufferUsage.COPY_DST | GPUBufferUsage.MAP_READ });
    const encoder = this.device.createCommandEncoder();
    encoder.copyTextureToBuffer({ texture, origin: { x, y } },
      { buffer, bytesPerRow, rowsPerImage: 1 }, { width: 1, height: 1 });
    this.device.queue.submit([encoder.finish()]);
    await buffer.mapAsync(GPUMapMode.READ);
    const data = new Uint8Array(buffer.getMappedRange()).slice(0, 4);
    buffer.unmap(); buffer.destroy();
    // Canvas formats are usually BGRA; report RGBA.
    return this.format.startsWith('bgra') ? new Uint8Array([data[2], data[1], data[0], data[3]]) : data;
  }

  dispose() { this.context.unconfigure?.(); }
}

class WebGl2Backend {
  constructor(canvas, gl) {
    this.name = 'webgl2'; this.canvas = canvas; this.gl = gl; this.program = null; this.buffer = null;
  }

  static async create(canvas) {
    const gl = canvas.getContext('webgl2', { preserveDrawingBuffer: true, antialias: false, alpha: false });
    if (!gl) throw new Error('WebGL2 is not available');
    return new WebGl2Backend(canvas, gl);
  }

  clear(color) {
    const { bytes } = hexToRgba(color);
    const gl = this.gl;
    gl.viewport(0, 0, this.canvas.width, this.canvas.height);
    gl.clearColor(bytes[0] / 255, bytes[1] / 255, bytes[2] / 255, bytes[3] / 255);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
  }

  ensurePipeline() {
    if (this.program) return this.program;
    const gl = this.gl;
    const compile = (type, src) => {
      const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s);
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
      return s;
    };
    const p = gl.createProgram();
    gl.attachShader(p, compile(gl.VERTEX_SHADER, TRIANGLE_GLSL_VS));
    gl.attachShader(p, compile(gl.FRAGMENT_SHADER, TRIANGLE_GLSL_FS));
    gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
    this.program = p;
    this.buffer = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, this.buffer);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([
      -0.6, -0.5, 1, 0, 0, 0.6, -0.5, 0, 1, 0, 0.0, 0.6, 0, 0, 1]), gl.STATIC_DRAW);
    return p;
  }

  drawTriangle() {
    const gl = this.gl, p = this.ensurePipeline();
    gl.viewport(0, 0, this.canvas.width, this.canvas.height);
    gl.clearColor(0, 0, 0, 1); gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    gl.useProgram(p);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.buffer);
    const pos = gl.getAttribLocation(p, 'a_pos'), col = gl.getAttribLocation(p, 'a_color');
    gl.enableVertexAttribArray(pos); gl.vertexAttribPointer(pos, 2, gl.FLOAT, false, 20, 0);
    gl.enableVertexAttribArray(col); gl.vertexAttribPointer(col, 3, gl.FLOAT, false, 20, 8);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
  }

  async readPixels(x = 0, y = 0) {
    const gl = this.gl, out = new Uint8Array(4);
    gl.readPixels(x, this.canvas.height - 1 - y, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, out);
    return out;
  }

  dispose() { /* context is owned by the canvas */ }
}

// Create the best available backend. `prefer` is an ordered list of names.
export async function createGpuBackend(canvas, { prefer = ['webgpu', 'webgl2'], force = null } = {}) {
  const order = force ? [force] : prefer;
  const errors = [];
  for (const name of order) {
    try {
      if (name === 'webgpu') return await WebGpuBackend.create(canvas);
      if (name === 'webgl2') return await WebGl2Backend.create(canvas);
    } catch (e) { errors.push(`${name}: ${e.message}`); }
  }
  throw new Error(`no GPU backend available (${errors.join('; ')})`);
}

export const gpuBackendNames = ['webgpu', 'webgl2'];
