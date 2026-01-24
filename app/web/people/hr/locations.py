"""Locations (Branches) routes."""

import json
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.models.finance.core_org.location import Location, LocationType
from app.services.common import coerce_uuid
from app.services.people.hr.web.employee_web import DEFAULT_PAGE_SIZE
from app.templates import templates
from app.web.deps import base_context, get_db, require_hr_access, WebAuthContext

from ._common import _parse_bool, _parse_location_type


router = APIRouter()


# =============================================================================
# Locations (Branches)
# =============================================================================


@router.get("/locations", response_class=HTMLResponse)
def list_locations(
    request: Request,
    search: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Location list page."""
    org_id = coerce_uuid(auth.organization_id)
    query = db.query(Location).filter(Location.organization_id == org_id)
    if search:
        search_term = f"%{search}%"
        query = query.filter(
            Location.location_code.ilike(search_term)
            | Location.location_name.ilike(search_term)
        )

    total = query.count()
    limit = DEFAULT_PAGE_SIZE
    offset = (page - 1) * limit
    items = (
        query.order_by(Location.location_name)
        .offset(offset)
        .limit(limit)
        .all()
    )

    total_pages = (total + limit - 1) // limit if total else 1

    context = {
        **base_context(request, auth, "Branches", "locations"),
        "locations": items,
        "search": search or "",
        "page": page,
        "total_pages": total_pages,
        "total": total,
        "has_prev": page > 1,
        "has_next": page < total_pages,
    }

    return templates.TemplateResponse(
        request,
        "people/hr/locations.html",
        context,
    )


@router.get("/locations/new", response_class=HTMLResponse)
def new_location_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New location form page."""
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


@router.get("/locations/{location_id}/edit", response_class=HTMLResponse)
def edit_location_form(
    request: Request,
    location_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Edit location form page."""
    org_id = coerce_uuid(auth.organization_id)
    location = db.get(Location, coerce_uuid(location_id))
    if not location or location.organization_id != org_id:
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


@router.post("/locations/new")
async def create_location(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Handle new location form submission."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    location_code = (form.get("location_code") or "").strip()
    location_name = (form.get("location_name") or "").strip()
    location_type = (form.get("location_type") or "").strip()
    address_line_1 = (form.get("address_line_1") or "").strip()
    address_line_2 = (form.get("address_line_2") or "").strip()
    city = (form.get("city") or "").strip()
    state_province = (form.get("state_province") or "").strip()
    postal_code = (form.get("postal_code") or "").strip()
    country_code = (form.get("country_code") or "").strip()
    latitude_value = (form.get("latitude") or "").strip()
    longitude_value = (form.get("longitude") or "").strip()
    radius_value = (form.get("geofence_radius_m") or "").strip()
    geofence_enabled = _parse_bool(form.get("geofence_enabled"), True)
    is_active = _parse_bool(form.get("is_active"), True)

    errors = {}
    if not location_code:
        errors["location_code"] = "Required"
    if not location_name:
        errors["location_name"] = "Required"

    latitude = None
    longitude = None
    geofence_radius_m = 500

    if latitude_value:
        try:
            latitude = Decimal(latitude_value)
        except (InvalidOperation, ValueError):
            errors["latitude"] = "Invalid latitude"
    if longitude_value:
        try:
            longitude = Decimal(longitude_value)
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
    location = Location(
        organization_id=org_id,
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
        is_active=is_active,
    )
    db.add(location)
    db.commit()

    return RedirectResponse(url="/people/hr/locations", status_code=303)


@router.post("/locations/{location_id}/edit")
async def update_location(
    request: Request,
    location_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Handle location update form submission."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    location_code = (form.get("location_code") or "").strip()
    location_name = (form.get("location_name") or "").strip()
    location_type = (form.get("location_type") or "").strip()
    address_line_1 = (form.get("address_line_1") or "").strip()
    address_line_2 = (form.get("address_line_2") or "").strip()
    city = (form.get("city") or "").strip()
    state_province = (form.get("state_province") or "").strip()
    postal_code = (form.get("postal_code") or "").strip()
    country_code = (form.get("country_code") or "").strip()
    latitude_value = (form.get("latitude") or "").strip()
    longitude_value = (form.get("longitude") or "").strip()
    radius_value = (form.get("geofence_radius_m") or "").strip()
    geofence_enabled = _parse_bool(form.get("geofence_enabled"), True)
    is_active = _parse_bool(form.get("is_active"), True)

    errors = {}
    if not location_code:
        errors["location_code"] = "Required"
    if not location_name:
        errors["location_name"] = "Required"

    latitude = None
    longitude = None
    geofence_radius_m = 500

    if latitude_value:
        try:
            latitude = Decimal(latitude_value)
        except (InvalidOperation, ValueError):
            errors["latitude"] = "Invalid latitude"
    if longitude_value:
        try:
            longitude = Decimal(longitude_value)
        except (InvalidOperation, ValueError):
            errors["longitude"] = "Invalid longitude"
    if radius_value:
        try:
            geofence_radius_m = int(radius_value)
        except (TypeError, ValueError):
            errors["geofence_radius_m"] = "Invalid radius"

    org_id = coerce_uuid(auth.organization_id)
    location = db.get(Location, coerce_uuid(location_id))
    if not location or location.organization_id != org_id:
        return RedirectResponse(url="/people/hr/locations", status_code=303)

    if errors:
        context = {
            **base_context(request, auth, "Edit Branch", "locations"),
            "location": SimpleNamespace(
                location_id=location.location_id,
                location_code=location_code or location.location_code,
                location_name=location_name or location.location_name,
                location_type=location_type or (location.location_type.value if location.location_type else None),
                address_line_1=address_line_1 or location.address_line_1,
                address_line_2=address_line_2 or location.address_line_2,
                city=city or location.city,
                state_province=state_province or location.state_province,
                postal_code=postal_code or location.postal_code,
                country_code=country_code or location.country_code,
                latitude=latitude if latitude_value else location.latitude,
                longitude=longitude if longitude_value else location.longitude,
                geofence_radius_m=geofence_radius_m if radius_value else location.geofence_radius_m,
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

    location.location_code = location_code
    location.location_name = location_name
    location.location_type = _parse_location_type(location_type)
    location.address_line_1 = address_line_1 or None
    location.address_line_2 = address_line_2 or None
    location.city = city or None
    location.state_province = state_province or None
    location.postal_code = postal_code or None
    location.country_code = country_code or None
    location.latitude = latitude
    location.longitude = longitude
    location.geofence_radius_m = geofence_radius_m
    location.geofence_enabled = geofence_enabled
    location.is_active = is_active

    db.commit()

    return RedirectResponse(url="/people/hr/locations", status_code=303)


# =============================================================================
# Geofence Map Editor
# =============================================================================


@router.get("/locations/{location_id}/geofence", response_class=HTMLResponse)
def geofence_editor(
    request: Request,
    location_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Geofence polygon editor page with interactive map."""
    org_id = coerce_uuid(auth.organization_id)
    location = db.get(Location, coerce_uuid(location_id))
    if not location or location.organization_id != org_id:
        return RedirectResponse(url="/people/hr/locations", status_code=303)

    # Prepare geofence data for the map
    geofence_data = {
        "center": {
            "lat": float(location.latitude) if location.latitude else 0,
            "lng": float(location.longitude) if location.longitude else 0,
        },
        "radius": location.geofence_radius_m or 500,
        "polygon": location.geofence_polygon,
    }

    context = {
        **base_context(request, auth, f"Geofence - {location.location_name}", "locations"),
        "location": location,
        "geofence_data": json.dumps(geofence_data),
    }

    return templates.TemplateResponse(
        request,
        "people/hr/geofence_editor.html",
        context,
    )


@router.post("/locations/{location_id}/geofence")
async def save_geofence(
    request: Request,
    location_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Save geofence settings (polygon or circle)."""
    org_id = coerce_uuid(auth.organization_id)
    location = db.get(Location, coerce_uuid(location_id))
    if not location or location.organization_id != org_id:
        return JSONResponse(
            {"error": "Location not found"},
            status_code=404,
        )

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {"error": "Invalid JSON"},
            status_code=400,
        )

    location.geofence_enabled = body.get("geofence_enabled", True)

    # Update circle parameters (used as fallback when no polygon)
    center = body.get("center", {})
    if center.get("lat") is not None:
        location.latitude = Decimal(str(center["lat"]))
    if center.get("lng") is not None:
        location.longitude = Decimal(str(center["lng"]))
    if body.get("radius") is not None:
        location.geofence_radius_m = int(body["radius"])

    # Update polygon if provided
    polygon = body.get("polygon")
    if polygon:
        # Validate GeoJSON structure
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
        # Clear polygon to use circle mode
        location.geofence_polygon = None

    db.commit()

    return JSONResponse({
        "success": True,
        "message": "Geofence saved successfully",
        "mode": "polygon" if location.geofence_polygon else "circle",
    })


@router.post("/locations/{location_id}/geofence/test")
async def test_geofence(
    request: Request,
    location_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Test if a point is within the geofence."""
    org_id = coerce_uuid(auth.organization_id)
    location = db.get(Location, coerce_uuid(location_id))
    if not location or location.organization_id != org_id:
        return JSONResponse(
            {"error": "Location not found"},
            status_code=404,
        )

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {"error": "Invalid JSON"},
            status_code=400,
        )

    lat = body.get("latitude")
    lng = body.get("longitude")

    if lat is None or lng is None:
        return JSONResponse(
            {"error": "latitude and longitude are required"},
            status_code=400,
        )

    # Use polygon if configured, otherwise use circle
    if location.geofence_polygon:
        # Use Shapely for point-in-polygon test
        try:
            from shapely.geometry import Point, shape
            from shapely.validation import make_valid

            point = Point(float(lng), float(lat))  # GeoJSON uses lng, lat
            polygon = shape(location.geofence_polygon)
            if not polygon.is_valid:
                polygon = make_valid(polygon)

            is_inside = polygon.contains(point)

            return JSONResponse({
                "inside": is_inside,
                "mode": "polygon",
                "message": "Inside geofence" if is_inside else "Outside geofence",
            })
        except ImportError:
            return JSONResponse({
                "error": "Shapely library not installed",
            }, status_code=500)
        except Exception as e:
            return JSONResponse({
                "error": f"Invalid polygon: {e}",
            }, status_code=400)

    else:
        # Circle-based test using Haversine
        if location.latitude is None or location.longitude is None:
            return JSONResponse({
                "inside": False,
                "error": "No center coordinates configured",
            })

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

        return JSONResponse({
            "inside": is_inside,
            "mode": "circle",
            "distance_m": round(distance, 1),
            "radius_m": radius,
            "message": f"{'Inside' if is_inside else 'Outside'} geofence ({distance:.0f}m / {radius:.0f}m)",
        })
