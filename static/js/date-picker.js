/* App calendar backed by the original ISO-valued date and datetime inputs. */
(function () {
    'use strict';

    const months = Array.from({ length: 12 }, (_, i) =>
        new Intl.DateTimeFormat('en', { month: 'long', timeZone: 'UTC' })
            .format(new Date(Date.UTC(2000, i, 1))));
    let open = null;

    function iso(year, month, day) {
        return `${String(year).padStart(4, '0')}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
    }

    function parts(value) {
        const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value || '');
        if (!match) return null;
        const year = Number(match[1]), month = Number(match[2]), day = Number(match[3]);
        const checked = new Date(Date.UTC(year, month - 1, day));
        return checked.getUTCFullYear() === year && checked.getUTCMonth() === month - 1 &&
            checked.getUTCDate() === day ? { year, month, day } : null;
    }

    function displayDate(value) {
        const format = document.documentElement.dataset.dateFormat;
        const date = parts(value);
        if (!date) return format || 'Select date';
        if (!format) return value;
        return format.replace(/YYYY|MMM|MM|DD/g, token => ({
            YYYY: String(date.year).padStart(4, '0'),
            MMM: months[date.month - 1].slice(0, 3),
            MM: String(date.month).padStart(2, '0'),
            DD: String(date.day).padStart(2, '0')
        })[token]);
    }

    function isDateTime(input) {
        return input.type === 'datetime-local';
    }

    function displayValue(input) {
        const date = displayDate(input.value.slice(0, 10));
        if (!isDateTime(input)) return date;
        return input.value ? `${date} ${input.value.slice(11, 16)}` : `${date} HH:MM`;
    }

    function close(focus) {
        if (!open) return;
        const { popup, trigger } = open;
        open = null;
        popup.remove();
        trigger.setAttribute('aria-expanded', 'false');
        trigger.removeAttribute('aria-controls');
        if (focus) trigger.focus();
    }

    function position() {
        if (!open) return;
        const rect = open.trigger.getBoundingClientRect();
        const width = Math.min(320, window.innerWidth - 16);
        open.popup.style.width = `${width}px`;
        open.popup.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - width - 8))}px`;
        const below = window.innerHeight - rect.bottom >= 340 || rect.top < 340;
        open.popup.style.top = below ? `${rect.bottom + 4}px` : '';
        open.popup.style.bottom = below ? '' : `${window.innerHeight - rect.top + 4}px`;
    }

    function commit(value) {
        const input = open.input;
        input.value = value;
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
        close(true);
    }

    function choose(value) {
        const input = open.input;
        if (isDateTime(input) && value) {
            open.selectedDate = value;
            render();
            return;
        }
        commit(value);
    }

    function canChoose(input, value) {
        if (isDateTime(input)) {
            const min = input.min.slice(0, 10), max = input.max.slice(0, 10);
            return (!min || value >= min) && (!max || value <= max);
        }
        const probe = input.cloneNode();
        probe.value = value;
        return probe.checkValidity();
    }

    function changeMonth(delta) {
        const shifted = new Date(Date.UTC(open.year, open.month - 1 + delta, 1));
        open.year = shifted.getUTCFullYear();
        open.month = shifted.getUTCMonth() + 1;
        if (isDateTime(open.input)) open.selectedDate = '';
        render();
    }

    function render() {
        if (!open) return;
        const { popup, input, year, month } = open;
        const min = parts(input.min.slice(0, 10)), max = parts(input.max.slice(0, 10));
        const firstYear = min ? min.year : Math.min(year - 100, 1900);
        const lastYear = max ? max.year : Math.max(year + 20, 2100);
        popup.replaceChildren();

        const header = document.createElement('div');
        header.className = 'erp-calendar__header';
        const previous = document.createElement('button');
        previous.type = 'button';
        previous.textContent = '‹';
        previous.setAttribute('aria-label', 'Previous month');
        previous.disabled = Boolean(min && iso(year, month, 1) <= iso(min.year, min.month, 1));
        previous.addEventListener('click', () => changeMonth(-1));
        const next = document.createElement('button');
        next.type = 'button';
        next.textContent = '›';
        next.setAttribute('aria-label', 'Next month');
        next.disabled = Boolean(max && iso(year, month, 1) >= iso(max.year, max.month, 1));
        next.addEventListener('click', () => changeMonth(1));
        const monthSelect = document.createElement('select');
        monthSelect.className = 'erp-calendar__month';
        monthSelect.setAttribute('aria-label', 'Month');
        months.forEach((name, index) => {
            const option = new Option(name, String(index + 1));
            const candidate = iso(year, index + 1, 1);
            option.disabled = Boolean(
                (min && candidate < iso(min.year, min.month, 1)) ||
                (max && candidate > iso(max.year, max.month, 1))
            );
            monthSelect.add(option);
        });
        monthSelect.value = String(month);
        monthSelect.addEventListener('change', () => {
            open.month = Number(monthSelect.value);
            if (isDateTime(input)) open.selectedDate = '';
            render();
        });
        const yearSelect = document.createElement('select');
        yearSelect.className = 'erp-calendar__year';
        yearSelect.setAttribute('aria-label', 'Year');
        for (let y = firstYear; y <= lastYear; y++) yearSelect.add(new Option(String(y), String(y)));
        yearSelect.value = String(year);
        yearSelect.addEventListener('change', () => {
            open.year = Number(yearSelect.value);
            if (isDateTime(input)) open.selectedDate = '';
            render();
        });
        header.append(previous, monthSelect, yearSelect, next);
        popup.append(header);

        const grid = document.createElement('div');
        grid.className = 'erp-calendar__grid';
        ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].forEach(name => {
            const label = document.createElement('span');
            label.className = 'erp-calendar__weekday';
            label.textContent = name;
            grid.append(label);
        });
        const offset = (new Date(Date.UTC(year, month - 1, 1)).getUTCDay() + 6) % 7;
        for (let index = 0; index < offset; index++) grid.append(document.createElement('span'));
        const days = new Date(Date.UTC(year, month, 0)).getUTCDate();
        for (let day = 1; day <= days; day++) {
            const value = iso(year, month, day);
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'erp-calendar__day';
            button.textContent = String(day);
            button.setAttribute('aria-label', displayDate(value));
            button.setAttribute('aria-pressed', String(
                (isDateTime(input) ? open.selectedDate : input.value) === value
            ));
            button.disabled = !canChoose(input, value);
            button.addEventListener('click', () => choose(value));
            grid.append(button);
        }
        popup.append(grid);

        if (isDateTime(input)) {
            const timeRow = document.createElement('label');
            timeRow.className = 'erp-calendar__time';
            timeRow.textContent = 'Time';
            const timeInput = document.createElement('input');
            timeInput.type = 'time';
            timeInput.value = open.time;
            if (input.step) timeInput.step = input.step;
            timeInput.setAttribute('aria-label', 'Time');
            timeInput.addEventListener('input', () => {
                open.time = timeInput.value;
                apply.disabled = !canApply();
            });
            timeRow.append(timeInput);
            popup.append(timeRow);
        }

        const footer = document.createElement('div');
        footer.className = 'erp-calendar__footer';
        const today = new Date();
        const todayValue = iso(today.getFullYear(), today.getMonth() + 1, today.getDate());
        const todayButton = document.createElement('button');
        todayButton.type = 'button';
        todayButton.textContent = 'Today';
        todayButton.disabled = !canChoose(input, todayValue);
        todayButton.addEventListener('click', () => choose(todayValue));
        const clearButton = document.createElement('button');
        clearButton.type = 'button';
        clearButton.textContent = 'Clear';
        clearButton.addEventListener('click', () => choose(''));
        footer.append(todayButton, clearButton);
        let apply;
        if (isDateTime(input)) {
            apply = document.createElement('button');
            apply.type = 'button';
            apply.textContent = 'Apply';
            apply.disabled = !canApply();
            apply.addEventListener('click', () => {
                commit(`${open.selectedDate}T${open.time}`);
            });
            footer.append(apply);
        }
        popup.append(footer);
    }

    function canApply() {
        if (!open || !open.selectedDate || !open.time) return false;
        const probe = open.input.cloneNode();
        probe.value = `${open.selectedDate}T${open.time}`;
        return probe.checkValidity();
    }

    function show(input, trigger) {
        if (input.disabled || input.readOnly) return;
        if (open && open.input === input) return close(true);
        close(false);
        const today = new Date();
        const selected = parts(input.value.slice(0, 10)) || {
            year: today.getFullYear(), month: today.getMonth() + 1
        };
        const popup = document.createElement('div');
        popup.className = 'erp-calendar';
        popup.id = 'erp-calendar-popup';
        popup.setAttribute('role', 'dialog');
        popup.setAttribute('aria-label', isDateTime(input) ? 'Choose date and time' : 'Choose date');
        open = {
            input, trigger, popup, year: selected.year, month: selected.month,
            selectedDate: input.value.slice(0, 10) || iso(today.getFullYear(), today.getMonth() + 1, today.getDate()),
            time: input.value.slice(11, 16) || `${String(today.getHours()).padStart(2, '0')}:${String(today.getMinutes()).padStart(2, '0')}`
        };
        document.body.append(popup);
        trigger.setAttribute('aria-expanded', 'true');
        trigger.setAttribute('aria-controls', popup.id);
        position();
        render();
        popup.querySelector('.erp-calendar__month').focus();
    }

    function enhance(input) {
        if (input.dataset.erpDatePicker) return;
        input.dataset.erpDatePicker = 'true';
        const wrapper = document.createElement('span');
        wrapper.className = `${input.className} erp-date-picker`;
        const trigger = document.createElement('button');
        trigger.type = 'button';
        trigger.className = 'erp-date-picker__trigger';
        trigger.setAttribute('aria-haspopup', 'dialog');
        trigger.setAttribute('aria-expanded', 'false');
        const label = input.labels && input.labels[0] ? input.labels[0].textContent.trim() : '';
        trigger.setAttribute('aria-label', input.getAttribute('aria-label') || label || 'Choose date');
        if (input.hasAttribute('aria-describedby')) {
            trigger.setAttribute('aria-describedby', input.getAttribute('aria-describedby'));
        }
        const value = document.createElement('span');
        value.className = 'erp-date-picker__value';
        const icon = document.createElement('span');
        icon.className = 'erp-date-picker__icon';
        icon.setAttribute('aria-hidden', 'true');
        icon.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M7 3v4M17 3v4M3 10h18"/></svg>';
        trigger.append(value, icon);
        input.parentNode.insertBefore(wrapper, input);
        wrapper.append(input, trigger);
        input.tabIndex = -1;
        function refresh() {
            value.textContent = displayValue(input);
            wrapper.classList.toggle('erp-date-picker__empty', !input.value);
            wrapper.classList.toggle('erp-date-picker__disabled', input.disabled || input.readOnly);
            trigger.disabled = input.disabled || input.readOnly;
            if (input.hasAttribute('aria-invalid')) {
                trigger.setAttribute('aria-invalid', input.getAttribute('aria-invalid'));
            } else trigger.removeAttribute('aria-invalid');
            wrapper.style.display = input.style.display === 'none' ? 'none' : '';
        }
        const valueProperty = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
        if (!Object.hasOwn(input, 'value') && valueProperty) {
            Object.defineProperty(input, 'value', {
                configurable: true,
                get() { return valueProperty.get.call(this); },
                set(next) {
                    valueProperty.set.call(this, next);
                    refresh();
                }
            });
        }
        trigger.addEventListener('click', () => show(input, trigger));
        input.addEventListener('focus', () => show(input, trigger));
        input.addEventListener('input', refresh);
        input.addEventListener('change', refresh);
        input.addEventListener('erp:date-refresh', refresh);
        input.addEventListener('invalid', () => trigger.focus());
        new MutationObserver(refresh).observe(input, {
            attributes: true,
            attributeFilter: ['value', 'disabled', 'readonly', 'style', 'aria-invalid']
        });
        if (input.form) input.form.addEventListener('reset', () => setTimeout(refresh, 0));
        requestAnimationFrame(refresh);
    }

    function enhanceAll(root) {
        const selector = 'input[type="date"], input[type="datetime-local"]';
        if (root.matches && root.matches(selector)) enhance(root);
        root.querySelectorAll(selector).forEach(enhance);
    }

    document.addEventListener('pointerdown', event => {
        if (open && !open.popup.contains(event.target) && !open.trigger.contains(event.target)) close(false);
    });
    document.addEventListener('keydown', event => {
        if (open && event.key === 'Escape') {
            event.preventDefault();
            close(true);
        }
    });
    window.addEventListener('resize', position);
    window.addEventListener('scroll', position, true);
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => enhanceAll(document));
    } else enhanceAll(document);
    document.addEventListener('htmx:load', event => enhanceAll(event.detail.elt));
    document.addEventListener('alpine:initialized', () => {
        document.querySelectorAll('input[data-erp-date-picker]').forEach(input => {
            input.dispatchEvent(new Event('erp:date-refresh'));
        });
    });
    new MutationObserver(records => {
        records.forEach(record => record.addedNodes.forEach(node => {
            if (node.nodeType === 1) enhanceAll(node);
        }));
        if (open && !open.input.isConnected) close(false);
    }).observe(document.documentElement, { childList: true, subtree: true });
}());
