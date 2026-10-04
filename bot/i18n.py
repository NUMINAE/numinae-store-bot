"""Customer-facing text, English only, in one module. Every string a customer sees
lives here behind t(key, **kwargs) (or tn(key, n, **kwargs) for counts), so adding a
locale later is a contained change to this file and its call sites. Admin screens are
English literals in the handlers (only plural-bearing admin messages use tn()).

t() looks the key up and applies str.format(); a missing key returns the key
itself and logs one WARNING, so a missing string never breaks a screen. Any
value that is user- or database-sourced must already be escaped via
render.esc() before being passed in, exactly as it would be formatted into an
f-string directly.

tn(key, n, **kwargs) picks "<key>.one" when n == 1 and "<key>.other"
otherwise, and supplies n as {count}, so "1 item" and "2 items" are both right.

Product/category/edition names and descriptions, delivery text, order numbers
and the settings values the owner types (welcome_text, support_note) are data,
not UI copy, and do not live here.
"""

from __future__ import annotations

import logging

from .ui_kit import ICON_FIELDS

logger = logging.getLogger(__name__)

STRINGS: dict[str, str] = {
    # -- design language additions ----------------------------------------
    "common.home_title": "Numinae Store",
    "common.help_title": "Help",
    "catalogue.added_title": "Added to cart",
    "catalogue.more_editions.one": "…and {count} more edition not shown.",
    "catalogue.more_editions.other": "…and {count} more editions not shown.",
    "cart.empty_next": "Browse the catalogue to find something you like.",
    "cart.clear_title": "Clear cart?",
    "checkout.title": "Checkout",
    "checkout.step_review": "Review",
    "checkout.step_payment": "Payment",
    "checkout.step_delivery": "Delivery",
    "checkout.more_items.one": "…and {count} more item.",
    "checkout.more_items.other": "…and {count} more items.",
    "deliver.receipt_title": "Order #{id} complete",
    "deliver.part_title": "{name} ({n}/{m})",
    "library.item_title": "Library item",
    # -- profile ----------------------------------------------------------
    "profile.cmd.start": "Open the main menu",
    "profile.cmd.library": "Your purchases",
    "profile.cmd.paysupport": "Help with a payment",
    "profile.cmd.admin": "Admin dashboard",
    "profile.cmd.order": "Open an order by its number",
    "profile.cmd.sweep": "Run an expiry and delivery pass now",
    "profile.description": "Numinae Store sells digital assets for creators and developers: files, licence keys and guides. Browse the catalogue, pay with Telegram Stars and get your purchase delivered right here in the chat.",
    "profile.short_description": "Digital assets for creators and developers. Pay with Stars, get them instantly.",
    # -- nav ------------------------------------------------------------
    "nav.back": "{i_back} Back",
    "nav.home": "{i_home} Home",
    # -- common ------------------------------------------------------
    "common.welcome_default": "Welcome to Numinae Store! Use the menu below to get started.",
    "common.browse_button": "Browse products",
    "common.my_cart_button": "My cart",
    "common.my_cart_count": "My cart ({count})",
    "common.my_library_button": "My Library",
    "common.help_button": "Help",
    "common.reply_keyboard_intro": "Use the buttons below to browse products, view your cart, or open your library.",
    "common.menu_restored": "Menu restored.",
    "common.nudge": "I'm not sure what to do with that. Here's the menu:\n\n{menu}",
    "common.expired_menu": "This menu has expired.",
    "common.help_text": "Browse the catalogue, add editions to your cart and check out with Telegram Stars. Digital goods are delivered right here in the chat as soon as the payment goes through.",
    # -- catalogue ---------------------------------------------------
    "catalogue.no_stock": "Nothing is available right now — check back soon!",
    "catalogue.heading": "Catalogue",
    "catalogue.choose_category": "Choose a category:",
    "catalogue.tap_to_view": "Tap a product to see its editions.",
    "catalogue.price_from": " — from {price}",
    "catalogue.no_editions": "No editions are currently available for this product.",
    "catalogue.edition_label": "Edition: {label}",
    "catalogue.price_label": "Price: {price}",
    "catalogue.delivery_line": "Delivery: {kind}",
    # -- delivery ----------------------------------------------------
    "delivery.file": "Instant download",
    "delivery.key": "Licence key",
    "delivery.text": "Delivered as a message",
    # -- catalogue ---------------------------------------------------
    "catalogue.total_for_qty": "Total for {qty}: {total}",
    "catalogue.min_qty_alert": "Minimum quantity is {min}.",
    "catalogue.max_qty_alert": "Only {max} available — that's the maximum.",
    "catalogue.out_of_stock_alert": "Sorry, this edition is out of stock.",
    "catalogue.already_in_cart_alert": "This edition is already in your cart.",
    "catalogue.add_to_cart_button": "Add to cart",
    "catalogue.item_unavailable_alert": "This item is no longer available.",
    "catalogue.added_to_cart": "Added {qty} × {product} — {variant} to your cart.",
    "catalogue.added_capped_note": "That's all the stock there is.",
    "catalogue.keep_browsing_button": "Keep browsing",
    "catalogue.view_cart_button": "View cart",
    # -- cart --------------------------------------------------------
    "cart.empty": "Your cart is empty.",
    "cart.heading": "Your cart",
    "cart.line_unavailable_suffix": " (no longer available)",
    "cart.removed_out_of_stock": "{name} was removed from your cart because it is now out of stock.",
    "cart.remove_button": "{i_remove} Remove {name}",
    "cart.subtotal": "Subtotal: {amount}",
    "cart.checkout_button": "Checkout",
    "cart.clear_cart_button": "Clear cart",
    "cart.yes_clear_button": "Yes, clear",
    "cart.no_button": "No",
    # -- pay ---------------------------------------------------------
    "pay.invoice_title": "Order #{id}",
    "pay.invoice_description": "Digital goods: {items}",
    "pay.pay_button": "Pay {i_stars} {total}",
    "pay.cancel_order_button": "Cancel order",
    "pay.mock_banner": "{i_demo} DEMO MODE — no real payment",
    "pay.mock_heading": "Order #{id}",
    "pay.mock_total": "Total: {total}",
    "pay.mock_pay_button": "Pay (demo)",
    # -- deliver -----------------------------------------------------
    "deliver.receipt": "Thank you for your purchase!",
    "deliver.total": "Total: {total}",
    # -- pay ---------------------------------------------------------
    "pay.err_unavailable": "This order is no longer available.",
    "pay.err_not_pending": "This order is no longer open for payment.",
    "pay.err_expired": "This order has expired. Please start a new one.",
    "pay.err_mode": "Payments for this order are not available right now.",
    "pay.err_amount": "The amount does not match this order.",
    "pay.err_reservation": "Your items are no longer reserved. Please start a new order.",
    "pay.received": "{i_ok} Payment received for order #{id}. Your items are on their way.",
    "pay.received_delayed": "{i_ok} Payment received for order #{id}. Delivery is delayed — we are sorting it out and will send your items shortly.",
    "pay.orphan_expired": "Your payment for order #{id} arrived after the order expired, so it has been refunded.",
    "pay.orphan_paid": "That was a duplicate payment for order #{id}, which was already paid. It has been refunded.",
    "pay.orphan_refund_failed": "Your payment for order #{id} could not be applied and the automatic refund failed. The shop owner has been alerted and will sort it out.",
    "pay.mismatch": "We could not match your payment to order #{id}. The shop owner has been alerted.",
    "pay.support_title": "Payment help",
    "pay.support_text": "Need help with a payment? Quote your order number when you contact the shop.",
    # -- checkout ----------------------------------------------------
    "checkout.empty": "There is nothing in your cart to check out. Add something from the catalogue first.",
    "checkout.open_order": "You already have an open order (#{id}). It expires in {minutes} min.",
    "checkout.resume_button": "Resume",
    "checkout.review_heading": "Review your order",
    "checkout.total": "Total: {total}",
    "checkout.demo_notice": "{i_demo} Demo mode: no real payment will be taken.",
    "checkout.pay_button": "Pay {i_stars} {total}",
    "checkout.too_large": "Your order comes to {i_stars} {total}, above the {i_stars} {limit} limit for a single order. Remove some items and try again.",
    "checkout.stock_short": "Only {available} left of one of your items. Lower its quantity in your cart and try again.",
    "checkout.presented": "Order #{id} is ready — complete the payment in the message below.",
    "checkout.mode_changed": "That order was created in a different payment mode and has been cancelled. Please start checkout again.",
    "checkout.cancel_confirm": "Cancel order #{id}? Your items will go back to your cart.",
    "checkout.yes_cancel_button": "Yes, cancel",
    "checkout.keep_button": "No, keep it",
    "checkout.cancelled": "Order #{id} was cancelled.",
    "checkout.cancelled_restored": "Order #{id} was cancelled. Your items are back in your cart.",
    "checkout.cancel_refused": "This order can't be cancelled any more.",
    "checkout.present_failed": "We couldn't start the payment. Your items are back in your cart. Please try again.",
    "checkout.mock_done": "{i_demo} Demo payment complete for order #{id}.",
    "checkout.mock_unavailable": "Demo payments are not available.",
    # -- cart --------------------------------------------------------
    "cart.over_limit_warning": "{i_warning} Your cart is over the {i_stars} {limit} limit for one order — remove some items to check out.",
    # -- jobs --------------------------------------------------------
    "jobs.order_expired": "Your order #{id} expired and its reservation was released. You can start a new one any time.",
    # -- owner -------------------------------------------------------
    "owner.order_cancelled": "Your order #{id} was cancelled by the shop.",
    "owner.order_refunded": "Your order #{id} was refunded ({total}). Things sent earlier cannot be taken back, but licence keys from this order are no longer valid.",
    # -- library -----------------------------------------------------
    "library.heading": "My Library",
    "library.empty": "Your library is empty. Things you buy will appear here.",
    "library.item_detail": "{name}\nOrder #{id}\nStatus: {status}",
    "library.being_delivered": "Being delivered",
    "library.status_delivered": "Delivered",
    "library.status_refunded": "Refunded",
    "library.send_button": "Send again",
    "library.resent": "Sent again.",
    "library.resend_failed": "Couldn't send it right now. Try again in a moment.",
    "library.unavailable": "That item is not available.",
    # -- status ------------------------------------------------------
    "status.pending_payment": "Awaiting payment",
    "status.paid": "Paid",
    "status.delivered": "Delivered",
    "status.cancelled": "Cancelled",
    "status.expired": "Expired",
    "status.refunded": "Refunded",
    # -- plural forms (used through tn) ------------------------------
    "cart.clear_confirm.one": "Clear {count} item from your cart?",
    "cart.clear_confirm.other": "Clear all {count} items from your cart?",
    "checkout.dropped_note.one": "{count} unavailable item was left out of this order.",
    "checkout.dropped_note.other": "{count} unavailable items were left out of this order.",
    "admin.categories.one": "{count} category",
    "admin.categories.other": "{count} categories",
    "admin.products.one": "{count} product",
    "admin.products.other": "{count} products",
    "admin.editions.one": "{count} edition",
    "admin.editions.other": "{count} editions",
    "admin.paid_awaiting.one": "{count} paid order awaiting delivery",
    "admin.paid_awaiting.other": "{count} paid orders awaiting delivery",
    "admin.keys_added.one": "Added {count} key.",
    "admin.keys_added.other": "Added {count} keys.",
    "admin.duplicates_skipped.one": "Skipped {count} duplicate.",
    "admin.duplicates_skipped.other": "Skipped {count} duplicates.",
    "admin.invalid_lines.one": "Skipped {count} invalid line: {lines}.",
    "admin.invalid_lines.other": "Skipped {count} invalid lines: {lines}.",
    "admin.more_editions.one": "…and {count} more edition",
    "admin.more_editions.other": "…and {count} more editions",
    "admin.more_items.one": "…and {count} more item",
    "admin.more_items.other": "…and {count} more items",
    "admin.and_more.one": "(and {count} more)",
    "admin.and_more.other": "(and {count} more)",
    "admin.items_delivered.one": "Delivered {count} item.",
    "admin.items_delivered.other": "Delivered {count} items.",
    "admin.still_failing": "{count} still failing (see the logs). It will keep retrying.",
    "admin.resent.one": "Re-sent {sent} of {count} item to the buyer.",
    "admin.resent.other": "Re-sent {sent} of {count} items to the buyer.",
    "admin.keys_revoked.one": "{count} key revoked",
    "admin.keys_revoked.other": "{count} keys revoked",
    "owner.more_customers.one": "{count} more new customer in the last hour",
    "owner.more_customers.other": "{count} more new customers in the last hour",
    "library.count.one": "{count} item",
    "library.count.other": "{count} items",
}


def t(key: str, **kwargs: object) -> str:
    template = STRINGS.get(key)
    if template is None:
        logger.warning("i18n: missing key %r", key)
        return key
    if "{i_" in template:  # icon placeholders ("{i_ok}") are always available
        kwargs = {**ICON_FIELDS, **kwargs}
    if not kwargs:
        return template
    try:
        return template.format(**kwargs)
    except (KeyError, IndexError):
        logger.warning("i18n: format error for key %r kwargs %r", key, kwargs)
        return template


def tn(key: str, n: int, **kwargs: object) -> str:
    """The plural form of `key` for n: "<key>.one" if n == 1, else "<key>.other".
    n is available to the template as {count}."""
    return t(f"{key}.{'one' if n == 1 else 'other'}", count=n, **kwargs)


def status_label(status: str) -> str:
    return t(f"status.{status}")
