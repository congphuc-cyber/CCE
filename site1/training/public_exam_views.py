from datetime import timedelta
from functools import lru_cache
from io import BytesIO
import os
from pathlib import Path
from xml.sax.saxutils import escape

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
from django.db import transaction
from django.db.models import Count, F, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import salted_hmac
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from training.models import (
    AuditLog,
    ExamApplication,
    ExamCertificate,
    ExamPeriod,
    ExamRoomAssignment,
    ExamScore,
)
from training.public_exam_forms import (
    PublicExamLookupRequestForm,
    PublicExamRegistrationForm,
)
from training.views import _exam_certificate_pdf_response


REGISTRATION_SUCCESS_SESSION_KEY = "public_exam_registration_id"
REGISTRATION_EXPORT_SESSION_KEY = "public_exam_registration_export_id"
LOOKUP_RESULT_SESSION_KEY = "public_exam_lookup_result"
LOOKUP_SESSION_TTL = timedelta(minutes=10)
LOOKUP_RATE_WINDOW = 15 * 60
LOOKUP_IP_LIMIT = 12
LOOKUP_IDENTITY_LIMIT = 5
CERTIFICATE_DOWNLOAD_SIGNER = TimestampSigner(salt="exam-public-certificate-pdf")
_PUBLIC_PDF_REQUIRED_GLYPHS = (
    0x00C2, 0x00E2, 0x00CA, 0x00EA, 0x00D4, 0x00F4,
    0x0102, 0x0103, 0x0110, 0x0111, 0x01A0, 0x01A1,
    0x01AF, 0x01B0,
) + tuple(range(0x1EA0, 0x1EFA))


def available_exam_periods():
    return (
        ExamPeriod.objects.filter(
            status=ExamPeriod.Status.PUBLISHED,
            registration_deadline__gte=timezone.localdate(),
        )
        .annotate(candidate_count=Count("applications__id_number", distinct=True))
        .filter(candidate_count__lt=F("max_candidates"))
        .select_related("cert_type")
        .order_by("exam_date", "name")
    )


def unavailable_period_state(period, id_number=""):
    if period.status != ExamPeriod.Status.PUBLISHED:
        return "closed"
    if period.registration_deadline < timezone.localdate():
        return "expired"
    candidate_count = period.applications.values("id_number").distinct().count()
    is_duplicate = bool(
        id_number
        and period.applications.filter(id_number=id_number).exists()
    )
    if candidate_count >= period.max_candidates and not is_duplicate:
        return "full"
    return None


def render_registration_closed(request, period=None, state="closed"):
    return render(
        request,
        "training/public_exam_registration_closed.html",
        {
            "period": period,
            "state": state,
        },
        status=200,
    )


@require_http_methods(["GET", "POST"])
def exam_application_register(request):
    request.session.pop(REGISTRATION_EXPORT_SESSION_KEY, None)
    available_periods = available_exam_periods()
    initial_period = None

    if request.method == "GET":
        raw_period_id = request.GET.get("exam_period", "")
        if raw_period_id.isdecimal():
            initial_period = ExamPeriod.objects.filter(pk=int(raw_period_id)).select_related(
                "cert_type"
            ).first()
            if initial_period:
                state = unavailable_period_state(initial_period)
                if state and state != "full":
                    return render_registration_closed(request, initial_period, state)
                if state == "full":
                    period_ids = list(available_periods.values_list("pk", flat=True))
                    period_ids.append(initial_period.pk)
                    available_periods = ExamPeriod.objects.filter(
                        pk__in=period_ids
                    ).select_related("cert_type").order_by("exam_date", "name")
    else:
        raw_period_id = request.POST.get("exam_period", "")
        if raw_period_id.isdecimal():
            selected_period = ExamPeriod.objects.filter(pk=int(raw_period_id)).select_related(
                "cert_type"
            ).first()
            if selected_period:
                id_number = request.POST.get("id_number", "").strip()
                state = unavailable_period_state(selected_period, id_number)
                if state:
                    return render_registration_closed(request, selected_period, state)
                if id_number and selected_period.applications.filter(
                    id_number=id_number
                ).exists():
                    period_ids = list(available_periods.values_list("pk", flat=True))
                    period_ids.append(selected_period.pk)
                    available_periods = ExamPeriod.objects.filter(
                        pk__in=period_ids
                    ).select_related("cert_type").order_by("exam_date", "name")

    if not available_periods.exists():
        return render_registration_closed(request)

    form = PublicExamRegistrationForm(
        request.POST if request.method == "POST" else None,
        available_periods=available_periods,
        initial_period=initial_period,
    )

    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                period = get_object_or_404(
                    ExamPeriod.objects.select_for_update().select_related("cert_type"),
                    pk=form.cleaned_data["exam_period"].pk,
                )
                state = unavailable_period_state(period)
                if state:
                    return render_registration_closed(request, period, state)

                application = ExamApplication(
                    exam_period=period,
                    full_name=form.cleaned_data["full_name"],
                    date_of_birth=form.cleaned_data["date_of_birth"],
                    id_number=form.cleaned_data["id_number"],
                    phone=form.cleaned_data["phone"],
                    email=form.cleaned_data["email"],
                    source=ExamApplication.Source.ONLINE,
                    status=ExamApplication.Status.NEW,
                )
                application.save()
        except ValidationError as error:
            if hasattr(error, "error_dict"):
                for field, errors in error.error_dict.items():
                    form_field = "exam_period" if field == "exam_period" else None
                    for validation_error in errors:
                        form.add_error(form_field, validation_error)
            else:
                form.add_error(None, error)
        else:
            request.session[REGISTRATION_SUCCESS_SESSION_KEY] = application.pk
            return redirect("exam-application-public-success")

    return render(
        request,
        "training/public_exam_registration.html",
        {
            "form": form,
            "registration_periods": available_periods,
            "error_summary": bool(form.errors),
        },
        status=400 if request.method == "POST" and form.errors else 200,
    )


def exam_application_registration_success(request):
    application_id = request.session.pop(REGISTRATION_SUCCESS_SESSION_KEY, None)
    if not application_id:
        return redirect("exam-application-public-register")

    application = get_object_or_404(
        ExamApplication.objects.select_related("exam_period__cert_type"),
        pk=application_id,
        source=ExamApplication.Source.ONLINE,
    )
    request.session[REGISTRATION_EXPORT_SESSION_KEY] = application.pk
    return render(
        request,
        "training/public_exam_registration_success.html",
        {"application": application},
    )


@never_cache
@require_http_methods(["GET"])
def exam_application_registration_export_pdf(request):
    application_id = request.session.get(REGISTRATION_EXPORT_SESSION_KEY)
    if not application_id:
        return redirect("exam-application-public-register")

    application = get_object_or_404(
        ExamApplication.objects.select_related("exam_period__cert_type"),
        pk=application_id,
        source=ExamApplication.Source.ONLINE,
    )
    response = _public_lookup_pdf_response(
        "Phiếu xác nhận đăng ký dự thi",
        application,
        _masked_id_number(application.id_number),
        "registration",
        [{
            "title": application.exam_period.name,
            "details": [
                application.exam_period.cert_type.name,
                "Ngày thi: "
                + application.exam_period.exam_date.strftime("%d/%m/%Y"),
                "Ngày đăng ký: "
                + timezone.localtime(application.registered_at).strftime(
                    "%d/%m/%Y %H:%M"
                ),
                "Tình trạng: Đã nhận hồ sơ đăng ký",
            ],
        }],
    )
    request.session.pop(REGISTRATION_EXPORT_SESSION_KEY, None)
    return response


def _rate_limit_key(request, *parts, include_ip=True):
    raw_key = ":".join(
        ((request.META.get("REMOTE_ADDR", "unknown"),) if include_ip else ())
        + parts
    )
    digest = salted_hmac(
        "training.public_exam_lookup.rate_limit",
        raw_key,
        secret=settings.SECRET_KEY,
        algorithm="sha256",
    ).hexdigest()
    return f"public-exam-lookup:{digest}"


def _increment_rate_limit(request, key_parts, limit, include_ip=True):
    key = _rate_limit_key(request, *key_parts, include_ip=include_ip)
    if cache.add(key, 1, timeout=LOOKUP_RATE_WINDOW):
        return limit < 1
    try:
        count = cache.incr(key)
    except ValueError:
        if cache.add(key, 1, timeout=LOOKUP_RATE_WINDOW):
            return False
        count = cache.incr(key)
    return count > limit


def _identity_is_rate_limited(request, action, id_number, email):
    identity_limited = _increment_rate_limit(
        request,
        (action, id_number, email.casefold()),
        LOOKUP_IDENTITY_LIMIT,
        include_ip=False,
    )
    if identity_limited:
        return True
    if action == "request":
        return _increment_rate_limit(
            request,
            ("recipient", email.casefold()),
            LOOKUP_IDENTITY_LIMIT,
            include_ip=False,
        )
    return False


def _render_lookup_page(
    request,
    purpose,
    step="request",
    request_form=None,
    state=None,
    state_title=None,
    state_message=None,
    results=None,
    can_export_pdf=False,
    candidate_name=None,
    masked_id_number=None,
    no_results=False,
    remaining_seconds=None,
    status=200,
):
    is_room_lookup = purpose == "rooms"
    response = render(
        request,
        "training/public_exam_room_lookup.html"
        if is_room_lookup
        else "training/public_exam_lookup.html",
        {
            "purpose": purpose,
            "step": step,
            "page_title": "Xem phòng thi" if is_room_lookup else "Tra cứu điểm và chứng chỉ",
            "request_form": request_form,
            "state": state,
            "state_title": state_title,
            "state_message": state_message,
            "results": results or [],
            "can_export_pdf": can_export_pdf,
            "candidate_name": candidate_name,
            "masked_id_number": masked_id_number,
            "no_results": no_results,
            "remaining_seconds": remaining_seconds or LOOKUP_RATE_WINDOW,
        },
        status=status,
    )
    response["Cache-Control"] = "no-store, private"
    response["Pragma"] = "no-cache"
    if status == 429:
        response["Retry-After"] = str(LOOKUP_RATE_WINDOW)
    return response


def _fresh_form_with_errors(form_class, submitted_form, initial=None):
    form = form_class(initial=initial or {})
    form.cleaned_data = {}
    for field_name, errors in submitted_form.errors.items():
        target = field_name if field_name in form.fields else None
        for error in errors:
            form.add_error(target, error)
    for field_name in form.errors:
        if field_name in form.fields:
            field = form[field_name]
            form.fields[field_name].widget.attrs.update(
                {
                    "aria-invalid": "true",
                    "aria-describedby": f"{field.id_for_label}-error",
                }
            )
    return form


def _lookup_rate_limit_response(request, purpose):
    return _render_lookup_page(
        request,
        purpose,
        state="error",
        state_title="Đã vượt giới hạn tra cứu",
        state_message="Bạn đã nhập sai thông tin quá nhiều lần. Vui lòng chờ 15 phút rồi thử lại.",
        remaining_seconds=LOOKUP_RATE_WINDOW,
        status=429,
    )


def _lookup_identity(request):
    identity = request.session.get(LOOKUP_RESULT_SESSION_KEY)
    if not identity:
        return None
    if identity.get("expires_at", 0) <= timezone.now().timestamp():
        request.session.pop(LOOKUP_RESULT_SESSION_KEY, None)
        return None
    return identity


def _masked_id_number(id_number):
    return f"{id_number[:3]}******{id_number[-3:]}"


def _published_exam_results(id_number, email, session_key=None):
    results = []
    scores = (
        ExamScore.objects.filter(
            assignment__application__id_number=id_number,
            assignment__application__email__iexact=email,
            is_published=True,
        )
        .filter(Q(score__isnull=False) | Q(is_absent=True))
        .select_related(
            "assignment__application",
            "assignment__exam_room__exam_period__cert_type",
        )
        .order_by(
            "assignment__exam_room__exam_period__exam_date",
            "assignment__exam_room__exam_period__name",
        )
    )
    score_rows = list(scores)
    if not score_rows:
        return results

    application_ids = {score.assignment.application_id for score in score_rows}
    period_ids = {score.assignment.exam_room.exam_period_id for score in score_rows}
    certificates = list(
        ExamCertificate.objects.filter(
            application_id__in=application_ids,
            exam_period_id__in=period_ids,
        )
        .select_related("replaced_by")
        .order_by("-issued_at", "-pk")
    )
    certificates_by_pair = {}
    for certificate in certificates:
        if certificate.status == ExamCertificate.Status.REPLACED:
            continue
        if session_key and certificate.status == ExamCertificate.Status.ISSUED:
            token = CERTIFICATE_DOWNLOAD_SIGNER.sign(f"{certificate.pk}:{session_key}")
            certificate.download_url = f"{reverse('exam-public-certificate-pdf')}?token={token}"
        else:
            certificate.download_url = None
        certificates_by_pair.setdefault(
            (certificate.application_id, certificate.exam_period_id),
            [],
        ).append(certificate)

    for score in score_rows:
        application = score.assignment.application
        period = score.assignment.exam_room.exam_period
        results.append(
            {
                "application": application,
                "period": period,
                "score": score,
                "certificates": certificates_by_pair.get(
                    (application.pk, period.pk),
                    [],
                ),
            }
        )
    return results


def _public_results_allow_pdf(results):
    for result in results:
        certificates = result["certificates"]
        if certificates and not any(
            certificate.status == ExamCertificate.Status.ISSUED
            for certificate in certificates
        ):
            return False
    return bool(results)


def _public_room_assignments(id_number, email):
    return list(
        ExamRoomAssignment.objects.filter(
            application__id_number=id_number,
            application__email__iexact=email,
            application__status=ExamApplication.Status.VALID,
        )
        .select_related(
            "application",
            "exam_room__exam_period__cert_type",
        )
        .order_by(
            "exam_room__exam_period__exam_date",
            "exam_room__name",
            "exam_room__session",
        )
    )


@lru_cache(maxsize=1)
def _register_public_pdf_font():
    windows_fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    font_candidates = (
        (windows_fonts / "arial.ttf", windows_fonts / "arialbd.ttf"),
        (
            Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
            Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
        ),
        (
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ),
        (
            Path("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"),
            Path("/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf"),
        ),
        (
            Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"),
            Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"),
        )
    )
    for regular_path, bold_path in font_candidates:
        if not regular_path.is_file() or not bold_path.is_file():
            continue
        regular_font = TTFont("PublicExamSans", str(regular_path))
        bold_font = TTFont("PublicExamSans-Bold", str(bold_path))
        if any(
            regular_font.face.charToGlyph.get(codepoint, 0) == 0
            or bold_font.face.charToGlyph.get(codepoint, 0) == 0
            for codepoint in _PUBLIC_PDF_REQUIRED_GLYPHS
        ):
            continue
        pdfmetrics.registerFont(regular_font)
        pdfmetrics.registerFont(bold_font)
        pdfmetrics.registerFontFamily(
            "PublicExamSans",
            normal="PublicExamSans",
            bold="PublicExamSans-Bold",
            italic="PublicExamSans",
            boldItalic="PublicExamSans-Bold",
        )
        return "PublicExamSans"

    raise ImproperlyConfigured(
        "PDF export requires a regular and bold font with Vietnamese glyph support."
    )


def _public_lookup_pdf_response(title, candidate, masked_id_number, purpose, rows):
    font_name = _register_public_pdf_font()
    styles = getSampleStyleSheet()
    navy = colors.HexColor("#173B57")
    ink = colors.HexColor("#19344A")
    muted = colors.HexColor("#587186")
    accent = colors.HexColor("#24A99A")
    border = colors.HexColor("#D7E2EA")
    pale = colors.HexColor("#F3F7FA")
    white = colors.white
    content_width = A4[0] - 34 * mm

    brand_mark_style = ParagraphStyle(
        "PublicExamBrandMark",
        fontName="PublicExamSans-Bold",
        fontSize=14,
        leading=17,
        textColor=white,
        alignment=1,
    )
    brand_name_style = ParagraphStyle(
        "PublicExamBrandName",
        fontName="PublicExamSans-Bold",
        fontSize=9,
        leading=12,
        textColor=white,
    )
    brand_school_style = ParagraphStyle(
        "PublicExamBrandSchool",
        fontName=font_name,
        fontSize=7.5,
        leading=10,
        textColor=colors.HexColor("#DCE8EF"),
    )
    kicker_style = ParagraphStyle(
        "PublicExamKicker",
        fontName="PublicExamSans-Bold",
        fontSize=8,
        leading=10,
        textColor=accent,
        spaceAfter=2 * mm,
    )
    title_style = ParagraphStyle(
        "PublicExamTitle",
        parent=styles["Title"],
        fontName="PublicExamSans-Bold",
        fontSize=21,
        leading=26,
        textColor=navy,
        alignment=0,
        spaceAfter=2 * mm,
    )
    subtitle_style = ParagraphStyle(
        "PublicExamSubtitle",
        parent=styles["BodyText"],
        fontName=font_name,
        fontSize=9,
        leading=13,
        textColor=muted,
        spaceAfter=5 * mm,
    )
    info_label_style = ParagraphStyle(
        "PublicExamInfoLabel",
        fontName="PublicExamSans-Bold",
        fontSize=7,
        leading=9,
        textColor=muted,
    )
    info_value_style = ParagraphStyle(
        "PublicExamInfoValue",
        fontName="PublicExamSans-Bold",
        fontSize=11,
        leading=15,
        textColor=ink,
    )
    exam_label_style = ParagraphStyle(
        "PublicExamCardLabel",
        fontName="PublicExamSans-Bold",
        fontSize=7,
        leading=9,
        textColor=colors.HexColor("#B7DCD8"),
    )
    exam_title_style = ParagraphStyle(
        "PublicExamCardTitle",
        fontName="PublicExamSans-Bold",
        fontSize=11,
        leading=15,
        textColor=white,
    )
    detail_label_style = ParagraphStyle(
        "PublicExamDetailLabel",
        fontName="PublicExamSans-Bold",
        fontSize=7,
        leading=9,
        textColor=muted,
    )
    detail_value_style = ParagraphStyle(
        "PublicExamDetailValue",
        fontName="PublicExamSans-Bold",
        fontSize=9.5,
        leading=13,
        textColor=ink,
    )

    brand_copy = Table(
        [
            [Paragraph("TRUNG TÂM KỸ THUẬT ĐIỆN TOÁN", brand_name_style)],
            [Paragraph("TRƯỜNG ĐẠI HỌC BÁCH KHOA - ĐHQG-HCM", brand_school_style)],
        ],
        colWidths=[content_width - 30 * mm],
    )
    brand = Table(
        [[Paragraph("CCE", brand_mark_style), brand_copy]],
        colWidths=[25 * mm, content_width - 25 * mm],
    )
    brand.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), navy),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (0, 0), 5 * mm),
                ("RIGHTPADDING", (0, 0), (0, 0), 2 * mm),
                ("LEFTPADDING", (1, 0), (1, 0), 1 * mm),
                ("RIGHTPADDING", (1, 0), (1, 0), 4 * mm),
                ("TOPPADDING", (0, 0), (-1, -1), 4 * mm),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4 * mm),
            ]
        )
    )
    brand_accent = Table([[""]], colWidths=[content_width], rowHeights=[1.5 * mm])
    brand_accent.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), accent)]))

    subtitles = {
        "rooms": "Thời gian, địa điểm và ca thi đã được xếp cho thí sinh.",
        "registration": "Thông tin xác nhận hồ sơ đăng ký dự thi trực tuyến.",
        "results": "Thông tin kết quả thi đã được công bố.",
    }
    story = [
        brand,
        brand_accent,
        Spacer(1, 8 * mm),
        Paragraph(escape(title), title_style),
        Paragraph(escape(subtitles.get(purpose, "")), subtitle_style),
    ]

    candidate_info = Table(
        [
            [
                Paragraph("THÍ SINH", info_label_style),
                Paragraph("CCCD", info_label_style),
            ],
            [
                Paragraph(escape(str(candidate.full_name)), info_value_style),
                Paragraph(escape(str(masked_id_number)), info_value_style),
            ],
        ],
        colWidths=[content_width * 0.68, content_width * 0.32],
    )
    candidate_info.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), pale),
                ("BOX", (0, 0), (-1, -1), 0.7, border),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, border),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5 * mm),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5 * mm),
                ("TOPPADDING", (0, 0), (-1, 0), 3 * mm),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 1 * mm),
                ("TOPPADDING", (0, 1), (-1, 1), 0),
                ("BOTTOMPADDING", (0, 1), (-1, 1), 3 * mm),
            ]
        )
    )
    story.extend(
        [
            Paragraph("THÔNG TIN THÍ SINH", kicker_style),
            candidate_info,
            Spacer(1, 7 * mm),
        ]
    )

    def detail_card(detail):
        if ":" in detail:
            label, value = detail.split(":", 1)
        else:
            label, value = "Loại chứng chỉ", detail
        card = Table(
            [
                [Paragraph(escape(label.strip().upper()), detail_label_style)],
                [Paragraph(escape(value.strip()), detail_value_style)],
            ],
            colWidths=[(content_width - 16 * mm) / 2],
        )
        card.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), pale),
                    ("BOX", (0, 0), (-1, -1), 0.5, border),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 3 * mm),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 3 * mm),
                    ("TOPPADDING", (0, 0), (0, 0), 2.5 * mm),
                    ("BOTTOMPADDING", (0, 0), (0, 0), 0.5 * mm),
                    ("TOPPADDING", (0, 1), (0, 1), 0),
                    ("BOTTOMPADDING", (0, 1), (0, 1), 2.5 * mm),
                ]
            )
        )
        return card

    for row in rows:
        exam_header = Table(
            [[
                Paragraph("ĐỢT THI", exam_label_style),
                Paragraph(escape(str(row["title"])), exam_title_style),
            ]],
            colWidths=[22 * mm, content_width - 22 * mm],
        )
        exam_header.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), navy),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (0, 0), 4 * mm),
                    ("RIGHTPADDING", (0, 0), (0, 0), 1 * mm),
                    ("LEFTPADDING", (1, 0), (1, 0), 1 * mm),
                    ("RIGHTPADDING", (1, 0), (1, 0), 4 * mm),
                    ("TOPPADDING", (0, 0), (-1, -1), 3 * mm),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3 * mm),
                ]
            )
        )

        details = [detail_card(detail) for detail in row["details"]]
        if len(details) % 2:
            details.append("")
        detail_rows = [
            details[index:index + 2]
            for index in range(0, len(details), 2)
        ]
        detail_grid = Table(
            detail_rows,
            colWidths=[(content_width - 16 * mm) / 2] * 2,
        )
        detail_grid.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (0, -1), 0),
                    ("RIGHTPADDING", (0, 0), (0, -1), 2 * mm),
                    ("LEFTPADDING", (1, 0), (1, -1), 2 * mm),
                    ("RIGHTPADDING", (1, 0), (1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 1.5 * mm),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5 * mm),
                ]
            )
        )
        exam_card = Table(
            [[exam_header], [detail_grid]],
            colWidths=[content_width],
        )
        exam_card.setStyle(
            TableStyle(
                [
                    ("BOX", (0, 0), (-1, -1), 0.7, border),
                    ("LEFTPADDING", (0, 0), (-1, 0), 0),
                    ("RIGHTPADDING", (0, 0), (-1, 0), 0),
                    ("TOPPADDING", (0, 0), (-1, 0), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, 0), 0),
                    ("LEFTPADDING", (0, 1), (-1, 1), 6 * mm),
                    ("RIGHTPADDING", (0, 1), (-1, 1), 6 * mm),
                    ("TOPPADDING", (0, 1), (-1, 1), 3 * mm),
                    ("BOTTOMPADDING", (0, 1), (-1, 1), 3 * mm),
                ]
            )
        )
        story.append(KeepTogether([exam_card, Spacer(1, 5 * mm)]))

    output = BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=A4,
        rightMargin=17 * mm,
        leftMargin=17 * mm,
        topMargin=15 * mm,
        bottomMargin=19 * mm,
        title=title,
        author="Trung tâm Kỹ thuật Điện toán",
    )

    def draw_page(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(navy)
        canvas.rect(0, A4[1] - 2 * mm, A4[0], 2 * mm, stroke=0, fill=1)
        canvas.setStrokeColor(border)
        canvas.setLineWidth(0.6)
        canvas.line(17 * mm, 14 * mm, A4[0] - 17 * mm, 14 * mm)
        canvas.setFillColor(muted)
        canvas.setFont(font_name, 7)
        canvas.drawString(
            17 * mm,
            9 * mm,
            "TRUNG TÂM KỸ THUẬT ĐIỆN TOÁN | ĐHQG-HCM",
        )
        canvas.drawRightString(A4[0] - 17 * mm, 9 * mm, f"Trang {doc.page}")
        canvas.restoreState()

    document.build(story, onFirstPage=draw_page, onLaterPages=draw_page)

    response = HttpResponse(output.getvalue(), content_type="application/pdf")
    filename = {
        "rooms": "lich-phong-thi.pdf",
        "registration": "phieu-xac-nhan-dang-ky-du-thi.pdf",
    }.get(purpose, "ket-qua-diem-chung-chi.pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["Cache-Control"] = "no-store, private"
    response["X-Content-Type-Options"] = "nosniff"
    return response


def _public_lookup_pdf(request, purpose):
    identity = _lookup_identity(request)
    lookup_route = (
        "exam-room-public-lookup" if purpose == "rooms" else "exam-public-lookup"
    )
    if not identity or identity.get("purpose") != purpose:
        return redirect(lookup_route)

    id_number = identity["id_number"]
    email = identity["email"]
    if purpose == "rooms":
        assignments = _public_room_assignments(id_number, email)
        if not assignments:
            return redirect(lookup_route)
        candidate = assignments[0].application
        rows = [
            {
                "title": assignment.exam_room.exam_period.name,
                "details": [
                    assignment.exam_room.exam_period.cert_type.name,
                    "Ngày thi: "
                    + assignment.exam_room.exam_period.exam_date.strftime("%d/%m/%Y"),
                    "Phòng thi: " + assignment.exam_room.name,
                    "Ca thi: " + assignment.exam_room.session,
                ],
            }
            for assignment in assignments
        ]
        title = "Lịch thi và phòng thi"
    else:
        results = _published_exam_results(id_number, email)
        if not results or not _public_results_allow_pdf(results):
            return redirect(lookup_route)
        candidate = results[0]["application"]
        rows = []
        for item in results:
            score = item["score"]
            score_text = (
                "Trạng thái: Vắng thi"
                if score.is_absent
                else (
                    f"Điểm đã công bố: {score.score:.2f} / "
                    f"{item['period'].score_scale:.2f}"
                )
            )
            certificates = item["certificates"]
            certificate_lines = [
                f"Chứng chỉ: {certificate.certificate_number} — "
                f"{certificate.get_status_display()}"
                for certificate in certificates
            ] or ["Chứng chỉ: Chưa được cấp"]
            rows.append(
                {
                    "title": item["period"].name,
                    "details": [
                        item["period"].cert_type.name,
                        "Ngày thi: " + item["period"].exam_date.strftime("%d/%m/%Y"),
                        score_text,
                        *certificate_lines,
                    ],
                }
            )
        title = "Kết quả thi và chứng chỉ"

    return _public_lookup_pdf_response(
        title,
        candidate,
        _masked_id_number(id_number),
        purpose,
        rows,
    )


def _public_lookup_page(request, purpose):
    if request.method == "POST" and _increment_rate_limit(
        request,
        ("ip",),
        LOOKUP_IP_LIMIT,
    ):
        return _lookup_rate_limit_response(request, purpose)

    if request.method == "POST":
        submitted_form = PublicExamLookupRequestForm(request.POST)
        if not submitted_form.is_valid():
            return _render_lookup_page(
                request,
                purpose,
                request_form=_fresh_form_with_errors(
                    PublicExamLookupRequestForm,
                    submitted_form,
                    initial={
                        "id_number": request.POST.get("id_number", ""),
                        "email": request.POST.get("email", ""),
                    },
                ),
                state="error",
                state_title="Thông tin chưa hợp lệ",
                state_message="Vui lòng kiểm tra CCCD, email và mã xác thực trong hình.",
                status=400,
            )

        id_number = submitted_form.cleaned_data["id_number"]
        email = submitted_form.cleaned_data["email"]
        if _identity_is_rate_limited(request, "request", id_number, email):
            return _lookup_rate_limit_response(request, purpose)

        if purpose == "rooms":
            valid_app = ExamApplication.objects.filter(
                id_number=id_number,
                email__iexact=email,
                status=ExamApplication.Status.VALID,
            ).first()
            if not valid_app:
                _increment_rate_limit(
                    request,
                    ("request", id_number, email.casefold()),
                    LOOKUP_IDENTITY_LIMIT,
                    include_ip=False,
                )
                fresh_form = _fresh_form_with_errors(
                    PublicExamLookupRequestForm,
                    submitted_form,
                    initial={
                        "id_number": id_number,
                        "email": email,
                    },
                )
                fresh_form.add_error(
                    None,
                    "Không tìm thấy thông tin phù hợp. Vui lòng kiểm tra lại CCCD và email đã đăng ký.",
                )
                return _render_lookup_page(
                    request,
                    purpose,
                    request_form=fresh_form,
                    state="error",
                    state_title="Không tìm thấy thông tin phù hợp",
                    state_message="Không tìm thấy thông tin phù hợp. Vui lòng kiểm tra lại CCCD và email đã đăng ký.",
                    status=400,
                )

        request.session[LOOKUP_RESULT_SESSION_KEY] = {
            "id_number": id_number,
            "email": email,
            "purpose": purpose,
            "candidate_name": valid_app.full_name if purpose == "rooms" and valid_app else "",
            "expires_at": (timezone.now() + LOOKUP_SESSION_TTL).timestamp(),
        }
        return redirect(
            "exam-room-public-lookup" if purpose == "rooms" else "exam-public-lookup"
        )

    if request.GET.get("new") == "1":
        request.session.pop(LOOKUP_RESULT_SESSION_KEY, None)

    identity = _lookup_identity(request)
    if not identity or identity.get("purpose") != purpose:
        if identity:
            request.session.pop(LOOKUP_RESULT_SESSION_KEY, None)
        return _render_lookup_page(
            request,
            purpose,
            request_form=PublicExamLookupRequestForm(),
        )

    id_number = identity["id_number"]
    email = identity["email"]
    masked_id = _masked_id_number(id_number)
    if purpose == "rooms":
        assignments = _public_room_assignments(id_number, email)
        candidate_name = identity.get("candidate_name") or (assignments[0].application.full_name if assignments else "")
        if not assignments:
            return _render_lookup_page(
                request,
                purpose,
                step="no_rooms",
                candidate_name=candidate_name,
                masked_id_number=masked_id,
                no_results=True,
            )
        return _render_lookup_page(
            request,
            purpose,
            step="results",
            results=assignments,
            candidate_name=candidate_name,
            masked_id_number=masked_id,
            no_results=False,
        )

    if not request.session.session_key:
        request.session.save()
    results = _published_exam_results(
        id_number,
        email,
        session_key=request.session.session_key,
    )
    return _render_lookup_page(
        request,
        purpose,
        step="results",
        results=results,
        can_export_pdf=_public_results_allow_pdf(results),
        candidate_name=results[0]["application"].full_name if results else "",
        masked_id_number=masked_id,
        no_results=not results,
    )


@never_cache
@require_http_methods(["GET", "POST"])
def exam_public_lookup(request):
    return _public_lookup_page(request, "results")


@never_cache
@require_http_methods(["GET", "POST"])
def exam_room_public_lookup(request):
    return _public_lookup_page(request, "rooms")


@never_cache
@require_http_methods(["GET"])
def exam_public_export_pdf(request):
    return _public_lookup_pdf(request, "results")


@never_cache
@require_http_methods(["GET"])
def exam_room_public_export_pdf(request):
    return _public_lookup_pdf(request, "rooms")


@never_cache
@require_http_methods(["GET"])
def exam_public_certificate_pdf(request):
    if _increment_rate_limit(request, ("ip",), LOOKUP_IP_LIMIT):
        return HttpResponse("Đã vượt quá giới hạn tải.", status=429)

    token = request.GET.get("token")
    if not token:
        raise Http404("Không tìm thấy chứng chỉ.")

    try:
        payload = CERTIFICATE_DOWNLOAD_SIGNER.unsign(token, max_age=300)
    except (BadSignature, SignatureExpired):
        raise Http404("Đường dẫn tải chứng chỉ không hợp lệ hoặc đã hết hạn.")

    try:
        cert_id_str, signed_session_key = payload.split(":", 1)
        cert_id = int(cert_id_str)
    except (ValueError, AttributeError):
        raise Http404("Chứng chỉ không hợp lệ.")

    if not request.session.session_key or request.session.session_key != signed_session_key:
        raise Http404("Phiên tra cứu không hợp lệ.")

    identity = _lookup_identity(request)
    if not identity or identity.get("purpose") != "results":
        raise Http404("Phiên tra cứu đã hết hạn.")

    certificate = (
        ExamCertificate.objects.select_related(
            "exam_period__cert_type",
            "application",
        )
        .filter(
            pk=cert_id,
            application__id_number=identity["id_number"],
            application__email__iexact=identity["email"],
            status=ExamCertificate.Status.ISSUED,
        )
        .first()
    )
    if not certificate:
        raise Http404("Chứng chỉ không tồn tại hoặc không còn hiệu lực.")

    AuditLog.objects.create(
        action=AuditLog.Action.EXPORT,
        actor=None,
        actor_username="Thí sinh công khai",
        object_type="ExamCertificate",
        object_id=str(certificate.pk),
        object_label=f"Chứng chỉ {certificate.certificate_number}",
        reason=f"Thí sinh tải PDF chứng chỉ {certificate.certificate_number}",
    )

    return _exam_certificate_pdf_response(certificate)
