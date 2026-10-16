// Barcode decoding in the browser. Safari on iPhone has no built-in BarcodeDetector, so this uses the
// barcode-detector ponyfill (ZXing-C++ compiled to WebAssembly) everywhere, for the same behaviour on every
// device. The .wasm file (about 1 MB) is bundled and served by our own API, never fetched from a CDN, and
// this module is only loaded when a scan starts, so it does not slow down the rest of the app.
import { BarcodeDetector, prepareZXingModule } from 'barcode-detector/ponyfill';
import wasmUrl from 'zxing-wasm/reader/zxing_reader.wasm?url';

prepareZXingModule({
	overrides: {
		locateFile: (/** @type {string} */ path, /** @type {string} */ prefix) => (path.endsWith('.wasm') ? wasmUrl : prefix + path)
	}
});

/** A detector for the codes on food packages: EAN-13, EAN-8 and UPC-A. */
export function createDetector() {
	return new BarcodeDetector({ formats: ['ean_13', 'ean_8', 'upc_a'] });
}

/**
 * GS1 check digit (the same check as the API): a misread almost always fails it.
 * @param {string} code
 */
export function checkDigitOk(code) {
	if (!/^(\d{8}|\d{12,14})$/.test(code)) return false;
	const body = code.slice(0, -1);
	let total = 0;
	for (let i = 0; i < body.length; i++) total += Number(body[body.length - 1 - i]) * (i % 2 === 0 ? 3 : 1);
	return (10 - (total % 10)) % 10 === Number(code[code.length - 1]);
}
