/**
 * Bulk Actions Component for Alpine.js
 * Provides reusable multi-select functionality for list tables
 *
 * Usage:
 *   <div x-data="bulkActions({ entityName: 'customers' })">
 *     <!-- Table with bulk select checkboxes -->
 *   </div>
 */

function bulkActions(config = {}) {
    return {
        // Selection state
        selected: [],
        allIds: [],

        // Configuration
        entityName: config.entityName || 'items',
        csrfToken: '',

        // UI state
        loading: false,
        actionInProgress: null,

        /**
         * Initialize the component
         * Collects all row IDs from data-bulk-id attributes
         */
        init() {
            // Get CSRF token from meta tag
            const meta = document.querySelector('meta[name="csrf-token"]');
            this.csrfToken = meta ? meta.getAttribute('content') : '';

            // Collect all row IDs from data attributes
            this.$nextTick(() => {
                this.refreshIds();
            });

            // Re-collect IDs after HTMX swaps (for pagination)
            document.body.addEventListener('htmx:afterSwap', (event) => {
                if (this.$root.contains(event.target)) {
                    this.refreshIds();
                }
            });
        },

        /**
         * Refresh the list of all IDs from the DOM
         */
        refreshIds() {
            this.allIds = Array.from(
                this.$root.querySelectorAll('[data-bulk-id]')
            ).map(el => el.dataset.bulkId);

            // Remove any selected IDs that are no longer in the list
            this.selected = this.selected.filter(id => this.allIds.includes(id));
        },

        /**
         * Check if all rows are selected
         */
        get isAllSelected() {
            return this.allIds.length > 0 &&
                   this.allIds.every(id => this.selected.includes(id));
        },

        /**
         * Check if some but not all rows are selected (for indeterminate checkbox)
         */
        get isIndeterminate() {
            return this.selected.length > 0 && !this.isAllSelected;
        },

        /**
         * Check if any rows are selected
         */
        get hasSelection() {
            return this.selected.length > 0;
        },

        /**
         * Get count of selected items
         */
        get selectedCount() {
            return this.selected.length;
        },

        /**
         * Toggle all rows selection
         */
        toggleAll() {
            if (this.isAllSelected) {
                this.selected = [];
            } else {
                this.selected = [...this.allIds];
            }
        },

        /**
         * Toggle a single row selection
         */
        toggleRow(id) {
            const idx = this.selected.indexOf(id);
            if (idx > -1) {
                this.selected.splice(idx, 1);
            } else {
                this.selected.push(id);
            }
        },

        /**
         * Check if a specific row is selected
         */
        isSelected(id) {
            return this.selected.includes(id);
        },

        /**
         * Clear all selections
         */
        clearSelection() {
            this.selected = [];
        },

        /**
         * Perform a bulk action
         * @param {string} action - Action name (delete, export, activate, etc.)
         * @param {string} endpoint - API endpoint to POST to
         * @param {object} options - Additional options
         */
        async performAction(action, endpoint, options = {}) {
            if (this.selected.length === 0) {
                this.showToast('No items selected', 'warning');
                return;
            }

            // Show confirmation dialog if specified
            if (options.confirm) {
                const confirmed = await this.showConfirm(options.confirm);
                if (!confirmed) return;
            }

            this.loading = true;
            this.actionInProgress = action;

            try {
                const response = await fetch(endpoint, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRF-Token': this.csrfToken,
                    },
                    body: JSON.stringify({
                        ids: this.selected,
                        action: action,
                        ...options.data
                    }),
                });

                if (response.ok) {
                    const contentType = response.headers.get('content-type');

                    // Handle file download (export)
                    if (contentType && (contentType.includes('text/csv') || contentType.includes('application/octet-stream'))) {
                        const blob = await response.blob();
                        const filename = this.getFilenameFromResponse(response) || `${this.entityName}_export.csv`;
                        this.downloadBlob(blob, filename);
                        this.showToast(`Exported ${this.selected.length} ${this.entityName}`, 'success');
                    } else {
                        // Handle JSON response
                        const result = await response.json();

                        if (result.success_count !== undefined) {
                            const message = result.failed_count > 0
                                ? `${result.success_count} succeeded, ${result.failed_count} failed`
                                : `${result.success_count} ${this.entityName} ${action}d successfully`;
                            this.showToast(message, result.failed_count > 0 ? 'warning' : 'success');
                        } else {
                            this.showToast(result.message || `Action completed successfully`, 'success');
                        }

                        // Clear selection and refresh the page/table
                        this.clearSelection();

                        if (options.refreshSelector) {
                            // HTMX refresh of specific element
                            const target = document.querySelector(options.refreshSelector);
                            if (target && target.getAttribute('hx-get')) {
                                htmx.trigger(target, 'refresh');
                            }
                        } else {
                            // Full page reload
                            window.location.reload();
                        }
                    }
                } else {
                    const error = await response.json().catch(() => ({ detail: 'Action failed' }));
                    this.showToast(error.detail || error.message || 'Action failed', 'error');
                }
            } catch (error) {
                console.error('Bulk action error:', error);
                this.showToast('Network error. Please try again.', 'error');
            } finally {
                this.loading = false;
                this.actionInProgress = null;
            }
        },

        /**
         * Show a confirmation dialog
         */
        showConfirm(message) {
            return new Promise((resolve) => {
                // Use native confirm for now - can be replaced with custom modal
                resolve(window.confirm(message));
            });
        },

        /**
         * Show a toast notification
         */
        showToast(message, type = 'info') {
            window.dispatchEvent(new CustomEvent('show-toast', {
                detail: { message, type }
            }));
        },

        /**
         * Download a blob as a file
         */
        downloadBlob(blob, filename) {
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = filename;
            document.body.appendChild(a);
            a.click();
            window.URL.revokeObjectURL(url);
            document.body.removeChild(a);
        },

        /**
         * Extract filename from Content-Disposition header
         */
        getFilenameFromResponse(response) {
            const disposition = response.headers.get('content-disposition');
            if (disposition) {
                const match = disposition.match(/filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/);
                if (match && match[1]) {
                    return match[1].replace(/['"]/g, '');
                }
            }
            return null;
        },

        /**
         * Shortcut for delete action with confirmation
         */
        async bulkDelete(endpoint) {
            await this.performAction('delete', endpoint, {
                confirm: `Are you sure you want to delete ${this.selected.length} ${this.entityName}? This action cannot be undone.`
            });
        },

        /**
         * Shortcut for export action
         */
        async bulkExport(endpoint, format = 'csv') {
            await this.performAction('export', endpoint, {
                data: { format }
            });
        },

        /**
         * Shortcut for status update action
         */
        async bulkUpdateStatus(endpoint, status) {
            await this.performAction('update_status', endpoint, {
                data: { status },
                confirm: `Update ${this.selected.length} ${this.entityName} to ${status}?`
            });
        }
    };
}

// Make it available globally for Alpine
window.bulkActions = bulkActions;

const BACKGROUND_EXPORTS = {
    '/finance/gl/journals/export': {
        label: 'GL Journals',
        statusBase: '/finance/gl/journals/exports'
    },
    '/finance/gl/ledger/export': {
        label: 'Ledger Transactions',
        statusBase: '/finance/gl/ledger/exports'
    },
    '/finance/ar/invoices/export': {
        label: 'AR Invoices',
        statusBase: '/finance/ar/invoices/exports'
    },
    '/finance/ar/receipts/export': {
        label: 'AR Receipts',
        statusBase: '/finance/ar/receipts/exports'
    }
};

function exportToast(message, type) {
    window.dispatchEvent(new CustomEvent('show-toast', {
        detail: { message, type }
    }));
}

function startExportDownload(downloadUrl) {
    var iframe = document.createElement('iframe');
    iframe.style.display = 'none';
    iframe.src = downloadUrl;
    document.body.appendChild(iframe);
    setTimeout(function() {
        if (iframe.parentNode) {
            iframe.parentNode.removeChild(iframe);
        }
    }, 30000);
}

function pollBackgroundExport(statusUrl, label, attempt) {
    var currentAttempt = attempt || 0;
    fetch(statusUrl, { method: 'GET', credentials: 'same-origin' })
        .then(function(response) {
            if (!response.ok) {
                throw new Error('Status check failed (HTTP ' + response.status + ')');
            }
            return response.json();
        })
        .then(function(data) {
            if (data.status === 'COMPLETED' && data.download_url) {
                exportToast(label + ' export is ready. Starting download...', 'success');
                startExportDownload(data.download_url);
                return;
            }
            if (data.status === 'FAILED') {
                exportToast(data.error || (label + ' export failed.'), 'error');
                return;
            }
            if (currentAttempt >= 180) {
                exportToast(label + ' export is still processing. You will be notified when it is ready.', 'info');
                return;
            }
            var delay = currentAttempt < 2 ? 1500 : 3000;
            setTimeout(function() {
                pollBackgroundExport(statusUrl, label, currentAttempt + 1);
            }, delay);
        })
        .catch(function(error) {
            console.warn('Background export status check failed:', error);
            exportToast(label + ' export is processing. You will be notified when it is ready.', 'info');
        });
}

function getCsrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute('content') : '';
}

function queueBackgroundExport(baseUrl, url) {
    var config = BACKGROUND_EXPORTS[baseUrl];
    exportToast(config.label + ' export is processing...', 'info');

    fetch(url, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'X-CSRF-Token': getCsrfToken() },
    })
        .then(function(response) {
            var contentType = response.headers.get('content-type') || '';
            // Small result set: the server decided to export inline and
            // returned the CSV directly (200). Download it now — no polling.
            if (response.ok && contentType.indexOf('text/csv') !== -1) {
                return saveResponseAsDownload(response).then(function() {
                    exportToast(config.label + ' export downloaded successfully', 'success');
                    return null;
                });
            }
            // Large result set: the server queued a background job (202 JSON).
            return response.json().then(function(data) {
                if (!response.ok) {
                    throw new Error(data.detail || data.message || 'Export failed');
                }
                return data;
            });
        })
        .then(function(data) {
            if (!data) {
                return; // inline download already handled
            }
            exportToast(data.message || (config.label + ' export is processing. You will be notified when it is ready.'), 'info');
            var statusUrl = data.status_url || (config.statusBase + '/' + data.instance_id + '/status');
            pollBackgroundExport(statusUrl, config.label, 0);
        })
        .catch(function(error) {
            console.warn('Background export failed, falling back to immediate export:', error);
            exportToast(error.message || 'Export failed. Trying immediate download...', 'warning');
            downloadExportNow(url);
        });
}

// Trigger a browser download from a fetch Response carrying file bytes.
// Reads the filename from Content-Disposition. Returns a promise.
function saveResponseAsDownload(response) {
    return response.blob().then(function(blob) {
        var disposition = response.headers.get('content-disposition');
        var filename = 'export.csv';
        if (disposition) {
            var match = disposition.match(/filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/);
            if (match && match[1]) {
                filename = match[1].replace(/['"]/g, '');
            }
        }
        var a = document.createElement('a');
        a.href = window.URL.createObjectURL(blob);
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(a.href);
        document.body.removeChild(a);
    });
}

function downloadExportNow(url) {
    fetch(url, { method: 'GET', credentials: 'same-origin' })
        .then(function(response) {
            if (!response.ok) {
                throw new Error('Export failed (HTTP ' + response.status + ')');
            }
            return saveResponseAsDownload(response).then(function() {
                exportToast('Export downloaded successfully', 'success');
            });
        })
        .catch(function(error) {
            console.warn('Fetch export failed, falling back to iframe:', error);
            // Fallback: use hidden iframe for browsers where fetch+blob fails
            startExportDownload(url);
            exportToast('Export started - check your downloads', 'success');
        });
}

/**
 * Export All - downloads CSV of all records matching current search/filters.
 * Works independently of the bulkActions component (no selection needed).
 *
 * Uses native browser download (window.location) instead of fetch+blob,
 * which is more reliable across browsers (no popup blocker issues,
 * no blob URL failures, no silent redirect-following).
 *
 * @param {string} baseUrl - GET endpoint path (e.g. "/finance/ar/invoices/export")
 */
function exportAll(baseUrl) {
    const filterKeys = ['search', 'status', 'category', 'type', 'item_type', 'start_date', 'end_date', 'customer_id', 'supplier_id', 'account_id', 'match_status', 'date_from', 'date_to'];

    function collectFilterValue(source, key) {
        const values = source.getAll(key).filter(function(value) {
            return value !== null && String(value).trim() !== '';
        });
        return values.length ? values[values.length - 1] : null;
    }

    const params = new URLSearchParams(window.location.search);
    const exportParams = new URLSearchParams();

    // Forward known filter params from the URL first.
    for (const key of filterKeys) {
        const val = collectFilterValue(params, key);
        if (val) {
            exportParams.set(key, val);
        }
    }

    // Then let the current filter form override the URL. This keeps export in
    // sync with HTMX-updated lists where the visible controls are the source of truth.
    document.querySelectorAll('form[action], form[hx-get]').forEach(function(form) {
        const action = form.getAttribute('action') || form.getAttribute('hx-get') || '';
        const actionPath = action.split('?')[0];
        if (actionPath !== baseUrl.replace(/\/export$/, '')) {
            return;
        }
        const formParams = new URLSearchParams(new FormData(form));
        for (const key of filterKeys) {
            const val = collectFilterValue(formParams, key);
            if (val) {
                exportParams.set(key, val);
            } else {
                exportParams.delete(key);
            }
        }
    });

    const url = exportParams.toString()
        ? `${baseUrl}?${exportParams.toString()}`
        : baseUrl;

    if (BACKGROUND_EXPORTS[baseUrl]) {
        queueBackgroundExport(baseUrl, url);
        return;
    }

    exportToast('Preparing export...', 'info');

    // Use fetch for download so we can provide success/error feedback,
    // then fall back to iframe if blob download fails.
    downloadExportNow(url);
}

window.exportAll = exportAll;
