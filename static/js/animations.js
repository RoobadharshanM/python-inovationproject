/* Stump AI — restrained progressive animations */
(() => {
  'use strict';

  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
  const progress = document.createElement('div');
  progress.className = 'scroll-progress';
  progress.setAttribute('aria-hidden', 'true');
  document.body.append(progress);

  function updateProgress() {
    const scrollable = document.documentElement.scrollHeight - window.innerHeight;
    const value = scrollable > 0 ? Math.min(window.scrollY / scrollable, 1) : 0;
    progress.style.transform = `scaleX(${value})`;
  }
  updateProgress();
  window.addEventListener('scroll', updateProgress, { passive: true });
  window.addEventListener('resize', updateProgress, { passive: true });

  const elements = [...document.querySelectorAll('.reveal')];
  if (reduced.matches || !('IntersectionObserver' in window)) {
    elements.forEach((element) => element.classList.add('is-visible'));
    return;
  }

  document.body.classList.add('motion-ready');
  const observer = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      entry.target.classList.add('is-visible');
      observer.unobserve(entry.target);
    });
  }, { rootMargin: '0px 0px -8% 0px', threshold: 0.08 });
  const observeReveal = (element) => {
    if (!element?.classList?.contains('reveal') || element.classList.contains('is-visible')) return;
    observer.observe(element);
  };
  elements.forEach(observeReveal);

  if ('MutationObserver' in window) {
    const mutationObserver = new MutationObserver((mutations) => {
      mutations.forEach((mutation) => {
        mutation.addedNodes.forEach((node) => {
          if (node.nodeType !== Node.ELEMENT_NODE) return;
          if (node.matches?.('.reveal')) observeReveal(node);
          node.querySelectorAll?.('.reveal').forEach(observeReveal);
        });
      });
    });
    mutationObserver.observe(document.body, { childList: true, subtree: true });
  }

  reduced.addEventListener?.('change', (event) => {
    document.body.classList.toggle('motion-ready', !event.matches);
    if (event.matches) {
      elements.forEach((element) => element.classList.add('is-visible'));
      observer.disconnect();
    }
  });
})();
