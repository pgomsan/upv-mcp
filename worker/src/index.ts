/**
 * Feed .ics de entregas de la UPV.
 *
 *   PUT /entregas.ics      upv-publish sube el feed (Authorization: Bearer FEED_TOKEN)
 *   GET /<FEED_PATH>.ics   el calendario del movil se suscribe aqui
 *   cualquier otra cosa    404 sin cuerpo
 *
 * iOS no manda cabeceras de autenticacion en una suscripcion de calendario, asi que
 * la URL larga del GET es la UNICA proteccion del feed. Es un secreto: sale de un
 * secret, nunca de un literal, se compara en tiempo constante y no se escribe en
 * ningun log (por eso los invocation logs estan desactivados en wrangler.jsonc).
 */

const PUT_PATH = "/entregas.ics";
const KV_KEY = "entregas.ics";
const MAX_BYTES = 1024 * 1024;

/** Un secreto corto o con caracteres raros es un error de configuracion, no un feed. */
const FEED_PATH_VALIDO = /^[A-Za-z0-9_-]{32,}$/;
const TOKEN_MINIMO = 32;

const vacia = (status: number): Response => new Response(null, { status });

function log(evento: string, datos: Record<string, string | number> = {}): void {
	// Nunca la URL, la ruta, las cabeceras ni el cuerpo: solo que paso y cuanto.
	console.log(JSON.stringify({ evento, ...datos }));
}

async function sha256(texto: string): Promise<ArrayBuffer> {
	return crypto.subtle.digest("SHA-256", new TextEncoder().encode(texto));
}

/** Comparacion en tiempo constante. Se hashea antes para no filtrar la longitud. */
async function iguales(recibido: string, esperado: string): Promise<boolean> {
	const [a, b] = await Promise.all([sha256(recibido), sha256(esperado)]);
	return crypto.subtle.timingSafeEqual(a, b);
}

/** Lee el cuerpo sin pasar de `tope` bytes. null si lo supera. */
async function leerConTope(cuerpo: ReadableStream<Uint8Array> | null, tope: number): Promise<Uint8Array | null> {
	if (cuerpo === null) return new Uint8Array();
	const lector = cuerpo.getReader();
	const trozos: Uint8Array[] = [];
	let total = 0;
	for (;;) {
		const { done, value } = await lector.read();
		if (done) break;
		total += value.byteLength;
		if (total > tope) {
			await lector.cancel();
			return null;
		}
		trozos.push(value);
	}
	const junto = new Uint8Array(total);
	let desplazamiento = 0;
	for (const trozo of trozos) {
		junto.set(trozo, desplazamiento);
		desplazamiento += trozo.byteLength;
	}
	return junto;
}

async function subir(request: Request, env: Env): Promise<Response> {
	if (!env.FEED_TOKEN || env.FEED_TOKEN.length < TOKEN_MINIMO) {
		log("config_invalida", { secreto: "FEED_TOKEN" });
		return vacia(500);
	}
	const autorizacion = request.headers.get("Authorization") ?? "";
	if (!(await iguales(autorizacion, `Bearer ${env.FEED_TOKEN}`))) {
		log("put_rechazado");
		return vacia(401);
	}

	const declarado = Number(request.headers.get("Content-Length") ?? "0");
	if (declarado > MAX_BYTES) return vacia(413);
	const bytes = await leerConTope(request.body, MAX_BYTES);
	if (bytes === null) return vacia(413);

	let ics: string;
	try {
		ics = new TextDecoder("utf-8", { fatal: true, ignoreBOM: false }).decode(bytes);
	} catch {
		return vacia(400);
	}
	if (!ics.startsWith("BEGIN:VCALENDAR\r\n") || !ics.endsWith("END:VCALENDAR\r\n")) {
		return vacia(400);
	}

	await env.FEED.put(KV_KEY, ics);
	log("feed_actualizado", { bytes: bytes.byteLength });
	return vacia(204);
}

async function servir(pathname: string, env: Env): Promise<Response> {
	if (!env.FEED_PATH || !FEED_PATH_VALIDO.test(env.FEED_PATH)) {
		log("config_invalida", { secreto: "FEED_PATH" });
		return vacia(404);
	}
	// Toda ruta incorrecta cuesta lo mismo (dos sha256) y no toca KV.
	if (!(await iguales(pathname, `/${env.FEED_PATH}.ics`))) return vacia(404);

	const ics = await env.FEED.get(KV_KEY);
	if (ics === null) return vacia(404);
	return new Response(ics, {
		headers: {
			"Content-Type": "text/calendar; charset=utf-8",
			"Cache-Control": "max-age=300",
		},
	});
}

export default {
	async fetch(request, env): Promise<Response> {
		try {
			const { pathname } = new URL(request.url);
			if (request.method === "PUT" && pathname === PUT_PATH) return await subir(request, env);
			if (request.method === "GET") return await servir(pathname, env);
			return vacia(404);
		} catch (error) {
			log("error", { tipo: error instanceof Error ? error.name : "desconocido" });
			return vacia(500);
		}
	},
} satisfies ExportedHandler<Env>;
