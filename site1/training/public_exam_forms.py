import re
from datetime import date, datetime

from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.utils import timezone
from captcha.fields import CaptchaField, CaptchaTextInput

from training.models import Course, ExamPeriod


class PublicExamPeriodChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, period):
        return f"{period.name} — {period.exam_date:%d/%m/%Y}"


class PublicExamPeriodSelect(forms.Select):
    def __init__(self, *args, period_course_ids=None, **kwargs):
        self.period_course_ids = period_course_ids or {}
        super().__init__(*args, **kwargs)

    def create_option(
        self,
        name,
        value,
        label,
        selected,
        index,
        subindex=None,
        attrs=None,
    ):
        option = super().create_option(
            name,
            value,
            label,
            selected,
            index,
            subindex=subindex,
            attrs=attrs,
        )
        course_id = self.period_course_ids.get(str(value))
        if course_id:
            option["attrs"]["data-cert-type"] = course_id
        return option


class PublicExamDatePickerInput(forms.DateInput):
    input_type = "text"
    template_name = "training/widgets/public_exam_datepicker.html"

    def __init__(self, attrs=None):
        widget_attrs = {
            "class": "cce-input public-date-input",
            "autocomplete": "off",
            "readonly": True,
            "placeholder": "mm/dd/yyyy",
        }
        widget_attrs.update(attrs or {})
        super().__init__(format="%m/%d/%Y", attrs=widget_attrs)

    def format_value(self, value):
        if not value:
            return ""
        if isinstance(value, str):
            for date_format in ("%Y-%m-%d", "%m/%d/%Y"):
                try:
                    value = datetime.strptime(value, date_format).date()
                    break
                except ValueError:
                    continue
        if isinstance(value, date):
            return value.strftime("%m/%d/%Y")
        return str(value)

    def value_from_datadict(self, data, files, name):
        value = data.get(name, "")
        for date_format in ("%m/%d/%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(value, date_format).date().isoformat()
            except (TypeError, ValueError):
                continue
        return value


class PublicExamCaptchaTextInput(CaptchaTextInput):
    template_name = "training/widgets/public_exam_captcha.html"


class PublicExamRegistrationForm(forms.Form):
    cert_type = forms.ModelChoiceField(
        label="Loại chứng chỉ",
        queryset=Course.objects.none(),
        empty_label="Chọn loại chứng chỉ",
        widget=forms.Select(attrs={"class": "cce-select", "autocomplete": "off"}),
    )
    exam_period = PublicExamPeriodChoiceField(
        label="Đợt thi",
        queryset=ExamPeriod.objects.none(),
        empty_label="Chọn đợt thi",
        help_text="Chọn loại chứng chỉ trước để xem các đợt thi tương ứng.",
        widget=PublicExamPeriodSelect(
            attrs={"class": "cce-select", "autocomplete": "off"}
        ),
    )
    full_name = forms.CharField(
        label="Họ và tên",
        max_length=150,
        widget=forms.TextInput(
            attrs={"class": "cce-input", "autocomplete": "name", "maxlength": "150"}
        ),
    )
    date_of_birth = forms.DateField(
        label="Ngày sinh",
        help_text="Chọn ngày trên lịch. Không thể chọn ngày trong tương lai.",
        widget=PublicExamDatePickerInput(),
    )
    id_number = forms.CharField(
        label="Số CCCD",
        max_length=12,
        validators=[
            RegexValidator(
                regex=r"^[0-9]{12}$",
                message="Số CCCD phải gồm đúng 12 chữ số.",
            )
        ],
        widget=forms.TextInput(
            attrs={
                "class": "cce-input",
                "autocomplete": "off",
                "inputmode": "numeric",
                "maxlength": "12",
            }
        ),
    )
    phone = forms.CharField(
        label="Số điện thoại",
        max_length=30,
        widget=forms.TextInput(
            attrs={
                "class": "cce-input",
                "type": "tel",
                "autocomplete": "tel",
                "inputmode": "tel",
                "maxlength": "30",
            }
        ),
    )
    email = forms.EmailField(
        label="Địa chỉ email",
        max_length=254,
        widget=forms.EmailInput(
            attrs={"class": "cce-input", "autocomplete": "email", "maxlength": "254"}
        ),
    )

    def __init__(self, *args, available_periods, initial_period=None, **kwargs):
        super().__init__(*args, **kwargs)
        available_periods = list(available_periods)
        period_ids = [period.pk for period in available_periods]
        self.fields["exam_period"].queryset = (
            ExamPeriod.objects.filter(pk__in=period_ids)
            .select_related("cert_type")
            .order_by("exam_date", "name")
        )
        self.fields["exam_period"].widget.period_course_ids = {
            str(period.pk): str(period.cert_type_id)
            for period in available_periods
        }
        self.fields["cert_type"].queryset = Course.objects.filter(
            exam_periods__pk__in=period_ids
        ).distinct().order_by("name")
        if initial_period:
            self.initial.setdefault("exam_period", initial_period.pk)
            self.initial.setdefault("cert_type", initial_period.cert_type_id)

    def clean_full_name(self):
        full_name = " ".join(self.cleaned_data["full_name"].split())
        if not full_name:
            raise ValidationError("Vui lòng nhập họ và tên.")
        return full_name

    def clean_date_of_birth(self):
        date_of_birth = self.cleaned_data["date_of_birth"]
        if date_of_birth > timezone.localdate():
            raise ValidationError("Ngày sinh không thể ở tương lai.")
        return date_of_birth

    def clean_id_number(self):
        return self.cleaned_data["id_number"].strip()

    def clean_phone(self):
        phone = self.cleaned_data["phone"].strip()
        if not re.fullmatch(r"[0-9+().\-\s]{8,30}", phone):
            raise ValidationError("Vui lòng nhập số điện thoại hợp lệ.")
        digit_count = sum(character.isdigit() for character in phone)
        if not 9 <= digit_count <= 11:
            raise ValidationError("Số điện thoại phải có từ 9 đến 11 chữ số.")
        return phone

    def clean_email(self):
        return self.cleaned_data["email"].strip()

    def clean(self):
        cleaned_data = super().clean()
        cert_type = cleaned_data.get("cert_type")
        exam_period = cleaned_data.get("exam_period")
        if cert_type and exam_period and exam_period.cert_type_id != cert_type.pk:
            self.add_error(
                "exam_period",
                "Đợt thi không thuộc loại chứng chỉ đã chọn. Hãy chọn lại đợt thi.",
            )
        for field_name in self.errors:
            if field_name in self.fields:
                widget = self.fields[field_name].widget
                widget.attrs["aria-invalid"] = "true"
                widget.attrs["aria-describedby"] = (
                    f"{self[field_name].auto_id}-error"
                )
        return cleaned_data


class PublicExamLookupRequestForm(forms.Form):
    id_number = forms.CharField(
        label="Số CCCD",
        max_length=12,
        validators=[
            RegexValidator(
                regex=r"^[0-9]{12}$",
                message="Số CCCD phải gồm đúng 12 chữ số.",
            )
        ],
        widget=forms.TextInput(
            attrs={
                "class": "cce-input",
                "autocomplete": "off",
                "inputmode": "numeric",
                "maxlength": "12",
            }
        ),
    )
    email = forms.EmailField(
        label="Địa chỉ email đã đăng ký",
        max_length=254,
        widget=forms.EmailInput(
            attrs={
                "class": "cce-input",
                "autocomplete": "email",
                "maxlength": "254",
            }
        ),
    )
    captcha = CaptchaField(
        label="Mã xác thực",
        help_text="Nhập các ký tự trong hình.",
        widget=PublicExamCaptchaTextInput(
            attrs={"class": "cce-input", "autocomplete": "off"}
        ),
        error_messages={
            "required": "Vui lòng nhập mã xác thực.",
            "invalid": "Mã xác thực không đúng hoặc đã hết hạn.",
        },
    )

    def clean_id_number(self):
        return self.cleaned_data["id_number"].strip()

    def clean_email(self):
        return self.cleaned_data["email"].strip()
