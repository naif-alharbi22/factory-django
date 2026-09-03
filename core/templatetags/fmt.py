"""Display filters."""

from decimal import Decimal, InvalidOperation

from django import template
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter
def money(value, decimals=2):
    """Format an amount with thousands separators."""
    if value is None or value == "":
        return "0"
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return value
    quant = Decimal("1") if int(decimals) == 0 else Decimal("0.01")
    number = number.quantize(quant)
    formatted = f"{number:,.{int(decimals)}f}"
    return formatted


@register.filter
def money0(value):
    return money(value, 0)


@register.filter
def hours(value):
    """Format hours without trailing zeros."""
    if value is None:
        return "0"
    try:
        number = Decimal(str(value)).normalize()
    except (InvalidOperation, ValueError):
        return value
    if number == number.to_integral_value():
        return str(int(number))
    return f"{number}"


@register.filter
def pct(value):
    if value is None:
        return "0"
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return value
    if number == number.to_integral_value():
        return str(int(number))
    return f"{number.quantize(Decimal('0.1'))}"


@register.filter
def bar_width(value, scale):
    """Relative bar width (0-100%), guarded against division by zero."""
    try:
        value = Decimal(str(value or 0))
        scale = Decimal(str(scale or 0))
    except (InvalidOperation, ValueError):
        return "0"
    if scale <= 0:
        return "0"
    width = min(value / scale * 100, Decimal("100"))
    return f"{width.quantize(Decimal('0.1'))}"


@register.filter
def status_badge(status):
    """Badge colour for a project status."""
    mapping = {
        "IN_PROGRESS": "badge-info",
        "APPROVAL": "badge-warning",
        "CLOSED": "badge-ghost",
        "ON_HOLD": "badge-error",
        "INSPECTION": "badge-secondary",
        "PLANNING": "badge-accent",
        "PAID": "badge-success",
        "APPROVED": "badge-info",
        "DRAFT": "badge-ghost",
        "CANCELLED": "badge-error",
    }
    return mapping.get(status, "badge-ghost")


@register.filter
def initials(name):
    if not name:
        return "؟"
    parts = str(name).split()
    return parts[0][0] if parts else "؟"
