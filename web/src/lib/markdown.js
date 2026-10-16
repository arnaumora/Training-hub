// The small Markdown subset the AI reports use, to HTML: "## " headings, "- " or "1. " list items,
// paragraphs, **bold** and *italic*. Everything is HTML-escaped first, so model output can never inject markup.

/** @param {string} s */
const escape = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');

/** @param {string} s */
const inline = (s) =>
	escape(s)
		.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
		.replace(/(^|[^*])\*([^*\s][^*]*?)\*(?!\*)/g, '$1<em>$2</em>');

/** @param {string} text */
export function markdown(text) {
	const out = [];
	/** @type {string[]} */
	let para = [];
	/** @type {{ tag: string, items: string[] } | null} */
	let list = null;
	const flush = () => {
		if (para.length) out.push(`<p>${inline(para.join(' '))}</p>`);
		para = [];
		if (list) out.push(`<${list.tag}>${list.items.map((i) => `<li>${inline(i)}</li>`).join('')}</${list.tag}>`);
		list = null;
	};
	for (const raw of text.split('\n')) {
		const line = raw.trim();
		const h = line.match(/^#{1,6}\s+(.*)$/);
		const ul = line.match(/^[-*]\s+(.*)$/);
		const ol = line.match(/^\d+[.)]\s+(.*)$/);
		if (!line) flush();
		else if (h) {
			flush();
			out.push(`<h4>${inline(h[1])}</h4>`);
		} else if (ul || ol) {
			const tag = ul ? 'ul' : 'ol';
			if (para.length || (list && list.tag !== tag)) flush();
			if (!list) list = { tag, items: [] };
			list.items.push((ul ?? ol)?.[1] ?? '');
		} else if (list) {
			list.items[list.items.length - 1] += ` ${line}`; // a wrapped list item
		} else para.push(line);
	}
	flush();
	return out.join('\n');
}
