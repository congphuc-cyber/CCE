"""
Stub views — chỉ còn certificate (phạm vi khác phụ trách).
Pha 2, 3, 4 đã được implement đầy đủ trong exam_views.py.
"""
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import render

from training.access import user_has_feature
from training.pagination import PAGE_SIZE

# Pha 2, 3, 4 — đã implement, re-export
from training.exam_views import (  # noqa: F401
    exam_room_list, exam_room_assign,
    exam_score_list, exam_score_entry, exam_score_upload, exam_score_publish,
    mybk_export, exam_statistics, report_list, report_export_excel,
)


def _stub_view(request, feature_key, page_title, active_menu, extra_ctx=None):
    if not request.user.is_authenticated:
        from django.conf import settings
        from django.shortcuts import redirect
        return redirect(settings.LOGIN_URL)
    if not user_has_feature(request.user, feature_key):
        raise PermissionDenied
    ctx = {
        "active_menu": active_menu,
        "page_title": page_title,
        "breadcrumbs": [{"label": page_title}],
        "is_stub": True,
    }
    if extra_ctx:
        ctx.update(extra_ctx)
    return render(request, "training/stub_coming_soon.html", ctx)


@login_required
def certificate_list(request):
    if not user_has_feature(request.user, "certificates"):
        raise PermissionDenied

    from decimal import Decimal
    from django.core.paginator import Paginator
    from training.models import ExamScore

    # Lấy các thí sinh đạt chuẩn khảo thí (score >= 5.0)
    passed_scores_qs = (
        ExamScore.objects.filter(score__gte=Decimal("5.0"), is_absent=False)
        .select_related(
            "assignment__application",
            "assignment__exam_room__exam_period",
            "assignment__exam_room__exam_period__cert_type",
        )
        .order_by("-id")
    )

    keyword = request.GET.get("keyword", "").strip()
    if keyword:
        from django.db.models import Q
        passed_scores_qs = passed_scores_qs.filter(
            Q(assignment__application__full_name__icontains=keyword)
            | Q(assignment__application__id_number__icontains=keyword)
            | Q(assignment__exam_room__exam_period__name__icontains=keyword)
        )

    total_eligible = ExamScore.objects.filter(score__gte=Decimal("5.0"), is_absent=False).count()
    total_issued = total_eligible
    pending_paper = 0
    all_scored = ExamScore.objects.filter(is_absent=False, score__isnull=False).count()
    graduation_rate = f"{round((total_eligible / all_scored * 100), 1)}%" if all_scored > 0 else "100%"

    paginator = Paginator(passed_scores_qs, PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get("page"))

    items = []
    for s in page_obj:
        app = s.assignment.application
        period = s.assignment.exam_room.exam_period
        cert_code = f"CCE-2026-CC{s.id:04d}"
        items.append({
            "full_name": app.full_name,
            "id_number": app.id_number,
            "phone": app.phone or "—",
            "period_name": period.name,
            "exam_date": period.exam_date,
            "score": s.score,
            "cert_code": cert_code,
        })
    page_obj.object_list = items

    filter_fields = [
        {
            "name": "keyword",
            "label": "Tìm kiếm thí sinh / CCCD / Đợt thi",
            "type": "text",
            "placeholder": "Nhập họ tên, số CCCD hoặc tên đợt thi...",
            "value": keyword,
        }
    ]

    return render(
        request,
        "training/certificate_list.html",
        {
            "active_menu": "certificates",
            "page_title": "Chứng nhận & Chứng chỉ",
            "breadcrumbs": [{"label": "Chứng nhận & Chứng chỉ"}],
            "total_eligible": total_eligible,
            "total_issued": total_issued,
            "pending_paper": pending_paper,
            "graduation_rate": graduation_rate,
            "page_obj": page_obj,
            "filter_fields": filter_fields,
        },
    )


@login_required
def exam_certificate_list(request):
    return _stub_view(request, "exam-certificates", "Chứng chỉ khảo thí", "exam-certificates")
