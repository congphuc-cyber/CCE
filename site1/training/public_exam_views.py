from datetime import timedelta
from functools import lru_cache
from io import BytesIO
import os
from pathlib import Path
from xml.sax.saxutils import escape

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import transaction
from django.db.models import Count, F, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
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
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from training.models import (
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


REGISTRATION_SUCCESS_SESSION_KEY = "public_exam_registration_id"
LOOKUP_RESULT_SESSION_KEY = "public_exam_lookup_result"
LOOKUP_SESSION_TTL = timedelta(minutes=10)
LOOKUP_RATE_WINDOW = 15 * 60
LOOKUP_IP_LIMIT = 12
LOOKUP_IDENTITY_LIMIT = 5
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
        .annotate(candidate_count=Count("applications"))
        .filter(candidate_count__lt=F("max_candidates"))
        .select_related("cert_type")
        .order_by("exam_date", "name")
    )


def unavailable_period_state(period):
    if period.status != ExamPeriod.Status.PUBLISHED:
        return "closed"
    if period.registration_deadline < timezone.localdate():
        return "expired"
    if period.applications.count() >= period.max_candidates:
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
                if state:
                    return render_registration_closed(request, initial_period, state)
    else:
        raw_period_id = request.POST.get("exam_period", "")
        if raw_period_id.isdecimal():
            selected_period = ExamPeriod.objects.filter(pk=int(raw_period_id)).select_related(
                "cert_type"
            ).first()
            if selected_period:
                state = unavailable_period_state(selected_period)
                if state:
                    return render_registration_closed(request, selected_period, state)

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
    return render(
        request,
        "training/public_exam_registration_success.html",
        {"application": application},
    )


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
    candidate_name=None,
    masked_id_number=None,
    no_results=False,
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
            "candidate_name": candidate_name,
            "masked_id_number": masked_id_number,
            "no_results": no_results,
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
        state_message="Bạn đã thử quá nhiều lần. Vui lòng chờ 15 phút rồi thử lại.",
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


def _published_exam_results(id_number, email):
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
    certificates = (
        ExamCertificate.objects.filter(
            application_id__in=application_ids,
            exam_period_id__in=period_ids,
        )
        .order_by("-issued_at", "-pk")
    )
    certificates_by_pair = {}
    for certificate in certificates:
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
    title_style = ParagraphStyle(
        "PublicExamTitle",
        parent=styles["Title"],
        fontName="PublicExamSans-Bold",
        fontSize=17,
        leading=22,
        textColor=colors.HexColor("#173b57"),
        alignment=0,
        spaceAfter=8 * mm,
    )
    section_style = ParagraphStyle(
        "PublicExamSection",
        parent=styles["Heading2"],
        fontName="PublicExamSans-Bold",
        fontSize=12,
        leading=17,
        textColor=colors.HexColor("#173b57"),
        spaceBefore=4 * mm,
        spaceAfter=2 * mm,
    )
    body_style = ParagraphStyle(
        "PublicExamBody",
        parent=styles["BodyText"],
        fontName=font_name,
        fontSize=10,
        leading=15,
        textColor=colors.HexColor("#19344a"),
        spaceAfter=2 * mm,
    )

    story = [
        Paragraph(escape(title), title_style),
        Paragraph(f"Thí sinh: {escape(candidate.full_name)}", body_style),
        Paragraph(f"CCCD: {escape(masked_id_number)}", body_style),
        Spacer(1, 3 * mm),
    ]
    for row in rows:
        story.append(Paragraph(escape(row["title"]), section_style))
        for detail in row["details"]:
            story.append(Paragraph(escape(detail), body_style))
        story.append(Spacer(1, 2 * mm))

    output = BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=A4,
        rightMargin=20 * mm,
        leftMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=title,
        author="Trung tâm Kỹ thuật Điện toán",
    )
    document.build(story)

    response = HttpResponse(output.getvalue(), content_type="application/pdf")
    filename = (
        "lich-phong-thi.pdf"
        if purpose == "rooms"
        else "ket-qua-diem-chung-chi.pdf"
    )
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
        if not results:
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

        request.session[LOOKUP_RESULT_SESSION_KEY] = {
            "id_number": id_number,
            "email": email,
            "purpose": purpose,
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
        return _render_lookup_page(
            request,
            purpose,
            step="results",
            results=assignments,
            candidate_name=assignments[0].application.full_name if assignments else "",
            masked_id_number=masked_id,
            no_results=not assignments,
        )

    results = _published_exam_results(id_number, email)
    return _render_lookup_page(
        request,
        purpose,
        step="results",
        results=results,
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
