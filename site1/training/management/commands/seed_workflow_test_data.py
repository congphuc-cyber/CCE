from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from training.audit import record_audit
from training.models import (
    AuditLog,
    Certificate,
    CertificateHistory,
    Course,
    Enrollment,
    EnrollmentTransferHistory,
    ExamApplication,
    ExamCertificate,
    ExamCertificateHistory,
    ExamPeriod,
    ExamRoom,
    ExamRoomAssignment,
    ExamScore,
    FinalCourseResult,
    Learner,
    TrainingClass,
)


TEST_REASON = "Dữ liệu kiểm thử nội bộ, được tạo cục bộ."
COURSE_STUDENTS = (
    ("990000000001", "Nguyễn Hoàng Minh", "hoangminh.nguyen@example.test", Decimal("92")),
    ("990000000002", "Trần Thảo Vy", "thaovy.tran@example.test", Decimal("82")),
    ("990000000003", "Lê Quốc Bảo", "quocbao.le@example.test", Decimal("75")),
    ("990000000004", "Phạm Ngọc Hà", "ngocha.pham@example.test", Decimal("55")),
    ("990000000005", "Võ Gia Huy", "giahuy.vo@example.test", Decimal("88")),
    ("990000000006", "Đặng Phương Linh", "phuonglinh.dang@example.test", None),
)
EXAM_CANDIDATES = (
    ("991000000001", "Nguyễn Minh Tuấn", "minhtuan.nguyen@example.test", Decimal("95"), False),
    ("991000000002", "Trần Thu Hà", "thuha.tran@example.test", Decimal("88"), False),
    ("991000000003", "Lê Đức Anh", "ducanh.le@example.test", Decimal("76"), False),
    ("991000000004", "Phạm Khánh Linh", "khanhlinh.pham@example.test", Decimal("60"), False),
    ("991000000005", "Võ Thanh Tùng", "thanhtung.vo@example.test", Decimal("4"), False),
    ("991000000006", "Đặng Bảo Châu", "baochau.dang@example.test", None, True),
)


class Command(BaseCommand):
    help = "Tạo dữ liệu kiểm thử nội bộ cho các luồng kết quả, chứng nhận và tra cứu."

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("Lệnh này chỉ được chạy khi DEBUG=True.")

        with transaction.atomic():
            course, _ = Course.objects.get_or_create(
                name="Lập trình Python ứng dụng",
                defaults={"pass_threshold": Decimal("65")},
            )
            result_class, _ = TrainingClass.objects.get_or_create(
                name="CCE-LAPTR-901",
                defaults={
                    "course": course,
                    "learning_format": TrainingClass.LearningFormat.AT_CENTER,
                    "schedule": "Thứ 2, 4, 6 — 18:00-20:00",
                    "start_date": timezone.localdate() - timedelta(days=60),
                    "max_students": 10,
                    "status": TrainingClass.Status.OPEN,
                },
            )
            transfer_class, _ = TrainingClass.objects.get_or_create(
                name="CCE-LAPTR-902",
                defaults={
                    "course": course,
                    "learning_format": TrainingClass.LearningFormat.ONLINE,
                    "schedule": "Thứ 3, 5 — 18:00-20:00",
                    "start_date": timezone.localdate() - timedelta(days=30),
                    "max_students": 10,
                    "status": TrainingClass.Status.OPEN,
                },
            )

            learners = self._ensure_course_learners()
            for learner in learners[:5]:
                Enrollment.objects.get_or_create(
                    training_class=result_class,
                    learner=learner,
                )
            self._ensure_transfer(learners[5], result_class, transfer_class)
            self._ensure_course_results(result_class, learners)
            self._ensure_course_certificates(result_class, learners)
            self._close_result_class(result_class)

            exam_period, _ = ExamPeriod.objects.get_or_create(
                name="Đợt thi CCE tháng 10/2026 — Lập trình Python ứng dụng (đợt bổ sung)",
                defaults={
                    "cert_type": course,
                    "exam_date": timezone.localdate() - timedelta(days=30),
                    "registration_deadline": timezone.localdate() - timedelta(days=45),
                    "max_candidates": 20,
                    "score_scale": Decimal("100"),
                    "status": ExamPeriod.Status.PUBLISHED,
                },
            )
            exam_candidates = self._ensure_exam_candidates(exam_period)
            room, _ = ExamRoom.objects.get_or_create(
                exam_period=exam_period,
                name="B201",
                defaults={"session": "Ca sáng (08:00-10:00)", "capacity": 10},
            )
            self._ensure_exam_scores(room, exam_candidates)
            self._ensure_exam_certificates(exam_period, exam_candidates)
            self._close_exam_period(exam_period)

            today = timezone.localdate()
            ExamPeriod.objects.get_or_create(
                name="Đợt thi CCE tháng 03/2027 — Lập trình Python ứng dụng",
                defaults={
                    "cert_type": course,
                    "exam_date": today + timedelta(days=60),
                    "registration_deadline": today + timedelta(days=30),
                    "max_candidates": 30,
                    "score_scale": Decimal("100"),
                    "status": ExamPeriod.Status.PUBLISHED,
                },
            )

        self.stdout.write(self.style.SUCCESS(
            "Workflow test data ready: 1 course class, 5 final results, "
            "5 course certificates, 6 exam candidates, 6 published scores, "
            "5 exam certificates, 1 transfer, and 1 upcoming registration period."
        ))
        self.stdout.write(
            "Public lookup examples: 990000000001 / hoangminh.nguyen@example.test; "
            "991000000001 / minhtuan.nguyen@example.test."
        )

    def _ensure_course_learners(self):
        learners = []
        birth_date = date(2000, 1, 15)
        for id_number, name, email, _score in COURSE_STUDENTS:
            learner, _ = Learner.objects.get_or_create(
                id_number=id_number,
                defaults={
                    "full_name": name,
                    "date_of_birth": birth_date,
                    "phone": "0900000001",
                    "email": email,
                },
            )
            learners.append(learner)
        return learners

    def _ensure_transfer(self, learner, source_class, destination_class):
        _history, created = EnrollmentTransferHistory.objects.get_or_create(
            learner=learner,
            source_class=source_class,
            destination_class=destination_class,
        )
        if created:
            Enrollment.objects.get_or_create(
                training_class=source_class,
                learner=learner,
            )
            Enrollment.objects.get_or_create(
                training_class=destination_class,
                learner=learner,
            )
            Enrollment.objects.filter(
                training_class=source_class,
                learner=learner,
            ).delete()
            record_audit(
                None,
                AuditLog.Action.EDIT,
                _history,
                old_values={
                    "source_class": source_class.name,
                    "destination_class": source_class.name,
                },
                new_values={
                    "source_class": source_class.name,
                    "destination_class": destination_class.name,
                },
                reason=TEST_REASON,
            )

    def _ensure_course_results(self, training_class, learners):
        for learner, row in zip(learners[:5], COURSE_STUDENTS[:5]):
            result, created = FinalCourseResult.objects.get_or_create(
                training_class=training_class,
                learner=learner,
                defaults={"score": row[3]},
            )
            if created:
                result.full_clean()
                record_audit(
                    None,
                    AuditLog.Action.CREATE,
                    result,
                    new_values={"score": str(result.score)},
                    reason=TEST_REASON,
                )

    def _ensure_course_certificates(self, training_class, learners):
        self._issue_course_certificate(
            training_class, learners[0], "CCE-PY-2026-1001"
        )
        revoked = self._issue_course_certificate(
            training_class, learners[1], "CCE-PY-2026-1002"
        )
        self._transition_certificate(
            revoked,
            Certificate.Status.REVOKED,
            AuditLog.Action.REVOKE,
            "Thu hồi sau khi kiểm tra thông tin chứng nhận.",
        )
        replaced = self._issue_course_certificate(
            training_class, learners[2], "CCE-PY-2026-1003"
        )
        self._replace_course_certificate(
            replaced, "CCE-PY-2026-1004"
        )
        self._issue_course_certificate(
            training_class, learners[4], "CCE-PY-2026-1005"
        )

    def _issue_course_certificate(self, training_class, learner, number):
        certificate, created = Certificate.objects.get_or_create(
            certificate_number=number,
            defaults={
                "training_class": training_class,
                "learner": learner,
                "status": Certificate.Status.ISSUED,
                "reason": TEST_REASON,
            },
        )
        if created:
            certificate.full_clean()
            certificate.save()
            self._history_once(
                CertificateHistory,
                certificate,
                Certificate.Status.ISSUED,
                Certificate.Status.ISSUED,
                TEST_REASON,
            )
            self._audit_once(
                AuditLog.Action.ISSUE,
                certificate,
                {"status": Certificate.Status.ISSUED},
                TEST_REASON,
            )
        return certificate

    def _transition_certificate(self, certificate, new_status, action, reason):
        if certificate.status != new_status:
            old_status = certificate.status
            certificate.status = new_status
            certificate.reason = reason
            certificate.save(update_fields=("status", "reason"))
            self._history_once(
                CertificateHistory, certificate, old_status, new_status, reason
            )
            self._audit_once(
                action,
                certificate,
                {"old_status": old_status, "status": new_status},
                reason,
            )

    def _replace_course_certificate(self, original, replacement_number):
        replacement = Certificate.objects.filter(
            certificate_number=replacement_number
        ).first()
        if original.status == Certificate.Status.ISSUED:
            original.status = Certificate.Status.REPLACED
            original.reason = TEST_REASON
            original.save(update_fields=("status", "reason"))
        replacement = replacement or self._issue_course_certificate(
            original.training_class,
            original.learner,
            replacement_number,
        )
        if original.replaced_by_id != replacement.pk:
            original.replaced_by = replacement
            original.replaced_at = timezone.now()
            original.save(update_fields=("replaced_by", "replaced_at"))
        self._history_once(
            CertificateHistory,
            original,
            Certificate.Status.ISSUED,
            Certificate.Status.REPLACED,
            TEST_REASON,
        )
        self._audit_once(
            AuditLog.Action.REPLACE,
            original,
            {"status": Certificate.Status.REPLACED, "replaced_by_id": replacement.pk},
            TEST_REASON,
        )

    def _close_result_class(self, training_class):
        if training_class.status != TrainingClass.Status.CLOSED:
            training_class.status = TrainingClass.Status.CLOSED
            training_class.close_reason = TEST_REASON
            training_class.final_results_locked_at = timezone.now()
            training_class.save(update_fields=(
                "status",
                "close_reason",
                "final_results_locked_at",
            ))
            record_audit(
                None,
                AuditLog.Action.EDIT,
                training_class,
                new_values={"status": training_class.status, "final_results_locked": True},
                reason=TEST_REASON,
            )
        elif not training_class.final_results_locked:
            training_class.final_results_locked_at = timezone.now()
            training_class.save(update_fields=("final_results_locked_at",))

    def _ensure_exam_candidates(self, period):
        candidates = []
        for id_number, name, email, score, absent in EXAM_CANDIDATES:
            candidate, _ = ExamApplication.objects.get_or_create(
                exam_period=period,
                id_number=id_number,
                defaults={
                    "full_name": name,
                    "date_of_birth": date(2000, 1, 15),
                    "phone": "0900000002",
                    "email": email,
                    "source": ExamApplication.Source.ONLINE,
                    "status": ExamApplication.Status.VALID,
                },
            )
            candidates.append((candidate, score, absent))
        return candidates

    def _ensure_exam_scores(self, room, candidates):
        for candidate, score, absent in candidates:
            assignment, _ = ExamRoomAssignment.objects.get_or_create(
                exam_room=room,
                application=candidate,
            )
            result, created = ExamScore.objects.get_or_create(
                assignment=assignment,
                defaults={
                    "score": score,
                    "is_absent": absent,
                    "is_published": True,
                    "is_locked": True,
                    "entered_at": timezone.now(),
                    "published_at": timezone.now(),
                },
            )
            if created:
                result.full_clean()
                result.save()
                record_audit(
                    None,
                    AuditLog.Action.PUBLISH,
                    result,
                    new_values={
                        "score": str(result.score) if result.score is not None else None,
                        "is_absent": result.is_absent,
                        "is_published": True,
                    },
                    reason=TEST_REASON,
                )

    def _ensure_exam_certificates(self, period, candidates):
        self._issue_exam_certificate(
            period, candidates[0][0], "CCE-EX-2026-2001"
        )
        revoked = self._issue_exam_certificate(
            period, candidates[1][0], "CCE-EX-2026-2002"
        )
        self._transition_exam_certificate(
            revoked,
            ExamCertificate.Status.REVOKED,
            AuditLog.Action.REVOKE,
            "Thu hồi sau khi kiểm tra kết quả thi.",
        )
        replaced = self._issue_exam_certificate(
            period, candidates[2][0], "CCE-EX-2026-2003"
        )
        self._replace_exam_certificate(
            replaced, "CCE-EX-2026-2004"
        )
        self._issue_exam_certificate(
            period, candidates[3][0], "CCE-EX-2026-2005"
        )

    def _issue_exam_certificate(self, period, application, number):
        certificate, created = ExamCertificate.objects.get_or_create(
            certificate_number=number,
            defaults={
                "exam_period": period,
                "application": application,
                "status": ExamCertificate.Status.ISSUED,
                "reason": TEST_REASON,
            },
        )
        if created:
            certificate.full_clean()
            certificate.save()
            self._history_once(
                ExamCertificateHistory,
                certificate,
                ExamCertificate.Status.ISSUED,
                ExamCertificate.Status.ISSUED,
                TEST_REASON,
            )
            self._audit_once(
                AuditLog.Action.ISSUE,
                certificate,
                {"status": ExamCertificate.Status.ISSUED},
                TEST_REASON,
            )
        return certificate

    def _transition_exam_certificate(self, certificate, new_status, action, reason):
        if certificate.status != new_status:
            old_status = certificate.status
            certificate.status = new_status
            certificate.reason = reason
            certificate.save(update_fields=("status", "reason"))
            self._history_once(
                ExamCertificateHistory, certificate, old_status, new_status, reason
            )
            self._audit_once(
                action,
                certificate,
                {"old_status": old_status, "status": new_status},
                reason,
            )

    def _replace_exam_certificate(self, original, replacement_number):
        replacement = ExamCertificate.objects.filter(
            certificate_number=replacement_number
        ).first()
        if original.status == ExamCertificate.Status.ISSUED:
            original.status = ExamCertificate.Status.REPLACED
            original.reason = TEST_REASON
            original.save(update_fields=("status", "reason"))
        replacement = replacement or self._issue_exam_certificate(
            original.exam_period,
            original.application,
            replacement_number,
        )
        if original.replaced_by_id != replacement.pk:
            original.replaced_by = replacement
            original.replaced_at = timezone.now()
            original.save(update_fields=("replaced_by", "replaced_at"))
        self._history_once(
            ExamCertificateHistory,
            original,
            ExamCertificate.Status.ISSUED,
            ExamCertificate.Status.REPLACED,
            TEST_REASON,
        )
        self._audit_once(
            AuditLog.Action.REPLACE,
            original,
            {"status": ExamCertificate.Status.REPLACED, "replaced_by_id": replacement.pk},
            TEST_REASON,
        )

    def _close_exam_period(self, period):
        if period.status != ExamPeriod.Status.CLOSED:
            period.status = ExamPeriod.Status.CLOSED
            period.save(update_fields=("status",))

    def _history_once(self, model, certificate, old_status, new_status, reason):
        model.objects.get_or_create(
            certificate=certificate,
            old_status=old_status,
            new_status=new_status,
            reason=reason,
            defaults={"changed_by": None},
        )

    def _audit_once(self, action, certificate, new_values, reason):
        if AuditLog.objects.filter(
            action=action,
            object_type=certificate._meta.label,
            object_id=str(certificate.pk),
            reason=reason,
        ).exists():
            return
        record_audit(
            None,
            action,
            certificate,
            new_values=new_values,
            reason=reason,
        )
