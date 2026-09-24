/* Progressive enhancement: without JS the complete Markdown remains readable. */
(() => {
  let nextId = 0;
  const cards = new Map();
  function update(content) {
    const state = cards.get(content);
    if (!state) return;
    const { preview, button } = state;
    const overflows = content.getBoundingClientRect().height > parseFloat(getComputedStyle(preview).lineHeight) * 6 + 1;
    button.hidden = !overflows;
    preview.dataset.collapsed = String(overflows && button.getAttribute('aria-expanded') !== 'true');
  }
  const observer = new ResizeObserver(entries => entries.forEach(entry => update(entry.target)));
  function init() {
    // HTMX can replace the entire reader; release detached elements.
    for (const [content] of cards) {
      if (!content.isConnected) { observer.unobserve(content); cards.delete(content); }
    }
    document.querySelectorAll('.notes-preview-content').forEach(content => {
      if (cards.has(content)) return;
      const preview = content.parentElement;
      const card = preview && preview.closest('.notes-card');
      const button = card && card.querySelector('.notes-more');
      // 展开项没有「更多」。跳过它，否则 setAttribute 会中断后面卡片的初始化。
      if (!button) return;
      preview.id = `notes-preview-${++nextId}`;
      button.setAttribute('aria-controls', preview.id);
      cards.set(content, { preview, button });
      function expand(expanded) {
        button.setAttribute('aria-expanded', String(expanded));
        button.textContent = expanded ? button.dataset.less : button.dataset.more;
        update(content);
      }
      button.addEventListener('click', () => expand(button.getAttribute('aria-expanded') !== 'true'));
      // Keyboard users must never focus links hidden beyond the clipped preview.
      preview.addEventListener('focusin', () => expand(true));
      observer.observe(content);
      update(content);
    });
  }
  document.addEventListener('DOMContentLoaded', init);
  document.addEventListener('htmx:afterSettle', init);
})();
