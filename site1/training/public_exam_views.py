import logging
import secrets
from datetime import timedelta
from smtplib import SMTPException

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Count, F, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from training.models import (
    ExamApplication,
    ExamCertificate,
    ExamPeriod,
    ExamRoomAssignment,
    ExamScore,
)
from training.public_exam_forms import (
    PublicExamLookupOTPForm,
    PublicExamLookupRequestForm,
    PublicExamRegistrationForm,
)


REGISTRATION_SUCCESS_SESSION_KEY = "public_exam_registration_id"
LOOKUP_PENDING_SESSION_KEY = "public_exam_lookup_pending"
LOOKUP_VERIFIED_SESSION_KEY = "public_exam_lookup_verified"
LOOKUP_CODE_TTL = timedelta(minutes=10)
LOOKUP_SESSION_TTL = timedelta(minutes=10)
LOOKUP_RATE_WINDOW = 15 * 60
LOOKUP_IP_LIMIT = 12
LOOKUP_IDENTITY_LIMIT = 5
LOOKUP_OTP_ATTEMPT_LIMIT = 5

logger = logging.getLogger(__name__)


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
    otp_form=None,
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
            "otp_form": otp_form,
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


def _otp_digest(pending, code):
    value = ":".join(
        (pending["nonce"], pending["id_number"], pending["email"], code)
    )
    return salted_hmac(
        "training.public_exam_lookup.otp",
        value,
        secret=settings.SECRET_KEY,
        algorithm="sha256",
    ).hexdigest()


def _send_lookup_code(email, code):
    return send_mail(
        subject="Mã xác thực tra cứu thông tin dự thi",
        message=(
            f"Mã xác thực của bạn là {code}. "
            "Mã có hiệu lực trong 10 phút. Nếu bạn không yêu cầu mã này, "
            "hãy bỏ qua email."
        ),
        from_email=None,
        recipient_list=[email],
        fail_silently=False,
    )


def _verified_identity(request):
    verified = request.session.get(LOOKUP_VERIFIED_SESSION_KEY)
    if not verified:
        return None
    if verified.get("expires_at", 0) <= timezone.now().timestamp():
        request.session.pop(LOOKUP_VERIFIED_SESSION_KEY, None)
        return None
    return verified


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


def _public_lookup_page(request, purpose):
    if request.method == "POST" and _increment_rate_limit(
        request,
        ("ip",),
        LOOKUP_IP_LIMIT,
    ):
        return _lookup_rate_limit_response(request, purpose)

    action = request.POST.get("action", "") if request.method == "POST" else ""
    pending = request.session.get(LOOKUP_PENDING_SESSION_KEY)

    if request.method == "POST" and action == "verify_code":
        if (
            not pending
            or pending.get("expires_at", 0) <= timezone.now().timestamp()
        ):
            request.session.pop(LOOKUP_PENDING_SESSION_KEY, None)
            return _render_lookup_page(
                request,
                purpose,
                state="warning",
                state_title="Mã xác thực đã hết hạn",
                state_message="Vui lòng nhập lại số CCCD và email để yêu cầu mã mới.",
            )

        if _identity_is_rate_limited(
            request,
            "otp",
            pending["id_number"],
            pending["email"],
        ):
            return _lookup_rate_limit_response(request, purpose)

        submitted_form = PublicExamLookupOTPForm(request.POST)
        if not submitted_form.is_valid():
            return _render_lookup_page(
                request,
                purpose,
                step="verify",
                otp_form=_fresh_form_with_errors(PublicExamLookupOTPForm, submitted_form),
                state="error",
                state_title="Thông tin chưa hợp lệ",
                state_message="Kiểm tra mã email và mã xác thực rồi thử lại.",
                status=400,
            )

        pending["attempts"] += 1
        request.session[LOOKUP_PENDING_SESSION_KEY] = pending
        submitted_code = submitted_form.cleaned_data["otp"]
        valid_code = constant_time_compare(
            pending["otp_digest"],
            _otp_digest(pending, submitted_code),
        )
        if not valid_code:
            if pending["attempts"] >= LOOKUP_OTP_ATTEMPT_LIMIT:
                request.session.pop(LOOKUP_PENDING_SESSION_KEY, None)
                return _lookup_rate_limit_response(request, purpose)
            return _render_lookup_page(
                request,
                purpose,
                step="verify",
                otp_form=PublicExamLookupOTPForm(),
                state="error",
                state_title="Mã xác thực không hợp lệ",
                state_message="Mã không chính xác hoặc đã hết hạn. Vui lòng thử lại.",
                status=400,
            )

        request.session.pop(LOOKUP_PENDING_SESSION_KEY, None)
        request.session[LOOKUP_VERIFIED_SESSION_KEY] = {
            "id_number": pending["id_number"],
            "email": pending["email"],
            "expires_at": (timezone.now() + LOOKUP_SESSION_TTL).timestamp(),
        }
        return redirect(
            "exam-room-public-lookup" if purpose == "rooms" else "exam-public-lookup"
        )

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
                state_message="Vui lòng kiểm tra số CCCD, email và mã xác thực.",
                status=400,
            )

        id_number = submitted_form.cleaned_data["id_number"]
        email = submitted_form.cleaned_data["email"]
        if _identity_is_rate_limited(request, "request", id_number, email):
            return _lookup_rate_limit_response(request, purpose)

        code = f"{secrets.randbelow(1_000_000):06d}"
        pending = {
            "id_number": id_number,
            "email": email,
            "nonce": secrets.token_urlsafe(24),
            "otp_digest": "",
            "expires_at": (timezone.now() + LOOKUP_CODE_TTL).timestamp(),
            "attempts": 0,
        }
        pending["otp_digest"] = _otp_digest(pending, code)
        request.session[LOOKUP_PENDING_SESSION_KEY] = pending

        try:
            sent_count = _send_lookup_code(email, code)
        except (OSError, SMTPException):
            logger.exception("Could not send an exam lookup OTP email")
            request.session.pop(LOOKUP_PENDING_SESSION_KEY, None)
            return _render_lookup_page(
                request,
                purpose,
                state="error",
                state_title="Chưa thể gửi mã xác thực",
                state_message="Dịch vụ email đang tạm thời không khả dụng. Vui lòng thử lại sau.",
                status=503,
            )
        if sent_count != 1:
            request.session.pop(LOOKUP_PENDING_SESSION_KEY, None)
            logger.error("Exam lookup OTP email backend did not send a message")
            return _render_lookup_page(
                request,
                purpose,
                state="error",
                state_title="Chưa thể gửi mã xác thực",
                state_message="Dịch vụ email đang tạm thời không khả dụng. Vui lòng thử lại sau.",
                status=503,
            )

        return _render_lookup_page(
            request,
            purpose,
            step="verify",
            otp_form=PublicExamLookupOTPForm(),
            state="warning",
            state_title="Kiểm tra email của bạn",
            state_message=(
                "Mã xác thực đã được gửi tới email bạn vừa nhập. "
                "Mã có hiệu lực trong 10 phút; chỉ thông tin khớp hồ sơ mới mở được dữ liệu."
            ),
        )

    if request.GET.get("new") == "1":
        request.session.pop(LOOKUP_PENDING_SESSION_KEY, None)
        request.session.pop(LOOKUP_VERIFIED_SESSION_KEY, None)
        pending = None

    verified = _verified_identity(request)
    if not verified:
        if pending and pending.get("expires_at", 0) > timezone.now().timestamp():
            return _render_lookup_page(
                request,
                purpose,
                step="verify",
                otp_form=PublicExamLookupOTPForm(),
                state="warning",
                state_title="Kiểm tra email của bạn",
                state_message=(
                    "Mã xác thực đã được gửi tới email bạn vừa nhập. "
                    "Mã có hiệu lực trong 10 phút; chỉ thông tin khớp hồ sơ mới mở được dữ liệu."
                ),
            )
        if pending:
            request.session.pop(LOOKUP_PENDING_SESSION_KEY, None)
            return _render_lookup_page(
                request,
                purpose,
                state="warning",
                state_title="Mã xác thực đã hết hạn",
                state_message="Vui lòng nhập lại số CCCD và email để yêu cầu mã mới.",
            )
        return _render_lookup_page(
            request,
            purpose,
            request_form=PublicExamLookupRequestForm(),
        )

    id_number = verified["id_number"]
    email = verified["email"]
    masked_id = _masked_id_number(id_number)
    if purpose == "rooms":
        assignments = list(
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
