from django import template

register = template.Library()

STATUS_CLASSES = {
    "đang mở": "cce-badge-open status-open",
    "đã đóng": "cce-badge-closed status-closed",
    "chưa công bố": "cce-badge-draft status-unpublished",
    "đã công bố": "cce-badge-published status-published",
    "mới": "cce-badge-new status-new",
    "hợp lệ": "cce-badge-valid status-valid",
    "trùng": "cce-badge-duplicate status-duplicate",
    "đã cấp": "cce-badge-issued status-issued",
    "thu hồi": "cce-badge-revoked status-revoked",
    "đã thay thế": "cce-badge-replaced status-replaced",
}


@register.filter
def status_class(value):
    return STATUS_CLASSES.get(str(value).strip().lower(), "status-neutral")


@register.filter
def get_item(mapping, key):
    if isinstance(mapping, dict):
        return mapping.get(key, "")
    return getattr(mapping, key, "")


@register.filter
def accessible_field(bound_field):
    attrs = {}
    described_by = []
    existing_described_by = bound_field.field.widget.attrs.get("aria-describedby")
    if existing_described_by:
        described_by.append(existing_described_by)
    if bound_field.help_text:
        described_by.append(f"{bound_field.auto_id}_helptext")
    if bound_field.errors:
        attrs["aria-invalid"] = "true"
        described_by.append(f"{bound_field.auto_id}_error")
    if described_by:
        attrs["aria-describedby"] = " ".join(described_by)
    return bound_field.as_widget(attrs=attrs)


@register.simple_tag
def page_url(request, page_number):
    query = request.GET.copy()
    query["page"] = page_number
    return f"?{query.urlencode()}"
