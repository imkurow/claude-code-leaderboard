/*
  Scroll motion for the project site (asked for by the owner, on top of the kit's motion layer).
  - Blocks below the first screen rise into place as they come on screen, siblings one after another.
  - The board window assembles: the podium rises, the day bars grow, the model bars fill.
  - Terminals write their lines one by one.
  - A thin progress line under the top edge; the hero's detail card drifts a little as the page scrolls.
  Everything is visible without this script, under reduced motion and when the observer never reports.
*/
const html = document.documentElement;
const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
const off = html.dataset.motion === 'off';

/* ---------- progress line ---------- */
const bar = document.createElement('div');
bar.className = 'sr-progress';
bar.setAttribute('aria-hidden', 'true');
document.body.append(bar);

/* ---------- the hero's detail card drifts ---------- */
const note = document.querySelector('.hero__art > .ly-piece--note');
const drift = !reduced && !off && matchMedia('(min-width: 640px)').matches;

let ticking = false;
function onScroll() {
  if (ticking) return;
  ticking = true;
  requestAnimationFrame(() => {
    ticking = false;
    const max = document.documentElement.scrollHeight - innerHeight;
    bar.style.transform = `scaleX(${max > 0 ? Math.min(1, scrollY / max) : 0})`;
    if (drift && note && scrollY < innerHeight * 1.2) note.style.translate = `0 ${(-scrollY * 0.12).toFixed(1)}px`;
  });
}
addEventListener('scroll', onScroll, { passive: true });
addEventListener('resize', onScroll, { passive: true });
onScroll();

/* ---------- reveals ---------- */
if (!reduced && !off && 'IntersectionObserver' in window) {
  const SKIP = '.hero, #setup, [data-moment]';
  const TARGETS = [
    'main > section h2', '.section-head > p', '.t-lead', '.checks > li', 'main a.link', '.run-more',
    '.stats > div', '.card', '.faq__item', '.frame', '.app', '.run-card', '.run-stack > .term',
    'footer .closing__cta h2', 'footer .closing__cta .section-head > p', 'footer .closing__cta > .btn',
  ].join(',');

  const els = [...document.querySelectorAll(TARGETS)].filter(el =>
    !el.closest(SKIP) && !el.parentElement.closest('[data-sr-pick]') && (el.dataset.srPick = '1'));
  els.forEach(el => delete el.dataset.srPick);

  // terminals write line by line
  document.querySelectorAll('main .term__body').forEach(pre => {
    if (pre.closest(SKIP)) return;
    const lines = pre.innerHTML.split('\n');
    pre.innerHTML = lines.map((l, i) => `<span class="sr-line" style="--l:${i}">${l || ' '}</span>`).join('\n');
  });
  // the board's day bars, one after another
  document.querySelectorAll('.app .lb-bars rect').forEach((r, i) => r.style.setProperty('--i', i));

  const vis = el => { const r = el.getBoundingClientRect(); return r.top < innerHeight * 0.92 && r.bottom > 0; };
  const pending = els.filter(el => !vis(el));
  if (pending.length) {
    // siblings that come on screen together follow each other
    const groups = new Map();
    pending.forEach(el => {
      const k = el.parentElement;
      const i = groups.get(k) || 0;
      groups.set(k, i + 1);
      el.style.setProperty('--sr-d', `${Math.min(i * 90, 450)}ms`);
      el.dataset.sr = el.matches('.frame, .app, .run-card, .run-stack > .term') ? 'zoom' : '';
    });
    html.classList.add('sr-on');

    let heard = false;
    const show = el => {
      el.classList.add('sr-in');
      io.unobserve(el);
      // hand the element back to the kit's own hover and motion once the reveal is over
      setTimeout(() => { el.removeAttribute('data-sr'); el.classList.remove('sr-in'); el.style.removeProperty('--sr-d'); }, 2400);
    };
    const io = new IntersectionObserver(entries => {
      heard = true;
      entries.forEach(e => { if (e.isIntersecting) show(e.target); });
    }, { rootMargin: '0px 0px -8% 0px', threshold: 0.12 });
    pending.forEach(el => io.observe(el));
    // an observer that never reports must never leave the page hidden
    setTimeout(() => { if (!heard) html.classList.remove('sr-on'); }, 1500);
  }
}
