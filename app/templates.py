"""
Centralized Jinja2 template configuration.

Import `templates` from this module instead of creating new Jinja2Templates instances.
This ensures consistent globals (i18n, datetime, etc.) across all routes.
"""

from datetime import datetime

from fastapi.templating import Jinja2Templates

from app.i18n import t

# Single shared templates instance
templates = Jinja2Templates(directory="templates")

# Register global functions
templates.env.globals["now"] = datetime.now
templates.env.globals["t"] = t      # Translation function
templates.env.globals["_"] = t      # Alias for convenience
