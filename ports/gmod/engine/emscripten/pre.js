// GameBox engine glue. The host page defines globalThis.gbConfig before this script runs, and
// prepends the JSON-safe part of it to the pthread worker script so the engine thread sees it:
//   game       mod folder passed to -game
//   args       extra command line
//   trace      base URL of tools/engine-server.mjs: files are served one by one and logged
//   manifest   pack index from tools/pack.mjs (files, per-map file lists, part hashes)
// Main thread only:
//   part(hash) Promise<ArrayBuffer> of one decompressed pack part (the host caches these)
//   onProgress(label, done, total), onStatus(text), onMiss(path), onFatal(message), onQuit(), onEngineUp()
const gb = globalThis.gbConfig || {}

const gbNormalize = path => path.replace(/\\/g, '/').replace(/^(\.\/|\/)+/, '').replace(/\/{2,}/g, '/').toLowerCase()

const gbSyncGet = url => {
	const xhr = new XMLHttpRequest()
	xhr.open('GET', url, false)
	xhr.responseType = 'arraybuffer'
	xhr.send()
	return xhr.status === 200 ? new Uint8Array(xhr.response) : null
}

const gbGlob = pattern => {
	const escaped = gbNormalize(pattern).replace(/[.+^${}()|[\]]/g, '\\$&').replace(/\*/g, '[^/]*').replace(/\?/g, '[^/]')
	return new RegExp(`^${escaped}$`)
}

// files[i] = [path, stream, offset, size]; streams[s] = part hashes of stream s.
const gbIndex = (() => {
	if (!gb.manifest) return null
	const byPath = new Map()
	gb.manifest.files.forEach((file, id) => byPath.set(file[0], id))
	return { byPath, files: gb.manifest.files }
})()

const GB_PART = gb.manifest?.partSize || 8 << 20

function gbPartsOf(file) {
	const [, stream, offset, size] = file
	const first = Math.floor(offset / GB_PART)
	const last = Math.floor((offset + Math.max(size, 1) - 1) / GB_PART)
	const parts = []
	for (let i = first; i <= last; i++) parts.push([stream, i])
	return parts
}

function gbSlice(file, partBytes) {
	const [, , offset, size] = file
	const out = new Uint8Array(size)
	let written = 0
	for (const [, index] of gbPartsOf(file)) {
		const bytes = partBytes(index)
		const start = Math.max(offset - index * GB_PART, 0)
		const end = Math.min(offset + size - index * GB_PART, bytes.length)
		out.set(bytes.subarray(start, end), written)
		written += end - start
	}
	return out
}

if (ENVIRONMENT_IS_PTHREAD) {
	// Engine thread: called from the filesystem hooks in filesystem_stdio.cpp.
	let traceIndex = null
	const traceHas = path => {
		traceIndex ??= new Set(JSON.parse(new TextDecoder().decode(gbSyncGet(`${gb.trace}index`))))
		return traceIndex.has(path)
	}

	// Packed games: the main thread loads the file into MEMFS while this thread waits.
	const fetchFromPack = id => {
		const lock = _malloc(4)
		const index = lock >> 2
		Atomics.store(HEAP32, index, 1)
		postMessage({ cmd: 'callHandler', handler: 'gbFetchFile', args: [index, id] })
		Atomics.wait(HEAP32, index, 1)
		const ok = Atomics.load(HEAP32, index) === 0
		_free(lock)
		return ok
	}

	Module['gbFetch'] = rawPath => {
		const path = gbNormalize(rawPath)
		if (gb.trace) return traceHas(path) ? gbSyncGet(`${gb.trace}file/${path}`) : null
		const id = gbIndex?.byPath.get(path)
		return id !== undefined && fetchFromPack(id) ? true : null
	}

	// Fatal engine errors (Sys_Error, missing files) show an SDL message box, which calls alert();
	// workers have none, so the thread died without saying why. Pass the message to the page.
	globalThis.alert = message => {
		postMessage({ cmd: 'callHandler', handler: 'gbFatal', args: [String(message)] })
	}

	Module['gbList'] = rawPattern => {
		if (gb.trace) {
			const found = gbSyncGet(`${gb.trace}list?pattern=${encodeURIComponent(gbNormalize(rawPattern))}`)
			return found && found.length ? new TextDecoder().decode(found) : null
		}
		if (!gbIndex) return null
		const re = gbGlob(rawPattern)
		// Map files are left out so a map listing can't pull every BSP into memory.
		const hits = gbIndex.files.filter(f => re.test(f[0]) && !f[0].endsWith('.bsp')).map(f => '/' + f[0])
		return hits.length ? hits.join('\n') : null
	}
} else {
	Module['arguments'] = [
		'-game', gb.game || 'hl2',
		'-noip',
		'-language', 'english',
		'-windowed',
		'-novid',
		// HL2's HDR maps lose dim light colours when quantized into 8-bit LDR lightmaps.
		// Keep their lighting in fp16; Portal retains its verified LDR rendering path.
		...(['hl2', 'episodic', 'ep2', 'lostcoast'].includes(gb.game) ? ['-gb_float_hdr'] : []),
		// Bloom reads render targets as sRGB, which WebGL can't switch per sampler (see
		// togles dxabstract.cpp m_cantAttachSRGB); it washes the frame out.
		'+mat_disable_bloom', '1',
		// Mix further ahead than the desktop default (0.1 s): at low-end frame rates the mixer
		// otherwise falls behind the audio callback and it replays stale buffer contents.
		'+snd_mixahead', '0.25',
		...(gb.args || [])
	]

	const writeFile = (path, bytes) => {
		const full = '/' + path
		FS.mkdirTree(full.slice(0, full.lastIndexOf('/')))
		FS.writeFile(full, bytes)
	}

	// Loads every listed file that isn't in MEMFS yet, fetching each part once.
	const loaded = new Set()
	const loadFiles = async (label, ids) => {
		const pending = ids.filter(id => !loaded.has(id))
		if (!pending.length) return
		const parts = new Map()
		for (const id of pending) {
			for (const [stream, index] of gbPartsOf(gbIndex.files[id])) {
				parts.set(`${stream}:${index}`, gb.manifest.streams[stream][index])
			}
		}
		const total = parts.size
		let done = 0
		if (label) gb.onProgress?.(label, 0, total)
		const bytes = new Map()
		const queue = [...parts]
		const fetchNext = async () => {
			while (queue.length) {
				const [key, hash] = queue.shift()
				bytes.set(key, new Uint8Array(await gb.part(hash)))
				if (label) gb.onProgress?.(label, ++done, total)
			}
		}
		await Promise.all(Array.from({ length: Math.min(4, total) }, fetchNext))
		for (const id of pending) {
			const file = gbIndex.files[id]
			writeFile(file[0], gbSlice(file, index => bytes.get(`${file[1]}:${index}`)))
			loaded.add(id)
		}
	}

	// Only the base set and the current map stay in MEMFS. Files the engine still has open
	// keep their data until closed.
	const evictExcept = keepIds => {
		const keep = new Set([...gb.manifest.base, ...keepIds])
		for (const id of loaded) {
			if (keep.has(id)) continue
			try { FS.unlink('/' + gbIndex.files[id][0]) } catch {}
			loaded.delete(id)
		}
	}

	// Trace bundles use the original chunk layout: { pathLen u32, dataLen u32, path, data }[]
	const unpackBundle = buffer => {
		const dv = new DataView(buffer)
		const decoder = new TextDecoder()
		for (let offset = 0; offset < dv.byteLength;) {
			const pathLen = dv.getUint32(offset, true)
			const dataLen = dv.getUint32(offset + 4, true)
			const path = decoder.decode(new Uint8Array(buffer, offset + 8, pathLen))
			writeFile(gbNormalize(path), new Uint8Array(buffer, offset + 8 + pathLen, dataLen))
			offset += 8 + pathLen + dataLen
		}
	}

	const loadMap = async mapName => {
		if (gb.trace) {
			await fetch(`${gb.trace}map/${encodeURIComponent(mapName)}`)
			return
		}
		const ids = gb.manifest?.maps[mapName.toLowerCase()] || []
		evictExcept(ids)
		await loadFiles(mapName, ids)
	}

	const release = (index, value) => {
		Atomics.store(HEAP32, index, value)
		Atomics.notify(HEAP32, index)
	}

	// engine/vengineserver_impl.cpp FindMap blocks the server thread on this lock.
	Module['downloadMap'] = (lock, mapName) => {
		gb.onStatus?.(`Loading ${mapName}`)
		// Always end with a complete progress report, even when every file was already loaded.
		loadMap(mapName).then(() => gb.onProgress?.(mapName, 1, 1), error => {
			console.error(error)
			gb.onStatus?.(`Could not load ${mapName}: ${error.message}`)
		}).finally(() => release(lock, 0))
	}

	// engine/host_cmd.cpp Host_Quit_f: flush saves and config, then hand back to the page.
	Module['gbQuit'] = () => {
		FS.syncfs(false, error => {
			if (error) console.error('gamebox: saving to IndexedDB failed', error)
			gb.onQuit?.()
		})
	}

	Module['gbEngineReady'] = () => gb.onEngineUp?.()

	// Queues a console command for engine/host.cpp Host_RunPageCommands (tests drive the engine with it).
	Module['gbCommand'] = text => {
		const base = Module['gbCommandMailbox']
		if (!base || Atomics.load(HEAP32, base >> 2)) return false
		const bytes = new TextEncoder().encode(text).subarray(0, 1022)
		HEAPU8.set(bytes, base + 4)
		HEAPU8[base + 4 + bytes.length] = 0
		Atomics.store(HEAP32, base >> 2, 1)
		return true
	}

	Module['gbFatal'] = message => {
		console.error('gamebox: engine error:', message)
		gb.onStatus?.(`Engine error: ${message}`)
		gb.onFatal?.(message)
	}

	// A file the traces didn't predict (see fetchFromPack).
	Module['gbFetchFile'] = (lock, id) => {
		const path = gbIndex.files[id][0]
		console.warn('gamebox: loading outside the pack plan:', path)
		gb.onMiss?.(path)
		loadFiles('', [id]).then(() => release(lock, 0), error => {
			console.error(error)
			release(lock, 2)
		})
	}

	// Saves and config.cfg live in IndexedDB; everything else is rebuilt from packs.
	const persistRoot = '/gamebox-persist'
	let syncing = false
	const persist = () => {
		if (syncing) return
		syncing = true
		FS.syncfs(false, error => {
			syncing = false
			if (error) console.error('gamebox: saving to IndexedDB failed', error)
		})
	}
	Module['gbPersist'] = persist

	Module['preRun'] = [
		...(Module['preRun'] || []),
		() => {
			const mod = gb.game || 'hl2'
			FS.mkdirTree(`/${mod}/save`)
			FS.mkdirTree(`/${mod}/cfg`)
			FS.mkdirTree(persistRoot)
			FS.mount(IDBFS, {}, `/${mod}/save`)
			FS.mount(IDBFS, {}, persistRoot)
			FS.symlink(`${persistRoot}/config.cfg`, `/${mod}/cfg/config.cfg`)
			addRunDependency('gamebox_persist')
			FS.syncfs(true, error => {
				if (error) console.error('gamebox: reading saves failed', error)
				removeRunDependency('gamebox_persist')
				setInterval(persist, 5000)
				addEventListener('pagehide', persist)
				addEventListener('visibilitychange', () => document.visibilityState === 'hidden' && persist())
			})

			addRunDependency('gamebox_base')
			const base = gb.trace
				? fetch(`${gb.trace}base`).then(r => r.arrayBuffer()).then(unpackBundle)
				: loadFiles('base', gb.manifest.base)
			base.then(() => removeRunDependency('gamebox_base'), error => {
				console.error(error)
				gb.onStatus?.(`Could not load game data: ${error.message}`)
			})
		}
	]
}

// glGetShaderiv(GL_INFO_LOG_LENGTH) occasionally gets a shader id with no live WebGLShader
// behind it, which throws and kills the engine thread. Report it instead of crashing.
// SDL asks for pointer lock every frame while relative mouse mode is on, which would grab the mouse
// back the moment the player lets go of it (Esc, switching tabs). Only a click on the game takes it.
if (!ENVIRONMENT_IS_PTHREAD && typeof Element !== 'undefined' && Element.prototype.requestPointerLock) {
	const requestPointerLock = Element.prototype.requestPointerLock
	let clickedAt = -Infinity
	addEventListener('pointerdown', e => { if (e.target instanceof HTMLCanvasElement) clickedAt = performance.now() }, true)
	Element.prototype.requestPointerLock = function (...a) {
		if (document.pointerLockElement === this || performance.now() - clickedAt > 1000) return Promise.resolve()
		clickedAt = -Infinity
		const result = requestPointerLock.apply(this, a)
		result?.catch?.(() => {})
		return result
	}
}

if (ENVIRONMENT_IS_PTHREAD && typeof WebGL2RenderingContext !== 'undefined') {
	const infoLog = WebGL2RenderingContext.prototype.getShaderInfoLog
	WebGL2RenderingContext.prototype.getShaderInfoLog = function (shader) {
		if (shader instanceof WebGLShader) return infoLog.call(this, shader)
		const stack = new Error().stack
		console.warn('gamebox: getShaderInfoLog on a dead shader', shader, stack)
		if (gb.trace) {
			const xhr = new XMLHttpRequest()
			xhr.open('POST', `${gb.trace}log`, false)
			xhr.send(`[gl] dead shader ${shader}\n${stack}\n`)
		}
		return ''
	}
}

// WebGL rejects feedback loops: drawing into a texture while sampling it (Source's intro effect,
// motion blur and HDR luminance passes do, which D3D9 tolerates), and blitting a texture onto
// itself. Such draws sample a copy of the render target instead; such blits go through the copy.
if (ENVIRONMENT_IS_PTHREAD && typeof WebGL2RenderingContext !== 'undefined') {
	const proto = WebGL2RenderingContext.prototype
	const orig = {}
	for (const fn of ['bindFramebuffer', 'framebufferTexture2D', 'activeTexture', 'bindTexture', 'texImage2D', 'texStorage2D', 'drawElements', 'drawArrays', 'drawRangeElements', 'blitFramebuffer']) orig[fn] = proto[fn]
	let drawFB = null, readFB = null, unit = 0
	const units = []
	const color = new WeakMap() // framebuffer -> color attachment 0 texture
	const shape = new WeakMap() // texture -> { w, h, format } of level 0
	const shadows = new Map() // `${w}x${h}:${format}` -> { tex, fb }

	proto.bindFramebuffer = function (target, fb) {
		if (target !== this.READ_FRAMEBUFFER) drawFB = fb
		if (target !== this.DRAW_FRAMEBUFFER) readFB = fb
		return orig.bindFramebuffer.call(this, target, fb)
	}
	proto.framebufferTexture2D = function (target, attachment, textarget, tex, level) {
		const fb = target === this.READ_FRAMEBUFFER ? readFB : drawFB
		if (fb && attachment === this.COLOR_ATTACHMENT0) tex && textarget === this.TEXTURE_2D ? color.set(fb, tex) : color.delete(fb)
		return orig.framebufferTexture2D.call(this, target, attachment, textarget, tex, level)
	}
	proto.activeTexture = function (u) { unit = u - this.TEXTURE0; return orig.activeTexture.call(this, u) }
	proto.bindTexture = function (target, tex) {
		if (target === this.TEXTURE_2D) units[unit] = tex
		return orig.bindTexture.call(this, target, tex)
	}
	proto.texImage2D = function (target, level, format, w, h, ...rest) {
		if (target === this.TEXTURE_2D && level === 0 && units[unit] && typeof w === 'number') shape.set(units[unit], { w, h, format })
		return orig.texImage2D.call(this, target, level, format, w, h, ...rest)
	}
	proto.texStorage2D = function (target, levels, format, w, h) {
		if (target === this.TEXTURE_2D && units[unit]) shape.set(units[unit], { w, h, format })
		return orig.texStorage2D.call(this, target, levels, format, w, h)
	}

	// Copies the given framebuffer's color texture into a cached shadow texture of the same shape.
	const copyOf = (gl, fb, tex) => {
		const sh = shape.get(tex)
		if (!sh) return null
		const key = `${sh.w}x${sh.h}:${sh.format}`
		let shadow = shadows.get(key)
		if (!shadow) {
			const t = gl.createTexture()
			orig.activeTexture.call(gl, gl.TEXTURE0 + unit)
			orig.bindTexture.call(gl, gl.TEXTURE_2D, t)
			orig.texStorage2D.call(gl, gl.TEXTURE_2D, 1, sh.format, sh.w, sh.h)
			gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR)
			gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR)
			gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE)
			gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE)
			orig.bindTexture.call(gl, gl.TEXTURE_2D, units[unit] ?? null)
			const f = gl.createFramebuffer()
			orig.bindFramebuffer.call(gl, gl.DRAW_FRAMEBUFFER, f)
			orig.framebufferTexture2D.call(gl, gl.DRAW_FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, t, 0)
			shadow = { tex: t, fb: f, w: sh.w, h: sh.h }
			shadows.set(key, shadow)
		}
		const scissor = gl.isEnabled(gl.SCISSOR_TEST)
		if (scissor) gl.disable(gl.SCISSOR_TEST)
		orig.bindFramebuffer.call(gl, gl.READ_FRAMEBUFFER, fb)
		orig.bindFramebuffer.call(gl, gl.DRAW_FRAMEBUFFER, shadow.fb)
		orig.blitFramebuffer.call(gl, 0, 0, shadow.w, shadow.h, 0, 0, shadow.w, shadow.h, gl.COLOR_BUFFER_BIT, gl.NEAREST)
		orig.bindFramebuffer.call(gl, gl.READ_FRAMEBUFFER, readFB)
		orig.bindFramebuffer.call(gl, gl.DRAW_FRAMEBUFFER, drawFB)
		if (scissor) gl.enable(gl.SCISSOR_TEST)
		return shadow
	}

	const guarded = name => function (...args) {
		const target = drawFB && color.get(drawFB)
		const loop = target ? units.indexOf(target) : -1
		if (loop < 0) return orig[name].apply(this, args)
		const shadow = copyOf(this, drawFB, target)
		if (!shadow) return orig[name].apply(this, args)
		const active = unit
		for (let u = 0; u < units.length; u++) {
			if (units[u] !== target) continue
			orig.activeTexture.call(this, this.TEXTURE0 + u)
			orig.bindTexture.call(this, this.TEXTURE_2D, shadow.tex)
		}
		const result = orig[name].apply(this, args)
		for (let u = 0; u < units.length; u++) {
			if (units[u] !== target) continue
			orig.activeTexture.call(this, this.TEXTURE0 + u)
			orig.bindTexture.call(this, this.TEXTURE_2D, target)
		}
		orig.activeTexture.call(this, this.TEXTURE0 + active)
		return result
	}
	proto.drawElements = guarded('drawElements')
	proto.drawArrays = guarded('drawArrays')
	proto.drawRangeElements = guarded('drawRangeElements')

	proto.blitFramebuffer = function (...args) {
		const src = readFB && color.get(readFB)
		if (!src || src !== (drawFB && color.get(drawFB))) return orig.blitFramebuffer.apply(this, args)
		const shadow = copyOf(this, readFB, src)
		if (!shadow) return orig.blitFramebuffer.apply(this, args)
		orig.bindFramebuffer.call(this, this.READ_FRAMEBUFFER, shadow.fb)
		const result = orig.blitFramebuffer.apply(this, args)
		orig.bindFramebuffer.call(this, this.READ_FRAMEBUFFER, readFB)
		return result
	}
}

// Trace-mode GL debugging (?gldebug=1): translated shaders and GL errors after texture uploads
// go to the trace server, since worker consoles aren't visible to the test driver.
if (ENVIRONMENT_IS_PTHREAD && gb.trace && gb.glDebug && typeof WebGL2RenderingContext !== 'undefined') {
	const post = (op, body) => {
		const xhr = new XMLHttpRequest()
		xhr.open('POST', `${gb.trace}${op}`, false)
		xhr.send(body)
	}
	const proto = WebGL2RenderingContext.prototype
	const seen = new Set()
	const shaderSource = proto.shaderSource
	// ?shaderdebug=<glsl expr>: LightmappedGeneric pixel shaders output that expression instead.
	proto.shaderSource = function (shader, source) {
		const label = source.match(/ps-file (\w+)/)
		if (label) labelOf.set(shader, label[1])
		if (gb.shaderDebug && /ps-file lightmappedgeneric/.test(source)) {
			source = source.replace(/gl_FragData\[0\] = (r\d+);/, (m, r) => `${m} gl_FragData[0] = vec4(${gb.shaderDebug}, 1.0);`)
		}
		if (!seen.has(source)) {
			seen.add(source)
			post('shader', source)
		}
		return shaderSource.call(this, shader, source)
	}
	const names = {}
	for (const k in WebGL2RenderingContext) if (typeof WebGL2RenderingContext[k] === 'number') names[WebGL2RenderingContext[k]] ??= k
	const errors = new Map()
	const raw = {}
	for (const fn of Object.getOwnPropertyNames(proto)) if (typeof Object.getOwnPropertyDescriptor(proto, fn).value === 'function') raw[fn] = proto[fn]
	// Texture bound on each unit, for feedback-loop checks.
	const boundTextures = new Map()
	let activeUnit = 0
	const activeTexture = proto.activeTexture
	proto.activeTexture = function (unit) { activeUnit = unit - this.TEXTURE0; return activeTexture.call(this, unit) }
	const bindTexture = proto.bindTexture
	proto.bindTexture = function (target, tex) {
		for (const [t, u] of boundTextures) if (u === activeUnit) boundTextures.delete(t)
		if (tex) boundTextures.set(tex, activeUnit)
		return bindTexture.call(this, target, tex)
	}
	// Draw calls per pixel-shader label, summarized every 300 frames.
	const labelOf = new WeakMap()
	const programLabel = new WeakMap()
	let current = '?', frame = 0
	const draws = new Map()
	const ss = proto.shaderSource
	const attach = proto.attachShader
	proto.attachShader = function (program, shader) {
		const label = labelOf.get(shader)
		if (label) programLabel.set(program, label)
		return attach.call(this, program, shader)
	}
	const compileShader = proto.compileShader
	proto.compileShader = function (shader) {
		compileShader.call(this, shader)
		if (!this.getShaderParameter(shader, this.COMPILE_STATUS)) {
			post('log', `[glsl] compile failed ${labelOf.get(shader) || '?'}: ${raw.getShaderInfoLog.call(this, shader)}\n`)
		}
	}
	const linkProgram = proto.linkProgram
	proto.linkProgram = function (program) {
		linkProgram.call(this, program)
		if (!this.getProgramParameter(program, this.LINK_STATUS)) {
			post('log', `[glsl] link failed ${programLabel.get(program) || '?'}: ${this.getProgramInfoLog(program)}\n`)
		}
	}
	const useProgram = proto.useProgram
	proto.useProgram = function (program) {
		current = (program && programLabel.get(program)) || '?'
		return useProgram.call(this, program)
	}
	for (const fn of ['drawElements', 'drawRangeElements', 'drawArrays']) {
		const orig = proto[fn]
		proto[fn] = function (...args) {
			draws.set(current, (draws.get(current) || 0) + 1)
			return orig.apply(this, args)
		}
	}
	const rAF = self.requestAnimationFrame
	self.requestAnimationFrame = cb => rAF.call(self, t => {
		if (++frame % 300 === 0) {
			post('log', `[draws] frame ${frame}: ${[...draws].sort((a, b) => b[1] - a[1]).map(([k, v]) => `${k}=${v}`).join(' ')}\n`)
			draws.clear()
		}
		cb(t)
	})

	// Texture uploads: format, size and the average of the first bytes, once per shape.
	const uploads = new Map()
	for (const fn of ['texImage2D', 'texSubImage2D', 'texStorage2D']) {
		const orig = proto[fn]
		proto[fn] = function (...args) {
			const at = args.findIndex(a => ArrayBuffer.isView(a))
			const data = at >= 0 ? args[at] : null
			const start = at >= 0 && typeof args[at + 1] === 'number' ? args[at + 1] : 0
			const key = `${fn} ${args.slice(0, fn === 'texSubImage2D' ? 8 : 8).map(a => typeof a === 'number' ? (names[a] && a > 255 ? names[a] : a) : a === null ? 'null' : typeof a).join(',')}`
			const n = (uploads.get(key) || 0) + 1
			uploads.set(key, n)
			if (n <= 6) {
				let stats = ''
				if (data) {
					const bytes = new Uint8Array(data.buffer, data.byteOffset + start * data.BYTES_PER_ELEMENT, 4096)
					const sum = [0, 0, 0, 0]
					for (let i = 0; i < bytes.length; i++) sum[i & 3] += bytes[i]
					stats = ' avg ' + sum.map(v => Math.round(v / (bytes.length / 4))).join('/')
				}
				post('log', `[tex] ${key}${stats}\n`)
			}
			return orig.apply(this, args)
		}
	}
	for (const fn of Object.getOwnPropertyNames(proto)) {
		const desc = Object.getOwnPropertyDescriptor(proto, fn)
		if (typeof desc.value !== 'function' || fn === 'getError' || fn === 'constructor' || fn === 'shaderSource') continue
		const orig = desc.value
		proto[fn] = function (...args) {
			const r = orig.apply(this, args)
			const e = orig === proto.getError ? 0 : this.getError()
			if (e) {
				const key = `${fn} ${names[e] || e} ${args.slice(0, 9).map(a => typeof a === 'number' ? (names[a] && a > 255 ? names[a] : a) : a === null ? 'null' : typeof a).join(',')}`
				const n = (errors.get(key) || 0) + 1
				errors.set(key, n)
				if (n === 1 || n === 1000) post('log', `[gl] ${key} x${n}\n`)
				if (e === this.INVALID_OPERATION && n <= 2 && (fn === 'drawElements' || fn === 'blitFramebuffer')) {
					// Feedback loops (sampling a texture attached to the bound framebuffer) and blit formats.
					const q = (target, a, pname) => raw.getFramebufferAttachmentParameter.call(this, target, a, pname)
					const fbInfo = target => {
						const fbo = raw.getParameter.call(this, target === this.READ_FRAMEBUFFER ? this.READ_FRAMEBUFFER_BINDING : this.DRAW_FRAMEBUFFER_BINDING)
						if (!fbo) return 'default'
						const type = q(target, this.COLOR_ATTACHMENT0, this.FRAMEBUFFER_ATTACHMENT_OBJECT_TYPE)
						if (type === this.NONE) return 'no color'
						const obj = q(target, this.COLOR_ATTACHMENT0, this.FRAMEBUFFER_ATTACHMENT_OBJECT_NAME)
						return `${names[type]} r${q(target, this.COLOR_ATTACHMENT0, this.FRAMEBUFFER_ATTACHMENT_RED_SIZE)} ${names[q(target, this.COLOR_ATTACHMENT0, this.FRAMEBUFFER_ATTACHMENT_COLOR_ENCODING)]}${obj && boundTextures.has(obj) ? ' FEEDBACK-LOOP(bound on unit ' + boundTextures.get(obj) + ')' : ''}`
					}
					post('log', `[gl]   ${fn}: program ${current} read=${fbInfo(this.READ_FRAMEBUFFER)} draw=${fbInfo(this.DRAW_FRAMEBUFFER)}\n`)
				}
				if (e === this.INVALID_FRAMEBUFFER_OPERATION && n === 1) {
					const fb = this.FRAMEBUFFER
					const att = a => {
						const type = raw.getFramebufferAttachmentParameter.call(this, fb, a, this.FRAMEBUFFER_ATTACHMENT_OBJECT_TYPE)
						if (type === this.NONE) return 'none'
						const q = p => raw.getFramebufferAttachmentParameter.call(this, fb, a, p)
						return `${names[type]} r${q(this.FRAMEBUFFER_ATTACHMENT_RED_SIZE)} d${q(this.FRAMEBUFFER_ATTACHMENT_DEPTH_SIZE)} s${q(this.FRAMEBUFFER_ATTACHMENT_STENCIL_SIZE)} ${names[q(this.FRAMEBUFFER_ATTACHMENT_COLOR_ENCODING)]}`
					}
					post('log', `[gl]   status ${names[orig === proto.checkFramebufferStatus ? 0 : raw.checkFramebufferStatus.call(this, fb)]} color0 ${att(this.COLOR_ATTACHMENT0)} depth ${att(this.DEPTH_ATTACHMENT)} stencil ${att(this.STENCIL_ATTACHMENT)} ds ${att(this.DEPTH_STENCIL_ATTACHMENT)}\n`)
				}
			}
			return r
		}
	}
}
