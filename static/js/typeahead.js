(function () {
    "use strict";

    const initializedSelects = new WeakSet();
    let controlSequence = 0;

    function dispatchValueEvents(element) {
        element.dispatchEvent(new Event("input", { bubbles: true }));
        element.dispatchEvent(new Event("change", { bubbles: true }));
    }

    function itemValue(item) {
        return String(item.value ?? item.ref ?? item.id ?? "");
    }

    function itemLabel(item) {
        return String(item.label ?? item.name ?? itemValue(item));
    }

    function createStatus(results, message) {
        results.replaceChildren();
        const status = document.createElement("div");
        status.className = "typeahead-status";
        status.setAttribute("role", "status");
        status.textContent = message;
        results.appendChild(status);
    }

    function positionResults(input, results) {
        window.requestAnimationFrame(function () {
            const popup = results.querySelector(".typeahead-menu, .typeahead-status");
            if (!popup || input.getAttribute("aria-expanded") !== "true") return;
            const rect = input.getBoundingClientRect();
            const gutter = 4;
            const viewportGutter = 8;
            const width = Math.max(rect.width, 224);
            const left = Math.max(viewportGutter, Math.min(rect.left, window.innerWidth - width - viewportGutter));
            popup.style.left = `${left}px`;
            popup.style.width = `${width}px`;
            popup.style.top = `${rect.bottom + gutter}px`;
            const availableBelow = window.innerHeight - rect.bottom - viewportGutter;
            const availableAbove = rect.top - viewportGutter;
            if (popup.offsetHeight > availableBelow && availableAbove > availableBelow) {
                popup.style.top = `${Math.max(viewportGutter, rect.top - popup.offsetHeight - gutter)}px`;
            }
        });
    }

    function initRemoteTypeahead(container) {
        if (container.dataset.typeaheadInitialized === "true") return;

        const input = container.querySelector("[data-typeahead-input]");
        const hidden = container.querySelector("[data-typeahead-hidden]");
        const results = container.querySelector("[data-typeahead-results]");
        const url = container.getAttribute("data-typeahead-url");
        const minChars = parseInt(container.getAttribute("data-typeahead-min") || "2", 10);
        const limit = parseInt(container.getAttribute("data-typeahead-limit") || "8", 10);
        if (!input || !hidden || !results || !url) return;

        container.dataset.typeaheadInitialized = "true";
        const listId = results.id || `remote-typeahead-${++controlSequence}`;
        results.id = listId;
        input.setAttribute("role", "combobox");
        input.setAttribute("aria-autocomplete", "list");
        input.setAttribute("aria-controls", listId);
        input.setAttribute("aria-expanded", "false");
        results.classList.add("typeahead-results");

        let timer = null;
        let request = null;
        let items = [];
        let activeIndex = -1;
        let selecting = false;
        let lastHiddenValue = hidden.value || "";

        function close() {
            results.replaceChildren();
            input.setAttribute("aria-expanded", "false");
            input.removeAttribute("aria-activedescendant");
            container.dataset.typeaheadOpen = "false";
            activeIndex = -1;
        }

        function setActive(index) {
            const options = Array.from(results.querySelectorAll("[role='option']"));
            if (!options.length) return;
            activeIndex = (index + options.length) % options.length;
            options.forEach(function (option, optionIndex) {
                const active = optionIndex === activeIndex;
                option.setAttribute("aria-selected", active ? "true" : "false");
                option.classList.toggle("is-active", active);
            });
            input.setAttribute("aria-activedescendant", options[activeIndex].id);
            options[activeIndex].scrollIntoView({ block: "nearest" });
        }

        function selectItem(item) {
            selecting = true;
            input.value = itemLabel(item);
            hidden.value = itemValue(item);
            try {
                hidden.setAttribute("data-typeahead-item", JSON.stringify(item));
            } catch (error) {
                hidden.removeAttribute("data-typeahead-item");
            }
            lastHiddenValue = hidden.value;
            dispatchValueEvents(hidden);
            input.dispatchEvent(new Event("change", { bubbles: true }));
            input.setCustomValidity("");
            close();
            selecting = false;
        }

        function render(newItems) {
            items = Array.isArray(newItems) ? newItems : [];
            results.replaceChildren();
            if (!items.length) {
                createStatus(results, "No matching options");
                input.setAttribute("aria-expanded", "true");
                positionResults(input, results);
                return;
            }
            const menu = document.createElement("div");
            menu.className = "typeahead-menu";
            menu.setAttribute("role", "listbox");
            items.forEach(function (item, index) {
                const option = document.createElement("div");
                option.id = `${listId}-option-${index}`;
                option.className = "typeahead-option";
                option.setAttribute("role", "option");
                option.setAttribute("aria-selected", "false");
                option.textContent = itemLabel(item);
                option.addEventListener("mousedown", function (event) {
                    event.preventDefault();
                    selectItem(item);
                });
                menu.appendChild(option);
            });
            results.appendChild(menu);
            input.setAttribute("aria-expanded", "true");
            container.dataset.typeaheadOpen = "true";
            positionResults(input, results);
        }

        function fetchResults(query) {
            if (request) request.abort();
            request = new AbortController();
            createStatus(results, "Loading options…");
            input.setAttribute("aria-expanded", "true");
            container.dataset.typeaheadOpen = "true";
            positionResults(input, results);
            const separator = url.includes("?") ? "&" : "?";
            fetch(`${url}${separator}q=${encodeURIComponent(query)}&limit=${limit}`, {
                signal: request.signal,
                headers: { Accept: "application/json" },
            })
                .then(function (response) {
                    if (!response.ok) throw new Error("typeahead request failed");
                    return response.json();
                })
                .then(function (data) { render((data && data.items) || []); })
                .catch(function (error) {
                    if (error.name !== "AbortError") {
                        createStatus(results, "Options could not be loaded");
                        positionResults(input, results);
                    }
                });
        }

        input.addEventListener("input", function () {
            if (selecting) return;
            const query = input.value.trim();
            if (hidden.value) {
                hidden.value = "";
                hidden.removeAttribute("data-typeahead-item");
                if (lastHiddenValue) dispatchValueEvents(hidden);
            }
            lastHiddenValue = "";
            window.clearTimeout(timer);
            if (query.length < minChars) {
                close();
                if (query) {
                    createStatus(results, `Type ${minChars} or more characters`);
                    input.setAttribute("aria-expanded", "true");
                    container.dataset.typeaheadOpen = "true";
                    positionResults(input, results);
                }
                return;
            }
            timer = window.setTimeout(function () { fetchResults(query); }, 250);
        });

        input.addEventListener("keydown", function (event) {
            if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                event.preventDefault();
                setActive(activeIndex + (event.key === "ArrowDown" ? 1 : -1));
            } else if (event.key === "Enter" && activeIndex >= 0) {
                event.preventDefault();
                selectItem(items[activeIndex]);
            } else if (event.key === "Escape") {
                close();
            }
        });

        container.addEventListener("typeahead:close", close);
    }

    function selectOptions(select) {
        return Array.from(select.options).map(function (option) {
            return {
                value: option.value,
                label: option.textContent.trim(),
                disabled: option.disabled,
                option: option,
            };
        });
    }

    function initSelectTypeahead(select) {
        if (initializedSelects.has(select) || select.matches("[data-no-typeahead]")) return;
        initializedSelects.add(select);

        const multiple = select.multiple;
        const wasRequired = select.required;
        const controlId = select.id || `select-typeahead-source-${++controlSequence}`;
        const instanceId = `${controlId}-typeahead-${++controlSequence}`;
        const inputId = `${instanceId}-input`;
        const listId = `${instanceId}-list`;
        const originalClass = select.className;
        const remoteUrl = select.dataset.typeaheadUrl || "";
        const minChars = parseInt(select.dataset.typeaheadMin || (remoteUrl ? "2" : "0"), 10);
        const limit = parseInt(select.dataset.typeaheadLimit || "50", 10);

        select.id = controlId;
        select.required = false;
        select.tabIndex = -1;
        select.classList.add("select-typeahead__native");

        const wrapper = document.createElement("div");
        wrapper.className = "select-typeahead";
        wrapper.dataset.selectTypeahead = "true";
        if (select.style.width) wrapper.style.width = select.style.width;
        const valueArea = document.createElement("div");
        valueArea.className = "select-typeahead__control";
        const chips = document.createElement("div");
        chips.className = "select-typeahead__chips";
        if (!multiple) chips.hidden = true;

        const input = document.createElement("input");
        input.type = "text";
        input.id = inputId;
        input.autocomplete = "off";
        input.spellcheck = false;
        input.className = originalClass.replace(/\bform-select\b/g, "form-input");
        input.classList.add("select-typeahead__input");
        input.setAttribute("role", "combobox");
        input.setAttribute("aria-autocomplete", "list");
        input.setAttribute("aria-controls", listId);
        input.setAttribute("aria-expanded", "false");
        input.setAttribute("aria-haspopup", "listbox");
        input.setAttribute("aria-label", select.getAttribute("aria-label") || "Choose an option");
        input.disabled = select.disabled;
        input.required = wasRequired && !multiple;

        const toggle = document.createElement("button");
        toggle.type = "button";
        toggle.className = "select-typeahead__toggle";
        toggle.setAttribute("aria-label", "Show options");
        toggle.tabIndex = -1;
        toggle.disabled = select.disabled;
        toggle.innerHTML = '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="m5 7.5 5 5 5-5" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5"/></svg>';

        const results = document.createElement("div");
        results.id = listId;
        results.className = "typeahead-results";

        select.parentNode.insertBefore(wrapper, select);
        wrapper.appendChild(select);
        valueArea.appendChild(chips);
        valueArea.appendChild(input);
        valueArea.appendChild(toggle);
        wrapper.appendChild(valueArea);
        wrapper.appendChild(results);

        const label = document.querySelector(`label[for="${CSS.escape(controlId)}"]`);
        if (label) label.htmlFor = inputId;

        let visibleItems = [];
        let activeIndex = -1;
        let request = null;
        let timer = null;
        let syncing = false;

        function selectedOptions() {
            return Array.from(select.selectedOptions).filter(function (option) { return option.value !== ""; });
        }

        function placeholder() {
            const empty = Array.from(select.options).find(function (option) { return option.value === ""; });
            return select.dataset.typeaheadPlaceholder || (empty && empty.textContent.trim()) || "Search options…";
        }

        function validate() {
            const missing = wasRequired && selectedOptions().length === 0;
            input.setCustomValidity(missing ? "Choose an option from the list." : "");
        }

        function renderChips() {
            chips.replaceChildren();
            if (!multiple) return;
            selectedOptions().forEach(function (option) {
                const chip = document.createElement("span");
                chip.className = "select-typeahead__chip";
                chip.append(document.createTextNode(option.textContent.trim()));
                const remove = document.createElement("button");
                remove.type = "button";
                remove.setAttribute("aria-label", `Remove ${option.textContent.trim()}`);
                remove.textContent = "×";
                remove.addEventListener("click", function () {
                    option.selected = false;
                    dispatchValueEvents(select);
                    syncFromSelect();
                    input.focus();
                });
                chip.appendChild(remove);
                chips.appendChild(chip);
            });
        }

        function syncFromSelect() {
            if (syncing) return;
            syncing = true;
            if (multiple) {
                input.value = "";
                input.placeholder = selectedOptions().length ? "Add another…" : placeholder();
                renderChips();
            } else {
                const selected = select.selectedOptions[0];
                input.value = selected && selected.value ? selected.textContent.trim() : "";
                input.placeholder = placeholder();
            }
            input.disabled = select.disabled;
            toggle.disabled = select.disabled;
            validate();
            syncing = false;
        }

        function syncSelectState() {
            const directlyHidden = select.hidden || select.style.display === "none" || select.classList.contains("hidden");
            wrapper.hidden = directlyHidden;
            syncFromSelect();
        }

        function close() {
            results.replaceChildren();
            input.setAttribute("aria-expanded", "false");
            input.removeAttribute("aria-activedescendant");
            wrapper.dataset.typeaheadOpen = "false";
            activeIndex = -1;
        }

        function setActive(index) {
            const options = Array.from(results.querySelectorAll("[role='option']"));
            if (!options.length) return;
            activeIndex = (index + options.length) % options.length;
            options.forEach(function (option, optionIndex) {
                const active = optionIndex === activeIndex;
                option.classList.toggle("is-active", active);
                option.setAttribute("aria-selected", active ? "true" : "false");
            });
            input.setAttribute("aria-activedescendant", options[activeIndex].id);
            options[activeIndex].scrollIntoView({ block: "nearest" });
        }

        function choose(item) {
            syncing = true;
            if (remoteUrl && !item.option) {
                let option = Array.from(select.options).find(function (candidate) { return candidate.value === item.value; });
                if (!option) {
                    option = new Option(item.label, item.value);
                    select.add(option);
                }
                item.option = option;
            }
            if (multiple) {
                item.option.selected = !item.option.selected;
                input.value = "";
            } else {
                select.value = item.value;
                input.value = item.label;
            }
            syncing = false;
            dispatchValueEvents(select);
            syncFromSelect();
            if (multiple) open(""); else close();
        }

        function render(items) {
            visibleItems = items.slice(0, limit);
            results.replaceChildren();
            if (!visibleItems.length) {
                createStatus(results, "No matching options");
                input.setAttribute("aria-expanded", "true");
                wrapper.dataset.typeaheadOpen = "true";
                positionResults(input, results);
                return;
            }
            const menu = document.createElement("div");
            menu.className = "typeahead-menu";
            menu.setAttribute("role", "listbox");
            if (multiple) menu.setAttribute("aria-multiselectable", "true");
            visibleItems.forEach(function (item, index) {
                const option = document.createElement("div");
                option.id = `${listId}-option-${index}`;
                option.className = "typeahead-option";
                option.setAttribute("role", "option");
                const selected = Boolean(item.option && item.option.selected);
                option.setAttribute("aria-selected", selected ? "true" : "false");
                if (selected) option.classList.add("is-selected");
                if (item.disabled) option.setAttribute("aria-disabled", "true");
                option.textContent = item.label;
                option.addEventListener("mousedown", function (event) {
                    event.preventDefault();
                    if (!item.disabled) choose(item);
                });
                menu.appendChild(option);
            });
            results.appendChild(menu);
            input.setAttribute("aria-expanded", "true");
            wrapper.dataset.typeaheadOpen = "true";
            positionResults(input, results);
        }

        function localItems(query) {
            const normalized = query.toLocaleLowerCase();
            return selectOptions(select).filter(function (item) {
                if (!item.value && item.option.index === 0) return false;
                return !normalized || item.label.toLocaleLowerCase().includes(normalized) || item.value.toLocaleLowerCase().includes(normalized);
            });
        }

        function fetchRemote(query) {
            if (request) request.abort();
            request = new AbortController();
            createStatus(results, "Loading options…");
            input.setAttribute("aria-expanded", "true");
            wrapper.dataset.typeaheadOpen = "true";
            positionResults(input, results);
            const separator = remoteUrl.includes("?") ? "&" : "?";
            fetch(`${remoteUrl}${separator}q=${encodeURIComponent(query)}&limit=${limit}`, {
                signal: request.signal,
                headers: { Accept: "application/json" },
            })
                .then(function (response) {
                    if (!response.ok) throw new Error("typeahead request failed");
                    return response.json();
                })
                .then(function (data) {
                    render(((data && data.items) || []).map(function (item) {
                        return { value: itemValue(item), label: itemLabel(item), disabled: false, option: null };
                    }));
                })
                .catch(function (error) {
                    if (error.name !== "AbortError") {
                        createStatus(results, "Options could not be loaded");
                        positionResults(input, results);
                    }
                });
        }

        function open(query) {
            if (input.disabled) return;
            const value = (query ?? input.value).trim();
            window.clearTimeout(timer);
            if (remoteUrl) {
                if (value.length < minChars) {
                    createStatus(results, `Type ${minChars} or more characters`);
                    input.setAttribute("aria-expanded", "true");
                    wrapper.dataset.typeaheadOpen = "true";
                    positionResults(input, results);
                    return;
                }
                timer = window.setTimeout(function () { fetchRemote(value); }, 250);
            } else {
                render(localItems(value));
            }
        }

        input.addEventListener("focus", function () { open(""); });
        input.addEventListener("input", function () {
            if (!syncing && !multiple && select.value) {
                select.value = "";
                select.dispatchEvent(new Event("input", { bubbles: true }));
            }
            validate();
            open(input.value);
        });
        input.addEventListener("keydown", function (event) {
            if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                event.preventDefault();
                if (input.getAttribute("aria-expanded") !== "true") open("");
                setActive(activeIndex + (event.key === "ArrowDown" ? 1 : -1));
            } else if (event.key === "Enter" && activeIndex >= 0) {
                event.preventDefault();
                choose(visibleItems[activeIndex]);
            } else if (event.key === "Escape") {
                close();
            } else if (multiple && event.key === "Backspace" && !input.value) {
                const selected = selectedOptions();
                if (selected.length) {
                    selected[selected.length - 1].selected = false;
                    dispatchValueEvents(select);
                    syncFromSelect();
                }
            }
        });
        input.addEventListener("blur", function () {
            window.setTimeout(function () {
                if (!wrapper.contains(document.activeElement)) {
                    if (!multiple) syncFromSelect();
                    close();
                }
            }, 0);
        });
        toggle.addEventListener("click", function () {
            input.focus();
            if (input.getAttribute("aria-expanded") === "true") close();
            else open("");
        });
        select.addEventListener("change", syncFromSelect);
        if (select.form) select.form.addEventListener("reset", function () { window.setTimeout(syncFromSelect, 0); });
        wrapper.addEventListener("typeahead:close", close);
        select.addEventListener("typeahead:sync", syncSelectState);
        syncSelectState();
    }

    function initAll(root) {
        const scope = root && root.querySelectorAll ? root : document;
        const remoteContainers = [];
        const selects = [];
        if (scope.matches && scope.matches("[data-typeahead-url]:not(select)")) remoteContainers.push(scope);
        if (scope.matches && scope.matches("select")) selects.push(scope);
        remoteContainers.push(...scope.querySelectorAll("[data-typeahead-url]:not(select)"));
        selects.push(...scope.querySelectorAll("select"));
        remoteContainers.forEach(initRemoteTypeahead);
        selects.forEach(initSelectTypeahead);
    }

    function start() {
        initAll(document);
        const observer = new MutationObserver(function (mutations) {
            mutations.forEach(function (mutation) {
                mutation.addedNodes.forEach(function (node) {
                    if (node.nodeType === Node.ELEMENT_NODE) initAll(node);
                });
                const target = mutation.target.nodeType === Node.ELEMENT_NODE ? mutation.target : null;
                const select = target && (target.matches("select") ? target : target.closest("select"));
                if (select) select.dispatchEvent(new Event("typeahead:sync"));
            });
        });
        observer.observe(document.body, {
            childList: true,
            subtree: true,
            attributes: true,
            attributeFilter: ["selected", "disabled", "hidden", "style", "class"],
        });
        document.addEventListener("pointerdown", function (event) {
            const activeContainer = event.target.closest("[data-typeahead-open='true']");
            document.querySelectorAll("[data-typeahead-open='true']").forEach(function (container) {
                if (container !== activeContainer) container.dispatchEvent(new Event("typeahead:close"));
            });
        });
        function closeOpenTypeaheads() {
            document.querySelectorAll("[data-typeahead-open='true']").forEach(function (container) {
                container.dispatchEvent(new Event("typeahead:close"));
            });
        }
        window.addEventListener("resize", closeOpenTypeaheads);
        window.addEventListener("scroll", closeOpenTypeaheads, true);
        document.body.addEventListener("htmx:afterSwap", function (event) { initAll(event.target); });
    }

    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
    else start();
})();
