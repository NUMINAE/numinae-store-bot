"""English/Russian strings for everything customer-facing. Admin-facing
strings stay in English and stay where they're already defined — this module
holds only what a customer sees.

t(key, lang, **kwargs) looks up STRINGS[key][lang], falls back to English on
a missing key or a missing language, and logs a WARNING rather than raising —
a missing translation must never break a screen. kwargs are applied with
str.format(); any value that is user- or database-sourced must already be
escaped via render.esc() before being passed in, exactly as it would be
formatted into an f-string directly.

Product/category/edition names and descriptions, delivery text, order
numbers, and the settings values the owner types (welcome_text, support_note)
are all data, not UI copy — none of it lives here, and none of it is
translated.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

LANGUAGES = ("en", "ru")
DEFAULT_LANGUAGE = "en"

STRINGS: dict[str, dict[str, str]] = {
    # -- language picker --------------------------------------------------
    "lang.picker": {
        "en": "Please choose your language / Пожалуйста, выберите язык:",
        "ru": "Please choose your language / Пожалуйста, выберите язык:",
    },
    "lang.button_en": {"en": "English", "ru": "English"},
    "lang.button_ru": {"en": "Русский", "ru": "Русский"},
    "lang.invalid": {"en": "Invalid language.", "ru": "Invalid language."},
    # -- common: main menu, nudges, shared chrome --------------------------
    "common.welcome_default": {
        "en": "Welcome to Numinae Store! Use the menu below to get started.",
        "ru": "Добро пожаловать в Numinae Store! Используйте меню ниже, чтобы начать.",
    },
    "common.browse_button": {"en": "Browse products", "ru": "Каталог товаров"},
    "common.my_cart_button": {"en": "My cart", "ru": "Моя корзина"},
    "common.my_cart_count": {"en": "My cart ({count})", "ru": "Моя корзина ({count})"},
    "common.my_library_button": {"en": "My Library", "ru": "Моя библиотека"},
    "common.help_button": {"en": "Help", "ru": "Помощь"},
    "common.language_button": {"en": "Language / Язык", "ru": "Language / Язык"},
    "common.back_to_menu_button": {"en": "Back to menu", "ru": "Назад в меню"},
    "common.reply_keyboard_intro": {
        "en": "Use the buttons below to browse products, view your cart, or open your library.",
        "ru": "Используйте кнопки ниже, чтобы открыть каталог, корзину или свою библиотеку.",
    },
    "common.menu_restored": {"en": "Menu restored.", "ru": "Меню восстановлено."},
    "common.type_below": {"en": "Type below.", "ru": "Напишите сообщение ниже."},
    "common.nudge": {
        "en": "I'm not sure what to do with that. Here's the menu:\n\n{menu}",
        "ru": "Не совсем понял. Вот меню:\n\n{menu}",
    },
    "common.expired_menu": {"en": "This menu has expired.", "ru": "Это меню больше не активно."},
    "common.help_text": {
        "en": (
            "Browse the catalogue, add editions to your cart and check out with "
            "Telegram Stars. Digital goods are delivered right here in the chat "
            "as soon as the payment goes through."
        ),
        "ru": (
            "Выберите товары в каталоге, добавьте их в корзину и оплатите звёздами "
            "Telegram. Цифровые товары приходят прямо в этот чат сразу после оплаты."
        ),
    },
    # -- catalogue.py -------------------------------------------------------
    "catalogue.no_stock": {
        "en": "Nothing is available right now — check back soon!",
        "ru": "Сейчас ничего нет в наличии — загляните позже!",
    },
    "catalogue.heading": {"en": "Catalogue", "ru": "Каталог"},
    "catalogue.choose_category": {"en": "Choose a category:", "ru": "Выберите категорию:"},
    "catalogue.tap_to_view": {
        "en": "Tap a product to see its editions.",
        "ru": "Нажмите на товар, чтобы посмотреть варианты.",
    },
    "catalogue.price_from": {"en": " — from {price}", "ru": " — от {price}"},
    "catalogue.no_editions": {
        "en": "No editions are currently available for this product.",
        "ru": "Для этого товара сейчас нет доступных вариантов.",
    },
    "catalogue.back_to_products": {"en": "Back to products", "ru": "Назад к товарам"},
    "catalogue.back_to_categories": {"en": "Back to categories", "ru": "Назад к категориям"},
    "catalogue.edition_label": {"en": "Edition: {label}", "ru": "Вариант: {label}"},
    "catalogue.price_label": {"en": "Price: {price}", "ru": "Цена: {price}"},
    "catalogue.delivery_line": {"en": "Delivery: {kind}", "ru": "Получение: {kind}"},
    "delivery.file": {"en": "Instant download", "ru": "Мгновенная загрузка"},
    "delivery.key": {"en": "Licence key", "ru": "Лицензионный ключ"},
    "delivery.text": {"en": "Delivered as a message", "ru": "Приходит сообщением"},
    "catalogue.total_for_qty": {
        "en": "Total for {qty}: {total}",
        "ru": "Итого за {qty} шт.: {total}",
    },
    "catalogue.min_qty_alert": {
        "en": "Minimum quantity is {min}.",
        "ru": "Минимальное количество — {min}.",
    },
    "catalogue.max_qty_alert": {
        "en": "Only {max} available — that's the maximum.",
        "ru": "Доступно только {max} — это максимум.",
    },
    "catalogue.out_of_stock_alert": {
        "en": "Sorry, this edition is out of stock.",
        "ru": "К сожалению, этот вариант закончился.",
    },
    "catalogue.already_in_cart_alert": {
        "en": "This edition is already in your cart.",
        "ru": "Этот вариант уже в вашей корзине.",
    },
    "catalogue.add_to_cart_button": {"en": "Add to cart", "ru": "Добавить в корзину"},
    "catalogue.back_to_product": {"en": "Back to product", "ru": "Назад к товару"},
    "catalogue.item_unavailable_alert": {
        "en": "This item is no longer available.",
        "ru": "Этот товар больше недоступен.",
    },
    "catalogue.added_to_cart": {
        "en": "Added {qty} × {product} — {variant} to your cart.",
        "ru": "Добавлено в корзину: {product} — {variant} × {qty}.",
    },
    "catalogue.added_capped_note": {
        "en": "That's all the stock there is.",
        "ru": "Это весь доступный запас.",
    },
    "catalogue.keep_browsing_button": {"en": "Keep browsing", "ru": "Продолжить покупки"},
    "catalogue.view_cart_button": {"en": "View cart", "ru": "Посмотреть корзину"},
    # -- cart.py --------------------------------------------------------------
    "cart.empty": {"en": "Your cart is empty.", "ru": "Ваша корзина пуста."},
    "cart.heading": {"en": "Your cart", "ru": "Ваша корзина"},
    "cart.line_unavailable_suffix": {"en": " (no longer available)", "ru": " (больше недоступно)"},
    "cart.removed_out_of_stock": {
        "en": "{name} was removed from your cart because it is now out of stock.",
        "ru": "{name} удалён из корзины, потому что закончился.",
    },
    "cart.remove_button": {"en": "✕ Remove {name}", "ru": "✕ Удалить {name}"},
    "cart.subtotal": {"en": "Subtotal: {amount}", "ru": "Промежуточный итог: {amount}"},
    "cart.checkout_button": {"en": "Checkout", "ru": "Оформить заказ"},
    "cart.checkout_stub": {
        "en": "Checkout opens in the next release.",
        "ru": "Оплата откроется в следующем выпуске.",
    },
    "cart.back_to_cart_button": {"en": "Back to cart", "ru": "Назад в корзину"},
    "cart.clear_cart_button": {"en": "Clear cart", "ru": "Очистить корзину"},
    "cart.clear_confirm": {
        "en": "Clear all {count} {noun} from your cart?",
        "ru": "Удалить все товары из корзины ({count} шт.)?",
    },
    "cart.item_singular": {"en": "item", "ru": "товар"},
    "cart.item_plural": {"en": "items", "ru": "товаров"},
    "cart.yes_clear_button": {"en": "Yes, clear", "ru": "Да, очистить"},
    "cart.no_button": {"en": "No", "ru": "Нет"},
    # -- payments / delivery ----------------------------------------------------
    "pay.invoice_title": {"en": "Order #{id}", "ru": "Заказ №{id}"},
    "pay.invoice_description": {
        "en": "Digital goods: {items}",
        "ru": "Цифровые товары: {items}",
    },
    "pay.pay_button": {"en": "Pay ⭐ {total}", "ru": "Оплатить ⭐ {total}"},
    "pay.cancel_order_button": {"en": "Cancel order", "ru": "Отменить заказ"},
    "pay.mock_banner": {
        "en": "🧪 DEMO MODE — no real payment",
        "ru": "🧪 ДЕМО-РЕЖИМ — реальная оплата не производится",
    },
    "pay.mock_heading": {"en": "Order #{id}", "ru": "Заказ №{id}"},
    "pay.mock_total": {"en": "Total: {total}", "ru": "Итого: {total}"},
    "pay.mock_pay_button": {"en": "Pay (demo)", "ru": "Оплатить (демо)"},
    "deliver.text": {"en": "{name}\n\n{body}", "ru": "{name}\n\n{body}"},
    "deliver.file_caption": {"en": "{name}", "ru": "{name}"},
    "deliver.keys": {"en": "{name}\n\n{keys}", "ru": "{name}\n\n{keys}"},
    "deliver.receipt": {
        "en": "✅ Thank you! Order #{id} is complete.\n\n{items}\n\nTotal: {total}",
        "ru": "✅ Спасибо! Заказ №{id} выполнен.\n\n{items}\n\nИтого: {total}",
    },
    "deliver.receipt_line": {"en": "{name} × {qty}", "ru": "{name} × {qty}"},
    "pay.err_unavailable": {
        "en": "This order is no longer available.",
        "ru": "Этот заказ больше недоступен.",
    },
    "pay.err_not_pending": {
        "en": "This order is no longer open for payment.",
        "ru": "Этот заказ больше не ожидает оплаты.",
    },
    "pay.err_expired": {
        "en": "This order has expired. Please start a new one.",
        "ru": "Срок заказа истёк. Оформите новый заказ.",
    },
    "pay.err_mode": {
        "en": "Payments for this order are not available right now.",
        "ru": "Оплата этого заказа сейчас недоступна.",
    },
    "pay.err_amount": {
        "en": "The amount does not match this order.",
        "ru": "Сумма не соответствует заказу.",
    },
    "pay.err_reservation": {
        "en": "Your items are no longer reserved. Please start a new order.",
        "ru": "Ваши товары больше не зарезервированы. Оформите новый заказ.",
    },
    "pay.received": {
        "en": "✅ Payment received for order #{id}. Your items are on their way.",
        "ru": "✅ Оплата заказа №{id} получена. Ваши товары уже в пути.",
    },
    "pay.received_delayed": {
        "en": "✅ Payment received for order #{id}. Delivery is delayed — we are sorting it out and will send your items shortly.",
        "ru": "✅ Оплата заказа №{id} получена. Доставка задерживается — мы разбираемся и скоро отправим ваши товары.",
    },
    "pay.orphan_expired": {
        "en": "Your payment for order #{id} arrived after the order expired, so it has been refunded.",
        "ru": "Оплата заказа №{id} пришла после истечения срока заказа, поэтому она возвращена.",
    },
    "pay.orphan_paid": {
        "en": "That was a duplicate payment for order #{id}, which was already paid. It has been refunded.",
        "ru": "Это был повторный платёж за уже оплаченный заказ №{id}. Он возвращён.",
    },
    "pay.orphan_refund_failed": {
        "en": "Your payment for order #{id} could not be applied and the automatic refund failed. The shop owner has been alerted and will sort it out.",
        "ru": "Платёж за заказ №{id} не удалось применить, а автоматический возврат не прошёл. Владелец магазина уведомлён и всё решит.",
    },
    "pay.mismatch": {
        "en": "We could not match your payment to order #{id}. The shop owner has been alerted.",
        "ru": "Не удалось сопоставить ваш платёж с заказом №{id}. Владелец магазина уведомлён.",
    },
    "pay.support_text": {
        "en": "Need help with a payment? Quote your order number when you contact the shop.\n\n{note}",
        "ru": "Нужна помощь с оплатой? При обращении в магазин назовите номер заказа.\n\n{note}",
    },
    "checkout.empty": {
        "en": "There is nothing in your cart to check out. Add something from the catalogue first.",
        "ru": "В корзине нечего оформлять. Сначала добавьте товары из каталога.",
    },
    "checkout.open_order": {
        "en": "You already have an open order (#{id}). It expires in {minutes} min.",
        "ru": "У вас уже есть открытый заказ (№{id}). Он истечёт через {minutes} мин.",
    },
    "checkout.resume_button": {"en": "Resume", "ru": "Продолжить"},
    "checkout.review_heading": {"en": "Review your order", "ru": "Проверьте заказ"},
    "checkout.total": {"en": "Total: {total}", "ru": "Итого: {total}"},
    "checkout.demo_notice": {
        "en": "🧪 Demo mode: no real payment will be taken.",
        "ru": "🧪 Демо-режим: реальная оплата не производится.",
    },
    "checkout.pay_button": {"en": "Pay ⭐ {total}", "ru": "Оплатить ⭐ {total}"},
    "checkout.dropped_note": {
        "en": "{count} unavailable item(s) were left out of this order.",
        "ru": "Недоступные товары ({count} шт.) не вошли в заказ.",
    },
    "checkout.too_large": {
        "en": "Your order comes to ⭐ {total}, above the ⭐ {limit} limit for a single order. Remove some items and try again.",
        "ru": "Сумма заказа ⭐ {total} превышает лимит ⭐ {limit} на один заказ. Уберите часть товаров и попробуйте снова.",
    },
    "checkout.stock_short": {
        "en": "Only {available} left of one of your items. Lower its quantity in your cart and try again.",
        "ru": "Одного из ваших товаров осталось только {available}. Уменьшите количество в корзине и попробуйте снова.",
    },
    "checkout.presented": {
        "en": "Order #{id} is ready — complete the payment in the message below.",
        "ru": "Заказ №{id} готов — завершите оплату в сообщении ниже.",
    },
    "checkout.mode_changed": {
        "en": "That order was created in a different payment mode and has been cancelled. Please start checkout again.",
        "ru": "Этот заказ был создан в другом режиме оплаты и отменён. Начните оформление заново.",
    },
    "checkout.cancel_confirm": {
        "en": "Cancel order #{id}? Your items will go back to your cart.",
        "ru": "Отменить заказ №{id}? Товары вернутся в корзину.",
    },
    "checkout.yes_cancel_button": {"en": "Yes, cancel", "ru": "Да, отменить"},
    "checkout.keep_button": {"en": "No, keep it", "ru": "Нет, оставить"},
    "checkout.cancelled": {
        "en": "Order #{id} was cancelled.",
        "ru": "Заказ №{id} отменён.",
    },
    "checkout.cancelled_restored": {
        "en": "Order #{id} was cancelled. Your items are back in your cart.",
        "ru": "Заказ №{id} отменён. Товары вернулись в корзину.",
    },
    "checkout.cancel_refused": {
        "en": "This order can't be cancelled any more.",
        "ru": "Этот заказ уже нельзя отменить.",
    },
    "checkout.present_failed": {
        "en": "We couldn't start the payment. Your items are back in your cart. Please try again.",
        "ru": "Не удалось начать оплату. Товары вернулись в корзину. Попробуйте ещё раз.",
    },
    "checkout.mock_done": {
        "en": "🧪 Demo payment complete for order #{id}.",
        "ru": "🧪 Демо-оплата заказа №{id} завершена.",
    },
    "checkout.mock_unavailable": {
        "en": "Demo payments are not available.",
        "ru": "Демо-оплата недоступна.",
    },
    "cart.over_limit_warning": {
        "en": "⚠️ Your cart is over the ⭐ {limit} limit for one order — remove some items to check out.",
        "ru": "⚠️ Сумма корзины превышает лимит ⭐ {limit} на один заказ — уберите часть товаров, чтобы оформить заказ.",
    },
    "jobs.order_expired": {
        "en": "Your order #{id} expired and its reservation was released. You can start a new one any time.",
        "ru": "Срок заказа №{id} истёк, резерв снят. Вы можете оформить новый заказ в любое время.",
    },
    "library.heading": {"en": "My Library", "ru": "Моя библиотека"},
    "library.empty": {
        "en": "Your library is empty. Things you buy will appear here.",
        "ru": "Ваша библиотека пуста. Здесь появятся ваши покупки.",
    },
    "library.item_detail": {
        "en": "{name}\nOrder #{id}\nStatus: {status}",
        "ru": "{name}\nЗаказ №{id}\nСтатус: {status}",
    },
    "library.being_delivered": {"en": "Being delivered", "ru": "Доставляется"},
    "library.status_delivered": {"en": "Delivered", "ru": "Доставлен"},
    "library.status_refunded": {"en": "Refunded", "ru": "Возвращён"},
    "library.send_button": {"en": "Send again", "ru": "Отправить снова"},
    "library.back_button": {"en": "Back to library", "ru": "Назад в библиотеку"},
    "library.resent": {"en": "Sent again.", "ru": "Отправлено снова."},
    "library.resend_failed": {
        "en": "Couldn't send it right now. Try again in a moment.",
        "ru": "Сейчас отправить не удалось. Попробуйте чуть позже.",
    },
    "library.unavailable": {
        "en": "That item is not available.",
        "ru": "Этот товар недоступен.",
    },
    # -- order status labels (customer-facing) -------------------------------
    "status.pending_payment": {"en": "Awaiting payment", "ru": "Ожидает оплаты"},
    "status.paid": {"en": "Paid", "ru": "Оплачен"},
    "status.delivered": {"en": "Delivered", "ru": "Доставлен"},
    "status.cancelled": {"en": "Cancelled", "ru": "Отменён"},
    "status.expired": {"en": "Expired", "ru": "Истёк срок"},
    "status.refunded": {"en": "Refunded", "ru": "Возвращён"},
}


def t(key: str, lang: str, **kwargs: object) -> str:
    entry = STRINGS.get(key)
    if entry is None:
        logger.warning("i18n: missing key %r", key)
        return key

    template = entry.get(lang)
    if template is None:
        logger.warning(
            "i18n: missing language %r for key %r, falling back to %r", lang, key, DEFAULT_LANGUAGE
        )
        template = entry.get(DEFAULT_LANGUAGE)
        if template is None:
            logger.warning("i18n: key %r has no %r fallback either", key, DEFAULT_LANGUAGE)
            return key

    if not kwargs:
        return template
    try:
        return template.format(**kwargs)
    except (KeyError, IndexError):
        logger.warning("i18n: format error for key %r lang %r kwargs %r", key, lang, kwargs)
        return template


def status_label(status: str, lang: str) -> str:
    return t(f"status.{status}", lang)
