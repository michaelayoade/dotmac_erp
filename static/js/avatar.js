(function () {
    function sendToast(message, type) {
        if (!message) {
            return;
        }
        window.dispatchEvent(
            new CustomEvent("show-toast", {
                detail: { message: message, type: type || "info" },
            })
        );
    }

    function setStatus(element, message, type) {
        if (!element) {
            return;
        }
        const allClasses = [
            "text-slate-500",
            "text-emerald-600",
            "text-red-600",
            "text-amber-600",
            "dark:text-slate-400",
            "dark:text-emerald-400",
            "dark:text-red-400",
            "dark:text-amber-400",
        ];
        allClasses.forEach(function (cls) {
            element.classList.remove(cls);
        });
        const statusClasses = {
            success: ["text-emerald-600", "dark:text-emerald-400"],
            error: ["text-red-600", "dark:text-red-400"],
            warning: ["text-amber-600", "dark:text-amber-400"],
            info: ["text-slate-500", "dark:text-slate-400"],
        };
        const classes = statusClasses[type] || statusClasses.info;
        classes.forEach(function (cls) {
            element.classList.add(cls);
        });
        element.textContent = message || "";
    }

    function readError(response) {
        return response
            .json()
            .then(function (data) {
                if (data && typeof data.detail === "string") {
                    return data.detail;
                }
                if (data && data.detail && data.detail.message) {
                    return data.detail.message;
                }
                return response.statusText || "Request failed";
            })
            .catch(function () {
                return response.statusText || "Request failed";
            });
    }

    function initAvatarUploader(container) {
        const tokenInput = container.querySelector("[data-avatar-token]");
        const fileInput = container.querySelector("[data-avatar-file]");
        const uploadButton = container.querySelector("[data-avatar-upload]");
        const deleteButton = container.querySelector("[data-avatar-delete]");
        const preview = container.querySelector("[data-avatar-preview]");
        const placeholder = container.querySelector("[data-avatar-placeholder]");
        const status = container.querySelector("[data-avatar-status]");
        const endpoint = container.getAttribute("data-avatar-endpoint") || "/auth/me/avatar";

        function setPreview(source) {
            if (!preview || !placeholder) {
                return;
            }
            preview.src = source;
            preview.classList.remove("hidden");
            placeholder.classList.add("hidden");
        }

        function clearPreview() {
            if (!preview || !placeholder) {
                return;
            }
            preview.src = "";
            preview.classList.add("hidden");
            placeholder.classList.remove("hidden");
        }

        function requireToken() {
            const token = tokenInput ? tokenInput.value.trim() : "";
            if (!token) {
                setStatus(status, "Access token required.", "error");
                sendToast("Access token required.", "error");
                return null;
            }
            return token;
        }

        if (fileInput) {
            fileInput.addEventListener("change", function () {
                const file = fileInput.files && fileInput.files[0];
                if (!file) {
                    clearPreview();
                    return;
                }
                const reader = new FileReader();
                reader.onload = function () {
                    setPreview(reader.result);
                };
                reader.readAsDataURL(file);
            });
        }

        if (uploadButton) {
            uploadButton.addEventListener("click", function () {
                const token = requireToken();
                if (!token) {
                    return;
                }
                const file = fileInput && fileInput.files && fileInput.files[0];
                if (!file) {
                    setStatus(status, "Select an image to upload.", "warning");
                    sendToast("Select an image to upload.", "warning");
                    return;
                }
                const payload = new FormData();
                payload.append("file", file);
                setStatus(status, "Uploading avatar...", "info");
                fetch(endpoint, {
                    method: "POST",
                    headers: {
                        Authorization: "Bearer " + token,
                    },
                    body: payload,
                    credentials: "same-origin",
                })
                    .then(function (response) {
                        if (!response.ok) {
                            return readError(response).then(function (message) {
                                throw new Error(message);
                            });
                        }
                        return response.json();
                    })
                    .then(function (data) {
                        if (data && data.avatar_url) {
                            setPreview(data.avatar_url);
                        }
                        setStatus(status, "Avatar updated.", "success");
                        sendToast("Avatar updated.", "success");
                        if (fileInput) {
                            fileInput.value = "";
                        }
                    })
                    .catch(function (error) {
                        const message = error && error.message ? error.message : "Upload failed.";
                        setStatus(status, message, "error");
                        sendToast(message, "error");
                    });
            });
        }

        if (deleteButton) {
            deleteButton.addEventListener("click", function () {
                const token = requireToken();
                if (!token) {
                    return;
                }
                setStatus(status, "Removing avatar...", "info");
                fetch(endpoint, {
                    method: "DELETE",
                    headers: {
                        Authorization: "Bearer " + token,
                    },
                    credentials: "same-origin",
                })
                    .then(function (response) {
                        if (response.status === 204) {
                            return null;
                        }
                        if (!response.ok) {
                            return readError(response).then(function (message) {
                                throw new Error(message);
                            });
                        }
                        return response.json();
                    })
                    .then(function () {
                        clearPreview();
                        setStatus(status, "Avatar removed.", "success");
                        sendToast("Avatar removed.", "success");
                    })
                    .catch(function (error) {
                        const message = error && error.message ? error.message : "Remove failed.";
                        setStatus(status, message, "error");
                        sendToast(message, "error");
                    });
            });
        }
    }

    function initAll() {
        const containers = document.querySelectorAll("[data-avatar-uploader]");
        containers.forEach(function (container) {
            initAvatarUploader(container);
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initAll);
    } else {
        initAll();
    }
})();
