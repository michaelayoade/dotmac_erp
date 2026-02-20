/**
 * Alpine.js Focus-Trap Plugin
 *
 * Usage:  <div x-show="open" x-trap="open" role="dialog" ...>
 *
 * When the expression is truthy the plugin:
 *   1. Saves the previously-focused element
 *   2. Moves focus to [autofocus] or the first focusable child
 *   3. Traps Tab / Shift+Tab within the container
 *   4. Restores focus to the saved element when deactivated
 */
(function () {
  var FOCUSABLE =
    'a[href]:not([tabindex="-1"]), button:not([disabled]):not([tabindex="-1"]), ' +
    'input:not([disabled]):not([type="hidden"]):not([tabindex="-1"]), ' +
    'select:not([disabled]):not([tabindex="-1"]), ' +
    'textarea:not([disabled]):not([tabindex="-1"]), ' +
    '[tabindex]:not([tabindex="-1"]), [contenteditable]:not([tabindex="-1"])';

  function getFocusables(container) {
    return Array.from(container.querySelectorAll(FOCUSABLE)).filter(function (el) {
      return !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
    });
  }

  function plugin(Alpine) {
    Alpine.directive("trap", function (el, { expression }, { effect, evaluateLater, cleanup }) {
      var evaluate = evaluateLater(expression);
      var previouslyFocused = null;
      var onKeydown = null;

      effect(function () {
        evaluate(function (active) {
          if (active) {
            activate(el);
          } else {
            deactivate();
          }
        });
      });

      function activate(container) {
        previouslyFocused = document.activeElement;

        // Defer one frame so the container is visible (x-show may not have applied yet)
        requestAnimationFrame(function () {
          if (!container.isConnected) return;

          var target = container.querySelector("[autofocus]");
          if (!target) {
            var focusables = getFocusables(container);
            target = focusables[0];
          }
          if (target) target.focus({ preventScroll: true });

          onKeydown = function (e) {
            if (e.key !== "Tab") return;
            var nodes = getFocusables(container);
            if (nodes.length === 0) { e.preventDefault(); return; }

            var first = nodes[0];
            var last = nodes[nodes.length - 1];

            if (e.shiftKey && document.activeElement === first) {
              e.preventDefault();
              last.focus({ preventScroll: true });
            } else if (!e.shiftKey && document.activeElement === last) {
              e.preventDefault();
              first.focus({ preventScroll: true });
            }
          };
          container.addEventListener("keydown", onKeydown);
        });
      }

      function deactivate() {
        if (onKeydown) {
          el.removeEventListener("keydown", onKeydown);
          onKeydown = null;
        }
        if (previouslyFocused && previouslyFocused.isConnected) {
          previouslyFocused.focus({ preventScroll: true });
        }
        previouslyFocused = null;
      }

      cleanup(deactivate);
    });
  }

  document.addEventListener("alpine:init", function () {
    window.Alpine.plugin(plugin);
  });
})();
