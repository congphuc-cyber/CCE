from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.contrib.auth.password_validation import validate_password
from django.db.models import Q
from training.models import ClassAssignment, Course, Enrollment, Learner, Teacher, TrainingClass
from training.navigation import ROLE_ORDER


class VietnameseAuthenticationForm(AuthenticationForm):
    error_messages = {
        **AuthenticationForm.error_messages,
        "invalid_login": "Tên đăng nhập hoặc mật khẩu không chính xác.",
        "inactive": "Tài khoản đã bị khóa hoặc vô hiệu hóa. Vui lòng liên hệ quản trị viên.",
    }

    username = forms.CharField(
        label="Tên đăng nhập",
        widget=forms.TextInput(
            attrs={
                "autocapitalize": "none",
                "autocomplete": "username",
                "autofocus": True,
            }
        ),
    )
    password = forms.CharField(
        label="Mật khẩu",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )

    def clean(self):
        try:
            return super().clean()
        except ValidationError as error:
            username = self.cleaned_data.get("username")
            password = self.cleaned_data.get("password")
            if username and password:
                user_model = get_user_model()
                try:
                    user = user_model._default_manager.get_by_natural_key(username)
                except user_model.DoesNotExist:
                    raise error
                if not user.is_active and user.check_password(password):
                    raise ValidationError(self.error_messages["inactive"], code="inactive")
            raise error


class ProfilePasswordChangeForm(PasswordChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["old_password"].label = "Mật khẩu hiện tại"
        self.fields["new_password1"].label = "Mật khẩu mới"
        self.fields["new_password2"].label = "Nhập lại mật khẩu mới"
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-control"


class DashboardDateRangeForm(forms.Form):
    from_date = forms.DateField(
        label="Từ ngày",
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
    )
    to_date = forms.DateField(
        label="Đến ngày",
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
    )

    def clean(self):
        cleaned_data = super().clean()
        from_date = cleaned_data.get("from_date")
        to_date = cleaned_data.get("to_date")
        if from_date and to_date and from_date > to_date:
            self.add_error("to_date", "Ngày kết thúc phải bằng hoặc sau ngày bắt đầu.")
        return cleaned_data


class CourseAdminForm(forms.ModelForm):
    class Meta:
        model = Course
        fields = ("name", "pass_threshold")


class TrainingClassAdminForm(forms.ModelForm):
    class Meta:
        model = TrainingClass
        fields = ("name", "course", "learning_format", "schedule", "start_date", "max_students")
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date", "class": "cce-input"}),
        }

    def clean(self):
        cleaned_data = super().clean()
        max_students = cleaned_data.get("max_students")
        if self.instance.pk and max_students is not None:
            enrolled_count = Enrollment.objects.filter(training_class=self.instance).count()
            if max_students < enrolled_count:
                self.add_error(
                    "max_students",
                    "Sĩ số tối đa không được nhỏ hơn số học viên đã ghi danh.",
                )
        return cleaned_data


class TeacherAdminForm(forms.ModelForm):
    class Meta:
        model = Teacher
        fields = ("full_name", "phone", "email", "user")


class AccountAdminForm(forms.ModelForm):
    full_name = forms.CharField(label="Họ và tên", max_length=150)
    email = forms.EmailField(label="Địa chỉ email")
    initial_password = forms.CharField(
        label="Mật khẩu ban đầu",
        required=False,
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    role = forms.ModelChoiceField(label="Vai trò", queryset=Group.objects.none())

    class Meta:
        model = get_user_model()
        fields = ("username", "email", "is_active")
        labels = {
            "username": "Tên đăng nhập",
            "is_active": "Tài khoản đang hoạt động",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.order_fields(
            ("full_name", "username", "email", "initial_password", "role", "is_active")
        )
        self.fields["role"].queryset = Group.objects.filter(name__in=ROLE_ORDER).order_by("name")
        self.fields["full_name"].initial = self.instance.get_full_name()
        self.fields["initial_password"].required = self.instance._state.adding
        if self.instance.pk:
            role_names = set(
                self.instance.groups.filter(name__in=ROLE_ORDER).values_list(
                    "name",
                    flat=True,
                )
            )
            current_role = next((role for role in ROLE_ORDER if role in role_names), None)
            if current_role:
                self.fields["role"].initial = Group.objects.get(name=current_role)

    def clean_email(self):
        email = self.cleaned_data["email"].strip()
        user_model = get_user_model()
        existing = user_model._default_manager.filter(email__iexact=email)
        if self.instance.pk:
            existing = existing.exclude(pk=self.instance.pk)
        if existing.exists():
            raise ValidationError("Địa chỉ email này đã được sử dụng.")
        return email

    def clean_initial_password(self):
        password = self.cleaned_data.get("initial_password")
        if password:
            validate_password(password, self.instance)
        return password

    def clean(self):
        cleaned_data = super().clean()
        if not cleaned_data.get("full_name", "").strip():
            self.add_error("full_name", "Vui lòng nhập họ và tên.")
        if not cleaned_data.get("role"):
            self.add_error("role", "Vui lòng chọn vai trò.")
        return cleaned_data


class EnrollmentForm(forms.ModelForm):
    class Meta:
        model = Enrollment
        fields = ("learner",)

    def __init__(self, *args, training_class, **kwargs):
        self.training_class = training_class
        super().__init__(*args, **kwargs)
        self.fields["learner"].queryset = Learner.objects.order_by("full_name")

    def clean(self):
        cleaned_data = super().clean()
        learner = cleaned_data.get("learner")
        if self.training_class.status == TrainingClass.Status.CLOSED:
            raise ValidationError("Lớp đã đóng, không thể ghi danh thêm.")
        if self.training_class.enrolled_count >= self.training_class.max_students:
            raise ValidationError("Lớp đã đạt sĩ số tối đa.")
        if learner and Enrollment.objects.filter(
            training_class=self.training_class,
            learner=learner,
        ).exists():
            self.add_error("learner", "Học viên này đã được ghi danh vào lớp.")
        return cleaned_data

    def save(self, commit=True):
        enrollment = super().save(commit=False)
        enrollment.training_class = self.training_class
        if commit:
            enrollment.save()
        return enrollment


class ClassAssignmentForm(forms.ModelForm):
    class Meta:
        model = ClassAssignment
        fields = ("teacher",)

    def __init__(self, *args, training_class, **kwargs):
        self.training_class = training_class
        super().__init__(*args, **kwargs)
        self.fields["teacher"].queryset = Teacher.objects.exclude(
            Q(teacher_assignments__training_class=training_class)
            & Q(teacher_assignments__is_active=True)
        ).distinct()

    def clean_teacher(self):
        teacher = self.cleaned_data["teacher"]
        if ClassAssignment.objects.filter(
            training_class=self.training_class,
            teacher=teacher,
            is_active=True,
        ).exists():
            raise ValidationError("Giáo viên này đã được phân công cho lớp.")
        return teacher

    def save(self, commit=True):
        assignment = super().save(commit=False)
        assignment.training_class = self.training_class
        if commit:
            assignment.save()
        return assignment
