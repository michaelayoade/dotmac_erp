from app.services.people.payroll.payslip_pdf import PayslipPDFService


def test_resolve_logo_url_absolute_kept() -> None:
    url = "https://erp.dotmac.io/files/branding/logo.png"
    assert PayslipPDFService._resolve_logo_url(url, "http://app:8002") == url


def test_resolve_logo_url_relative_prefixed() -> None:
    url = "/files/branding/logo.png"
    assert (
        PayslipPDFService._resolve_logo_url(url, "http://app:8002")
        == "http://app:8002/files/branding/logo.png"
    )


def test_resolve_logo_url_none() -> None:
    assert PayslipPDFService._resolve_logo_url(None, "http://app:8002") is None


def test_extract_branding_s3_key_files_url() -> None:
    url = "/files/branding/00000000-0000-0000-0000-000000000001/logo_abc123.png"
    assert (
        PayslipPDFService._extract_branding_s3_key(url)
        == "branding/00000000-0000-0000-0000-000000000001/logo_abc123.png"
    )


def test_extract_branding_s3_key_absolute_url() -> None:
    url = (
        "https://erp.dotmac.io/files/branding/"
        "00000000-0000-0000-0000-000000000001/logo_abc123.png"
    )
    assert (
        PayslipPDFService._extract_branding_s3_key(url)
        == "branding/00000000-0000-0000-0000-000000000001/logo_abc123.png"
    )
