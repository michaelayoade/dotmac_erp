"""Location (Branch) web service."""

from __future__ import annotations

import json
import logging
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.models.finance.core_org.location import LocationType
from app.services.common import PaginationParams, coerce_uuid
from app.services.people.hr import OrganizationService
from app.services.people.hr.web.constants import DEFAULT_PAGE_SIZE
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

logger = logging.getLogger(__name__)


def _parse_bool(value: Any, default: bool = False) -> bool:
    """Parse a form value to boolean."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("true", "1", "yes", "on")
    return bool(value)


def _parse_location_type(value: Any) -> LocationType | None:
    """Parse a form value to LocationType."""
    if value is None or value == "":
        return None
    if isinstance(value, LocationType):
        return value
    try:
        return LocationType(str(value).upper())
    except ValueError:
        return None


class LocationWebService:
    """Web service for location (branch) routes."""

    @staticmethod
    def _form_str(form: Any, key: str) -> str:
        value = form.get(key)
        if value is None:
            return ""
        return str(value).strip()

    @staticmethod
    def list_locations_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
        search: str | None,
        page: int,
    ) -> HTMLResponse:
        org_id = coerce_uuid(auth.organization_id)
        svc = OrganizationService(db, org_id)

        limit = DEFAULT_PAGE_SIZE
        offset = (page - 1) * limit
        result = svc.list_locations(
            search=search,
            pagination=PaginationParams(offset=offset, limit=limit),
        )

        total_pages = (result.total + limit - 1) // limit if result.total else 1

        context = {
            **base_context(request, auth, "Branches", "locations"),
            "locations": result.items,
            "search": search or "",
            "page": page,
            "total_pages": total_pages,
            "total": result.total,
            "has_prev": page > 1,
            "has_next": page < total_pages,
        }
        return templates.TemplateResponse(
            request,
            "people/hr/locations.html",
            context,
        )

    @staticmethod
    def new_location_form_response(
        request: Request,
        auth: WebAuthContext,
    ) -> HTMLResponse:
        context = {
            **base_context(request, auth, "New Branch", "locations"),
            "location": None,
            "location_types": [t.value for t in LocationType],
            "errors": {},
        }
        return templates.TemplateResponse(
            request,
            "people/hr/location_form.html",
            context,
        )

    @staticmethod
    def edit_location_form_response(
        request: Request,
        location_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        org_id = coerce_uuid(auth.organization_id)
        svc = OrganizationService(db, org_id)
        try:
            location = svc.get_location(coerce_uuid(location_id))
        except Exception:
            return RedirectResponse(url="/people/hr/locations", status_code=303)

        context = {
            **base_context(request, auth, "Edit Branch", "locations"),
            "location": location,
            "location_types": [t.value for t in LocationType],
            "errors": {},
        }
        return templates.TemplateResponse(
            request,
            "people/hr/location_form.html",
            context,
        )

    @staticmethod
    async def create_location_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()

        location_code = LocationWebService._form_str(form, "location_code")
        location_name = LocationWebService._form_str(form, "location_name")
        location_type = LocationWebService._form_str(form, "location_type")
        address_line_1 = LocationWebService._form_str(form, "address_line_1")
        address_line_2 = LocationWebService._form_str(form, "address_line_2")
        city = LocationWebService._form_str(form, "city")
        state_province = LocationWebService._form_str(form, "state_province")
        postal_code = LocationWebService._form_str(form, "postal_code")
        country_code = LocationWebService._form_str(form, "country_code")
        latitude_value = LocationWebService._form_str(form, "latitude")
        longitude_value = LocationWebService._form_str(form, "longitude")
        radius_value = LocationWebService._form_str(form, "geofence_radius_m")
        geofence_enabled = _parse_bool(form.get("geofence_enabled"), True)
        is_active = _parse_bool(form.get("is_active"), True)

        errors = {}
        if not location_code:
            errors["location_code"] = "Required"
        if not location_name:
            errors["location_name"] = "Required"

        latitude: float | None = None
        longitude: float | None = None
        geofence_radius_m = 500

        if latitude_value:
            try:
                latitude = float(Decimal(latitude_value))
            except (InvalidOperation, ValueError):
                errors["latitude"] = "Invalid latitude"
        if longitude_value:
            try:
                longitude = float(Decimal(longitude_value))
            except (InvalidOperation, ValueError):
                errors["longitude"] = "Invalid longitude"
        if radius_value:
            try:
                geofence_radius_m = int(radius_value)
            except (TypeError, ValueError):
                errors["geofence_radius_m"] = "Invalid radius"

        if errors:
            context = {
                **base_context(request, auth, "New Branch", "locations"),
                "location": SimpleNamespace(
                    location_code=location_code,
                    location_name=location_name,
                    location_type=location_type or None,
                    address_line_1=address_line_1 or None,
                    address_line_2=address_line_2 or None,
                    city=city or None,
                    state_province=state_province or None,
                    postal_code=postal_code or None,
                    country_code=country_code or None,
                    latitude=latitude,
                    longitude=longitude,
                    geofence_radius_m=geofence_radius_m,
                    geofence_enabled=geofence_enabled,
                    is_active=is_active,
                ),
                "location_types": [t.value for t in LocationType],
                "errors": errors,
                "error": "Location code and name are required.",
            }
            return templates.TemplateResponse(
                request,
                "people/hr/location_form.html",
                context,
            )

        org_id = coerce_uuid(auth.organization_id)
        svc = OrganizationService(db, org_id)
        svc.create_location(
            location_code=location_code,
            location_name=location_name,
            location_type=_parse_location_type(location_type),
            address_line_1=address_line_1 or None,
            address_line_2=address_line_2 or None,
            city=city or None,
            state_province=state_province or None,
            postal_code=postal_code or None,
            country_code=country_code or None,
            latitude=latitude,
            longitude=longitude,
            geofence_radius_m=geofence_radius_m,
            geofence_enabled=geofence_enabled,
            geofence_polygon=None,
            is_active=is_active,
        )
        db.commit()

        return RedirectResponse(url="/people/hr/locations", status_code=303)

    @staticmethod
    async def update_location_response(
        request: Request,
        location_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()

        location_code = LocationWebService._form_str(form, "location_code")
        location_name = LocationWebService._form_str(form, "location_name")
        location_type = LocationWebService._form_str(form, "location_type")
        address_line_1 = LocationWebService._form_str(form, "address_line_1")
        address_line_2 = LocationWebService._form_str(form, "address_line_2")
        city = LocationWebService._form_str(form, "city")
        state_province = LocationWebService._form_str(form, "state_province")
        postal_code = LocationWebService._form_str(form, "postal_code")
        country_code = LocationWebService._form_str(form, "country_code")
        latitude_value = LocationWebService._form_str(form, "latitude")
        longitude_value = LocationWebService._form_str(form, "longitude")
        radius_value = LocationWebService._form_str(form, "geofence_radius_m")
        geofence_enabled = _parse_bool(form.get("geofence_enabled"), True)
        is_active = _parse_bool(form.get("is_active"), True)

        errors = {}
        if not location_code:
            errors["location_code"] = "Required"
        if not location_name:
            errors["location_name"] = "Required"

        latitude: float | None = None
        longitude: float | None = None
        geofence_radius_m = 500

        if latitude_value:
            try:
                latitude = float(Decimal(latitude_value))
            except (InvalidOperation, ValueError):
                errors["latitude"] = "Invalid latitude"
        if longitude_value:
            try:
                longitude = float(Decimal(longitude_value))
            except (InvalidOperation, ValueError):
                errors["longitude"] = "Invalid longitude"
        if radius_value:
            try:
                geofence_radius_m = int(radius_value)
            except (TypeError, ValueError):
                errors["geofence_radius_m"] = "Invalid radius"

        org_id = coerce_uuid(auth.organization_id)
        svc = OrganizationService(db, org_id)
        try:
            location = svc.get_location(coerce_uuid(location_id))
        except Exception:
            return RedirectResponse(url="/people/hr/locations", status_code=303)

        if errors:
            context = {
                **base_context(request, auth, "Edit Branch", "locations"),
                "location": SimpleNamespace(
                    location_id=location.location_id,
                    location_code=location_code or location.location_code,
                    location_name=location_name or location.location_name,
                    location_type=location_type
                    or (
                        location.location_type.value if location.location_type else None
                    ),
                    address_line_1=address_line_1 or location.address_line_1,
                    address_line_2=address_line_2 or location.address_line_2,
                    city=city or location.city,
                    state_province=state_province or location.state_province,
                    postal_code=postal_code or location.postal_code,
                    country_code=country_code or location.country_code,
                    latitude=latitude if latitude_value else location.latitude,
                    longitude=longitude if longitude_value else location.longitude,
                    geofence_radius_m=geofence_radius_m
                    if radius_value
                    else location.geofence_radius_m,
                    geofence_enabled=geofence_enabled,
                    is_active=is_active,
                ),
                "location_types": [t.value for t in LocationType],
                "errors": errors,
                "error": "Location code and name are required.",
            }
            return templates.TemplateResponse(
                request,
                "people/hr/location_form.html",
                context,
            )

        svc.update_location(
            coerce_uuid(location_id),
            {
                "location_code": location_code,
                "location_name": location_name,
                "location_type": _parse_location_type(location_type),
                "address_line_1": address_line_1 or None,
                "address_line_2": address_line_2 or None,
                "city": city or None,
                "state_province": state_province or None,
                "postal_code": postal_code or None,
                "country_code": country_code or None,
                "latitude": latitude,
                "longitude": longitude,
                "geofence_radius_m": geofence_radius_m,
                "geofence_enabled": geofence_enabled,
                "is_active": is_active,
            },
        )
        db.commit()

        return RedirectResponse(url="/people/hr/locations", status_code=303)

    @staticmethod
    def geofence_editor_response(
        request: Request,
        location_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        org_id = coerce_uuid(auth.organization_id)
        svc = OrganizationService(db, org_id)
        try:
            location = svc.get_location(coerce_uuid(location_id))
        except Exception:
            return RedirectResponse(url="/people/hr/locations", status_code=303)

        geofence_data = {
            "center": {
                "lat": float(location.latitude) if location.latitude else 0,
                "lng": float(location.longitude) if location.longitude else 0,
            },
            "radius": location.geofence_radius_m or 500,
            "polygon": location.geofence_polygon,
        }

        context = {
            **base_context(
                request, auth, f"Geofence - {location.location_name}", "locations"
            ),
            "location": location,
            "geofence_data": json.dumps(geofence_data),
        }
        return templates.TemplateResponse(
            request,
            "people/hr/geofence_editor.html",
            context,
        )

    @staticmethod
    async def save_geofence_response(
        request: Request,
        location_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> JSONResponse:
        org_id = coerce_uuid(auth.organization_id)
        svc = OrganizationService(db, org_id)
        try:
            location = svc.get_location(coerce_uuid(location_id))
        except Exception:
            return JSONResponse({"error": "Location not found"}, status_code=404)

        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON"}, status_code=400)

        location.geofence_enabled = body.get("geofence_enabled", True)

        center = body.get("center", {})
        if center.get("lat") is not None:
            location.latitude = float(center["lat"])
        if center.get("lng") is not None:
            location.longitude = float(center["lng"])
        if body.get("radius") is not None:
            location.geofence_radius_m = int(body["radius"])

        polygon = body.get("polygon")
        if polygon:
            if polygon.get("type") not in ("Polygon", "MultiPolygon"):
                return JSONResponse(
                    {"error": "Invalid GeoJSON: must be Polygon or MultiPolygon"},
                    status_code=400,
                )
            if "coordinates" not in polygon:
                return JSONResponse(
                    {"error": "Invalid GeoJSON: missing coordinates"},
                    status_code=400,
                )
            location.geofence_polygon = polygon
        else:
            location.geofence_polygon = None

        db.commit()

        return JSONResponse(
            {
                "success": True,
                "message": "Geofence saved successfully",
                "mode": "polygon" if location.geofence_polygon else "circle",
            }
        )

    @staticmethod
    async def test_geofence_response(
        request: Request,
        location_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> JSONResponse:
        org_id = coerce_uuid(auth.organization_id)
        svc = OrganizationService(db, org_id)
        try:
            location = svc.get_location(coerce_uuid(location_id))
        except Exception:
            return JSONResponse({"error": "Location not found"}, status_code=404)

        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON"}, status_code=400)

        lat = body.get("latitude")
        lng = body.get("longitude")

        if lat is None or lng is None:
            return JSONResponse(
                {"error": "latitude and longitude are required"}, status_code=400
            )

        if location.geofence_polygon:
            try:
                from shapely.geometry import Point, shape
                from shapely.validation import make_valid

                point = Point(float(lng), float(lat))
                polygon = shape(location.geofence_polygon)
                if not polygon.is_valid:
                    polygon = make_valid(polygon)

                is_inside = polygon.contains(point)

                return JSONResponse(
                    {
                        "inside": is_inside,
                        "mode": "polygon",
                        "message": "Inside geofence"
                        if is_inside
                        else "Outside geofence",
                    }
                )
            except ImportError:
                return JSONResponse(
                    {"error": "Shapely library not installed"}, status_code=500
                )
            except Exception as e:
                return JSONResponse({"error": f"Invalid polygon: {e}"}, status_code=400)

        if location.latitude is None or location.longitude is None:
            return JSONResponse(
                {"inside": False, "error": "No center coordinates configured"}
            )

        import math

        def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
            radius_m = 6371000.0
            phi1 = math.radians(lat1)
            phi2 = math.radians(lat2)
            d_phi = math.radians(lat2 - lat1)
            d_lambda = math.radians(lon2 - lon1)
            a = (
                math.sin(d_phi / 2) ** 2
                + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
            )
            return 2 * radius_m * math.asin(math.sqrt(a))

        distance = haversine_m(
            float(lat),
            float(lng),
            float(location.latitude),
            float(location.longitude),
        )
        radius = float(location.geofence_radius_m or 500)
        is_inside = distance <= radius

        return JSONResponse(
            {
                "inside": is_inside,
                "mode": "circle",
                "distance_m": round(distance, 1),
                "radius_m": radius,
                "message": f"{'Inside' if is_inside else 'Outside'} geofence ({distance:.0f}m / {radius:.0f}m)",
            }
        )


location_web_service = LocationWebService()
