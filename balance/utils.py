import os
import uuid
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from django.core.files.storage import default_storage
from django.core.exceptions import ValidationError

from balance.models import Wallet, RechargeRequest, Voucher, RechargeHistory
from stoppoints.models import StopPointProgress
from stoppoints.utils import get_active_stop_point, apply_recharge_to_stop_point
from notification.utils import notify_roles, create_admin_dashboard_event


# -----------------------------
# Wallet Utilities
# -----------------------------
def get_wallet(user):
    """Get or create wallet for a user."""
    wallet, _ = Wallet.objects.get_or_create(user=user)
    return wallet


def get_wallet_balance(user):
    """Returns current balance."""
    wallet = get_wallet(user)
    return wallet.current_balance


def handle_stop_point_recharge(user, wallet, recharge_amount):
    """Apply a recharge toward the active stop point requirement."""
    stop_point = get_active_stop_point(user)
    if not stop_point:
        return None

    if recharge_amount is None:
        return stop_point.required_balance_remaining or Decimal("0.00")

    new_remaining = apply_recharge_to_stop_point(stop_point, recharge_amount)
    if new_remaining is None:
        return None

    if new_remaining <= Decimal("0.00"):
        finalize_stop_point_clearance(user, wallet, stop_point)

    return new_remaining


def finalize_stop_point_clearance(user, wallet, stop_point):
    """Mark stop point as cleared and disburse any pending bonus."""
    bonus_amount = Decimal(stop_point.special_bonus_amount or Decimal("0.00"))
    if bonus_amount > Decimal("0.00"):
        wallet.current_balance += bonus_amount
        wallet.save(update_fields=["current_balance"])
        stop_point.bonus_disbursed = True
        stop_point.bonus_disbursed_at = timezone.now()

    updates = []
    if bonus_amount > Decimal("0.00"):
        updates.extend(["bonus_disbursed", "bonus_disbursed_at"])
    if stop_point.required_balance_remaining not in (None, Decimal("0.00")):
        stop_point.required_balance_remaining = Decimal("0.00")
        updates.append("required_balance_remaining")
    if stop_point.status != "approved":
        stop_point.status = "approved"
        updates.append("status")
    if updates:
        stop_point.save(update_fields=updates)

    progress, _ = StopPointProgress.objects.get_or_create(user=user)
    progress.last_cleared = stop_point
    progress.is_stopped = False
    progress.save(update_fields=["last_cleared", "is_stopped"])


@transaction.atomic
def update_wallet_balance(user, amount, action="add", balance_type="current"):
    """Generic wallet update."""
    wallet = get_wallet(user)
    amount = Decimal(amount)
    updated_fields = set()

    if balance_type == "current":
        if action == "add":
            wallet.current_balance += amount
            updated_fields.add("current_balance")
        elif action == "subtract":
            if wallet.current_balance >= amount:
                wallet.current_balance -= amount
                updated_fields.add("current_balance")
            else:
                return False
    elif balance_type == "product_commission":
        if action == "add":
            wallet.product_commission += amount
            updated_fields.add("product_commission")
        elif action == "subtract":
            if wallet.product_commission >= amount:
                wallet.product_commission -= amount
                updated_fields.add("product_commission")
            else:
                return False
    elif balance_type == "referral_commission":
        if action == "add":
            wallet.referral_commission += amount
            updated_fields.add("referral_commission")
        elif action == "subtract":
            if wallet.referral_commission >= amount:
                wallet.referral_commission -= amount
                updated_fields.add("referral_commission")
            else:
                return False
    else:
        return False

    wallet.save(update_fields=list(updated_fields))
    return wallet


# -----------------------------
# Recharge Utilities
# -----------------------------
@transaction.atomic
def create_recharge_request(user, amount):
    """Create a pending recharge request."""
    amount = Decimal(amount)
    recharge = RechargeRequest.objects.create(user=user, amount=amount)

    notify_roles(
        roles=("customerservice",),
        title="New recharge request",
        message=f"{user.username} requested a recharge of ${amount}.",
        category="recharge_request",
        metadata={
            "username": user.username,
            "amount": str(amount),
            "referrer": getattr(user.referred_by, "username", ""),
            "event": "recharge_request",
            "status": "pending",
            "recharge_id": recharge.id,
        },
    )

    create_admin_dashboard_event(
        user=user,
        event_type="recharge_request",
        message=f"{user.username} requested a recharge of ${amount}.",
        metadata={
            "amount": str(amount),
            "event": "recharge_request",
            "status": "pending",
        },
    )

    return recharge


@transaction.atomic
def approve_recharge(recharge_request, voucher_file=None):
    """Approve recharge request."""
    if recharge_request.status != "pending":
        raise ValueError("Recharge already processed")

    user = recharge_request.user
    wallet = Wallet.objects.select_for_update().get(user=user)

    wallet.current_balance += recharge_request.amount
    wallet.balance_source = 'recharge'
    wallet.has_recharged = True
    wallet.is_fake_display_mode = False
    wallet.save(update_fields=[
        'current_balance',
        'balance_source',
        'has_recharged',
        'is_fake_display_mode',
    ])

    handle_stop_point_recharge(user, wallet, recharge_request.amount)

    notify_roles(
        roles=("customerservice",),
        title="Recharge approved",
        message=f"{user.username}'s recharge of ${recharge_request.amount} was approved.",
        category="recharge_request",
        metadata={
            "username": user.username,
            "amount": str(recharge_request.amount),
            "referrer": getattr(user.referred_by, "username", ""),
            "event": "recharge_request",
            "status": "approved",
            "recharge_id": recharge_request.id,
        },
    )

    recharge_request.status = "approved"
    recharge_request.save(update_fields=['status'])

    history = RechargeHistory.objects.create(
        user=user,
        recharge_request=recharge_request,
        amount=recharge_request.amount,
        status="approved",
    )

    # ✅ Safe archive: no open(), no crash if file missing
    if voucher_file and getattr(voucher_file, "name", ""):
        try:
            history.voucher_file.save(
                os.path.basename(voucher_file.name),
                voucher_file,
                save=True,
            )
        except FileNotFoundError:
            pass  # file missing in Backblaze -> skip, don't crash
        except Exception:
            pass  # any storage/network error -> skip, don't crash

    return recharge_request


@transaction.atomic
def reject_recharge(recharge_request, voucher_file=None):
    """Reject recharge request."""
    if recharge_request.status != "pending":
        raise ValueError("Recharge already processed")

    recharge_request.status = "rejected"
    recharge_request.save(update_fields=["status"])

    history = RechargeHistory.objects.create(
        user=recharge_request.user,
        recharge_request=recharge_request,
        amount=recharge_request.amount,
        status="rejected",
    )

    # ✅ Safe archive: no open(), no crash if file missing
    if voucher_file and getattr(voucher_file, "name", ""):
        try:
            history.voucher_file.save(
                os.path.basename(voucher_file.name),
                voucher_file,
                save=True,
            )
        except FileNotFoundError:
            pass  # file missing -> skip, don't crash
        except Exception:
            pass  # storage/network error -> skip, don't crash

    return recharge_request


# -----------------------------
# Voucher Utilities
# -----------------------------
MAX_VOUCHER_BYTES = 3 * 1024 * 1024  # 3MB


@transaction.atomic
def upload_voucher(recharge_request, uploaded_file):
    """
    Robust upload:
    - validate max size (3MB)
    - upload to Backblaze with unique key
    - verify object exists
    - update DB only after confirmed upload
    """
    if uploaded_file.size > MAX_VOUCHER_BYTES:
        raise ValidationError("File too large (max 3MB).")

    voucher, _ = Voucher.objects.get_or_create(recharge_request=recharge_request)

    ext = uploaded_file.name.rsplit(".", 1)[-1].lower() if "." in uploaded_file.name else ""
    key = f"vouchers/{recharge_request.id}/{uuid.uuid4().hex}{('.' + ext) if ext else ''}"

    saved_name = default_storage.save(key, uploaded_file)

    # verify it really exists
    try:
        if not default_storage.exists(saved_name):
            raise IOError("Upload incomplete.")
    except Exception:
        try:
            default_storage.delete(saved_name)
        except Exception:
            pass
        raise IOError("Upload failed or was interrupted.")

    # delete old file best-effort if replacing
    old_name = voucher.file.name if voucher.file and voucher.file.name else ""
    if old_name and old_name != saved_name:
        try:
            default_storage.delete(old_name)
        except Exception:
            pass

    voucher.file.name = saved_name
    voucher.save(update_fields=["file"])
    return voucher


# -----------------------------
# History Utilities
# -----------------------------
def get_recharge_history_maps(user_ids):
    """Return history entries and document-ready histories for the given user IDs."""
    histories = (
        RechargeHistory.objects.filter(user_id__in=user_ids)
        .select_related('recharge_request', 'recharge_request__voucher')
        .order_by('-action_date')
    )

    history_map = {}
    history_documents_map = {}

    for history in histories:
        history_map.setdefault(history.user_id, []).append(history)

        # ✅ Safe URL generation: won't crash if file missing or URL signing fails
        voucher_url = None
        try:
            if history.voucher_file and history.voucher_file.name:
                voucher_url = history.voucher_file.url
            else:
                recharge_request = history.recharge_request
                voucher = getattr(recharge_request, 'voucher', None) if recharge_request else None
                if voucher and voucher.file and voucher.file.name:
                    voucher_url = voucher.file.url
        except Exception:
            voucher_url = None

        if voucher_url:
            history.display_voucher_url = voucher_url
            history_documents_map.setdefault(history.user_id, []).append(history)

    return history_map, history_documents_map


# -----------------------------
# Fake Display Mode Utilities
# -----------------------------
@transaction.atomic
def activate_fake_display_mode(user, referral_amount):
    """Activates fake display mode when user receives referral earnings."""
    wallet, _ = Wallet.objects.get_or_create(user=user)

    if wallet.current_balance == 0 and not wallet.is_fake_display_mode:
        wallet.referral_earned_balance += referral_amount
        wallet.is_fake_display_mode = True
        wallet.fake_mode_started_at = timezone.now()
        wallet.save(update_fields=[
            'referral_earned_balance',
            'is_fake_display_mode',
            'fake_mode_started_at',
        ])
        return True
    return False
