(function () {
  'use strict';

  function cleanText(value) {
    return (value || '').replace(/\s+/g, ' ').trim();
  }

  function humanizeToken(value) {
    return cleanText((value || '').replace(/[_-]+/g, ' ').replace(/\b\w/g, function (m) {
      return m.toUpperCase();
    }));
  }

  function hasAccessibleName(el) {
    return Boolean(
      el.getAttribute('aria-label') ||
      el.getAttribute('aria-labelledby') ||
      el.getAttribute('title')
    );
  }

  function controlHasLabel(el) {
    if (hasAccessibleName(el)) return true;

    var id = el.getAttribute('id');
    if (id) {
      var explicit = document.querySelector('label[for="' + CSS.escape(id) + '"]');
      if (explicit && cleanText(explicit.textContent)) return true;
    }

    var wrapping = el.closest('label');
    if (wrapping && cleanText(wrapping.textContent)) return true;

    return false;
  }

  function deriveControlLabel(el) {
    var ariaFallback = el.getAttribute('data-label') || '';
    if (cleanText(ariaFallback)) return cleanText(ariaFallback);

    var id = el.getAttribute('id');
    if (id) {
      var explicit = document.querySelector('label[for="' + CSS.escape(id) + '"]');
      if (explicit) {
        var explicitText = cleanText(explicit.textContent);
        if (explicitText) return explicitText;
      }
    }

    var wrapping = el.closest('label');
    if (wrapping) {
      var wrappingText = cleanText(wrapping.textContent);
      if (wrappingText) return wrappingText;
    }

    var parent = el.parentElement;
    if (parent) {
      var siblingLabel = parent.querySelector(':scope > label');
      if (siblingLabel) {
        var siblingText = cleanText(siblingLabel.textContent);
        if (siblingText) return siblingText;
      }
    }

    var placeholder = cleanText(el.getAttribute('placeholder') || '');
    if (placeholder) return placeholder;

    var name = cleanText(el.getAttribute('name') || '');
    if (name) return humanizeToken(name);

    if (id) return humanizeToken(id);

    return '';
  }

  function ensureControlLabels(root) {
    var scope = root || document;
    var controls = scope.querySelectorAll('input, select, textarea');

    controls.forEach(function (el) {
      if (el.disabled) return;

      var tag = el.tagName.toLowerCase();
      var type = (el.getAttribute('type') || '').toLowerCase();
      if (tag === 'input' && ['hidden', 'submit', 'reset', 'button', 'image'].indexOf(type) !== -1) {
        return;
      }

      if (controlHasLabel(el)) return;

      var label = deriveControlLabel(el);
      if (!label) return;

      el.setAttribute('aria-label', label);
    });
  }

  function getIconActionName(el) {
    var title = cleanText(el.getAttribute('title') || '');
    if (title) return title;

    var dataAction = cleanText(el.getAttribute('data-action') || '');
    if (dataAction) return humanizeToken(dataAction);

    var href = cleanText(el.getAttribute('href') || '');
    if (href) {
      if (/\/delete\b|\/remove\b/.test(href)) return 'Delete';
      if (/\/edit\b/.test(href)) return 'Edit';
      if (/\/view\b|\/detail\b/.test(href)) return 'View';
      if (/\/download\b/.test(href)) return 'Download';
    }

    var cls = cleanText(el.className || '').toLowerCase();
    if (cls.indexOf('delete') !== -1 || cls.indexOf('danger') !== -1 || cls.indexOf('rose') !== -1) return 'Delete';
    if (cls.indexOf('edit') !== -1) return 'Edit';
    if (cls.indexOf('view') !== -1) return 'View';
    if (cls.indexOf('close') !== -1) return 'Close';

    return 'Action';
  }

  function ensureIconActionNames(root) {
    var scope = root || document;
    var interactive = scope.querySelectorAll('button, a[role="button"], .table-action-btn');

    interactive.forEach(function (el) {
      if (hasAccessibleName(el)) return;

      var visibleText = cleanText(el.textContent || '');
      if (visibleText) return;

      el.setAttribute('aria-label', getIconActionName(el));
    });
  }

  function extractSubmitText(el) {
    if (!el) return '';
    if (el.tagName.toLowerCase() === 'input') {
      return cleanText(el.value || '');
    }
    var labelTarget = el.querySelector('.btn-hide-on-load') || el.querySelector('span:not([x-show]):not([aria-hidden="true"])');
    if (labelTarget && cleanText(labelTarget.textContent)) {
      return cleanText(labelTarget.textContent);
    }
    return cleanText(el.textContent || '');
  }

  function setSubmitText(el, text) {
    if (!el || !text) return;

    if (el.tagName.toLowerCase() === 'input') {
      el.value = text;
      return;
    }

    var labelTarget = el.querySelector('.btn-hide-on-load') || el.querySelector('span:not([x-show]):not([aria-hidden="true"])');
    if (labelTarget && cleanText(labelTarget.textContent)) {
      labelTarget.textContent = text;
      return;
    }

    el.childNodes.forEach(function (node) {
      if (node.nodeType === Node.TEXT_NODE && cleanText(node.textContent)) {
        node.textContent = ' ' + text + ' ';
      }
    });
  }

  function normalizeActionText(value) {
    var text = cleanText(value);
    if (!text) return text;

    if (/^save changes$/i.test(text)) return 'Save';
    if (/^update(\s.+)?$/i.test(text)) return 'Save';
    if (/^add new\s+/i.test(text)) return text.replace(/^add new\s+/i, 'Create ');
    if (/^add\s+/i.test(text)) return text.replace(/^add\s+/i, 'Create ');
    if (/^submit reservation$/i.test(text)) return 'Create Reservation';

    return text;
  }

  function normalizePrimaryActions(root) {
    var scope = root || document;
    var actions = scope.querySelectorAll('button[type="submit"], input[type="submit"]');

    actions.forEach(function (el) {
      if (el.getAttribute('data-preserve-action-label') === 'true') return;

      var current = extractSubmitText(el);
      if (!current) return;

      var normalized = normalizeActionText(current);
      if (normalized !== current) {
        setSubmitText(el, normalized);
      }
    });
  }

  function ensureTruncationTooltips(root) {
    var scope = root || document;
    var truncated = scope.querySelectorAll('.truncate, [data-truncate-tooltip="true"]');

    truncated.forEach(function (el) {
      if (cleanText(el.getAttribute('title') || '')) return;
      var text = cleanText(el.textContent || '');
      if (!text) return;
      el.setAttribute('title', text);
    });
  }

  function ensureCopyAffordance(root) {
    var scope = root || document;
    var copyables = scope.querySelectorAll('[data-copy-text]');

    copyables.forEach(function (el) {
      if (!el.hasAttribute('tabindex')) {
        el.setAttribute('tabindex', '0');
      }
      if (!el.hasAttribute('role')) {
        el.setAttribute('role', 'button');
      }
      if (!el.hasAttribute('aria-label')) {
        el.setAttribute('aria-label', 'Copy value');
      }
      if (!cleanText(el.getAttribute('title') || '')) {
        el.setAttribute('title', 'Click to copy');
      }
      el.classList.add('copy-token');
    });
  }

  function copyTextToClipboard(text) {
    if (!text) return;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).catch(function () {});
      return;
    }

    var helper = document.createElement('textarea');
    helper.value = text;
    helper.setAttribute('readonly', '');
    helper.style.position = 'absolute';
    helper.style.left = '-9999px';
    document.body.appendChild(helper);
    helper.select();
    try {
      document.execCommand('copy');
    } catch (_err) {
      // no-op fallback
    }
    document.body.removeChild(helper);
  }

  function onCopyIntent(target) {
    if (!target) return;
    var value = cleanText(target.getAttribute('data-copy-text') || target.textContent || '');
    if (!value) return;
    copyTextToClipboard(value);
    target.setAttribute('data-copied', 'true');
    window.setTimeout(function () {
      target.removeAttribute('data-copied');
    }, 1200);
  }

  function runPass(root) {
    ensureControlLabels(root);
    ensureIconActionNames(root);
    normalizePrimaryActions(root);
    ensureTruncationTooltips(root);
    ensureCopyAffordance(root);
  }

  function schedulePass(root) {
    window.requestAnimationFrame(function () {
      runPass(root);
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    runPass(document);

    document.body.addEventListener('htmx:afterSwap', function (event) {
      schedulePass(event.target || document);
    });

    var observer = new MutationObserver(function () {
      schedulePass(document);
    });

    observer.observe(document.body, {
      childList: true,
      subtree: true
    });

    document.body.addEventListener('click', function (event) {
      var target = event.target.closest('[data-copy-text]');
      if (!target) return;
      onCopyIntent(target);
    });

    document.body.addEventListener('keydown', function (event) {
      if (event.key !== 'Enter' && event.key !== ' ') return;
      var target = event.target.closest('[data-copy-text]');
      if (!target) return;
      event.preventDefault();
      onCopyIntent(target);
    });
  });
})();
