// A small DOM-only Markdown renderer: model output is never interpreted as HTML.
export function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

export function icon(name) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.classList.add('icon');
  svg.setAttribute('aria-hidden', 'true');
  const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
  use.setAttribute('href', `#i-${name}`);
  svg.append(use);
  return svg;
}

function inline(parent, text) {
  const pattern = /(`[^`]+`|\*\*[^*]+\*\*|\[[^\]]+\]\([^\s)]+\))/g;
  let start = 0;
  for (const match of text.matchAll(pattern)) {
    parent.append(document.createTextNode(text.slice(start, match.index)));
    const value = match[0];
    if (value.startsWith('`')) parent.append(element('code', '', value.slice(1, -1)));
    else if (value.startsWith('**')) parent.append(element('strong', '', value.slice(2, -2)));
    else {
      const link = value.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
      try {
        const url = new URL(link[2]);
        if (!['https:', 'http:', 'mailto:'].includes(url.protocol)) throw new Error('Unsafe URL');
        const anchor = element('a', '', link[1]);
        anchor.href = url.href;
        anchor.target = '_blank';
        anchor.rel = 'noopener noreferrer';
        parent.append(anchor);
      } catch {
        parent.append(document.createTextNode(value));
      }
    }
    start = match.index + value.length;
  }
  parent.append(document.createTextNode(text.slice(start)));
}

function codeBlock(source, language) {
  const block = element('div', 'code-block');
  const heading = element('div', 'code-heading');
  const copy = element('button', 'copy-code');
  copy.type = 'button';
  copy.append(icon('copy'), document.createTextNode('复制'));
  copy.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(source);
      copy.textContent = '已复制';
      setTimeout(() => copy.replaceChildren(icon('copy'), document.createTextNode('复制')), 1500);
    } catch { copy.textContent = '无法复制'; }
  });
  heading.append(element('span', '', language || 'code'), copy);
  block.append(heading, element('pre', '', source));
  return block;
}

export function renderMarkdown(target, source) {
  const fragment = document.createDocumentFragment();
  const lines = source.replaceAll('\r\n', '\n').split('\n');
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    if (/^```/.test(line)) {
      const language = line.slice(3).trim();
      const content = [];
      i++;
      while (i < lines.length && !/^```/.test(lines[i])) content.push(lines[i++]);
      if (i < lines.length) i++;
      fragment.append(codeBlock(content.join('\n'), language));
      continue;
    }
    if (i + 1 < lines.length && line.includes('|') && /^\s*\|?\s*:?-{3,}/.test(lines[i + 1])) {
      const wrapper = element('div', 'markdown-table');
      const table = element('table');
      const cells = value => value.trim().replace(/^\|/, '').replace(/\|$/, '').split('|');
      const header = element('tr');
      cells(line).forEach(value => { const th = element('th'); inline(th, value.trim()); header.append(th); });
      const thead = element('thead'); thead.append(header); table.append(thead);
      const tbody = element('tbody'); i += 2;
      while (i < lines.length && lines[i].includes('|') && lines[i].trim()) {
        const row = element('tr');
        cells(lines[i++]).forEach(value => { const cell = element('td'); inline(cell, value.trim()); row.append(cell); });
        tbody.append(row);
      }
      table.append(tbody); wrapper.append(table); fragment.append(wrapper); continue;
    }
    const heading = line.match(/^(#{1,3})\s+(.*)$/);
    if (heading) { const node = element(`h${heading[1].length}`); inline(node, heading[2]); fragment.append(node); i++; continue; }
    if (/^\s*([-*]|\d+\.)\s/.test(line)) {
      const ordered = /^\s*\d+\./.test(line);
      const list = element(ordered ? 'ol' : 'ul');
      while (i < lines.length && /^\s*([-*]|\d+\.)\s/.test(lines[i])) {
        const item = element('li'); inline(item, lines[i++].replace(/^\s*([-*]|\d+\.)\s+/, '')); list.append(item);
      }
      fragment.append(list); continue;
    }
    if (line.startsWith('> ')) { const quote = element('blockquote'); inline(quote, line.slice(2)); fragment.append(quote); i++; continue; }
    const paragraph = element('p');
    inline(paragraph, line);
    i++;
    while (i < lines.length && lines[i].trim() && !/^(#{1,3}\s|```|>\s|\s*[-*]\s|\s*\d+\.\s)/.test(lines[i])) {
      if (i + 1 < lines.length && /^\s*\|?\s*:?-{3,}/.test(lines[i + 1])) break;
      paragraph.append(element('br')); inline(paragraph, lines[i++]);
    }
    fragment.append(paragraph);
  }
  target.replaceChildren(fragment);
}
