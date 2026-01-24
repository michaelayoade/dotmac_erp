(function () {
    function initTypeahead(container) {
        const input = container.querySelector("[data-typeahead-input]");
        const hidden = container.querySelector("[data-typeahead-hidden]");
        const results = container.querySelector("[data-typeahead-results]");
        const url = container.getAttribute("data-typeahead-url");
        const minChars = parseInt(container.getAttribute("data-typeahead-min") || "2", 10);
        const limit = parseInt(container.getAttribute("data-typeahead-limit") || "8", 10);
        if (!input || !hidden || !results || !url) {
            return;
        }
        let timer = null;
        let lastQuery = "";

        function clearResults() {
            results.innerHTML = "";
        }

        function renderResults(items) {
            if (!items || !items.length) {
                clearResults();
                return;
            }
            const menu = document.createElement("div");
            menu.className = "absolute z-10 mt-2 w-full rounded-lg border border-slate-200 bg-white shadow-lg dark:border-slate-700 dark:bg-slate-800";
            items.forEach(function (item) {
                const button = document.createElement("button");
                button.type = "button";
                button.className = "w-full px-3 py-2 text-left text-sm text-slate-700 hover:bg-slate-50 dark:text-slate-200 dark:hover:bg-slate-700";
                button.textContent = item.label || item.name || "";
                button.addEventListener("click", function () {
                    input.value = item.label || item.name || "";
                    hidden.value = item.ref || item.id || "";
                    clearResults();
                });
                menu.appendChild(button);
            });
            results.innerHTML = "";
            results.appendChild(menu);
        }

        function fetchResults(query) {
            const requestUrl = url + "?q=" + encodeURIComponent(query) + "&limit=" + limit;
            fetch(requestUrl)
                .then(function (response) {
                    if (!response.ok) {
                        throw new Error("typeahead request failed");
                    }
                    return response.json();
                })
                .then(function (data) {
                    renderResults((data && data.items) || []);
                })
                .catch(function () {
                    clearResults();
                });
        }

        input.addEventListener("input", function () {
            const query = input.value.trim();
            hidden.value = "";
            if (query.length < minChars) {
                clearResults();
                lastQuery = query;
                return;
            }
            if (timer) {
                window.clearTimeout(timer);
            }
            timer = window.setTimeout(function () {
                if (query !== lastQuery) {
                    fetchResults(query);
                    lastQuery = query;
                }
            }, 250);
        });

        document.addEventListener("click", function (event) {
            if (!container.contains(event.target)) {
                clearResults();
            }
        });
    }

    function initAll() {
        const containers = document.querySelectorAll("[data-typeahead-url]");
        containers.forEach(function (container) {
            initTypeahead(container);
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initAll);
    } else {
        initAll();
    }
})();
