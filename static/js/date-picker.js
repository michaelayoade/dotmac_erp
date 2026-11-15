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
        const height = open.popup.offsetHeight;
        const below = window.innerHeight - rect.bottom >= height + 12 || rect.top < height + 12;
        const top = below ? rect.bottom + 4 : rect.top - height - 4;
        open.popup.style.top = `${Math.max(8, Math.min(top, window.innerHeight - height - 8))}px`;
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
        open.firstYear = Math.min(open.firstYear, open.year);
        open.lastYear = Math.max(open.lastYear, open.year);
        if (isDateTime(open.input)) open.selectedDate = '';
        render(delta < 0 ? '.erp-calendar__previous' : '.erp-calendar__next');
    }

    function monthAllowed(year, month, min, max) {
        const value = iso(year, month, 1);
        return (!min || value >= iso(min.year, min.month, 1)) &&
            (!max || value <= iso(max.year, max.month, 1));
    }

    function calendarButton(label, text, callback, className) {
        const button = document.createElement('button');
        button.type = 'button';
        button.textContent = text;
        button.setAttribute('aria-label', label);
        button.className = className;
        button.addEventListener('click', callback);
        return button;
    }

    function moveDay(key, shift) {
        const active = document.activeElement;
        if (!active.classList.contains('erp-calendar__day')) return;
        const current = parts(active.dataset.date);
        const date = new Date(Date.UTC(current.year, current.month - 1, current.day));
        if (key === 'ArrowLeft') date.setUTCDate(date.getUTCDate() - 1);
        else if (key === 'ArrowRight') date.setUTCDate(date.getUTCDate() + 1);
        else if (key === 'ArrowUp') date.setUTCDate(date.getUTCDate() - 7);
        else if (key === 'ArrowDown') date.setUTCDate(date.getUTCDate() + 7);
        else if (key === 'Home') date.setUTCDate(date.getUTCDate() - ((date.getUTCDay() + 6) % 7));
        else if (key === 'End') date.setUTCDate(date.getUTCDate() + (6 - ((date.getUTCDay() + 6) % 7)));
        else if (key === 'PageUp' || key === 'PageDown') {
            const delta = (key === 'PageUp' ? -1 : 1) * (shift ? 12 : 1);
            const targetMonth = new Date(Date.UTC(current.year, current.month - 1 + delta, 1));
            const lastDay = new Date(Date.UTC(targetMonth.getUTCFullYear(), targetMonth.getUTCMonth() + 1, 0)).getUTCDate();
            date.setTime(Date.UTC(targetMonth.getUTCFullYear(), targetMonth.getUTCMonth(), Math.min(current.day, lastDay)));
        } else return;
        const value = iso(date.getUTCFullYear(), date.getUTCMonth() + 1, date.getUTCDate());
        if (!canChoose(open.input, value)) return;
        if (isDateTime(open.input) && (open.year !== date.getUTCFullYear() || open.month !== date.getUTCMonth() + 1)) {
            open.selectedDate = '';
        }
        open.year = date.getUTCFullYear();
        open.month = date.getUTCMonth() + 1;
        open.firstYear = Math.min(open.firstYear, open.year);
        open.lastYear = Math.max(open.lastYear, open.year);
        render(`.erp-calendar__day[data-date="${value}"]`);
    }

    function render(focusTarget) {
        if (!open) return;
        const { popup, input, year, month } = open;
        const min = parts(input.min.slice(0, 10)), max = parts(input.max.slice(0, 10));
        const { firstYear, lastYear } = open;
        popup.replaceChildren();

        const header = document.createElement('div');
        header.className = 'erp-calendar__header';
        const yearPage = Math.floor(year / 12) * 12;
        const previous = calendarButton(
            open.view === 'years' ? 'Previous 12 years' : open.view === 'months' ? 'Previous year' : 'Previous month',
            '‹', () => {
                if (open.view === 'years') {
                    open.year -= 12;
                    render('.erp-calendar__previous');
                } else if (open.view === 'months') {
                    open.year -= 1;
                    render('.erp-calendar__previous');
                } else changeMonth(-1);
            }, 'erp-calendar__previous');
        const next = calendarButton(
            open.view === 'years' ? 'Next 12 years' : open.view === 'months' ? 'Next year' : 'Next month',
            '›', () => {
                if (open.view === 'years') {
                    open.year += 12;
                    render('.erp-calendar__next');
                } else if (open.view === 'months') {
                    open.year += 1;
                    render('.erp-calendar__next');
                } else changeMonth(1);
            }, 'erp-calendar__next');
        previous.disabled = open.view === 'years' ? yearPage <= firstYear : year <= firstYear;
        next.disabled = open.view === 'years' ? yearPage + 11 >= lastYear : year >= lastYear;
        // Day navigation uses the adjacent month, including the year boundary.
        if (open.view === 'days') {
            previous.disabled = Boolean(min && iso(year, month, 1) <= iso(min.year, min.month, 1));
            next.disabled = Boolean(max && iso(year, month, 1) >= iso(max.year, max.month, 1));
        }
        const title = calendarButton(
            open.view === 'days' ? 'Choose month and year' : open.view === 'months' ? 'Choose year' : 'Year range',
            open.view === 'years' ? `${yearPage}–${yearPage + 11}` :
                open.view === 'months' ? String(year) : `${months[month - 1]} ${year}`,
            () => {
                if (open.view === 'days' || open.view === 'months') {
                    open.view = 'years';
                    render('.erp-calendar__title');
                }
            }, 'erp-calendar__title');
        title.setAttribute('aria-live', 'polite');
        if (open.view === 'years') title.disabled = true;
        header.append(previous, title, next);
        popup.append(header);

        if (open.view === 'years') {
            const grid = document.createElement('div');
            grid.className = 'erp-calendar__choices';
            for (let value = yearPage; value < yearPage + 12; value++) {
                const choice = calendarButton(`Choose ${value}`, String(value), () => {
                    open.year = value;
                    if (isDateTime(input)) open.selectedDate = '';
                    open.view = 'months';
                    render('.erp-calendar__choice[aria-pressed="true"]');
                }, 'erp-calendar__choice');
                choice.disabled = value < firstYear || value > lastYear;
                choice.setAttribute('aria-pressed', String(value === year));
                grid.append(choice);
            }
            popup.append(grid);
        } else if (open.view === 'months') {
            const grid = document.createElement('div');
            grid.className = 'erp-calendar__choices';
            months.forEach((name, index) => {
                const value = index + 1;
                const choice = calendarButton(`Choose ${name} ${year}`, name.slice(0, 3), () => {
                    open.month = value;
                    if (isDateTime(input)) open.selectedDate = '';
                    open.view = 'days';
                    render('.erp-calendar__day[tabindex="0"]');
                }, 'erp-calendar__choice');
                choice.disabled = !monthAllowed(year, value, min, max);
                choice.setAttribute('aria-pressed', String(value === month));
                grid.append(choice);
            });
            popup.append(grid);
        } else {
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
            const today = new Date();
            const todayValue = iso(today.getFullYear(), today.getMonth() + 1, today.getDate());
            const selectedValue = isDateTime(input) ? open.selectedDate : input.value;
            const focusedValue = parts(selectedValue)?.month === month && parts(selectedValue)?.year === year ?
                selectedValue : parts(todayValue)?.month === month && parts(todayValue)?.year === year ? todayValue : iso(year, month, 1);
            for (let day = 1; day <= days; day++) {
                const value = iso(year, month, day);
                const button = document.createElement('button');
                button.type = 'button';
                button.className = 'erp-calendar__day';
                button.dataset.date = value;
                button.tabIndex = -1;
                button.textContent = String(day);
                button.setAttribute('aria-label', displayDate(value));
                button.setAttribute('aria-pressed', String(selectedValue === value));
                button.disabled = !canChoose(input, value);
                button.addEventListener('click', () => choose(value));
                button.addEventListener('keydown', event => {
                    if (['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End', 'PageUp', 'PageDown'].includes(event.key)) {
                        event.preventDefault();
                        moveDay(event.key, event.shiftKey);
                    }
                });
                grid.append(button);
            }
            const focusDay = grid.querySelector(`.erp-calendar__day[data-date="${focusedValue}"]:not(:disabled)`) ||
                grid.querySelector('.erp-calendar__day:not(:disabled)');
            if (focusDay) focusDay.tabIndex = 0;
            popup.append(grid);
        }

        if (open.view === 'days' && isDateTime(input)) {
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
        if (open.view === 'days') footer.append(todayButton, clearButton);
        else footer.append(calendarButton('Back to calendar', 'Back to calendar', () => {
            open.view = 'days';
            render('.erp-calendar__day[tabindex="0"]');
        }, 'erp-calendar__back'));
        let apply;
        if (open.view === 'days' && isDateTime(input)) {
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
        position();
        if (focusTarget) {
            const target = popup.querySelector(focusTarget);
            (target && !target.disabled ? target : popup.querySelector('button:not(:disabled)'))?.focus();
        }
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
        popup.setAttribute('aria-modal', 'true');
        const min = parts(input.min.slice(0, 10)), max = parts(input.max.slice(0, 10));
        open = {
            input, trigger, popup, year: selected.year, month: selected.month,
            view: 'days',
            firstYear: min ? min.year : Math.min(selected.year - 100, 1900),
            lastYear: max ? max.year : Math.max(selected.year + 20, 2100),
            selectedDate: input.value.slice(0, 10) || iso(today.getFullYear(), today.getMonth() + 1, today.getDate()),
            time: input.value.slice(11, 16) || `${String(today.getHours()).padStart(2, '0')}:${String(today.getMinutes()).padStart(2, '0')}`
        };
        document.body.append(popup);
        trigger.setAttribute('aria-expanded', 'true');
        trigger.setAttribute('aria-controls', popup.id);
        render();
        (popup.querySelector('.erp-calendar__day[tabindex="0"]') || popup.querySelector('.erp-calendar__title')).focus();
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
        } else if (open && event.key === 'Tab') {
            const controls = [...open.popup.querySelectorAll('button:not(:disabled), input:not(:disabled)')];
            const first = controls[0], last = controls[controls.length - 1];
            if (event.shiftKey && (document.activeElement === first || !open.popup.contains(document.activeElement))) {
                event.preventDefault();
                last.focus();
            } else if (!event.shiftKey && (document.activeElement === last || !open.popup.contains(document.activeElement))) {
                event.preventDefault();
                first.focus();
            }
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
